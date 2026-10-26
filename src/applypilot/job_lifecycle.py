"""Explicit closure and user-requested cleanup with compact retained history."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from pathlib import Path

from applypilot import command_center as cc

REASONS = {
    "advert_closed": "Advert closed",
    "requirements_not_met": "Requirements not met",
    "preparation_failed": "CV / document preparation failed",
    "rejected": "Rejected by employer",
    "withdrawn": "Application withdrawn",
    "not_pursuing": "No longer pursuing",
}
GENERATED = {"tailored_resumes", "cover_letters", "supporting_statements", "document_history", "writing_documents", "browser_sessions"}
JOB_PATHS = ("tailored_resume_path", "cover_letter_path", "supporting_statement_path", "tailor_report_path", "cover_letter_report_path")
KEEP = ("id", "url", "title", "company", "site", "fit_score", "applied_at", "discovered_at", "apply_status", "country_code", "employer_id", "vacancy_metadata", "immigration_assessment")


def init_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cc_job_closures(job_url TEXT PRIMARY KEY,reason TEXT NOT NULL,detail TEXT,closed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cc_retired_jobs(id TEXT PRIMARY KEY,job_url TEXT UNIQUE NOT NULL,canonical_url TEXT,
            original_job_id INTEGER NOT NULL,snapshot TEXT NOT NULL,reason TEXT NOT NULL,detail TEXT,deleted_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS cc_retired_number ON cc_retired_jobs(original_job_id);
        CREATE TABLE IF NOT EXISTS cc_cleanup_files(id INTEGER PRIMARY KEY,history_id TEXT NOT NULL,path TEXT NOT NULL,
            bytes INTEGER NOT NULL,state TEXT NOT NULL,detail TEXT,updated_at TEXT NOT NULL,UNIQUE(history_id,path));
        CREATE TRIGGER IF NOT EXISTS cc_retired_no_reinsert BEFORE INSERT ON jobs
        WHEN EXISTS(SELECT 1 FROM cc_retired_jobs WHERE job_url=NEW.url)
        BEGIN SELECT RAISE(IGNORE); END;
        CREATE TRIGGER IF NOT EXISTS cc_retired_id_floor AFTER INSERT ON jobs
        WHEN NEW.rowid <= (SELECT MAX(original_job_id) FROM cc_retired_jobs)
        BEGIN UPDATE jobs SET rowid=(SELECT MAX(n)+1 FROM
            (SELECT COALESCE(MAX(rowid),0) AS n FROM jobs UNION ALL SELECT COALESCE(MAX(original_job_id),0) FROM cc_retired_jobs))
            WHERE url=NEW.url; END;
    """)


def _table(conn, name):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _safe(root, raw):
    if not raw:
        return None
    path = Path(raw)
    path = path if path.is_absolute() else root / path
    try:
        resolved = path.resolve()
        relative = resolved.relative_to(root)
        if not (relative.parts[0] in GENERATED or relative.parts[:2] == ("apply_workers", "current")):
            return None
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root and p.is_relative_to(root)):
            return None
        return resolved
    except (OSError, ValueError, IndexError):
        return None


def _family(root, raw, *, reference=False):
    if reference and raw:
        candidate = Path(raw)
        raw = (candidate if candidate.is_absolute() else root / candidate).resolve()
    path = _safe(root, raw)
    if not path:
        return set()
    stem = path.stem.removesuffix("_REPORT").removesuffix("_JOB")
    return {path, *(path.with_name(stem+s) for s in (".txt", ".pdf", ".html", "_REPORT.json", "_JOB.txt", "_fresh.pdf", ".txt.claims.json", ".pdf.claims.json", ".review.json"))}


