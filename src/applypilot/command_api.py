"""HTTP adapter for the authenticated command-center workspace."""
from __future__ import annotations

import csv
import io
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import parse_qs

from applypilot import command_center as cc
from applypilot.database import workspace_connection
from applypilot.source_registry import SourcePreviewUnavailable
from applypilot.read_model import read_scope


def _submit_authority(handler,action,data,conn,app_dir):
    from applypilot.apply import authority
    from http.cookies import SimpleCookie
    from urllib.parse import urlsplit
    import hashlib
    user=handler._current_user() if hasattr(handler,"_current_user") else None
    actor=str(user["username"]) if user else "local_workspace_owner"
    headers=getattr(handler,"headers",{})
    origin=headers.get("Origin","")
    if origin and urlsplit(origin).netloc!=headers.get("Host",""):raise ValueError("Submit authority requires the current same-origin workspace")
    cookies=SimpleCookie();cookies.load(headers.get("Cookie",""))
    csrf=cookies.get("ap_csrf")
    if action in {"approve","execute","cancel"} and headers:
        if csrf is None or not headers.get("X-CSRF-Token") or headers.get("X-CSRF-Token")!=csrf.value:raise ValueError("Fresh authority requires the authenticated workspace CSRF context")
    session=cookies.get("ap_session")
    binding=hashlib.sha256((authority.BOOT_ID+"|"+(session.value if session else csrf.value if csrf else "implicit-local")+"|"+actor).encode()).hexdigest()
    if action=="review":return authority.review(conn,app_dir,int(data.get("id") or 0),actor=actor,session_binding=binding,country_code=data.get("country_code"))
    if action=="approve":return authority.approve(conn,app_dir,str(data.get("review_id") or ""),actor=actor,session_binding=binding,expected_review_hash=str(data.get("expected_review_hash") or ""),human_approved=data.get("human_approved"),seconds=data.get("seconds",300))
    if action=="execute":return authority.execute(conn,app_dir,str(data.get("review_id") or ""),data.get("approval_token"),actor=actor,session_binding=binding)
    if action=="cancel":return authority.cancel(conn,app_dir,str(data.get("review_id") or ""),actor=actor,session_binding=binding)
    raise ValueError("Unknown submit authority action")


def get(handler, path: str, query: str, app_dir: Path, runner=None) -> bool:
    from applypilot.ai_settings import workspace_context
    snapshot = read_scope(app_dir) if path.startswith('/api/center') and not path.startswith('/api/center/browser') and path != '/api/center/stream' else nullcontext()
    with workspace_context(app_dir), snapshot:
        if not path.startswith("/api/center"):
            return _get(handler, path, query, app_dir, runner)
        from applypilot.country_context import CountryError, operation, resolve_view, require_preparation
        try:
            code = parse_qs(query, keep_blank_values=True).get("country_code", [None])[0]
            hot = path in {"/api/center/browser/screenshot", "/api/center/browser/stream"} or (path == "/api/center/browser" and parse_qs(query).get("session_id"))
            context = resolve_view(app_dir, country_code=code, conn=None if hot else workspace_connection(app_dir / "applypilot.db"))
            if path not in {"/api/center", "/api/center/documents/review", "/api/center/country/preferences", "/api/center/preparation/request", "/api/center/canonical", "/api/center/nz/preparation", "/api/center/nz/messages", "/api/center/nz/history", "/api/center/nz/calendar", "/api/center/nz/lifecycle/preview", "/api/center/nz/packet/document", "/api/center/nz/document", "/api/center/nz/documents/review", "/api/center/nz/cv", "/api/center/nz/cv/source", "/api/center/nz/discovery", "/api/center/nz/evidence", "/api/center/nz/evidence/history", "/api/center/nz/evidence/artifact", "/api/center/nz/employers/suggest"}:
                require_preparation(context.country_code)
            with operation(context):
                return _get(handler, path, query, app_dir, runner)
        except CountryError as exc:
            handler._send_json(400, {"ok": False, "error": str(exc)})
            return True


