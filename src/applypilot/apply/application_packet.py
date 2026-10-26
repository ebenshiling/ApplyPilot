"""Workspace-owned PDF readiness and preparation for the assisted browser."""
from __future__ import annotations

import hashlib
import secrets
import threading
from pathlib import Path

from applypilot import command_center as cc
from applypilot.eligibility import packet

DOCUMENTS = (("resume", "CV", "tailored_resume_path"),
             ("cover", "Cover letter", "cover_letter_path"),
             ("statement", "Supporting statement", "supporting_statement_path"))
_locks = {}
_guard = threading.Lock()


def _paths(root, raw):
    source = Path(raw).resolve()
    pdf = source if source.suffix.lower() == ".pdf" else source.with_suffix(".pdf").resolve()
    if not source.is_relative_to(root) or not pdf.is_relative_to(root):
        raise ValueError("Document unavailable in this workspace")
    return source, pdf


def _stamp(source):
    report = source.with_name(source.stem + "_REPORT.json")
    return max(source.stat().st_mtime_ns, report.stat().st_mtime_ns if report.is_file() else 0)


def _signature(source):
    return hashlib.sha256(source.read_bytes()).hexdigest(), _stamp(source)


def status(conn, app_dir: Path, job_id: int, *,include_review=True) -> dict:
    root = app_dir.resolve()
    job = cc.get_job(conn, job_id)
    base = packet(conn, job)
    documents, digest = [], hashlib.sha256()
    missing = [m for m in base["missing"] if m not in {"resume", "cover", "statement", "PDF rendering"}]
    for kind, label, field in DOCUMENTS:
        raw = job.get(field)
        item = {"kind": kind, "label": label, "required": bool(base["required"][kind]),
                "included": bool(raw), "text_ready": False, "pdf_state": "not_included", "can_render": False}
        digest.update((kind + str(raw or "")).encode())
        if raw:
            try:
                source, pdf = _paths(root, raw)
                if not source.is_file():
                    item["pdf_state"] = "source_missing"
                elif source.suffix.lower() not in {".txt", ".pdf"}:
                    item["pdf_state"] = "unsupported_format"
                else:
                    content = source.read_bytes()
                    digest.update(hashlib.sha256(content).digest())
                    if pdf.is_file():
                        digest.update(hashlib.sha256(pdf.read_bytes()).digest())
                    digest.update(str(_stamp(source)).encode())
                    if not content.strip():
                        item["pdf_state"] = "source_empty"
                    else:
                        from applypilot.canonical import require_final,require_pdf,FactReviewRequired
                        try:
                            factual = require_final(conn,root,source,job,kind=kind)
                            digest.update(factual["dependency_hash"].encode())
                            item["factual_state"]="current"
                        except FactReviewRequired:
                            item["factual_state"]="review_required"
                            item["pdf_state"]="fact_review_required"
                            raise ValueError("Factual review required")
                        item["text_ready"] = source.suffix.lower() == ".txt"
                        stamp = source.stat().st_mtime_ns if source.suffix.lower() == ".pdf" else _stamp(source)
                        item["pdf_state"] = "missing" if not pdf.is_file() or pdf.stat().st_size == 0 else "stale" if pdf.stat().st_mtime_ns < stamp else "ready"
                        item["can_render"] = item["text_ready"] and item["pdf_state"] in {"missing", "stale"}
                        if item["pdf_state"]=="ready":
                            try:require_pdf(conn,root,source,pdf,job,kind=kind)
                            except FactReviewRequired:
                                item["pdf_state"]="unbound"
                                item["can_render"]=item["text_ready"]
            except (OSError, ValueError):
                if item.get("factual_state")!="review_required":item["pdf_state"] = "unavailable"
        if (item["required"] or item["included"]) and item["pdf_state"] != "ready":
            reason = {"missing": "PDF missing", "stale": "PDF out of date", "unavailable": "document unavailable in this workspace",
                       "unsupported_format": "unsupported document format", "source_empty": "draft is empty", "fact_review_required":"factual review required", "unbound":"PDF factual round-trip required"}.get(item["pdf_state"], "draft missing")
            missing.append(label + ": " + reason)
        documents.append(item)
    excluded = list(base["excluded"])
    if job.get("apply_status") == "submission_uncertain":
        excluded.append("Please reconcile the uncertain submission before starting another application")
    if job.get("apply_status") == "applied" and not job.get("applied_at"):
        excluded.append("Already submitted")
    output={"documents": documents, "missing": missing, "excluded": excluded,"required":base["required"],
            "ready": not missing and not excluded, "can_prepare": any(d["can_render"] for d in documents) and not excluded,
             "packet_token": digest.hexdigest()}
    if include_review:
        from applypilot.document_review import current
        reviewed=current(conn,app_dir,job_id,packet=output)
        output.update(document_review=reviewed,application_ready=output["ready"] and reviewed["approved"])
    return output


def _mutable(conn, job_id):
    job = cc.get_job(conn, job_id)
    from applypilot.eligibility import exclusions
    if exclusions(conn, job,preparation=False) or job.get("apply_status") in cc.ACTIVE_STATES | {"applied", "submission_uncertain"}:
        raise ValueError("PDF preparation is unavailable for an active, submitted, archived, blocked or uncertain application")
    return job


