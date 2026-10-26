"""Versioned, workspace-scoped resume uploads and source editing."""
from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path
import re
import uuid
import zipfile
from xml.etree import ElementTree

from applypilot import setup_workspace as setup
from applypilot.pipeline_store import now


def init_schema(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS cc_cv_sources(source_key TEXT PRIMARY KEY,label TEXT,original_path TEXT,format TEXT,role TEXT,archived INTEGER DEFAULT 0,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS cc_cv_revisions(id INTEGER PRIMARY KEY,source_key TEXT,text TEXT,content_hash TEXT,created_at TEXT);
    CREATE TABLE IF NOT EXISTS cc_portfolio_assets(id INTEGER PRIMARY KEY,project_id TEXT,name TEXT,path TEXT,mime TEXT,created_at TEXT);
    """)
    if "archived" not in {r[1] for r in conn.execute("PRAGMA table_info(cc_portfolio_assets)")}:
        conn.execute("ALTER TABLE cc_portfolio_assets ADD COLUMN archived INTEGER DEFAULT 0")
    columns={r[1] for r in conn.execute("PRAGMA table_info(cc_cv_sources)")}
    for key in ("country_code","category","reconciliation_json"):
        if key not in columns:conn.execute(f"ALTER TABLE cc_cv_sources ADD COLUMN {key} TEXT")


def revision(conn, key: str, text: str):
    if not text.strip():
        return
    digest = hashlib.sha256(text.encode()).hexdigest()
    last = conn.execute("SELECT content_hash FROM cc_cv_revisions WHERE source_key=? ORDER BY id DESC LIMIT 1", (key,)).fetchone()
    if not last or last[0] != digest:
        conn.execute("INSERT INTO cc_cv_revisions(source_key,text,content_hash,created_at) VALUES(?,?,?,?)", (key, text, digest, now()))
        conn.commit()


def extract(name: str, data: bytes) -> str:
    suffix = Path(name).suffix.lower()
    if not data or len(data) > 12 * 1024 * 1024:
        raise ValueError("Upload a nonempty file no larger than 12 MB")
    if suffix in {".txt", ".md"}:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Save text files as UTF-8 before uploading") from exc
    elif suffix == ".pdf":
        from pypdf import PdfReader
        if not data.startswith(b"%PDF-"):
            raise ValueError("This file is not a PDF")
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("Upload an unlocked PDF")
        if len(reader.pages) > 50:
            raise ValueError("CV PDFs are limited to 50 pages")
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        if not text.strip():
            raise ValueError("No readable text found; upload a text-based PDF, DOCX or TXT")
    elif suffix == ".docx":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 25_000_000 or len(archive.infolist()) > 2000:
                raise ValueError("DOCX decompressed content exceeds the import limit")
            xml = archive.read("word/document.xml")
            if len(xml) > 3_000_000:
                raise ValueError("DOCX document content is too large")
        root = ElementTree.fromstring(xml)
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        text = "\n".join("".join(p.itertext()) for p in root.findall(".//w:p", namespace))
    else:
        raise ValueError("Supported CV formats: PDF, DOCX, TXT and MD")
    text = text.replace("\x00", "").strip()
    if not text or len(text) > 600_000:
        raise ValueError("Extracted text is empty or exceeds 600,000 characters")
    return text + "\n"


def import_files(conn, app_dir: Path, files: list) -> dict:
    if not isinstance(files, list) or not 1 <= len(files) <= 20:
        raise ValueError("Choose between 1 and 20 CV files")
    results = []
    for item in files:
        name = str(item.get("name") or "")[:200]
        try:
            data = base64.b64decode(item.get("b64") or "", validate=True)
            text = extract(name, data)
            key = re.sub(r"[^a-z0-9_-]+", "_", str(item.get("key") or Path(name).stem).lower()).strip("_-")[:35]
            if len(key) < 2:
                key = "cv_" + (key or "source")
            candidate = key
            counter = 2
            while (app_dir/"resume_variants"/(candidate+".txt")).exists() or conn.execute("SELECT 1 FROM cc_cv_sources WHERE source_key=?", (candidate,)).fetchone():
                candidate = key + "_" + str(counter)
                counter += 1
            key = candidate
            role = str(item.get("role") or "").strip()[:160]
            from applypilot.country_context import normalize_country,require_enabled
            code=normalize_country(item.get("country_code"))
            from applypilot.country_context import current
            active=current()
            if active and ((active.country_code=="NZ") != (code=="NZ")):
                raise ValueError("CV import country must match the captured country view")
            require_enabled(code,setup.read_searches_dict(app_dir))
            category=item.get("category") if code=="NZ" else None
            if code=="NZ" and category not in {"software","data_bi","support"}:
                raise ValueError("NZ masters require software, data_bi or support category")
            uploads = app_dir/"cv_uploads"
            if code=="NZ":
                from applypilot.command_center import record_legacy_compatibility
                record_legacy_compatibility(conn,app_dir)
            uploads.mkdir(parents=True, exist_ok=True)
            original = uploads/(uuid.uuid4().hex + Path(name).suffix.lower())
            setup._atomic_write_bytes(original, data)
            setup.write_resume_variant(app_dir, key, text)
            conn.execute("INSERT INTO cc_cv_sources(source_key,label,original_path,format,role,archived,updated_at,country_code,category) VALUES(?,?,?,?,?,0,?,?,?)", (key, name, str(original), Path(name).suffix.lower(), role, now(),code,category))
            revision(conn, key, text)
            conn.commit()
            warning = None
            if role and code!="NZ":
                try:
                    assign_role(conn, app_dir, key, role)
                except ValueError as exc:
                    warning = "CV imported; role mapping needs attention: " + str(exc)
            results.append({"name": name, "key": key, "ok": True, "characters": len(text), "role": role, "warning": warning})
        except Exception as exc:
            # Parsing errors can include file names, never provider credentials.
            results.append({"name": name, "ok": False, "error": str(exc)[:300]})
    return {"results": results, "imported": sum(r["ok"] for r in results)}


def assign_role(conn, app_dir: Path, key: str, role: str):
    row=conn.execute("SELECT country_code FROM cc_cv_sources WHERE source_key=?",(key,)).fetchone()
    if row and row[0]=="NZ":raise ValueError("Use the NZ master action; NZ imports cannot change root UK routing")
    from applypilot.pipeline_workspace import save_config
    from applypilot.role_routing import suggested_routing
    cfg = setup.read_searches_dict(app_dir)
    rr = cfg.get("resume_routing") or suggested_routing(app_dir)
    rr.setdefault("variants", {})[key] = "resume_variants/"+key+".txt"
    rr.setdefault("title_routes", {})[role] = key
    patch = {"resume_routing": rr}
    if not cfg.get("queries"):
        patch["queries"] = [{"query": role, "tier": 1}]
        patch["locations"] = [{"location": "Remote", "remote": True}]
    save_config(conn, app_dir, {"patch": patch})


def sources(conn, app_dir: Path) -> dict:
    metadata = {r["source_key"]: dict(r) for r in conn.execute("SELECT * FROM cc_cv_sources")}
    rows = [{"key": "default", "name": "Default resume", "bytes": (app_dir/"resume.txt").stat().st_size if (app_dir/"resume.txt").exists() else 0}]
    rows += setup.list_resume_variants(app_dir)
    return {"sources": [r | {"metadata": metadata.get(r["key"], {})} for r in rows],
            "archived": [m for m in metadata.values() if m["archived"]],
            "revisions": [dict(r) for r in conn.execute("SELECT id,source_key,content_hash,created_at FROM cc_cv_revisions ORDER BY id DESC LIMIT 300")]}


def source_text(app_dir: Path, key: str) -> str:
    path = app_dir/"resume.txt" if key == "default" else setup.resume_variant_path(app_dir, key)
    if not path.is_file():
        raise LookupError("CV source is unavailable")
    return path.read_text(encoding="utf-8")


def export_pdf(app_dir: Path, key: str) -> Path:
    from applypilot.scoring.pdf import parse_resume, build_html, build_letter_html, render_pdf
    text = source_text(app_dir, key)
    directory = app_dir/"cv_previews"
    directory.mkdir(parents=True, exist_ok=True)
    output = directory/(hashlib.sha256(text.encode()).hexdigest()+".pdf")
    if not output.is_file():
        parsed = parse_resume(text)
        html = build_html(parsed) if parsed.get("sections") else build_letter_html(text)
        render_pdf(html, str(output))
    return output


def action(conn, app_dir: Path, data: dict) -> dict:
    key = str(data.get("key") or "default")
    source=conn.execute("SELECT country_code FROM cc_cv_sources WHERE source_key=?",(key,)).fetchone()
    if source and source[0]=="NZ":
        from applypilot.country_context import current
        if not current() or current().country_code!="NZ":raise ValueError("NZ master edits require the NZ country view")
        from applypilot.nz_documents import source_action
        return source_action(conn,app_dir,data)
    operation = data.get("action")
    if operation == "default":
        setup.write_resume_text(app_dir, source_text(app_dir, key))
    elif operation == "duplicate":
        new = str(data.get("new_key") or "")
        if (app_dir/"resume_variants"/(new+".txt")).exists():
            raise ValueError("A source with this key already exists")
        setup.write_resume_variant(app_dir, new, source_text(app_dir, key))
    elif operation == "rename":
        if key == "default":
            raise ValueError("The default CV uses a fixed identity; duplicate it to make a named variant")
        new = str(data.get("new_key") or "")
        setup.rename_resume_variant(app_dir, key, new)
        conn.execute("UPDATE cc_cv_sources SET source_key=?,label=?,updated_at=? WHERE source_key=?", (new, str(data.get("name") or new), now(), key))
        conn.execute("UPDATE cc_cv_revisions SET source_key=? WHERE source_key=?", (new, key))
        conn.execute("UPDATE jobs SET resume_route_override=? WHERE resume_route_override=?", (new, key))
        cfg = setup.read_searches_dict(app_dir)
        rr = cfg.get("resume_routing")
        if rr:
            rr.get("variants", {}).pop(key, None)
            rr.setdefault("variants", {})[new] = "resume_variants/"+new+".txt"
            for field in ("families", "context_families"):
                for family in rr.get(field, []):
                    if family.get("variant") == key:
                        family["variant"] = new
            rr["title_routes"] = {title: new if value == key else value for title, value in rr.get("title_routes", {}).items()}
            setup.write_searches_yaml(app_dir, __import__("yaml").safe_dump(cfg, sort_keys=False))
    elif operation == "restore_revision":
        row = conn.execute("SELECT source_key,text FROM cc_cv_revisions WHERE id=?", (int(data.get("revision_id") or 0),)).fetchone()
        if not row or row[0] != key:
            raise ValueError("Choose a revision for this source")
        (setup.write_resume_text(app_dir, row[1]) if key == "default" else setup.write_resume_variant(app_dir, key, row[1]))
        conn.execute("UPDATE cc_cv_sources SET archived=0,updated_at=? WHERE source_key=?", (now(), key))
    elif operation == "archive":
        if key == "default":
            raise ValueError("Choose a replacement default instead of archiving it")
        text = source_text(app_dir, key)
        revision(conn, key, text)
        path = setup.resume_variant_path(app_dir, key)
        target = app_dir/"cv_archive"/(uuid.uuid4().hex+".txt")
        target.parent.mkdir(parents=True, exist_ok=True)
        path.replace(target)
        conn.execute("INSERT INTO cc_cv_sources(source_key,label,archived,updated_at) VALUES(?,?,1,?) ON CONFLICT(source_key) DO UPDATE SET archived=1,updated_at=excluded.updated_at", (key, key, now()))
        cfg = setup.read_searches_dict(app_dir)
        rr = cfg.get("resume_routing")
        if rr:
            rr.get("variants", {}).pop(key, None)
            for field in ("families", "context_families"):
                rr[field] = [f for f in rr.get(field, []) if f.get("variant") != key]
            rr["title_routes"] = {title: value for title, value in rr.get("title_routes", {}).items() if value != key}
            setup.write_searches_yaml(app_dir, __import__("yaml").safe_dump(cfg, sort_keys=False))
    elif operation == "map":
        role = str(data.get("role") or "").strip()
        if not role or len(role) > 160 or key == "default":
            raise ValueError("Choose a variant and a target role")
        source_text(app_dir, key)
        assign_role(conn, app_dir, key, role)
    else:
        raise ValueError("Unknown CV source action")
    conn.commit()
    return sources(conn, app_dir)


def build_profile_cv(profile: dict) -> str:
    from applypilot.professional import credential_line
    personal = profile.get("personal") or {}
    sections = profile.get("resume_sections") or {}
    experience = profile.get("experience") or {}
    parts = [personal.get("full_name", "Your name"), experience.get("target_role") or experience.get("current_job_title") or "Professional profile",
             ", ".join(str(personal.get(k) or "") for k in ("city", "country") if personal.get(k)),
             " | ".join(str(personal.get(k) or "") for k in ("email", "phone", "linkedin_url") if personal.get(k))]
    for title, key in (("SUMMARY", "summary"), ("EDUCATION", "education"), ("CERTIFICATIONS", "certifications")):
        value = sections.get(key) or []
        lines = value.splitlines() if isinstance(value, str) else list(value)
        if key == "certifications":
            lines += [credential_line(e) for e in sections.get("credentials", []) if e.get("include", True)]
        if lines:
            parts += ["", title, *lines]
    for title, key in (("PROFESSIONAL EXPERIENCE", "professional_experience"), ("PROJECTS", "projects")):
        entries = sections.get(key) or []
        if entries:
            parts += ["", title]
        for entry in entries:
            if entry.get("include", True):
                parts += [entry["header"], entry.get("subtitle", ""), *["- "+b for b in entry.get("bullets", [])], ""]
    skills = profile.get("skills_boundary") or {}
    if skills:
        parts += ["", "TECHNICAL SKILLS", *[name.replace("_", " ").title()+": "+", ".join(values) for name, values in skills.items()]]
    return "\n".join(parts).strip()+"\n"


def suggestions(app_dir: Path, key: str) -> dict:
    """Literal parsing proposals for review, never an automatic profile overwrite."""
    from applypilot.scoring.pdf import parse_resume, parse_entries, parse_skills
    text = source_text(app_dir, key)
    parsed = parse_resume(text)
    personal = {}
    name = parsed.get("name", "")
    if 1 <= len(name.split()) <= 6 and not any(c in name for c in ("@", "|", ":")) and name.lower() not in {"cv", "resume", "curriculum vitae", "summary"}:
        personal["full_name"] = name[:300]
    email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    if email:
        personal["email"] = email.group(0)
    linkedin = re.search(r"(?:https?://)?(?:www\.)?linkedin\.com/[^\s|]+", text, re.I)
    if linkedin:
        personal["linkedin_url"] = linkedin.group(0).rstrip(".,)")
    sections = parsed["sections"]
    projects = []
    for entry in parse_entries(sections.get("PROJECTS") or sections.get("TECHNICAL PROJECTS") or ""):
        title = entry.get("title") or entry.get("header") or ""
        if title:
            projects.append({"id": uuid.uuid4().hex, "header": title[:200], "subtitle": str(entry.get("subtitle") or "")[:300], "bullets": [b[:500] for b in entry.get("bullets", [])[:12]], "include": True, "family": "all"})
    summary = sections.get("SUMMARY") or sections.get("PROFESSIONAL SUMMARY") or ""
    skills = {}
    for category, values in parse_skills(sections.get("TECHNICAL SKILLS") or ""):
        skills[re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_") or "tools"] = [s.strip()[:80] for s in values.split(",") if s.strip()][:80]
    return {"personal": personal, "summary": [s[:400] for s in summary.splitlines() if s.strip()][:20], "skills_boundary": skills,
            "projects": projects[:40], "notice": "Literal proposals from the selected CV. Review names, contact details and project parsing before saving."}