def _references(conn, root, excluding=None):
    protected = set()
    for row in conn.execute("SELECT * FROM jobs WHERE url!=?", (excluding or "",)):
        for key in JOB_PATHS:
            protected.update(_family(root, row[key], reference=True))
    for table, fields in (("cc_documents", ("text_path", "pdf_path", "report_path")), ("cc_writing_documents", ("text_path", "pdf_path"))):
        for row in conn.execute(f"SELECT * FROM {table} WHERE job_url IS NULL OR job_url!=?", (excluding or "",)):
            for key in fields:
                protected.update(_family(root, row[key], reference=True))
    for row in conn.execute("SELECT d.* FROM cc_documents d JOIN cc_job_meta m ON m.document_id=d.id WHERE m.job_url!=?", (excluding or "",)):
        for key in ("text_path", "pdf_path", "report_path"):
            protected.update(_family(root, row[key], reference=True))
    for table, key in (("cc_cv_sources", "original_path"), ("cc_portfolio_assets", "path")):
        if _table(conn, table):
            for row in conn.execute(f"SELECT {key} FROM {table}"):
                protected.update(_family(root, row[0], reference=True))
    from applypilot.setup_workspace import read_searches_dict
    for value in read_searches_dict(root).get("resume_routing", {}).get("variants", {}).values():
        if isinstance(value, str):
            protected.update(_family(root, value, reference=True))
    # A retained browser packet can be referenced by another session/job.
    if _table(conn, "cc_browser_sessions"):
        for row in conn.execute("SELECT id FROM cc_browser_sessions WHERE job_url!=?", (excluding or "",)):
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(row[0])):
                continue
            folder = _safe(root, root / "browser_sessions" / str(row[0]))
            if folder and folder.is_dir():
                protected.update(p.resolve() for p in folder.rglob("*") if _safe(root, p) and p.is_file())
    return protected


def _artifacts(conn, root, job):
    candidates = set()
    for key in JOB_PATHS:
        candidates.update(_family(root, job.get(key)))
    for table, fields in (("cc_documents", ("text_path", "pdf_path", "report_path")), ("cc_writing_documents", ("text_path", "pdf_path"))):
        for row in conn.execute(f"SELECT * FROM {table} WHERE job_url=?", (job["url"],)):
            for key in fields:
                candidates.update(_family(root, row[key]))
    # Include unapproved versions and their diagnostics using both stable URL hash
    # and job number, never title/number alone.
    uid = hashlib.sha1(job["url"].encode()).hexdigest()[:8]
    marker = re.compile(rf"(?:^|_)J{job['id']}_(?:[-A-Za-z0-9_]+_)?{uid}(?:[_.]|$)")
    for name in ("tailored_resumes", "cover_letters", "supporting_statements"):
        folder = root / name
        if folder.is_dir() and folder.resolve().is_relative_to(root):
            candidates.update(p for p in folder.iterdir() if p.is_file() and marker.search(p.name))
    if _table(conn, "cc_browser_sessions"):
        for row in conn.execute("SELECT id FROM cc_browser_sessions WHERE job_url=?", (job["url"],)):
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(row[0])):
                continue
            folder = _safe(root, root / "browser_sessions" / str(row[0]))
            if folder and folder.is_dir():
                candidates.update(p for p in folder.rglob("*") if p.is_file())
    protected = _references(conn, root, job["url"])
    files, retained = [], []
    for candidate in sorted(candidates, key=str):
        path = _safe(root, candidate)
        if not path or not path.is_file():
            continue
        stat = path.stat()
        item = {"path": path.relative_to(root).as_posix(), "bytes": stat.st_size, "stamp": stat.st_mtime_ns}
        if path in protected or stat.st_nlink > 1:
            retained.append(item)
        else:
            files.append(item)
    return files, retained


def _busy(conn, job):
    reasons = []
    if job.get("apply_status") in cc.ACTIVE_STATES:
        reasons.append("Stop the active application worker first")
    if job.get("apply_status") == "submission_uncertain":
        reasons.append("Reconcile the uncertain submission before closing or deleting this record")
    if _table(conn, "cc_browser_sessions") and conn.execute("SELECT 1 FROM cc_browser_sessions WHERE job_url=? AND state NOT IN ('confirmed','uncertain','stopped','failed')", (job["url"],)).fetchone():
        reasons.append("Stop the active browser session first")
    if conn.execute("SELECT 1 FROM cc_schedules WHERE job_url=? AND state='running'", (job["url"],)).fetchone():
        reasons.append("Wait for the scheduled application to finish or stop it")
    return reasons


def _pipeline_busy(conn):
    return _table(conn, "cc_pipeline_runs") and bool(conn.execute("SELECT 1 FROM cc_pipeline_runs WHERE status='running'").fetchone())


def reconcile_pipeline(conn, runner):
    """Only settle runs the dashboard can prove have ended; unknown CLI work stays guarded."""
    if not runner or not _table(conn, 'cc_pipeline_runs'):
        return
    current = runner.status()
    for run in runner.history(limit=500):
        if not run.get('ended_at'):
            continue
        if run.get('run_id') == current.get('run_id') and (current.get('running') or current.get('starting')):
            continue
        conn.execute("UPDATE cc_pipeline_runs SET status='interrupted',ended_at=COALESCE(ended_at,?) WHERE run_id=? AND status='running'", (cc.now(), run['run_id']))
    conn.commit()