def _get(handler, path: str, query: str, app_dir: Path, runner=None) -> bool:
    assets = {"/workspace": "workspace.html", "/assets/workspace.css": "workspace.css", "/assets/workspace.js": "workspace.js", "/assets/pipeline.js": "pipeline.js", "/assets/pipeline.css": "pipeline.css", "/assets/studios.js": "studios.js", "/assets/studios.css": "studios.css", "/assets/nhs-planner.js": "nhs-planner.js", "/assets/assisted-browser.js": "assisted-browser.js", "/assets/assisted-browser.css": "assisted-browser.css"}
    if path in assets:
        asset = Path(__file__).parent / "web" / assets[path]
        mime = "text/html" if asset.suffix == ".html" else "text/css" if asset.suffix == ".css" else "application/javascript"
        handler._send(200, asset.read_bytes(), mime + "; charset=utf-8")
        return True
    if path in {"/assets/pipeline-scope.js", "/assets/nz-search.js", "/assets/document-review.js", "/assets/nz-preparation.js", "/assets/canonical.js", "/assets/nz-evidence.js", "/assets/nz-discovery.js", "/assets/job-lifecycle.js", "/assets/job-lifecycle.css", "/assets/opportunity-insights.js", "/assets/opportunity-insights.css", "/assets/browser-frame-view.js", "/assets/geography-editor.js", "/assets/insights-charts.js"}:
        asset = Path(__file__).parent / "web" / path.rsplit('/', 1)[1]
        handler._send(200, asset.read_bytes(), "application/javascript; charset=utf-8" if asset.suffix == '.js' else "text/css; charset=utf-8")
        return True
    if not path.startswith("/api/center"):
        return False
    qs = parse_qs(query)
    ident=qs.get('session_id',[''])[0]
    if path in {'/api/center/browser/screenshot','/api/center/browser/stream'} or (path=='/api/center/browser' and ident):
        from applypilot.apply import assisted_browser as browser
        try:
            if path.endswith('/stream'):
                browser.stream(handler,app_dir,ident)
            elif path.endswith('/screenshot'):
                handler._send(200,browser.screenshot(app_dir,ident),'image/jpeg',headers={'Cache-Control':'no-store'})
            else:
                handler._send_json(200,{'ok':True,**browser.status(app_dir,ident)})
        except (ValueError,LookupError,TypeError) as exc:
            handler._send_json(404 if isinstance(exc,LookupError) else 400,{'ok':False,'error':str(exc)})
        except Exception:
            handler._send_json(503,{'ok':False,'error':'Browser view temporarily unavailable; retry the live session'})
        return True
    conn = workspace_connection(app_dir / "applypilot.db")
    qs = parse_qs(query)
    try:
        if path == "/api/center/documents/review":
            from applypilot.apply.application_packet import status
            handler._send_json(200,{"ok":True,"packet":status(conn,app_dir,int(qs.get("id",["0"])[0]))})
        elif path == "/api/center/preparation/request":
            from applypilot.operations import status
            from applypilot.country_context import current
            handler._send_json(200,{"ok":True,"request":status(conn,qs.get("id",["0"])[0],current().country_code)})
        elif path == "/api/center/canonical":
            from applypilot.canonical import state
            handler._send_json(200,{"ok":True,**state(conn,app_dir)})
        elif path.startswith("/api/center/nz/") and path != "/api/center/nz/discovery":
            from applypilot.country_context import current
            from applypilot import nz_evidence as evidence
            if current().country_code != "NZ":
                raise ValueError("Choose the NZ country view")
            if path == "/api/center/nz/discovery/status":
                from applypilot.nz_search import discovery_status
                handler._send_json(200, {"ok": True, **discovery_status(conn, app_dir, runner, qs.get("run_id", [None])[0])})
            elif path == "/api/center/nz/messages":
                from applypilot.preparation import messages
                handler._send_json(200,{"ok":True,"messages":messages(conn)})
            elif path == "/api/center/nz/history":
                from applypilot.job_lifecycle import history
                handler._send_json(200,{"ok":True,**history(conn,app_dir,qs.get("search",[""])[0],int(qs.get("offset",["0"])[0]))})
            elif path == "/api/center/nz/lifecycle/preview":
                from applypilot.job_lifecycle import preview
                handler._send_json(200,{"ok":True,**preview(conn,app_dir,int(qs.get("id",["0"])[0]))})
            elif path == "/api/center/nz/calendar":
                from applypilot.country_context import check_job
                job=cc.get_job(conn,int(qs.get("id",["0"])[0]));check_job(job,app_dir,country_code="NZ")
                return _get(handler,"/api/center/calendar",query,app_dir,runner)
            elif path == "/api/center/nz/packet/document":
                from applypilot.apply.application_packet import document
                body,mime=document(conn,app_dir,int(qs.get("id",["0"])[0]),qs.get("kind",[""])[0],qs.get("format",["html"])[0])
                handler._send(200,body,mime,headers={"Cache-Control":"no-store"})
            elif path == "/api/center/nz/documents/review":
                from applypilot.apply.application_packet import status
                handler._send_json(200,{"ok":True,"packet":status(conn,app_dir,int(qs.get("id",["0"])[0]))})
            elif path == "/api/center/nz/preparation":
                from applypilot.preparation import state
                handler._send_json(200,{"ok":True,**state(conn,app_dir)})
            elif path == "/api/center/nz/document":
                from applypilot.preparation import document
                body,mime=document(conn,app_dir,qs.get("id",["0"])[0],qs.get("format",["txt"])[0])
                handler._send(200,body,mime,headers={"Cache-Control":"no-store"})
            elif path == "/api/center/nz/cv/source":
                from applypilot.nz_documents import _source
                from applypilot.cv_library import source_text,export_pdf
                key=qs.get("key",[""])[0]
                item=_source(conn,key)
                fmt=qs.get("format",["json"])[0]
                if fmt=="json":handler._send_json(200,{"ok":True,"text":source_text(app_dir,key),"source":item})
                elif fmt=="txt":handler._send(200,source_text(app_dir,key).encode(),"text/plain; charset=utf-8",headers={"Content-Disposition":'attachment; filename="nz-master-source.txt"'})
                elif fmt=="pdf":handler._send(200,export_pdf(app_dir,key).read_bytes(),"application/pdf",headers={"Content-Disposition":'attachment; filename="nz-master-source-preview.pdf"'})
                elif fmt=="original":
                    document=Path(item["original_path"] or "").resolve()
                    if not document.is_relative_to(app_dir.resolve()) or not document.is_file():raise ValueError("Original source unavailable")
                    handler._send(200,document.read_bytes(),"application/octet-stream",headers={"Content-Disposition":'attachment; filename="nz-master-original'+item["format"]+'"'})
                else:raise ValueError("Choose JSON/TXT/PDF source preview or retained original; generated DOCX is not supported")
            elif path == "/api/center/nz/cv":
                from applypilot.nz_documents import state
                handler._send_json(200,{"ok":True,**state(conn,app_dir)})
            elif path == "/api/center/nz/evidence":
                handler._send_json(200,{"ok":True,**evidence.state(conn,app_dir)})
            elif path == "/api/center/nz/evidence/history":
                ident=qs.get("employer_id",[""])[0]
                handler._send_json(200,{"ok":True,"history":evidence.history(conn,app_dir,ident),"current":evidence.reduce(conn,app_dir,ident)})
            elif path == "/api/center/nz/evidence/artifact":
                body,mime=evidence.artifact(conn,app_dir,qs.get("id",[""])[0])
                handler._send(200,body,mime,headers={"Cache-Control":"no-store","Content-Disposition":"attachment"})
            elif path == "/api/center/nz/employers/suggest":
                handler._send_json(200,{"ok":True,"matches":evidence.suggestions(conn,app_dir,qs.get("query",[""])[0],qs.get("nzbn",[None])[0])})
            else:
                raise ValueError("Unknown NZ evidence route")
        elif path == "/api/center/nz/discovery":
            from applypilot.country_context import current
            if current().country_code != "NZ":
                raise ValueError("Choose the NZ country view")
            from applypilot.discovery.nz import preview
            from applypilot.nz_search import discovery_status
            handler._send_json(200, {"ok": True, **preview(conn, app_dir), "import_status": discovery_status(conn, app_dir, runner)})
        elif path in {'/api/center/insights','/api/center/insights/export'}:
            from applypilot import opportunity_insights as insights
            filters={k:v[0] for k,v in qs.items()}
            if path.endswith('/export'):
                body,mime,name=insights.export(conn,filters)
                handler._send(200,body,mime,headers={'Content-Disposition':f'attachment; filename="{name}"','Cache-Control':'no-store'})
            else:
                handler._send_json(200,{'ok':True,**insights.summary(conn,filters,app_dir)})
        elif path=='/api/center/submission/reconciliation':
            from applypilot.submission_reconciliation import context
            handler._send_json(200,{'ok':True,**context(conn,app_dir,int(qs.get('id',['0'])[0]))})
        elif path=='/api/center/submission/attempt-image':
            from applypilot.submission_reconciliation import attempt_image
            handler._send(200,attempt_image(conn,app_dir,int(qs.get('id',['0'])[0])),'image/jpeg',headers={'Cache-Control':'no-store'})
        elif path == "/api/center/lifecycle/preview":
            from applypilot.job_lifecycle import preview, reconcile_pipeline
            reconcile_pipeline(conn, runner)
            handler._send_json(200, {"ok": True, **preview(conn, app_dir, int(qs.get("id", ["0"])[0]))})
        elif path == "/api/center/lifecycle/history":
            from applypilot.job_lifecycle import history
            handler._send_json(200, {"ok": True, **history(conn, app_dir, qs.get("search", [""])[0], int(qs.get("offset", ["0"])[0]))})
        elif path == "/api/center/lifecycle/detail":
            from applypilot.job_lifecycle import detail
            handler._send_json(200, {"ok": True, **detail(conn, qs.get("id", [""])[0])})
        elif path == "/api/center/decisions":
            from applypilot.decision_observer import report
            handler._send_json(200, {"ok": True, **report(conn, app_dir, qs.get("session_id", [None])[0])})
        elif path == "/api/center/browser":
            from applypilot.apply.assisted_browser import current, status
            ident = qs.get("session_id", [""])[0]
            out = status(app_dir, ident) if ident else current(conn, app_dir, int(qs.get("id", ["0"])[0]))
            if not ident:
                from applypilot.apply.application_packet import status as packet_status
                out["packet"] = packet_status(conn, app_dir, int(qs.get("id", ["0"])[0]))
            handler._send_json(200, {"ok": True, **out})
        elif path == "/api/center/browser/document":
            from applypilot.apply.application_packet import document
            body, mime = document(conn, app_dir, int(qs.get("id", ["0"])[0]), qs.get("kind", [""])[0], qs.get("format", ["html"])[0])
            handler._send(200, body, mime, headers={"Cache-Control": "no-store"})
        elif path == "/api/center/browser/screenshot":
            from applypilot.apply.assisted_browser import screenshot
            handler._send(200, screenshot(app_dir, qs.get("session_id", [""])[0]), "image/jpeg", headers={"Cache-Control": "no-store"})
        elif path == "/api/center/browser/evidence":
            job = cc.get_job(conn, int(qs.get("id", ["0"])[0]))
            row = conn.execute("SELECT id FROM cc_browser_sessions WHERE job_url=? AND state='confirmed' ORDER BY created_at DESC LIMIT 1", (job["url"],)).fetchone()
            if not row:
                raise LookupError("No confirmed browser evidence for this application")
            doc = app_dir / "browser_sessions" / row["id"] / "confirmation.png"
            handler._send(200, doc.read_bytes(), "image/png", headers={"Cache-Control": "no-store"})
        elif path == "/api/center":
            from applypilot.country_context import current, nz_enabled
            from applypilot.apply.safety import status as safety_status
            contents = {"jobs": [], "metrics": {}} if current().country_code == "NZ" else cc.overview(conn, app_dir)
            handler._send_json(200, {"ok": True, **contents,
                                  "country_context": current().public(),
                                  "nz_workflow_enabled": nz_enabled(current().config),
                                  "submission_safety": safety_status()})
        elif path == "/api/center/country/preferences":
            from applypilot.country_context import current, read_preferences
            code = current().country_code
            if not code:
                raise ValueError("Choose an explicit country for country preferences")
            handler._send_json(200, {"ok": True, **read_preferences(conn, code)})
        elif path == "/api/center/pipeline":
            from applypilot.pipeline_workspace import state
            handler._send_json(200, {"ok": True, **state(conn, app_dir,bounded=True,campaign_id=qs.get('campaign_id',[None])[0])})
        elif path == "/api/center/pipeline/progress":
            from applypilot.pipeline_workspace import progress
            handler._send_json(200, {"ok": True, **progress(conn, app_dir,qs.get('bounded',['0'])[0]=='1')})
        elif path == "/api/center/pipeline/insights":
            from applypilot.pipeline_workspace import insights
            handler._send_json(200, {"ok": True, "insights": insights(conn, app_dir)})
        elif path == "/api/center/pipeline/detail":
            from applypilot.pipeline_workspace import inspect_job
            handler._send_json(200, {"ok": True, **inspect_job(conn, app_dir, int(qs.get("id", ["0"])[0]))})
        elif path == "/api/center/ai/settings":
            from applypilot.ai_settings import public
            handler._send_json(200, {"ok": True, **public(app_dir)})
        elif path == "/api/center/cv":
            from applypilot.cv_library import sources
            handler._send_json(200, {"ok": True, **sources(conn, app_dir)})
        elif path == "/api/center/cv/source":
            from applypilot.cv_library import source_text
            from applypilot.scoring.pdf import build_html, parse_resume
            import html
            key = qs.get("key", ["default"])[0]
            text = source_text(app_dir, key)
            fmt = qs.get("format", ["json"])[0]
            if fmt == "json":
                handler._send_json(200, {"ok": True, "text": text})
            elif fmt == "html":
                parsed = parse_resume(text)
                body = build_html(parsed) if parsed.get("sections") else "<pre style='white-space:pre-wrap'>"+html.escape(text)+"</pre>"
                handler._send(200, body.encode(), "text/html; charset=utf-8")
            elif fmt == "txt":
                handler._send(200, text.encode(), "text/plain; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="cv-source.txt"'})
            elif fmt == "original":
                row = conn.execute("SELECT original_path,format FROM cc_cv_sources WHERE source_key=?", (key,)).fetchone()
                doc = Path(row[0]) if row and row[0] else None
                if not doc or not doc.resolve().is_relative_to(app_dir.resolve()) or not doc.is_file():
                    raise LookupError("Original upload not available")
                handler._send(200, doc.read_bytes(), "application/octet-stream", headers={"Content-Disposition": 'attachment; filename="original-cv'+str(row[1])+'"'})
            elif fmt == "pdf":
                from applypilot.cv_library import export_pdf
                doc = export_pdf(app_dir, key)
                handler._send(200, doc.read_bytes(), "application/pdf", headers={"Content-Disposition": 'attachment; filename="cv-source.pdf"'})
            else:
                raise ValueError("Unsupported source format")
        elif path == "/api/center/studio":
            from applypilot.writing_studio import listing
            handler._send_json(200, {"ok": True, **listing(conn, qs.get("kind", ["cover"])[0])})
        elif path == "/api/center/studio/request":
            from applypilot.writing_studio import request_status
            from applypilot.country_context import current
            handler._send_json(200,{"ok":True,**request_status(conn,app_dir,int(qs.get("id",["0"])[0]),current().country_code or "GB")})
        elif path == "/api/center/studio/document":
            from applypilot.writing_studio import document, pdf
            from applypilot.scoring.pdf import build_letter_html
            ident = int(qs.get("id", ["0"])[0])
            out = document(conn, app_dir, ident)
            fmt = qs.get("format", ["json"])[0]
            if fmt == "json":
                handler._send_json(200, {"ok": True, **out})
            elif fmt == "html":
                handler._send(200, build_letter_html(out["text"]).encode(), "text/html; charset=utf-8")
            elif fmt == "txt":
                handler._send(200, out["text"].encode(), "text/plain; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="'+out["document"]["kind"]+'-draft.txt"'})
            elif fmt == "pdf":
                doc = pdf(conn, app_dir, ident)
                handler._send(200, doc.read_bytes(), "application/pdf", headers={"Content-Disposition": 'attachment; filename="'+out["document"]["kind"]+'-draft.pdf"'})
            else:
                raise ValueError("Unsupported document format")
        elif path == "/api/center/portfolio/assets":
            handler._send_json(200, {"ok": True, "assets": [dict(r) for r in conn.execute("SELECT id,project_id,name,mime,created_at,archived FROM cc_portfolio_assets ORDER BY id DESC")]})
        elif path == "/api/center/portfolio/asset":
            row = conn.execute("SELECT * FROM cc_portfolio_assets WHERE id=?", (int(qs.get("id", ["0"])[0]),)).fetchone()
            doc = Path(row["path"]) if row else None
            if not doc or not doc.resolve().is_relative_to(app_dir.resolve()) or not doc.is_file():
                raise LookupError("Portfolio attachment unavailable")
            handler._send(200, doc.read_bytes(), row["mime"], headers={"Content-Disposition": 'inline; filename="portfolio-attachment'+doc.suffix+'"'})
        elif path == "/api/center/workspace/export":
            from applypilot.workspace_transfer import export_workspace
            import uuid
            path = app_dir/"exports"/(uuid.uuid4().hex+".zip")
            export_workspace(app_dir, path, include_generated=True, include_env=False,include_evidence=qs.get("include_evidence",[""])[0] in {"1","true"})
            try:
                handler._send(200, path.read_bytes(), "application/zip", headers={"Content-Disposition": 'attachment; filename="applypilot-workspace.zip"'})
            finally:
                path.unlink(missing_ok=True)
        elif path == "/api/center/ai/routes":
            from applypilot.ai_routing import route_table

            handler._send_json(200, {"ok": True, "routes": route_table()})
        elif path == "/api/center/messages":
            from applypilot.evidence_inbox import listing

            handler._send_json(200, {"ok": True, **listing(conn, qs.get("bucket", ["applications"])[0], qs.get("search", [""])[0], int(qs.get("offset", ["0"])[0]), int(qs.get("limit", ["25"])[0]))})
        elif path == "/api/center/mail":
            from applypilot.mail_connectors import connections

            handler._send_json(200, {"ok": True, "connections": connections(conn)})
        elif path == "/api/center/mail/callback":
            from applypilot.mail_connectors import callback

            callback(conn, app_dir, qs.get("state", [""])[0], qs.get("code", [""])[0])
            handler._send(302, b"", "text/plain", headers={"Location": "/workspace#inbox"})
        elif path == "/api/center/detail":
            job_id = int(qs.get("id", ["0"])[0])
            document_notice = cc.adopt_legacy_document(conn, app_dir, job_id)
            out = cc.details(conn, app_dir, job_id)
            if document_notice:
                out["document_notice"] = document_notice
            out["failure"] = cc.classify_failure(out["job"].get("apply_error") or "")
            handler._send_json(200, {"ok": True, **out})
        elif path == "/api/center/events":
            since = max(0, int(qs.get("since", ["0"])[0]))
            rows = conn.execute("SELECT * FROM cc_events WHERE id>? ORDER BY id LIMIT 500", (since,)).fetchall()
            handler._send_json(200, {"ok": True, "events": [dict(r) for r in rows]})
        elif path == "/api/center/export":
            out = io.StringIO()
            fields = ["id", "title", "company", "site", "status", "hiring_status", "applied_at", "priority", "due_at", "notes"]
            writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in cc.overview(conn, app_dir)["jobs"]:
                # Prevent spreadsheet formula execution in user-entered fields.
                writer.writerow({k: "'" + str(v) if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v for k, v in row.items()})
            handler._send(200, out.getvalue().encode(), "text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="applications.csv"'})
        elif path == "/api/center/calendar":
            job = cc.get_job(conn, int(qs.get("id", ["0"])[0]))
            meta = conn.execute("SELECT due_at,notes FROM cc_job_meta WHERE job_url=?", (job["url"],)).fetchone()
            if not meta or not meta[0]:
                raise ValueError("Add a reminder date to this application first")
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(meta[0].replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            start = dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            def escape(value):
                return str(value or "").replace("\\", "\\\\").replace("\r", "").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")
            body = f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//ApplyPilot//Application Reminders//EN\r\nBEGIN:VEVENT\r\nUID:job-{job['id']}-{start}@applypilot\r\nDTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}\r\nDTSTART:{start}\r\nSUMMARY:{escape(job['title'])}\r\nDESCRIPTION:{escape(meta[1])}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
            handler._send(200, body.encode(), "text/calendar; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="application-reminder.ics"'})
        elif path == "/api/center/document":
            document_id = int(qs.get("id", ["0"])[0])
            row = conn.execute("SELECT * FROM cc_documents WHERE id=?", (document_id,)).fetchone()
            if not row:
                raise LookupError("Document not found")
            fmt = qs.get("format", ["html"])[0]
            if fmt not in {"html", "txt", "pdf"}:
                raise ValueError("Unsupported document format")
            raw_path = row["pdf_path"] if fmt == "pdf" else row["text_path"]
            if not raw_path:
                raise LookupError("PDF is not available; generate the PDF stage first")
            doc = Path(raw_path).resolve()
            if not doc.is_relative_to(app_dir.resolve()) or not doc.is_file():
                raise LookupError("Document unavailable in this workspace")
            if fmt == "html":
                from applypilot.scoring.pdf import build_html, parse_resume
                body = build_html(parse_resume(doc.read_text(encoding="utf-8"))).encode()
                handler._send(200, body, "text/html; charset=utf-8")
            else:
                handler._send(200, doc.read_bytes(), "application/pdf" if fmt == "pdf" else "text/plain; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="cv-version-{document_id}.{fmt}"'})
        else:
            handler._send_json(404, {"ok": False, "error": "not_found"})
    except (ValueError, LookupError) as exc:
        handler._send_json(404 if isinstance(exc, LookupError) else 400, {"ok": False, "error": str(exc)})
    except (BrokenPipeError, ConnectionError):
        pass
    except Exception:
        conn.rollback()
        handler._send_json(503, {"ok": False, "error": "Operation unavailable. Retry loading this workspace resource."})
    return True


def post(handler, path: str, data: dict, app_dir: Path, runner) -> bool:
    from applypilot.ai_settings import workspace_context
    with workspace_context(app_dir):
        if not path.startswith("/api/center/") or not isinstance(data, dict):
            return _post(handler, path, data, app_dir, runner)
        from applypilot.country_context import CountryError, operation, resolve_view, require_preparation
        try:
            hot = path == "/api/center/browser/command"
            context = resolve_view(app_dir, country_code=data.get("country_code"), conn=None if hot else workspace_connection(app_dir / "applypilot.db"))
            nz_path = path.startswith("/api/center/nz/")
            if nz_path and context.country_code != "NZ":
                raise CountryError("Choose the NZ country view")
            if path not in {"/api/center/country/preferences", "/api/center/canonical/action", "/api/center/canonical/check"} and not nz_path:
                require_preparation(context.country_code)
            from applypilot.country_context import validate_request_jobs
            if not hot and not nz_path:
                validate_request_jobs(workspace_connection(app_dir / "applypilot.db"), app_dir, path, data)
            with operation(context):
                return _post(handler, path, data, app_dir, runner)
        except CountryError as exc:
            handler._send_json(400, {"ok": False, "error": str(exc)})
            return True


def _post(handler, path: str, data: dict, app_dir: Path, runner) -> bool:
    if not path.startswith("/api/center/"):
        return False
    if not isinstance(data, dict):
        handler._send_json(400, {"ok": False, "error": "Request body must be a JSON object"})
        return True
    if path=='/api/center/browser/command':
        from applypilot.apply.assisted_browser import command
        try:
            handler._send_json(200,{'ok':True,**command(app_dir,data)})
        except (ValueError,LookupError,TypeError,OverflowError) as exc:
            handler._send_json(400,{'ok':False,'error':str(exc)})
        except Exception:
            handler._send_json(503,{'ok':False,'error':'Browser input temporarily unavailable; retry after the current action'})
        return True
    conn = workspace_connection(app_dir / "applypilot.db")
    try:
        out = {}
        if path.startswith("/api/center/submit/"):
            out=_submit_authority(handler,path.removeprefix("/api/center/submit/"),data,conn,app_dir)
        elif path == "/api/center/documents/review":
            from applypilot.document_review import approve
            user=handler._current_user() if hasattr(handler,"_current_user") else None
            out=approve(conn,app_dir,data,actor=str(user["username"]) if user else "local_workspace_owner")
        elif path == "/api/center/canonical/check":
            from applypilot.canonical import check
            from applypilot.country_context import current,check_job
            text=data.get("text")
            if not isinstance(text,str) or not 1<=len(text)<=100000:raise ValueError("Provide draft text up to 100,000 characters")
            job=cc.get_job(conn,int(data["job_id"])) if data.get("job_id") else {"country_code":current().country_code}
            check_job(job,app_dir,country_code=current().country_code)
            out={"validation":check(conn,app_dir,text,job,kind=str(data.get("kind") or "resume"))}
        elif path == "/api/center/canonical/action":
            from applypilot.canonical import action
            user=handler._current_user() if hasattr(handler,"_current_user") else None
            out=action(conn,app_dir,data,actor=str(user["username"]) if user else "local_workspace_owner")
        elif path.startswith("/api/center/nz/"):
            from applypilot.discovery import nz
            from applypilot import nz_evidence as evidence
            user = handler._current_user() if hasattr(handler,"_current_user") else None
            actor = str(user["username"]) if user else "local_workspace_owner"
            action = path.removeprefix("/api/center/nz/")
            if action.startswith("submit/"):
                out=_submit_authority(handler,action.removeprefix("submit/"),data,conn,app_dir)
            elif action == "messages/review":
                from applypilot.evidence_inbox import review
                from applypilot.country_context import check_job
                ident=int(data.get("id") or 0)
                check_job(cc.get_job(conn,ident),app_dir,country_code="NZ")
                review(conn,app_dir,int(data.get("message_id") or 0),ident,str(data.get("hiring_status") or ""),due_at=data.get("due_at"))
                out={"reviewed":True}
            elif action == "packet/requirements":
                from applypilot.country_context import check_job
                job=cc.get_job(conn,int(data.get("id") or 0));check_job(job,app_dir,country_code="NZ")
                requirements=data.get("requirements")
                if not isinstance(requirements,dict) or set(requirements)-{"cover","statement","answers"} or any(k in requirements and not isinstance(requirements[k],bool) for k in ("cover","statement")):
                    raise ValueError("Use explicit cover/statement flags and required question strings")
                answers=requirements.get("answers",[])
                if not isinstance(answers,list) or len(answers)>50 or any(not isinstance(q,str) or not 1<=len(q.strip())<=1000 for q in answers):raise ValueError("Choose up to 50 bounded required questions")
                import json
                conn.execute("UPDATE jobs SET document_requirements=? WHERE url=? AND country_code='NZ'",(json.dumps(requirements),job["url"]));conn.commit()
                out={"requirements":requirements}
            elif action in {"lifecycle/close","lifecycle/delete","submission/reconcile"}:
                from applypilot.country_context import check_job
                check_job(cc.get_job(conn,int(data.get("id") or 0)),app_dir,country_code="NZ")
                if action=="lifecycle/close":
                    from applypilot.job_lifecycle import close_record
                    out=close_record(conn,app_dir,int(data["id"]),str(data.get("reason") or ""),str(data.get("detail") or ""))
                elif action=="lifecycle/delete":
                    from applypilot.job_lifecycle import retire
                    out=retire(conn,app_dir,data)
                else:
                    from applypilot.submission_reconciliation import reconcile
                    out=reconcile(conn,app_dir,data)
            elif action == "answer/review":
                from applypilot.country_answers import save
                out=save(conn,app_dir,data,actor=actor)
            elif action == "documents/review":
                from applypilot.document_review import approve
                out=approve(conn,app_dir,data,actor=actor)
            elif action == "prepare/start":
                from applypilot.preparation import require_ready
                require_ready(app_dir,conn)
                ids=data.get("ids")
                stages=data.get("stages") or ["score"]
                if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(isinstance(i,bool) or not isinstance(i,int) for i in ids):raise ValueError("Choose 1–100 NZ jobs")
                if not isinstance(stages,list) or not stages or any(s not in {"enrich","score","tailor","statement","cover","pdf"} for s in stages):raise ValueError("Choose supported preparation stages; submission is separate")
                from applypilot.country_context import check_job
                for ident in ids:check_job(cc.get_job(conn,ident),app_dir,country_code="NZ")
                ok,out=runner.start({"country_code":"NZ","campaign_id":nz.CAMPAIGN_ID,"stages":stages,"job_ids":ids})
                if not ok:raise ValueError(out.get("error","Cannot start NZ preparation"))
            elif action == "document/manual":
                from applypilot.preparation import manual_document
                out=manual_document(conn,app_dir,data,actor=actor)
            elif action == "destination/choose":
                from applypilot.preparation import choose_destination
                out=choose_destination(conn,app_dir,data,actor=actor)
            elif action == "tracking":
                from applypilot.preparation import track
                out=track(conn,app_dir,data)
            elif action == "packet/prepare":
                from applypilot.apply.application_packet import prepare
                from applypilot.country_context import check_job
                check_job(cc.get_job(conn,int(data.get("id") or 0)),app_dir,country_code="NZ")
                out=prepare(conn,app_dir,int(data.get("id") or 0))
            elif action == "cv/import":
                from applypilot.cv_library import import_files
                files=data.get("files")
                if not isinstance(files,list) or any(not isinstance(f,dict) for f in files):
                    raise ValueError("Provide NZ master files")
                out=import_files(conn,app_dir,[f|{"country_code":"NZ"} for f in files])
            elif action == "cv/reconcile":
                from applypilot.nz_documents import reconcile
                out={"reconciliation":reconcile(conn,app_dir,str(data.get("key") or ""))}
            elif action == "cv/action":
                from applypilot.cv_library import action as cv_action
                out=cv_action(conn,app_dir,data)
            elif action == "employer/save":
                out={"employer":evidence.save_employer(conn,app_dir,data,actor=actor)}
            elif action == "employer/link":
                out={"assessment":evidence.link_job(conn,app_dir,data,actor=actor)}
            elif action == "evidence/record":
                out=evidence.record(conn,app_dir,data,actor=actor)
            elif action == "evidence/review":
                out=evidence.review(conn,app_dir,data,actor=actor)
            elif action == "search":
                from applypilot.nz_search import search
                out = search(app_dir, data)
            elif action == "stop":
                from applypilot.nz_search import discovery_status
                state = discovery_status(conn, app_dir, runner, data.get("run_id"))
                if not state["active"] or not data.get("run_id"):
                    raise ValueError("Choose the active NZ discovery run to stop")
                ok, out = runner.stop(expected_run_id=state["run_id"])
                if not ok:
                    raise ValueError("The active run changed or has already stopped; refresh its status")
            elif action == "campaign/save":
                out = nz.save_campaign(conn, app_dir, data)
            elif action == "capture":
                out = nz.capture(conn, app_dir, data)
            elif action == "source/save":
                out = {"source": nz.save_source(app_dir, data)}
            elif action == "source/validate":
                out = {"validation": nz.validate_source(app_dir, str(data.get("id") or ""), str(data.get("query") or "Software Developer"), str(data.get("location") or "Auckland, New Zealand"))}
            elif action == "listing/check":
                job = cc.get_job(conn, int(data.get("id") or 0))
                if job.get("country_code") != "NZ":
                    raise ValueError("Choose an NZ vacancy")
                out = nz.check_listing(conn, app_dir, job)
            elif action == "start":
                from applypilot.pipeline_workspace import campaign_config
                running = runner.status()
                if running.get("running") or running.get("starting"):
                    raise ValueError("Another workflow is already running. Check its status before starting another import.")
                cfg = campaign_config(app_dir, nz.CAMPAIGN_ID)
                if not nz.matrix(app_dir, cfg)["automatic_discovery_available"]:
                    raise ValueError("No automatic source is ready. Use Find jobs to open NZ job-board results, or configure an import source under Advanced.")
                ok, out = runner.start({"country_code": "NZ", "campaign_id": nz.CAMPAIGN_ID, "stages": ["discover"]})
                if not ok:
                    raise ValueError(out.get("error", "Cannot start discovery"))
            else:
                raise ValueError("Unknown NZ discovery operation")
        elif path == "/api/center/country/preferences":
            from applypilot.country_context import current, save_preferences
            out = save_preferences(conn, current().country_code, data.get("patch"), data.get("expected_revision"), current().config)
        elif path=='/api/center/insights/activity':
            from applypilot.opportunity_insights import record_manual
            out=record_manual(conn,data)
        elif path=='/api/center/submission/reconcile':
            from applypilot.submission_reconciliation import reconcile
            out=reconcile(conn,app_dir,data)
        elif path.startswith("/api/center/lifecycle/"):
            from applypilot import job_lifecycle as lifecycle
            operation = path.rsplit('/', 1)[1]
            if operation == "close":
                out = lifecycle.close_record(conn, app_dir, int(data.get("id") or 0), str(data.get("reason") or ""), str(data.get("detail") or ""))
            elif operation in {"delete", "retry", "compact"}:
                runtime = runner.status()
                if runtime.get("running") or runtime.get("starting"):
                    raise ValueError("Stop the preparation pipeline before deleting files or compacting storage")
                lifecycle.reconcile_pipeline(conn, runner)
                out = lifecycle.retire(conn, app_dir, data) if operation == "delete" else lifecycle.retry_cleanup(conn, app_dir, str(data.get("history_id") or "")) if operation == "retry" else lifecycle.compact(conn, app_dir)
            else:
                raise ValueError("Unknown lifecycle operation")
        elif path == "/api/center/decisions/benchmark":
            from applypilot.decision_observer import start_benchmark
            out = start_benchmark(conn, app_dir)
        elif path == "/api/center/browser/start":
            from applypilot.apply.assisted_browser import start
            out = start(conn, app_dir, data)
        elif path == "/api/center/browser/prepare":
            from applypilot.apply.application_packet import prepare
            out = prepare(conn, app_dir, int(data.get("id") or 0))
        elif path == "/api/center/browser/command":
            from applypilot.apply.assisted_browser import command
            out = command(app_dir, data)
        elif path == "/api/center/ai/settings":
            from applypilot.ai_settings import save
            out = save(app_dir, data)
        elif path == "/api/center/cv/import":
            from applypilot.cv_library import import_files
            out = import_files(conn, app_dir, data.get("files"))
        elif path == "/api/center/cv/action":
            from applypilot.cv_library import action
            out = action(conn, app_dir, data)
        elif path == "/api/center/cv/build":
            from applypilot.cv_library import build_profile_cv
            from applypilot.setup_workspace import read_profile
            out = {"text": build_profile_cv(read_profile(app_dir))}
        elif path == "/api/center/cv/suggest":
            from applypilot.cv_library import suggestions
            out = suggestions(app_dir, str(data.get("key") or "default"))
        elif path.startswith("/api/center/studio/"):
            from applypilot import writing_studio as studio
            action = path.rsplit("/", 1)[1]
            if action == "generate":
                out = studio.generate(conn, app_dir, data)
            elif action == "save":
                out = studio.save(conn, app_dir, data)
            elif action == "validate":
                out = {"validation": studio.validate(str(data.get("text") or ""), studio.context(conn, app_dir, data))}
            elif action == "publish":
                out = studio.publish(conn, app_dir, int(data.get("id") or 0))
            elif action == "criteria":
                from applypilot.scoring.supporting_statement import _extract_person_spec_criteria_by_priority
                text = str(data.get("job_text") or "")
                if len(text) > 50000:
                    raise ValueError("Job description exceeds 50,000 characters")
                out = {"criteria": _extract_person_spec_criteria_by_priority(text)}
            elif action == "plan":
                from applypilot.nhs_writing import validated_plan, review
                plan = validated_plan(data, data.get("kind", "statement"))
                out = {"plan": plan, "review": review(plan, str(data.get("text") or ""))}
            else:
                raise ValueError("Unknown studio operation")
        elif path == "/api/center/portfolio/asset/action":
            ident = int(data.get("id") or 0)
            changed = conn.execute("UPDATE cc_portfolio_assets SET archived=? WHERE id=?", (0 if data.get("restore") else 1, ident)).rowcount
            if not changed:
                raise LookupError("Attachment not found")
            conn.commit()
        elif path == "/api/center/portfolio/upload":
            import base64, uuid
            from applypilot.setup_workspace import _atomic_write_bytes, read_profile
            project = str(data.get("project_id") or "")
            project_ids = {p.get("id") for key in ("projects", "data_projects", "support_projects", "application_support_projects") for p in read_profile(app_dir).get("resume_sections", {}).get(key, [])}
            if project not in project_ids and project != "__profile__":
                raise ValueError("Save the project before attaching evidence")
            name = str(data.get("name") or "")[:200]
            extension = Path(name).suffix.lower()
            types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".pdf": "application/pdf", ".txt": "text/plain"}
            blob = base64.b64decode(data.get("b64") or "", validate=True)
            if extension not in types or not blob or len(blob) > 8*1024*1024:
                raise ValueError("Attach a PNG, JPEG, PDF or TXT file up to 8 MB")
            if extension == ".png" and not blob.startswith(b"\x89PNG\r\n\x1a\n") or extension in {".jpg", ".jpeg"} and not blob.startswith(b"\xff\xd8") or extension == ".pdf" and not blob.startswith(b"%PDF-"):
                raise ValueError("Attachment does not match its file type")
            target = app_dir/"portfolio_assets"/(uuid.uuid4().hex+extension)
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_bytes(target, blob)
            out = {"id": conn.execute("INSERT INTO cc_portfolio_assets(project_id,name,path,mime,created_at) VALUES(?,?,?,?,?)", (project, name, str(target), types[extension], cc.now())).lastrowid}
            conn.commit()
        elif path.startswith("/api/center/pipeline/"):
            from applypilot import pipeline_workspace as pw
            action = path.removeprefix("/api/center/pipeline/")
            if action == "preview":
                out = pw.preview(conn, app_dir, data)
            elif action == "config/preview":
                out = pw.merge_config(app_dir, data)
            elif action == "config/save":
                out = pw.save_config(conn, app_dir, data)
            elif action == "campaign/save":
                out = pw.save_campaign(conn, app_dir, data)
            elif action == "source/save":
                from applypilot.source_registry import save_source
                out = {"source": save_source(app_dir, data)}
            elif action == "source/delete":
                from applypilot.source_registry import remove_source
                remove_source(app_dir, str(data.get("id") or ""))
                from applypilot.config import load_sites_config
                from applypilot.setup_workspace import read_searches_dict
                cfg = read_searches_dict(app_dir)
                names = {s["name"] for s in load_sites_config(app_dir).get("sites", [])}
                if cfg.get("smart_sites"):
                    retained = [s for s in cfg["smart_sites"] if s in names]
                    pw.save_config(conn, app_dir, {"patch": {"smart_sites": retained, "smart_sources_disabled": not bool(retained)}})
            elif action == "source/preview":
                from applypilot.source_registry import preview
                out = preview(app_dir, data.get("source") or {}, str(data.get("query") or "Analyst"), str(data.get("location") or "London"))
            elif action == "job/save":
                pw.save_job(conn, app_dir, data)
            elif action == "employer/save":
                from applypilot.source_registry import save_employer
                out = {"employer": save_employer(app_dir, data)}
            elif action == "employer/preview":
                from applypilot.source_registry import preview_employer
                out = preview_employer(data, str(data.get("query") or "Analyst"))
            elif action == "evaluate":
                out = pw.evaluate_variants(conn, app_dir, data)
            elif action == "retry":
                ids = pw.retry(conn, app_dir, data)
                ok, out = runner.start({"stages": [data["stage"]], "job_ids": ids})
                if not ok:
                    raise ValueError(out.get("error", "Cannot start retry"))
            elif action == "start":
                stages = data.get("stages") or ["discover", "enrich", "score"]
                if not isinstance(stages, list) or any(s not in pw.STAGES for s in stages):
                    raise ValueError("Choose supported preparation stages")
                if 'discover' in stages and data.get('scope_policy') is None:
                    raise ValueError('Refresh Pipeline and review the bounded search scope before starting discovery.')
                if data.get('scope_policy') is None and not (data.get('job_ids') or data.get('regenerate_ids')):
                    raise ValueError('Refresh Pipeline to choose the exact jobs and limits for this run.')
                plan=pw.preview(conn, app_dir, data)
                if data.get('scope_policy') is not None:
                    if not plan.get('can_start'):raise ValueError(plan.get('start_problem') or 'No work is selected')
                    if plan.get('missing_inputs'):raise ValueError('Complete processing inputs before starting: '+', '.join(plan['missing_inputs']))
                    if data.get('expected_preview_token')!=plan.get('preview_token'):raise ValueError('Search settings changed or were not reviewed. Preview the current scope before starting.')
                ok, out = runner.start(data | {"stages": stages})
                if not ok:
                    raise ValueError(out.get("error", "Cannot start run"))
            else:
                raise ValueError("Unknown pipeline operation")
        elif path == "/api/center/action":
            ids = data.get("ids") or [data.get("id")]
            if not isinstance(ids, list) or not 1 <= len(ids) <= 100:
                raise ValueError("Choose between 1 and 100 jobs")
            results = []
            for job_id in ids:
                try:
                    result = cc.update_job(conn, app_dir, int(job_id), str(data.get("action") or ""), data)
                    results.append({"id": job_id, "ok": True, **result})
                except (ValueError, LookupError) as exc:
                    results.append({"id": job_id, "ok": False, "error": str(exc)})
            out = {"results": results}
        elif path == "/api/center/unblock":
            cc.unblock(conn, int(data.get("id") or 0))
        elif path == "/api/center/regenerate":
            ids = data.get("ids")
            if ids is None:
                ids = [r[0] for r in conn.execute("SELECT rowid FROM jobs WHERE apply_status='selected'")]
            if not isinstance(ids, list) or not ids or len(ids) > 100:
                raise ValueError("Choose between 1 and 100 jobs to regenerate")
            for job_id in ids:
                job = cc.get_job(conn, int(job_id))
                if job.get("apply_status") in cc.ACTIVE_STATES:
                    raise ValueError("Cannot regenerate a CV while its application worker is active")
                if not job.get("full_description"):
                    raise ValueError(f"Job #{job_id} needs enrichment before regeneration")
            ok, info = runner.start({"stages": ["tailor", "pdf"], "regenerate_ids": [int(i) for i in ids], "selected_only": False})
            if not ok:
                raise ValueError(info.get("error", "Pipeline could not start"))
            out = info
        elif path == "/api/center/apply":
            job = cc.get_job(conn, int(data.get("id") or 0))
            if job.get("applied_at") or job.get("apply_status") in cc.ACTIVE_STATES | {"submission_uncertain"}:
                raise ValueError("This job is already submitted, active, or has an uncertain submission")
            if cc.blocked(conn, job["url"], job.get("company")):
                raise ValueError("Unblock this job before applying")
            if cc.preference_exclusions(conn, job):
                raise ValueError("This job is excluded by your application preferences")
            check = conn.execute("SELECT status FROM cc_listing_checks WHERE job_url=?", (job["url"],)).fetchone()
            if check and check[0] == "closed":
                raise ValueError("This listing was checked as closed. Recheck it before starting an application.")
            hidden = conn.execute("SELECT visibility FROM cc_job_meta WHERE job_url=?", (job["url"],)).fetchone()
            if hidden and hidden[0] != "active":
                raise ValueError("Restore this job before applying")
            if not job.get("tailored_resume_path"):
                raise ValueError("Prepare an approved CV before starting application automation")
            from applypilot.eligibility import packet
            ready = packet(conn, job)
            if not ready["ready"]:
                raise ValueError("Application packet needs: " + "; ".join(ready["missing"] + ready["excluded"]))
            ok, info = runner.start({"stages": ["apply"], "application_target": job["url"],
                                    "country_code": job.get("country_code"), "campaign_id": job.get("campaign_id") if job.get("country_code") == "NZ" else None,
                                    "engine": data.get("engine", "claude"), "submit": data.get("submit", False),
                                    "dry_run": data.get("dry_run", True)})
            if not ok:
                raise ValueError(info.get("error", "Application could not start"))
            out = info
        elif path == "/api/center/assistant":
            out = cc.assistant(conn, app_dir, data)
        elif path == "/api/center/listing/check":
            out = cc.check_listing(conn, int(data.get("id") or 0))
        elif path == "/api/center/preferences":
            cc.save_preferences(conn, data)
        elif path == "/api/center/answers/save":
            question, answer = str(data.get("question") or "").strip(), str(data.get("answer") or "").strip()
            if not question or not answer or len(question) > 1000 or len(answer) > 6000:
                raise ValueError("Provide a question (up to 1000 characters) and verified answer (up to 6000 characters)")
            if data.get("id"):
                if not conn.execute("UPDATE cc_answers SET question=?,answer=?,updated_at=? WHERE id=?", (question, answer, cc.now(), int(data["id"]))).rowcount:
                    raise LookupError("Verified answer not found")
            else:
                conn.execute("INSERT INTO cc_answers(question,answer,updated_at) VALUES(?,?,?) ON CONFLICT(question) DO UPDATE SET answer=excluded.answer,updated_at=excluded.updated_at", (question, answer, cc.now()))
            conn.commit()
        elif path == "/api/center/answers/delete":
            conn.execute("DELETE FROM cc_answers WHERE id=?", (int(data.get("id") or 0),))
            conn.commit()
        elif path == "/api/center/schedule":
            from applypilot.scheduler import schedule

            out = {"schedule_id": schedule(conn, int(data.get("id") or 0), str(data.get("run_at") or ""), str(data.get("mode") or "prepare"))}
        elif path == "/api/center/schedule/cancel":
            changed = conn.execute("UPDATE cc_schedules SET state='canceled',detail='Canceled by user' WHERE id=? AND state='queued'", (int(data.get("id") or 0),)).rowcount
            conn.commit()
            if not changed:
                raise ValueError("Only queued schedules can be canceled; stop active workers from Pipeline")
        elif path == "/api/center/messages/import":
            out = {"message": cc.import_message(conn, data)}
        elif path == "/api/center/messages/analyze":
            from applypilot.evidence_inbox import analyze

            out = analyze(conn, app_dir, int(data.get("message_id") or 0))
        elif path == "/api/center/messages/action":
            from applypilot.evidence_inbox import message_action

            out = {"message": message_action(conn, int(data.get("message_id") or 0), str(data.get("action") or ""), data.get("category"))}
        elif path == "/api/center/messages/unignore":
            from applypilot.evidence_inbox import unignore

            unignore(conn, int(data.get("id") or 0))
        elif path == "/api/center/mail/connect":
            from applypilot.mail_connectors import connect

            out = {"authorization_url": connect(conn, str(data.get("provider") or ""))}
        elif path == "/api/center/mail/sync":
            from applypilot.mail_connectors import sync

            out = {"processed": sync(conn, app_dir, str(data.get("provider") or ""))}
        elif path == "/api/center/mail/disconnect":
            from applypilot.mail_connectors import disconnect

            disconnect(conn, str(data.get("provider") or ""))
        elif path == "/api/center/messages/review":
            from applypilot.evidence_inbox import review

            review(conn, app_dir, int(data.get("message_id") or 0), int(data.get("id") or 0), str(data.get("hiring_status") or ""), due_at=data.get("due_at") if data.get("use_date") else None, include_details=data.get("include_details") is True, use_recruiter=data.get("use_recruiter") is True)
        else:
            handler._send_json(404, {"ok": False, "error": "not_found"})
            return True
        handler._send_json(200, {"ok": True, **out})
    except SourcePreviewUnavailable as exc:
        conn.rollback()
        handler._send_json(504 if exc.category == 'timeout' else 502, {'ok':False,'error':str(exc),'category':exc.category,'remote_http_status':exc.http_status,'can_save_source':True})
    except cc.AssistantUnavailable as exc:
        conn.rollback()
        handler._send_json(504 if exc.category == "timeout" else 503, {"ok": False, "error": str(exc), "category": exc.category})
    except (ValueError, LookupError, TypeError) as exc:
        conn.rollback()
        handler._send_json(400, {"ok": False, "error": str(exc)})
    except Exception:
        conn.rollback()
        # Provider errors may contain request credentials or portal URLs.
        message = "Source operation could not finish. Check workspace source settings or server logs, then retry." if path.startswith('/api/center/pipeline/source/') else "Operation unavailable. Check provider configuration or server logs, then retry."
        handler._send_json(503, {"ok": False, "error": message})
    return True