def prepare(conn, app_dir: Path, job_id: int) -> dict:
    root = app_dir.resolve()
    with _guard:
        lock = _locks.setdefault((root, job_id), threading.Lock())
    if not lock.acquire(blocking=False):
        raise ValueError("PDF preparation is already running for this application")
    try:
        job = _mutable(conn, job_id)
        before = status(conn, root, job_id)
        if not before["can_prepare"] and not before["ready"]:
            raise ValueError("Prepare the missing or unavailable drafts in their Studios before rendering PDFs")
        rendered, errors = [], []
        from applypilot.scoring.pdf import convert_to_pdf, build_pdf_metadata
        from pypdf import PdfReader
        for item in before["documents"]:
            if not item["can_render"]:
                continue
            field = next(field for kind, label, field in DOCUMENTS if kind == item["kind"])
            source, canonical = _paths(root, job[field])
            temporary = canonical.with_name("." + canonical.stem + "-" + secrets.token_hex(8) + ".pdf")
            try:
                signature = _signature(source)
                output = convert_to_pdf(source, output_path=temporary, document_kind=item["kind"], pdf_metadata=build_pdf_metadata("resume" if item["kind"] == "resume" else "letter", job=job))
                if Path(output) != temporary or not temporary.is_file() or not PdfReader(str(temporary)).pages:
                    raise ValueError("Renderer did not produce a valid PDF")
                current = _mutable(conn, job_id)
                if str(current.get(field) or "") != str(job[field]) or _signature(source) != signature:
                    raise ValueError("Draft changed during PDF preparation; refresh and retry")
                if temporary.stat().st_mtime_ns < _stamp(source):
                    raise ValueError("Draft timestamp is newer than the rendered PDF; check the workspace clock")
                from applypilot.canonical import bind_pdf
                bind_pdf(conn,root,source,temporary,job,kind=item["kind"])
                current = _mutable(conn,job_id)
                if str(current.get(field) or "") != str(job[field]) or _signature(source) != signature:
                    raise ValueError("Draft changed during PDF preparation; refresh and retry")
                temporary.replace(canonical)
                temporary.with_suffix(temporary.suffix+".claims.json").replace(canonical.with_suffix(canonical.suffix+".claims.json"))
                conn.execute("UPDATE cc_documents SET pdf_path=? WHERE job_url=? AND text_path=?", (str(canonical), job["url"], str(source)))
                conn.execute("UPDATE cc_writing_documents SET pdf_path=? WHERE job_url=? AND text_path=?", (str(canonical), job["url"], str(source)))
                conn.commit()
                rendered.append(item["kind"])
            except PermissionError:
                errors.append({"kind": item["kind"], "error": "PDF is locked; close its viewer and retry"})
            except ValueError as exc:
                errors.append({"kind": item["kind"], "error": str(exc) if str(exc).startswith(("Draft changed", "Draft timestamp", "Renderer did not")) else "PDF rendering failed; check this document's text"})
            except Exception:
                errors.append({"kind": item["kind"], "error": "PDF rendering failed; check Chromium and this document's text"})
            finally:
                temporary.unlink(missing_ok=True)
                temporary.with_suffix(temporary.suffix+".claims.json").unlink(missing_ok=True)
                temporary.with_name(temporary.stem + "_fresh.pdf").unlink(missing_ok=True)
        _mutable(conn, job_id)
        conn.execute("UPDATE jobs SET pdf_status=?,pdf_error=?,pdf_attempts=CASE WHEN ? THEN COALESCE(pdf_attempts,0)+1 ELSE 0 END WHERE rowid=?",
                     ("failed" if errors else "completed", "; ".join(e["kind"] + ": " + e["error"] for e in errors) or None, bool(errors), job_id))
        if rendered or errors:
            cc.event(conn, job["url"], "packet", "Rendered PDFs: " + ", ".join(rendered) if rendered else "PDF preparation needs attention", state="pdf_failed" if errors else "pdf_ready", source="candidate")
        conn.commit()
        return {"packet": status(conn, root, job_id), "rendered": rendered, "errors": errors}
    finally:
        lock.release()


def document(conn, app_dir: Path, job_id: int, kind: str, fmt: str):
    selected = next((d for d in DOCUMENTS if d[0] == kind), None)
    if not selected or fmt not in {"html", "pdf"}:
        raise ValueError("Choose a packet document and HTML or PDF format")
    job = cc.get_job(conn, job_id)
    if not job.get(selected[2]):
        raise LookupError("Packet document is not prepared")
    source, pdf = _paths(app_dir.resolve(), job[selected[2]])
    if not source.is_file():
        raise LookupError("Packet document is unavailable")
    if fmt == "pdf":
        item = next(d for d in status(conn, app_dir, job_id)["documents"] if d["kind"] == kind)
        if item["pdf_state"] != "ready":
            raise LookupError("Prepare the current PDF before downloading it")
        return pdf.read_bytes(), "application/pdf"
    if source.suffix.lower() != ".txt":
        raise LookupError("Text preview is not available for this document")
    from applypilot.scoring.pdf import build_html, parse_resume, build_letter_html
    text = source.read_text(encoding="utf-8")
    if kind == "resume":
        parsed = parse_resume(text)
        if parsed["sections"]:
            html = build_html(parsed)
        else:
            import html as escaping
            html = '<pre style="white-space:pre-wrap">' + escaping.escape(text) + '</pre>'
    else:
        html = build_letter_html(text)
    return html.encode(), "text/html; charset=utf-8"