def preview(conn, app_dir, job_id):
    root = Path(app_dir).resolve()
    job = cc.get_job(conn, job_id)
    files, retained = _artifacts(conn, root, job)
    closure = conn.execute("SELECT * FROM cc_job_closures WHERE job_url=?", (job["url"],)).fetchone()
    meta = conn.execute("SELECT * FROM cc_job_meta WHERE job_url=?", (job["url"],)).fetchone()
    check = conn.execute("SELECT * FROM cc_listing_checks WHERE job_url=?", (job["url"],)).fetchone()
    context = {key: str(job.get(key) or "")[:1600] for key in ("tailor_status", "tailor_failure_detail", "tailor_requirement_gaps", "apply_error")}
    reason = closure["reason"] if closure else "rejected" if meta and meta["hiring_status"] == "rejected" else "advert_closed" if check and check["status"] in {"closed", "possibly_closed"} else "preparation_failed" if job.get("tailor_status") and job["tailor_status"] != "approved" else "not_pursuing"
    token = hashlib.sha256(json.dumps({"job": job, "meta": dict(meta) if meta else {}, "closure": dict(closure) if closure else {}, "files": files, "retained": retained}, sort_keys=True).encode()).hexdigest()
    blockers = _busy(conn, job)
    return {"job": {k: job.get(k) for k in KEEP}, "reason": reason, "closure": dict(closure) if closure else None,
            "reasons": REASONS, "preparation": context, "listing_check": dict(check) if check else None,
            "files": files, "retained_files": retained, "reclaimable_bytes": sum(p["bytes"] for p in files),
            "cleanup_token": token, "close_blockers": blockers,
            "delete_blockers": blockers + (["Wait for the preparation pipeline to finish or stop it before deleting files"] if _pipeline_busy(conn) else [])}


def _close(conn, job, reason, detail):
    if reason not in REASONS:
        raise ValueError("Choose an end-of-opportunity reason")
    if reason in {"rejected", "withdrawn"} and not job.get("applied_at"):
        raise ValueError("Record a submitted application before adding rejection or withdrawal; use a preparation/closure reason otherwise")
    blockers = _busy(conn, job)
    if blockers:
        raise ValueError("; ".join(blockers))
    if len(detail) > 2000:
        raise ValueError("Closure note must be at most 2,000 characters")
    detail = cc.redact(detail)
    conn.execute("INSERT INTO cc_job_closures VALUES(?,?,?,?) ON CONFLICT(job_url) DO UPDATE SET reason=excluded.reason,detail=excluded.detail,closed_at=excluded.closed_at", (job["url"], reason, detail, cc.now()))
    conn.execute("INSERT OR IGNORE INTO cc_job_meta(job_url) VALUES(?)", (job["url"],))
    conn.execute("UPDATE cc_job_meta SET visibility='archived',due_at=NULL,updated_at=? WHERE job_url=?", (cc.now(), job["url"]))
    if reason in {"rejected", "withdrawn"}:
        conn.execute("UPDATE cc_job_meta SET hiring_status=? WHERE job_url=?", (reason, job["url"]))
        cc.event(conn, job["url"], "hiring", detail, state=reason, source="candidate")
    if reason == "advert_closed":
        conn.execute("INSERT INTO cc_listing_checks VALUES(?,'closed',?,?) ON CONFLICT(job_url) DO UPDATE SET status='closed',detail=excluded.detail,checked_at=excluded.checked_at", (job["url"], "Closed by candidate: " + detail, cc.now()))
    conn.execute("UPDATE cc_schedules SET state='canceled',detail='Opportunity closed' WHERE job_url=? AND state='queued'", (job["url"],))
    context = "; ".join(str(job.get(k) or "")[:800] for k in ("tailor_status", "tailor_failure_detail", "tailor_requirement_gaps") if job.get(k))
    cc.event(conn, job["url"], "closure", REASONS[reason] + (": " + detail if detail else "") + ("\nPreparation evidence: " + context if reason in {"requirements_not_met", "preparation_failed"} and context else ""), state=reason, source="candidate")


