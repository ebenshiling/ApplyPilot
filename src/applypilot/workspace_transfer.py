"""Workspace transfer helpers for moving ApplyPilot between computers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import zipfile
import sqlite3


BASE_FILES = (
    "profile.json",
    "resume.txt",
    "resume.pdf",
    "searches.yaml",
    "sites.yaml",
    "employers.yaml",
    ".ai-settings.json",
    ".connector-key",
    "applypilot.db",
    ".env",
)

GENERATED_DIRS = (
    "tailored_resumes",
    "cover_letters",
    "supporting_statements",
    "documents",
    "document_history",
    "writing_documents",
    "cv_uploads",
    "cv_archive",
    "portfolio_assets",
)

OPTIONAL_DIRS = ("logs",)
EVIDENCE_DIRS = ("evidence/nz", "browser_sessions")

MANIFEST_NAME = "manifest.json"


def _portable_sqlite_image(data):
    # Online backup/serialize includes every committed page. Its inherited WAL
    # header is not usable by sqlite3.deserialize without a filesystem journal.
    # The standalone snapshot uses rollback-journal protocol, not the live DB.
    if data.startswith(b"SQLite format 3\x00") and len(data)>=100:
        value=bytearray(data)
        value[18:20]=b"\x01\x01"
        return bytes(value)
    return data


def _iter_files_under(root: Path) -> list[Path]:
    files: list[Path] = []
    if not root.exists() or not root.is_dir():
        return files
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files.append(p)
    return files


def _safe_archive_name(rel_path: Path) -> str:
    return rel_path.as_posix().lstrip("/")


def _owned(workspace,path):
    root=workspace.resolve()
    return path.resolve().is_relative_to(root) and not path.is_symlink() and not any(p.is_symlink() for p in path.parents if p!=root and p.is_relative_to(root))


def export_workspace(
    workspace: Path,
    archive_path: Path,
    *,
    include_env: bool = False,
    include_db: bool = True,
    include_generated: bool = False,
    include_logs: bool = False,
    include_evidence: bool = False,
) -> dict[str, object]:
    """Export workspace files into a zip archive.

    Returns a summary payload with counts and included paths.
    """
    workspace = Path(workspace).expanduser()
    archive_path = Path(archive_path).expanduser()

    if not workspace.exists() or not workspace.is_dir():
        raise FileNotFoundError(f"Workspace does not exist: {workspace}")

    archive_path.parent.mkdir(parents=True, exist_ok=True)

    include_names: set[str] = {"profile.json", "resume.txt", "resume.pdf", "searches.yaml", "sites.yaml", "employers.yaml"}
    if include_db:
        include_names.add("applypilot.db")
    if include_env:
        include_names.add(".env")
        include_names.add(".connector-key")
    include_names.add(".ai-settings.json")

    included: list[str] = []
    missing: list[str] = []

    with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in BASE_FILES:
            if name not in include_names:
                continue
            p = workspace / name
            if not p.exists() or not p.is_file():
                missing.append(name)
                continue
            if not _owned(workspace,p):
                missing.append(name)
                continue
            if name == "applypilot.db":
                # Capture committed WAL-backed data without altering the live DB.
                with sqlite3.connect(p.resolve().as_uri()+"?mode=ro", uri=True) as source:
                    with sqlite3.connect(":memory:") as snapshot:
                        source.backup(snapshot)
                        zf.writestr(name, _portable_sqlite_image(snapshot.serialize()))
            elif name == ".ai-settings.json" and not include_env:
                settings = json.loads(p.read_text(encoding="utf-8"))
                settings["credentials"] = {}
                zf.writestr(name, json.dumps(settings))
            else:
                zf.write(p, arcname=name)
            included.append(name)

        for p in _iter_files_under(workspace / "resume_variants"):
            if not _owned(workspace,p) or p.resolve()==archive_path.resolve():continue
            arc = _safe_archive_name(p.relative_to(workspace))
            zf.write(p, arcname=arc)
            included.append(arc)

        if include_generated:
            for d in GENERATED_DIRS:
                root = workspace / d
                for p in _iter_files_under(root):
                    if not _owned(workspace,p) or p.resolve()==archive_path.resolve():continue
                    rel = p.relative_to(workspace)
                    arc = _safe_archive_name(rel)
                    zf.write(p, arcname=arc)
                    included.append(arc)

        if include_evidence:
            for directory in EVIDENCE_DIRS:
                for p in _iter_files_under(workspace/directory):
                    if not _owned(workspace,p) or p.resolve()==archive_path.resolve():continue
                    arc=_safe_archive_name(p.relative_to(workspace))
                    zf.write(p,arcname=arc)
                    included.append(arc)

        if include_logs:
            for d in OPTIONAL_DIRS:
                root = workspace / d
                for p in _iter_files_under(root):
                    if not _owned(workspace,p) or p.resolve()==archive_path.resolve():continue
                    rel = p.relative_to(workspace)
                    arc = _safe_archive_name(rel)
                    zf.write(p, arcname=arc)
                    included.append(arc)

        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "workspace": str(workspace),
            "included": included,
            "missing": missing,
            "options": {
                "include_env": include_env,
                "include_db": include_db,
                "include_generated": include_generated,
                "include_logs": include_logs,
                "include_evidence":include_evidence,
            },
        }
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))

    return {
        "archive": str(archive_path),
        "included": included,
        "missing": missing,
        "count": len(included),
    }


def import_workspace(archive_path: Path, workspace: Path, *, overwrite: bool = False) -> dict[str, object]:
    """Import selected workspace files from a transfer zip archive."""
    archive_path = Path(archive_path).expanduser()
    workspace = Path(workspace).expanduser()

    if not archive_path.exists() or not archive_path.is_file():
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    workspace.mkdir(parents=True, exist_ok=True)
    ws_resolved = workspace.resolve()

    with zipfile.ZipFile(archive_path) as archive:
        if len(archive.infolist())>50000 or sum(i.file_size for i in archive.infolist())>500_000_000:
            raise ValueError("Workspace archive exceeds the bounded restore limit")
        database=workspace/"applypilot.db"
        if overwrite and "applypilot.db" in archive.namelist() and database.is_file():
            with sqlite3.connect(database.resolve().as_uri()+"?mode=ro",uri=True) as existing:
                for table in ("jobs","cc_retired_jobs","cc_opportunity_registry","cc_documents","cc_writing_documents","cc_cv_sources","cc_writing_requests","cc_ai_usage","cc_pipeline_usage","cc_pipeline_runs","cc_country_profiles","cc_employer_evidence"):
                    if existing.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type='table'",(table,)).fetchone() and existing.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                        raise ValueError("Restore into a new workspace: existing applications, evidence or usage history cannot be overwritten by a backup")

    allowed_roots = set(BASE_FILES) | set(GENERATED_DIRS) | set(OPTIONAL_DIRS) | {MANIFEST_NAME, "resume_variants", "evidence", "browser_sessions"}

    restored: list[str] = []
    skipped_exists: list[str] = []
    skipped_unsafe: list[str] = []
    skipped_unknown: list[str] = []

    with zipfile.ZipFile(archive_path, mode="r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue

            name = str(info.filename or "").replace("\\", "/").strip()
            if not name:
                continue

            parts = [p for p in name.split("/") if p]
            if not parts:
                continue

            if name == MANIFEST_NAME:
                continue

            # Prevent path traversal before allowlist checks.
            if any(p in ("..", ".") for p in parts):
                skipped_unsafe.append(name)
                continue

            root = parts[0]
            if root not in allowed_roots:
                skipped_unknown.append(name)
                continue

            rel_path = Path(*parts)
            dest = workspace / rel_path
            if dest.is_symlink():
                skipped_unsafe.append(name)
                continue
            try:
                dest_parent = dest.parent.resolve()
                if ws_resolved != dest_parent and ws_resolved not in dest_parent.parents:
                    skipped_unsafe.append(name)
                    continue
            except Exception:
                skipped_unsafe.append(name)
                continue

            if dest.exists() and not overwrite:
                skipped_exists.append(name)
                continue

            dest.parent.mkdir(parents=True, exist_ok=True)
            data = zf.read(info)
            if name=="applypilot.db":
                if len(data)>250_000_000:raise ValueError("Workspace database exceeds the bounded restore limit")
                from applypilot.database import close_connection
                close_connection(dest)
                with sqlite3.connect(":memory:") as snapshot:
                    snapshot.deserialize(_portable_sqlite_image(data))
                    if snapshot.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise ValueError("Archive database failed integrity validation")
                    with sqlite3.connect(dest) as target:snapshot.backup(target)
            else:dest.write_bytes(data)
            restored.append(name)

    if "applypilot.db" in restored:
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read(MANIFEST_NAME)) if MANIFEST_NAME in archive.namelist() else {}
        old = str(manifest.get("workspace") or "").replace("\\", "/").rstrip("/")
        if old:
            def moved(raw):
                value = str(raw or "").replace("\\", "/")
                if value == old:
                    return str(workspace.resolve())
                if value.startswith(old+"/"):
                    relative = value[len(old)+1:]
                    candidate = workspace / relative
                    if candidate.resolve().is_relative_to(workspace.resolve()) and candidate.is_file():
                        return str(candidate)
                return raw
            tables = {"jobs": ["tailored_resume_path", "cover_letter_path", "supporting_statement_path", "tailor_report_path", "cover_letter_report_path"],
                      "cc_documents": ["text_path", "pdf_path", "report_path"], "cc_writing_documents": ["text_path", "pdf_path"],
                      "cc_cv_sources": ["original_path"], "cc_portfolio_assets": ["path"]}
            with sqlite3.connect(workspace/"applypilot.db") as conn:
                for table, fields in tables.items():
                    columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                    for field in fields:
                        if field in columns:
                            for ident, raw in conn.execute(f"SELECT rowid,{field} FROM {table}").fetchall():
                                updated = moved(raw)
                                if updated != raw:
                                    conn.execute(f"UPDATE {table} SET {field}=? WHERE rowid=?", (updated, ident))
                def nested(value):
                    if isinstance(value,dict):return {k:nested(v) for k,v in value.items()}
                    if isinstance(value,list):return [nested(v) for v in value]
                    if isinstance(value,str):return moved(value)
                    return value
                for table,field in (("cc_writing_documents","source_json"),("cc_documents","context_json"),("cc_retired_jobs","snapshot")):
                    if field not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:continue
                    for ident,raw in conn.execute(f"SELECT rowid,{field} FROM {table}").fetchall():
                        try:value=json.loads(raw or "{}")
                        except (ValueError,TypeError):continue
                        updated=nested(value)
                        if updated!=value:conn.execute(f"UPDATE {table} SET {field}=? WHERE rowid=?",(json.dumps(updated),ident))

    if restored and (workspace/"applypilot.db").is_file():
        with sqlite3.connect(workspace/"applypilot.db") as conn:
            def columns(table):return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if "document_approval_json" in columns("cc_job_meta"):conn.execute("UPDATE cc_job_meta SET document_approval_json=NULL")
            if {"operation_key","owner_token","status_detail"}<=columns("cc_writing_requests"):
                conn.execute("UPDATE cc_writing_requests SET operation_key=NULL,owner_token=NULL,state=CASE WHEN state='reserved' THEN 'needs_review' ELSE state END,status_detail='Imported operation cannot replay; explicit review/retry required'")
            if "status" in columns("cc_pipeline_runs"):conn.execute("UPDATE cc_pipeline_runs SET status='interrupted' WHERE status='running'")
            if "state" in columns("cc_schedules"):
                detail=",detail='Imported schedule held for fresh review'" if "detail" in columns("cc_schedules") else ""
                conn.execute("UPDATE cc_schedules SET state='needs_review'"+detail+" WHERE state IN ('queued','running')")
            if "state" in columns("cc_browser_sessions"):
                detail=",detail='Imported session cannot resume; reconcile any attempted submission'" if "detail" in columns("cc_browser_sessions") else ""
                conn.execute("UPDATE cc_browser_sessions SET state='uncertain'"+detail+" WHERE state NOT IN ('confirmed','uncertain','stopped','failed')")
                if "approval_json" in columns("cc_browser_sessions"):
                    conn.execute("UPDATE cc_browser_sessions SET approval_json=NULL,authority_boot=NULL,approval_revision=approval_revision+1")
            if "apply_status" in columns("jobs"):
                error=",apply_error='Imported active attempt requires reconciliation'" if "apply_error" in columns("jobs") else ""
                conn.execute("UPDATE jobs SET apply_status='submission_uncertain'"+error+" WHERE apply_status IN ('in_progress','running','submitting')")
            if "kind" in columns("cc_events"):
                for kind in ("canonical_fact_verified","canonical_wording_reviewed","canonical_document_finalized","canonical_pdf_bound","canonical_source_reconciled","final_documents_reviewed","country_answer_reviewed"):
                    conn.execute("UPDATE cc_events SET kind=? WHERE kind=?",("imported_"+kind,kind))
        # Source validation is server-observed reliability, not a portable entitlement.
        import yaml
        for filename,key in (("sites.yaml","sites"),("employers.yaml","employers")):
            path=workspace/filename
            if filename not in restored or not path.is_file():continue
            value=yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(value,dict):continue
            entries=value.get(key,[] if key=="sites" else {})
            items=entries if isinstance(entries,list) else list(entries.values()) if isinstance(entries,dict) else []
            if filename=="sites.yaml" and isinstance(value.get("board_access"),dict):items+=list(value["board_access"].values())
            changed=False
            for item in items:
                if isinstance(item,dict) and "_nz_validation" in item:item.pop("_nz_validation");changed=True
            if changed:path.write_bytes(yaml.safe_dump(value,sort_keys=False).encode())

    return {
        "archive": str(archive_path),
        "workspace": str(workspace),
        "restored": restored,
        "skipped_exists": skipped_exists,
        "skipped_unsafe": skipped_unsafe,
        "skipped_unknown": skipped_unknown,
        "count": len(restored),
    }