def close_record(conn, root, job_id, reason, detail=""):
    conn.execute("BEGIN IMMEDIATE")
    try:
        job = cc.get_job(conn, job_id)
        _close(conn, job, reason, str(detail))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"closed": True, "reason": reason}


def retire(conn, root, data):
    root = Path(root).resolve()
    conn.execute("BEGIN IMMEDIATE")
    try:
        job = cc.get_job(conn, int(data.get("id") or 0))
        plan = preview(conn, root, job["id"])
        if plan["delete_blockers"]:
            raise ValueError("; ".join(plan["delete_blockers"]))
        if not secrets.compare_digest(str(data.get("cleanup_token") or ""), plan["cleanup_token"]):
            raise ValueError("Cleanup preview changed; reload it before deleting")
        reason = str(data.get("reason") or "")
        _close(conn, job, reason, str(data.get("detail") or ""))
        meta = dict(conn.execute("SELECT * FROM cc_job_meta WHERE job_url=?", (job["url"],)).fetchone())
        snapshot = {k: job.get(k) for k in KEEP}
        snapshot.update({k: meta.get(k) for k in ("hiring_status", "reference", "submission_method", "evidence")})
        snapshot["notes"] = str(meta.get("notes") or "")[:2000]
        snapshot["evidence"] = str(snapshot.get("evidence") or "")[:2000]
        ident = secrets.token_urlsafe(18)
        from applypilot.database import normalize_url
        conn.execute("INSERT INTO cc_retired_jobs VALUES(?,?,?,?,?,?,?,?)", (ident, job["url"], normalize_url(job["url"]), job["id"], json.dumps(snapshot), reason, cc.redact(str(data.get("detail") or "")), cc.now()))
        for item in plan["files"]:
            conn.execute("INSERT INTO cc_cleanup_files(history_id,path,bytes,state,updated_at) VALUES(?,?,?,'pending',?)", (ident, item["path"], item["bytes"], cc.now()))
        # Remove bulky job-specific contexts, keeping events, outcomes, source CVs,
        # inbox messages and usage ledgers. Shared versions stay addressable.
        conn.execute("DELETE FROM cc_documents WHERE job_url=? AND id NOT IN (SELECT document_id FROM cc_job_meta WHERE job_url!=? AND document_id IS NOT NULL)", (job["url"], job["url"]))
        conn.execute("DELETE FROM cc_writing_documents WHERE job_url=?", (job["url"],))
        for table in ("cc_pipeline_items", "cc_variant_evaluations", "cc_campaign_jobs", "cc_controls"):
            if _table(conn, table):
                conn.execute(f"DELETE FROM {table} WHERE job_url=?", (job["url"],))
        if _table(conn, "cc_browser_sessions"):
            conn.execute("DELETE FROM cc_browser_sessions WHERE job_url=?", (job["url"],))
        conn.execute("UPDATE cc_job_meta SET visibility='deleted',document_id=NULL,notes='',recruiter='',due_at=NULL WHERE job_url=?", (job["url"],))
        cc.event(conn, job["url"], "deletion", "Job removed; compact identity, outcome/reference and activity timeline retained. Generated-file cleanup requested.", state="deleted", source="candidate")
        conn.execute("DELETE FROM jobs WHERE url=?", (job["url"],))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    from applypilot.apply import assisted_browser as browser
    with browser._lock:
        for key, session in list(browser._sessions.items()):
            if session.root == root and session.job["url"] == job["url"] and session.state in browser.TERMINAL:
                browser._sessions.pop(key, None)
    try:
        cleanup = retry_cleanup(conn, root, ident)
    except Exception:
        conn.rollback()
        cleanup = {"freed_bytes": 0, "pending_files": conn.execute("SELECT COUNT(*) FROM cc_cleanup_files WHERE history_id=? AND state IN ('pending','failed')", (ident,)).fetchone()[0], "cleanup_deferred": True}
    return {"deleted": True, "history_id": ident, **cleanup}


def retry_cleanup(conn, root, ident):
    root = Path(root).resolve()
    history = conn.execute("SELECT * FROM cc_retired_jobs WHERE id=?", (ident,)).fetchone()
    if not history:
        raise LookupError("History record not found")
    if _pipeline_busy(conn):
        raise ValueError("Wait for the preparation pipeline to finish or stop it before cleanup")
    protected = _references(conn, root)
    freed = 0
    for item in conn.execute("SELECT * FROM cc_cleanup_files WHERE history_id=? AND state IN ('pending','failed')", (ident,)).fetchall():
        path = _safe(root, item["path"])
        state, detail = "removed", ""
        try:
            if not path:
                state, detail = "retained", "Unsafe or non-generated path retained"
            elif path in protected or (path.is_file() and path.stat().st_nlink > 1):
                state, detail = "retained", "Now referenced by another record or source"
            elif path.is_file():
                size = path.stat().st_size
                path.unlink()
                freed += size
                # Only empty generated directories, never recursive deletion.
                parent = path.parent
                while parent != root and parent.name not in GENERATED and _safe(root, parent):
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
        except OSError:
            state, detail = "failed", "File locked or unavailable; retry cleanup after closing its viewer"
        conn.execute("UPDATE cc_cleanup_files SET state=?,detail=?,updated_at=? WHERE id=?", (state, detail, cc.now(), item["id"]))
        conn.commit()
    pending = conn.execute("SELECT COUNT(*) FROM cc_cleanup_files WHERE history_id=? AND state IN ('pending','failed')", (ident,)).fetchone()[0]
    cc.event(conn, history["job_url"], "cleanup", f"Generated files removed: {freed} bytes this pass; {pending} files still pending.", state="cleanup_pending" if pending else "cleanup_complete", source="candidate")
    conn.commit()
    return {"freed_bytes": freed, "pending_files": pending}


def history(conn, root, search="", offset=0):
    rows = conn.execute("SELECT * FROM cc_retired_jobs ORDER BY deleted_at DESC").fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["snapshot"] = json.loads(item.pop("snapshot"))
        from applypilot.country_context import in_operation_queue
        if not in_operation_queue(item["snapshot"]):continue
        if search and search.casefold() not in " ".join(str(item["snapshot"].get(k) or "") for k in ("title", "company", "site")).casefold():
            continue
        item["pending_files"] = conn.execute("SELECT COUNT(*) FROM cc_cleanup_files WHERE history_id=? AND state IN ('pending','failed')", (item["id"],)).fetchone()[0]
        items.append(item)
    offset = max(0, int(offset))
    return {"records": items[offset:offset+50], "total": len(items), "offset": offset, "reasons": REASONS, "storage": storage(conn, root)}


def detail(conn, ident):
    row = conn.execute("SELECT * FROM cc_retired_jobs WHERE id=?", (ident,)).fetchone()
    if not row:
        raise LookupError("History record not found")
    item = dict(row)
    item["snapshot"] = json.loads(item["snapshot"])
    from applypilot.country_context import in_operation_queue
    if not in_operation_queue(item["snapshot"]):raise ValueError("History record is outside this country view")
    return {"record": item, "events": [dict(r) for r in conn.execute("SELECT * FROM cc_events WHERE job_url=? ORDER BY id DESC LIMIT 200", (row["job_url"],))],
            "cleanup": [dict(r) for r in conn.execute("SELECT path,bytes,state,detail FROM cc_cleanup_files WHERE history_id=?", (ident,))]}


def storage(conn, root):
    root = Path(root).resolve()
    total = 0
    for name in GENERATED:
        folder = root / name
        if folder.is_dir() and folder.resolve().is_relative_to(root):
            total += sum(p.stat().st_size for p in folder.rglob('*') if _safe(root, p) and p.is_file())
    database_bytes = sum(p.stat().st_size for p in (root/'applypilot.db', root/'applypilot.db-wal') if p.is_file())
    return {"generated_bytes": total, "database_bytes": database_bytes,
            "reusable_database_bytes": conn.execute("PRAGMA freelist_count").fetchone()[0]*conn.execute("PRAGMA page_size").fetchone()[0],
            "pending_files": conn.execute("SELECT COUNT(*) FROM cc_cleanup_files WHERE state IN ('pending','failed')").fetchone()[0]}


def compact(conn, root):
    if _pipeline_busy(conn) or conn.execute("SELECT 1 FROM jobs WHERE apply_status IN ('in_progress','running','submitting')").fetchone():
        raise ValueError("Stop active pipeline/application work before compacting the database")
    before = storage(conn, root)["database_bytes"]
    checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if checkpoint[0]:
        raise ValueError("The database is busy; retry compaction when workspace activity is idle")
    conn.execute("VACUUM")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    after = storage(conn, root)
    return {"freed_bytes": max(0, before-after["database_bytes"]), "storage": after}
