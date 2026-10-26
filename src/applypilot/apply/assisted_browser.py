"""Workspace-owned, single-thread Playwright sessions for semi-assisted applications.

The authenticated dashboard carries screenshots and input; no browser control port
is exposed. Each session owns its browser and immutable reviewed packet. Final
submission is a separate, single-use command bound to the inspected page.
"""
from __future__ import annotations

import hashlib
import base64
import json
import logging
import queue
import re
import secrets
import shutil
import socket
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from applypilot import command_center as cc
from applypilot.ai_settings import workspace_context
from applypilot.database import close_connection, init_db
from applypilot.setup_workspace import read_profile
from applypilot.apply.browser_fields import FIELD_HELPERS, FOCUSED_FIELD, QUESTION_FIELDS, FOCUS_TRACKER

WIDTH, HEIGHT = 1280, 900
TARGET_FPS = 30
TERMINAL = {"confirmed", "uncertain", "stopped", "failed"}
_sessions: dict[str, "Session"] = {}
_lock = threading.RLock()
log = logging.getLogger(__name__)

# Keep forward navigation deliberately narrow. Ambiguous controls require takeover.
FORWARD = {"next", "continue", "save and continue", "save & continue", "review", "review application"}
FINAL = {"submit", "submit application", "send application", "apply", "apply now", "complete application", "finish"}
CONTROLS = "button, a, [role=button], input[type=submit], input[type=button]"
INSPECT = "() => {" + FIELD_HELPERS + r"""
  const label = el => (el.innerText||el.value||el.getAttribute('aria-label')||'').replace(/\s+/g,' ').trim();
  const controls = [...document.querySelectorAll('button, a, [role=button], input[type=submit], input[type=button]')].map((el,index)=>({index,label:label(el),tag:el.tagName.toLowerCase(),href:el.getAttribute('href')||'',name:el.name||el.id||'',postback_entry:el.classList.contains('AdvortoApplyNowButton'),disabled:el.disabled||el.getAttribute('aria-disabled')==='true',visible:visible(el)})).filter(c=>c.visible);
  const fields=nodes().filter(el=>!['hidden','submit','button','reset','image'].includes(el.type)&&(visible(el)||el.type==='file')).map(describe);
  return {controls,fields,postback_listing:!!document.querySelector('#vacancy-information .AdvortoApplyNowButton'),text:(document.body?.innerText||'').slice(0,60000)};
}"""
GUARD = r"""(() => {
  const forwards=new Set(['next','continue','save and continue','save & continue','review','review application']);
  const finals=new Set(['submit','submit application','send application','apply','apply now','complete application','finish']);
  const auth=new Set(['sign in','log in','login','register','create account','create an account']);
  const label=el=>(el?.innerText||el?.value||el?.getAttribute('aria-label')||'').replace(/\s+/g,' ').trim().toLowerCase();
  window.__applypilotApproved=false;
  window.__applypilotEntryControl=null;
  document.addEventListener('click',e=>{const el=e.target.closest('button,a,[role=button],input[type=submit],input[type=button]');if(!el)return;const t=label(el),href=el.getAttribute('href')||'';const entry=(el.tagName==='A'&&href&&!href.startsWith('#')&&!href.toLowerCase().startsWith('javascript:'))||el===window.__applypilotEntryControl;if(!window.__applypilotApproved&&!entry&&(finals.has(t)||/\b(submit|send application|complete application)\b/.test(t))){e.preventDefault();e.stopImmediatePropagation();}},true);
  document.addEventListener('submit',e=>{const t=label(e.submitter),entry=window.__applypilotEntryControl&&e.submitter===window.__applypilotEntryControl;if(!window.__applypilotApproved&&!entry&&!forwards.has(t)&&!auth.has(t)){e.preventDefault();e.stopImmediatePropagation();}},true);
})()"""


def error_category(exc, operation):
    """Inspect error wording privately; expose/log only a known category."""
    message = str(exc).lower()
    if any(x in message for x in ('execution context was destroyed', 'cannot find context', 'frame was detached', 'frame has been detached')):
        return 'navigation_changed'
    if any(x in message for x in ('target page, context or browser has been closed', 'browser has been closed', 'no live application tab')):
        return 'browser_closed'
    if operation == 'launch':
        return 'browser_launch'
    if 'timeout' in type(exc).__name__.lower() or 'timeout' in message or 'timed out' in message:
        return 'screenshot_timeout' if operation == 'capture' else 'navigation_timeout'
    return 'browser_action'


def listing_entries(snapshots):
    if any(info['fields'] for _, info in snapshots):
        return []
    links = [(frame, c, False) for frame, info in snapshots for c in info['controls']
             if not c['disabled'] and c['tag']=='a' and c['href'] and not c['href'].lower().startswith(('#','javascript:'))
             and c['label'].lower() in {'apply','apply now','start application','begin application'}]
    if len(links)==1:
        return links
    postbacks=[]
    for frame, info in snapshots:
        parsed=urlsplit(frame.url)
        if not (parsed.hostname or '').lower().endswith('.kallidusrecruit.com') or parsed.path.lower()!='/vacancyinformation.aspx' or not info.get('postback_listing'):
            continue
        matches=[c for c in info['controls'] if not c['disabled'] and c.get('postback_entry') and c['label'].lower() in {'apply','apply now'}]
        # Kallidus renders equivalent top/bottom entry controls. Authorize just
        # the first current element, never final submission or a general form.
        if matches and all(c['label'].lower()==matches[0]['label'].lower() for c in matches):
            postbacks.append((frame,matches[0],True))
    return postbacks if len(postbacks)==1 else []


def _root(app_dir: Path) -> Path:
    return app_dir.resolve()


def _owned(app_dir: Path, ident: str) -> "Session":
    with _lock:
        session = _sessions.get(ident)
        if not session or session.root != _root(app_dir):
            raise LookupError("Browser session not found in this workspace")
        return session


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS cc_browser_sessions(
        id TEXT PRIMARY KEY,job_url TEXT NOT NULL,state TEXT NOT NULL,
        detail TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
    from applypilot.opportunity_insights import init_browser_history
    init_browser_history(conn)
    conn.commit()


def current(conn, app_dir: Path, job_id: int) -> dict:
    job = cc.get_job(conn, job_id)
    _schema(conn)
    with _lock:
        for session in reversed(list(_sessions.values())):
            if session.root == _root(app_dir) and session.job["url"] == job["url"]:
                return {"session": session.public()}
        # A durable interrupted attempt must never silently become retryable.
        rows = conn.execute("SELECT * FROM cc_browser_sessions WHERE job_url=? ORDER BY created_at DESC LIMIT 1", (job["url"],)).fetchall()
        if rows and rows[0]["state"] not in TERMINAL:
            row = rows[0]
            state = "uncertain" if row["state"] == "submitting" else "stopped"
            detail = "Browser interrupted. Reconcile employer confirmation before retrying." if state == "uncertain" else "Browser interrupted before approval. Start a new reviewed session."
            conn.execute("UPDATE cc_browser_sessions SET state=?,detail=?,updated_at=? WHERE id=?", (state, detail, cc.now(), row["id"]))
            conn.execute("UPDATE jobs SET apply_status=?,agent_id=NULL,apply_error=? WHERE url=? AND agent_id=?", ("submission_uncertain" if state == "uncertain" else "prepared", detail, job["url"], "browser:"+row["id"]))
            conn.commit()
            rows = conn.execute("SELECT * FROM cc_browser_sessions WHERE id=?", (row["id"],)).fetchall()
        if rows:
            row = rows[0]
            meta = conn.execute("SELECT reference FROM cc_job_meta WHERE job_url=?", (job["url"],)).fetchone()
            return {"session": {"id": row["id"], "job_id": job_id, "state": row["state"], "detail": row["detail"],
                                "width": WIDTH, "height": HEIGHT, "steps": 0, "page_url": "", "approval_token": "",
                                "review_summary": [], "documents": [], "reference": meta[0] if meta else ""}}
        return {"session": None}


def start(conn, app_dir: Path, data: dict) -> dict:
    from applypilot.apply.safety import require_live_execution
    require_live_execution()
    if data.get("documents_approved") is not True:
        raise ValueError("Review and approve the application documents first")
    root = _root(app_dir)
    _schema(conn)
    with _lock:
        current(conn, root, int(data.get("id") or 0))
        live = [s for s in _sessions.values() if s.root == root and s.state not in TERMINAL]
        if live:
            raise ValueError("Finish or stop your current browser session first")
        if sum(s.state not in TERMINAL for s in _sessions.values()) >= 4:
            raise ValueError("Browser capacity reached. Try again after an active session finishes")
        conn.execute("BEGIN IMMEDIATE")
        try:
            job = cc.get_job(conn, int(data.get("id") or 0))
            from applypilot.country_context import check_job
            check_job(job, root)
            from applypilot.apply.application_packet import status as packet_status
            ready = packet_status(conn, root, job["id"])
            if data.get("packet_token") and data["packet_token"] != ready["packet_token"]:
                raise ValueError("Documents changed since this screen loaded. Refresh and review the current packet")
            if job.get("apply_status") == "submission_uncertain" or job.get("applied_at") or not ready["ready"]:
                raise ValueError("Application is not ready: " + "; ".join(ready["missing"] + ready["excluded"] or ["reconcile uncertain submission"]))
            checked = conn.execute("SELECT status FROM cc_listing_checks WHERE job_url=?", (job["url"],)).fetchone()
            if checked and checked[0] == "closed":
                raise ValueError("Recheck this closed listing before applying")
            target = job.get("application_url") or job["url"]
            if urlsplit(target).scheme not in {"http", "https"} or not urlsplit(target).hostname:
                raise ValueError("Application link must be an HTTP(S) URL")
            ident = secrets.token_urlsafe(24)
            folder = root / "browser_sessions" / ident
            folder.mkdir(parents=True, exist_ok=True)
            documents, texts = {}, {}
            for kind, field in (("resume", "tailored_resume_path"), ("cover", "cover_letter_path"), ("statement", "supporting_statement_path")):
                raw = job.get(field)
                if not raw:
                    continue
                source = Path(raw).resolve()
                if not source.is_relative_to(root) or not source.is_file():
                    raise ValueError("Application documents must belong to this workspace")
                if source.suffix.lower() == ".txt":
                    texts[kind] = source.read_text(encoding="utf-8")
                    shutil.copy2(source, folder / (kind + ".txt"))
                    source = source.with_suffix(".pdf").resolve()
                    if not source.is_relative_to(root):
                        raise ValueError("Application PDFs must belong to this workspace")
                if source.is_file():
                    saved = folder / (kind + source.suffix)
                    shutil.copy2(source, saved)
                    documents[kind] = str(saved)
            if "resume" not in documents:
                raise ValueError("Render the reviewed CV PDF before starting")
            profile, verified = application_data(conn, root, job)
            session = Session(root, ident, job, documents, texts, profile, verified)
            session.packet_binding = application_binding(conn, root, job, documents)
            document_id = cc.snapshot_document(conn, root, job)
            conn.execute("INSERT OR IGNORE INTO cc_job_meta(job_url) VALUES(?)", (job["url"],))
            conn.execute("UPDATE cc_job_meta SET document_id=? WHERE job_url=?", (document_id, job["url"]))
            conn.execute("UPDATE jobs SET apply_status='in_progress',agent_id=?,last_attempted_at=?,apply_error=NULL WHERE url=?", ("browser:"+ident, cc.now(), job["url"]))
            conn.execute("INSERT INTO cc_browser_sessions(id,job_url,state,detail,created_at,updated_at) VALUES(?,?,?,?,?,?)", (ident, job["url"], "opening", "Starting reviewed browser session", cc.now(), cc.now()))
            cc.event(conn, job["url"], "browser", "Candidate approved the document packet; visible browser starting", state="opening", source="candidate")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        # Prune ended in-memory sessions, retaining their durable evidence on disk.
        for key, old in list(_sessions.items()):
            if old.root == root and old.state in TERMINAL:
                del _sessions[key]
        _sessions[ident] = session
        threading.Thread(target=session.run, name="assisted-browser", daemon=True).start()
        return {"session": session.public()}


def application_data(conn, root, job):
    profile = read_profile(root)
    if job.get("country_code") == "NZ":
        from applypilot.country_context import read_preferences
        from applypilot.country_answers import reviewed
        from applypilot.preparation import country_profile
        preferences = read_preferences(conn, "NZ")["config"]
        profile = country_profile(profile, preferences)
        verified = []
        for item in preferences.get("application_operations", {}).get("answers", []):
            if not isinstance(item, dict):
                continue
            answer = reviewed(conn, "NZ", item.get("question", ""), job=job)
            if answer and not answer["manual_review_only"] and not any(v["id"] == answer["id"] for v in verified):
                verified.append(answer)
    else:
        verified = [dict(r) for r in conn.execute("SELECT id,question,answer FROM cc_answers ORDER BY id LIMIT 30")]
    return profile, verified


def application_binding(conn, root, job, documents):
    """Bind the reviewed packet, destination, answers and copied upload bytes."""
    from applypilot.canonical import digest
    from applypilot.apply.application_packet import status as packet_status
    packet = packet_status(conn, root, job["id"], include_review=False)
    exclusions = [r for r in packet["excluded"] if r != "Job is skipped, blocked or being applied to"]
    if packet["missing"] or exclusions:
        raise ValueError("Application packet changed; review its current documents and answers")
    profile, answers = application_data(conn, root, job)
    return digest({"job": {k: job.get(k) for k in ("url", "application_url", "country_code", "title", "company", "document_requirements")},
                   "packet": packet["packet_token"], "profile": profile, "answers": answers,
                   "uploads": {k: hashlib.sha256(Path(v).read_bytes()).hexdigest() for k, v in documents.items()}})


def status(app_dir: Path, ident: str) -> dict:
    session = _owned(app_dir, ident)
    session.touched = time.monotonic()
    return {"session": session.public()}


def screenshot(app_dir: Path, ident: str) -> bytes:
    session = _owned(app_dir, ident)
    session.touched = time.monotonic()
    with session.lock:
        image = session.image
    if not image:
        raise ValueError("Browser is still opening")
    return image


def stream(handler,app_dir,ident):
    session=_owned(app_dir,ident)
    authenticated=bool(handler._current_user())
    try:
        handler.connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
    except (AttributeError,OSError):
        pass
    handler.send_response(200)
    handler.send_header('Content-Type','multipart/x-mixed-replace; boundary=applypilot-frame')
    handler.send_header('Cache-Control','no-store')
    handler.send_header('X-Content-Type-Options','nosniff')
    handler.end_headers()
    handler.wfile.write(b'--applypilot-frame\r\n')
    handler.wfile.flush()
    last=-1
    checked=0.0
    try:
        while True:
            now=time.monotonic()
            if now-checked>2:
                if authenticated and not handler._current_user():
                    break
                checked=now
            with session.frames:
                if session.frame_sequence==last and session.state not in TERMINAL:
                    session.frames.wait(timeout=1)
                image=session.image
                last=session.frame_sequence
                frame_command=session.frame_command_id
                ended=session.state in TERMINAL
                session.touched=time.monotonic()
            if image:
                header=b'Content-Type: image/jpeg\r\nContent-Length: '+str(len(image)).encode()+b'\r\nX-Frame-Sequence: '+str(last).encode()+b'\r\nX-Input-Command: '+str(frame_command).encode()+b'\r\n\r\n'
                # End the part now, so the decoder need not wait for the next frame.
                handler.wfile.write(header+image+b'\r\n--applypilot-frame\r\n')
                handler.wfile.flush()
            if ended:
                break
    except (BrokenPipeError,ConnectionError,OSError):
        pass
    finally:
        handler.close_connection=True


INPUT_KEYS={'Tab','Shift+Tab','Backspace','Delete','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','Control+A','Meta+A','Enter','Escape','Space'}


def input_events(events):
    if not isinstance(events,list) or not 1<=len(events)<=64:
        raise ValueError('Provide 1–64 ordered browser input events')
    clean=[]
    chars=0
    for event in events:
        if not isinstance(event,dict) or event.get('type') not in {'click','text','key','scroll','mouse'}:
            raise ValueError('Unsupported browser input event')
        kind=event['type']
        if kind=='text':
            text=event.get('text')
            if not isinstance(text,str):
                raise ValueError('Text input must be a string')
            chars+=len(text)
            if chars>15000:
                raise ValueError('Input batch text exceeds 15,000 characters')
            clean.append({'type':kind,'text':text})
        elif kind=='key':
            if event.get('key') not in INPUT_KEYS:
                raise ValueError('Unsupported browser key')
            clean.append({'type':kind,'key':event['key']})
        elif kind=='click':
            clean.append({'type':kind,'x':max(0,min(WIDTH-1,int(event.get('x',0)))),'y':max(0,min(HEIGHT-1,int(event.get('y',0))))})
        elif kind=='mouse':
            phase=event.get('phase')
            button=event.get('button','none')
            if phase not in {'move','down','up','cancel'} or button not in {'none','left','right','middle'} or (phase!='move' and button=='none'):
                raise ValueError('Unsupported mouse phase/button')
            count=int(event.get('click_count',1))
            buttons=int(event.get('buttons',0))
            modifiers=int(event.get('modifiers',0))
            if count not in {1,2,3} or not 0<=buttons<=7 or not 0<=modifiers<=15:
                raise ValueError('Unsupported mouse click count/modifiers')
            clean.append({'type':kind,'phase':phase,'button':button,'buttons':buttons,'click_count':count,'modifiers':modifiers,
                          'x':max(0,min(WIDTH-1,int(event.get('x',0)))),'y':max(0,min(HEIGHT-1,int(event.get('y',0))))})
        else:
            clean.append({'type':kind,'delta':max(-1800,min(1800,int(event.get('delta',0))))})
    return clean


def command(app_dir: Path, data: dict) -> dict:
    session = _owned(app_dir, str(data.get("session_id") or ""))
    action = str(data.get("action") or "")
    if action not in {"takeover", "resume", "review", "approve", "check", "stop", "click", "text", "key", "scroll", "upload", "cookie", "input", "read_fields", "select_field", "draft_answer", "insert_answer"}:
        raise ValueError("Unsupported browser action")
    with session.lock:
        if session.state in TERMINAL:
            raise ValueError("This browser session has ended")
        if action in {"click", "text", "key", "scroll", "upload", "cookie", "input", "read_fields", "select_field", "draft_answer", "insert_answer"} and session.state != "manual":
            raise ValueError("Take over the browser before entering input")
        if action == "click":
            data = data | {"x": max(0, min(WIDTH-1, int(data.get("x", 0)))), "y": max(0, min(HEIGHT-1, int(data.get("y", 0))))}
        if action == "text" and (not isinstance(data.get("text"), str) or len(data["text"]) > 15000):
            raise ValueError("Text input must be at most 15,000 characters")
        if action == "key" and data.get("key") not in {"Tab", "Shift+Tab", "Backspace", "Delete", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "Control+A", "Meta+A", "Enter", "Escape", "Space"}:
            raise ValueError("Unsupported browser key")
        if action == "scroll":
            data = data | {"delta": max(-900, min(900, int(data.get("delta", 500))))}
        if action=='cookie' and data.get('choice') not in {'save_settings','accept_all'}:
            raise ValueError('Choose a displayed cookie preference action')
        if action=='input':
            data=data|{'events':input_events(data.get('events'))}
        if action=='insert_answer' and (not isinstance(data.get('text'),str) or not data['text'].strip() or len(data['text'])>15000):
            raise ValueError('Provide an answer of 1–15,000 characters')
        if action=='draft_answer' and len(str(data.get('guidance') or ''))>1000:
            raise ValueError('Draft guidance must be at most 1,000 characters')
        if action == "approve" and (session.state != "review" or not session.approval or not secrets.compare_digest(str(data.get("approval_token") or ""), session.approval)):
            raise ValueError("Approval expired. Review the current page again")
        if action in {"takeover", "stop"}:
            session.interrupt.set()
        if action != "approve":
            session.approval = ""
        session.touched = time.monotonic()
        session.command_sequence+=1
        command_id=session.command_sequence
        data=data|{'_queued_at':time.monotonic(),'_command_id':command_id}
        try:
            session.commands.put_nowait((action, dict(data)))
        except queue.Full:
            raise ValueError("Browser is busy; wait for the current action") from None
    if action=='input' and data.get('compact') is True:
        return {'accepted':True,'command_id':command_id}
    return {"session": session.public(),"command_id":command_id}


class Session:
    def __init__(self, root, ident, job, documents, texts, profile, verified):
        self.root, self.id, self.job = root, ident, job
        self.documents, self.texts, self.profile, self.verified = documents, texts, profile, verified
        self.state, self.detail = "opening", "Opening the employer application"
        self.lock = threading.RLock()
        self.frames=threading.Condition(self.lock)
        self.commands = queue.Queue(maxsize=40)
        self.interrupt = threading.Event()
        self.touched = time.monotonic()
        self.image = b""
        self.frame_sequence=0
        self.frame_command_id=0
        self.last_input_command=0
        self.last_input_ms=None
        self.command_sequence=0
        self.command_results=[]
        self.focused_field=None
        self.question_fields=[]
        self.targets={}
        self.answer_task=None
        self.answer_draft=None
        self.last_focus_read=0.0
        self.focus_tracking=False
        self.focus_frame=None
        self.last_input_at=0.0
        self.last_dispatch_ms=None
        self.last_queue_ms=None
        self.input_audits=[]
        self.audit_future=None
        self.last_audit_flush=0.0
        self._input_page=None
        self._input_session=None
        self.pointer_page=None
        self.pointer_buttons=0
        self.screencasting=False
        self.frame_times=[]
        self.pending_frame=None
        self.capture_ms=None
        self.plan_cache={}
        self.last_capture=0.0
        self.last_inspection=0.0
        self.approval = ""
        self.approval_expires = 0.0
        self.review_signature = ""
        self.review_control = None
        self.review_summary = []
        self.steps = 0
        self.page_url = ""
        self.submission_attempted = False
        self.reference = ""
        self.uploaded = set()
        self.operation = 'launch'
        self.last_error = None
        self._diagnostic_key=None
        self._capture_page = None
        self._capture_session = None
        self._frame_handler=None
        self.cookie_controls=[]
        from applypilot.decision_observer import Observer
        self.observer = Observer(root, ident)

    def public(self):
        with self.lock:
            return {"id": self.id, "job_id": self.job["id"], "state": self.state, "detail": self.detail,
                    "steps": self.steps, "page_url": self.page_url, "width": WIDTH, "height": HEIGHT,
                    "approval_token": self.approval, "review_summary": list(self.review_summary),
                    "documents": list(self.documents), "reference": self.reference,
                    "decision_observation": self.observer.public(), "last_error": self.last_error,
                    "cookie_controls": self.cookie_controls,
                    "focused_field":self.focused_field,"question_fields":list(self.question_fields),
                    "answer_draft":self.answer_draft,"command_results":list(self.command_results),
                    "performance":{'frame_sequence':self.frame_sequence,'target_fps':TARGET_FPS,'last_input_ms':self.last_input_ms,'pending_commands':self.commands.qsize(),
                                   'dispatch_ms':self.last_dispatch_ms,'queue_ms':self.last_queue_ms,'focus_transport':'events' if self.focus_tracking else 'reader',
                                   'frame_transport':'cdp_screencast' if self.screencasting else 'screenshot_fallback','capture_ms':self.capture_ms,
                                   'recent_fps':round((len(self.frame_times)-1)/(self.frame_times[-1]-self.frame_times[0]),1) if len(self.frame_times)>1 and self.frame_times[-1]>self.frame_times[0] else None}}

    def transition(self, state, detail):
        conn = self.conn
        conn.execute("UPDATE cc_browser_sessions SET state=?,detail=?,updated_at=? WHERE id=?", (state, detail, cc.now(), self.id))
        conn.execute("UPDATE jobs SET last_attempted_at=? WHERE url=? AND agent_id=?", (cc.now(), self.job["url"], "browser:"+self.id))
        if state in TERMINAL:
            result = {"confirmed": "applied", "uncertain": "submission_uncertain", "stopped": "prepared", "failed": "failed"}[state]
            conn.execute("UPDATE jobs SET apply_status=?,apply_error=?,agent_id=NULL,applied_at=CASE WHEN ?='applied' THEN ? ELSE applied_at END WHERE url=? AND agent_id=?", (result, detail, result, cc.now(), self.job["url"], "browser:"+self.id))
        payload={'attempt_id':self.id,'steps':self.steps,'performance':self.public()['performance']}
        if state=='review':
            payload['answers']=list(self.review_summary)
            payload['reviewed_documents']=list(self.documents)
        cc.event(conn, self.job["url"], "browser", detail, state=state, source="agent",payload=payload)
        conn.commit()
        # Publish state only after its record is durable (especially submitting).
        with self.lock:
            if state in TERMINAL:
                self.publish_frame(force=True)
            self.state, self.detail = state, detail
            if state != "review":
                self.approval = ""
            self.frames.notify_all()

    def select_page(self):
        live=[p for p in self.context.pages if not p.is_closed()]
        if not live:
            raise RuntimeError('No live application tab')
        previous=getattr(self,'page',None)
        self.page=live[-1]
        if previous and self.page!=previous:
            self.page.bring_to_front()
            self.focused_field=None
            self.focus_frame=None
            self.question_fields=[]

    @staticmethod
    def value_hash(value):
        return hashlib.sha256(str(value or '').encode()).hexdigest()

    def remember_field(self,frame,field):
        self.targets[field['token']]=(frame,dict(field))
        # Public question metadata excludes field contents and authentication values.
        return {k:field.get(k) for k in ('token','document_token','label','help','type','tag','maxlength','options')} | {
            'has_value':bool(field.get('value')),'value_hash':self.value_hash(field.get('value')),
            'writable':not field.get('disabled') and not field.get('readonly') and field['type'] not in {'password','file','checkbox','radio'} }

    def read_focus(self):
        self.last_focus_read=time.monotonic()
        self.select_page()
        self.page_url=self.page.url
        current=None
        for frame in list(self.page.frames):
            try:
                if frame!=self.page.main_frame and not frame.frame_element().is_visible():
                    continue
                field=frame.evaluate(FOCUSED_FIELD)
                if field and field['type']!='password':
                    current=self.remember_field(frame,field)
            except Exception:
                continue  # A changing iframe must not stall manual input.
        self.focused_field=current

    def focus_changed(self,source,field):
        if self.state!='manual' or source['page']!=self.page:
            return
        if field is None:
            if self.focus_frame==source['frame']:
                self.focused_field=None
            return
        if not isinstance(field,dict) or not isinstance(field.get('token'),str) or len(field['token'])>120 or not isinstance(field.get('type'),str) or field['type']=='password':
            return
        self.focus_frame=source['frame']
        self.focused_field=self.remember_field(source['frame'],field)

    def watch_page(self,page):
        def navigated(frame):
            if page==getattr(self,'page',None) and frame==page.main_frame:
                self.page_url=page.url
                self.focused_field=None
                self.focus_frame=None
                self.question_fields=[]
        page.on('framenavigated',navigated)

    def read_fields(self):
        self.select_page()
        fields=[]
        for frame in list(self.page.frames):
            if frame.is_detached():
                continue
            if frame!=self.page.main_frame and not frame.frame_element().is_visible():
                continue
            for field in frame.evaluate(QUESTION_FIELDS):
                fields.append(self.remember_field(frame,field))
        self.question_fields=fields[:100]
        self.read_focus()

    def resolve_field(self,token):
        self.select_page()
        if not token or token not in self.targets:
            raise ValueError('Select a field in the live browser or read the page fields first')
        frame,original=self.targets[token]
        if frame.is_detached() or frame not in self.page.frames:
            raise ValueError('The employer page changed. Read and select the current field again')
        from applypilot.apply.llm_engine import _css_escape
        loc=frame.locator('[data-applypilot-field="'+_css_escape(token)+'"]')
        if loc.count()!=1 or not loc.is_visible() or not loc.is_enabled() or not loc.is_editable():
            raise ValueError('The selected field is no longer writable. Select the current answer field')
        field=loc.evaluate('(el)=>{'+FIELD_HELPERS+'return describe(el);}')
        if field['document_token']!=original['document_token'] or field['type'] in {'password','file','checkbox','radio'}:
            raise ValueError('This field needs direct manual input; select a text answer field')
        return loc,field

    def request_draft(self,data):
        if self.answer_task and not self.answer_task.done():
            raise ValueError('An answer draft is already running; the live browser remains usable')
        token=data.get('field_token') or (self.focused_field or {}).get('token')
        _,field=self.resolve_field(token)
        target_hash=self.value_hash(field['value'])
        ident=secrets.token_urlsafe(12)
        self.answer_draft={'id':ident,'status':'pending','field_token':token,'question':field['label'],'value_hash':target_hash}
        def draft():
            from applypilot.apply.question_assistant import draft_answer
            with workspace_context(self.root):
                return draft_answer(self.job,self.profile,self.texts,self.verified,field,str(data.get('guidance') or ''))
        self.answer_task=self.answer_planner.submit(draft)
        self.answer_started=time.monotonic()
        cc.event(self.conn,self.job['url'],'answer_request',field['label'],state='pending',source='candidate',payload={'attempt_id':self.id,'draft_id':ident,'field_token':token})
        self.conn.commit()

    def finish_draft(self):
        if not self.answer_task or not self.answer_task.done():
            return
        future=self.answer_task
        self.answer_task=None
        try:
            result=future.result()
        except Exception as exc:
            result={'status':'error','answer':'','sources':[],'missing':['The answer model could not finish. Check AI preferences or write an answer, then insert it.']}
            log.warning('Answer draft job=%s exception=%s',self.job['id'],type(exc).__name__)
        latency=round((time.monotonic()-self.answer_started)*1000,2)
        with self.lock:
            self.answer_draft=self.answer_draft|result|{'latency_ms':latency}
        cc.event(self.conn,self.job['url'],'answer_draft',self.answer_draft['question'],state=result['status'],source='assistant',
                 payload={'attempt_id':self.id,'draft_id':self.answer_draft['id'],'latency_ms':latency,'answer':result.get('answer',''),'sources':result.get('sources',[]),'missing':result.get('missing',[]),
                          'provider':result.get('provider'),'model':result.get('model'),'usage':result.get('usage',{})})
        if result.get('provider'):
            usage=result.get('usage') or {}
            self.conn.execute("INSERT INTO cc_ai_usage(task,provider,model,input_tokens,output_tokens,created_at,request_status) VALUES(?,?,?,?,?,?,'completed')",
                              ('form_answer',result['provider'],result.get('model'),usage.get('input_tokens',usage.get('prompt_tokens')),usage.get('output_tokens',usage.get('completion_tokens')),cc.now()))
        self.conn.commit()

    def insert_answer(self,data):
        from applypilot.apply.safety import require_live_execution
        require_live_execution()
        loc,field=self.resolve_field(data.get('field_token'))
        if self.value_hash(field['value'])!=data.get('value_hash'):
            raise ValueError('The field contents changed after selection/drafting. Read the field again before inserting')
        if field['value'] and data.get('replace') is not True:
            raise ValueError('This field already has an answer. Confirm replacement or choose an empty field')
        text=data['text']
        if field.get('maxlength') and len(text)>field['maxlength']:
            raise ValueError(f"Answer exceeds this field’s {field['maxlength']}-character limit")
        if data.get('save_verified') is True and (len(text)>6000 or len(field['label'])>1000):
            raise ValueError('Reusable answers support questions up to 1,000 and answers up to 6,000 characters; turn off saving to insert this longer answer')
        if field['tag']=='select':
            choices=[o for o in field['options'] if text.strip().casefold() in {o['label'].casefold(),o['value'].casefold()}]
            if len(choices)!=1:
                raise ValueError('The answer must match exactly one displayed choice')
            loc.select_option(choices[0]['value'],timeout=1500)
        else:
            loc.fill(text,timeout=1500)
        self.approval=''
        cc.event(self.conn,self.job['url'],'answer_inserted',field['label'],state='inserted',source='candidate',
                 payload={'attempt_id':self.id,'question':field['label'],'answer':text,'replaced_existing':bool(field['value']),'characters':len(text)})
        if data.get('save_verified') is True:
            from applypilot.apply.question_assistant import blocked_question
            if not blocked_question(field):
                self.conn.execute('INSERT INTO cc_answers(question,answer,updated_at) VALUES(?,?,?) ON CONFLICT(question) DO UPDATE SET answer=excluded.answer,updated_at=excluded.updated_at',(field['label'][:1000],text[:6000],cc.now()))
                self.verified=[dict(r) for r in self.conn.execute('SELECT id,question,answer FROM cc_answers ORDER BY id LIMIT 100')]
        self.conn.commit()
        self.read_fields()

    def processed(self,action,data,error=''):
        result={'id':data.get('_command_id',0),'action':action,'ok':not error,'error':error,
                'latency_ms':round((time.monotonic()-data.get('_queued_at',time.monotonic()))*1000,2)}
        with self.lock:
            self.command_results=(self.command_results+[result])[-16:]
        if action=='input':
            self.last_input_ms=result['latency_ms']
            self.input_audits.append({'command_id':result['id'],'events':len(data.get('events',[])),
                                     'types':[e['type']+(':'+e['phase'] if e['type']=='mouse' else '') for e in data.get('events',[])],
                                     'characters':sum(len(e.get('text','')) for e in data.get('events',[])),
                                     'latency_ms':result['latency_ms'],'dispatch_ms':self.last_dispatch_ms,'queue_ms':self.last_queue_ms,
                                     'ok':not error,'occurred_at':cc.now()})

    def flush_input_audit(self,force=False):
        if self.audit_future and self.audit_future.done():
            try:
                self.audit_future.result()
            except Exception:
                log.warning('Browser input audit write failed job=%s',self.job['id'])
            self.audit_future=None
        if self.audit_future and not force:
            return  # One writer in flight; retain new samples until its commit finishes.
        if not self.input_audits or (not force and time.monotonic()-self.last_audit_flush<1):
            return
        samples=self.input_audits
        self.input_audits=[]
        self.last_audit_flush=time.monotonic()
        def write():
            conn=sqlite3.connect(str(self.root/'applypilot.db'),timeout=30)
            try:
                cc.event(conn,self.job['url'],'browser_input','Manual input burst processed',state='processed' if all(s['ok'] for s in samples) else 'error',source='candidate',
                         occurred_at=samples[-1]['occurred_at'],payload={'attempt_id':self.id,'batches':samples,'batch_count':len(samples),
                             'events':sum(s['events'] for s in samples),'characters':sum(s['characters'] for s in samples),
                             'latency_ms':max(s['latency_ms'] for s in samples)})
                conn.commit()
            finally:
                conn.close()
        self.audit_future=self.audit_writer.submit(write)

    def inspect(self, stable=False):
        self.operation='inspect'
        self.last_inspection=time.monotonic()
        self.select_page()
        self.page_url = self.page.url
        snapshots=[]
        for frame in list(self.page.frames):
            try:
                if frame.is_detached():
                    continue
                if frame!=self.page.main_frame and not frame.frame_element().is_visible():
                    continue
                snapshots.append((frame,frame.evaluate(INSPECT)))
            except Exception:
                if frame!=self.page.main_frame and frame.is_detached():
                    continue
                raise  # Do not review/approve an incomplete changing form.
        self.cookie_controls=[{'choice':choice,'label':name} for frame,info in snapshots
            if '/ico-compliance/' in urlsplit(frame.url).path and urlsplit(frame.url).hostname==urlsplit(self.page.url).hostname
            for choice,name in (('save_settings','Save my settings'),('accept_all','Accept all cookies'))
            if any(c['label']==name and not c['disabled'] for c in info['controls'])]
        if self.observer.enabled:
            try:
                from applypilot.decision_observer import page_view
                self.observer.observe(page_view([info for _, info in snapshots], submitted=self.submission_attempted, personal=self.profile.get("personal", {})))
            except Exception:
                pass  # An optional observation must never change browser execution.
        return snapshots

    def signature(self, snapshots):
        return hashlib.sha256(json.dumps([(f.url, d) for f, d in snapshots], sort_keys=True).encode()).hexdigest()

    def controls(self, snapshots, labels):
        return [(frame, c) for frame, info in snapshots for c in info["controls"] if c["label"].lower() in labels and not c["disabled"]
                and not (labels is FINAL and c["tag"] == "a" and c["href"] and not c["href"].lower().startswith(("#", "javascript:")))]

    def final_review(self):
        if self.submission_attempted:
            return
        snapshots = self.inspect(stable=True)
        final = self.controls(snapshots, FINAL)
        if len(final) != 1:
            self.transition("manual", "Locate the final review page. One unambiguous Submit application control is required for agent submission.")
            return
        if any(f["required"] and not f["valid"] for _, info in snapshots for f in info["fields"]):
            self.transition("manual", "Complete the required fields, then request final review.")
            return
        self.review_control = final[0]
        self.review_signature = self.signature(snapshots)
        self.review_summary = [{"label": field["label"], "value": ("Checked" if field["checked"] else "Not checked") if field["type"] in {"checkbox", "radio"} else field["value"][:15000]}
                               for _, info in snapshots for field in info["fields"] if field["type"] not in {"password", "hidden", "file"} and field["value"]
                               and not re.search(r'password|verification code|one.time|access.token',field['label'],re.I)][:80]
        self.approval = secrets.token_urlsafe(24)
        self.approval_expires = time.monotonic() + 300
        self.transition("review", "Review the employer page and answers. Your approval authorizes one final submission attempt.")

    def click_control(self, frame, control):
        from applypilot.apply.safety import require_live_execution
        require_live_execution()
        self.operation='navigate'
        frame.locator(CONTROLS).nth(control["index"]).click(timeout=5000)
        self.page.wait_for_timeout(500)

    def open_entry(self, entry):
        from applypilot.apply.safety import require_live_execution
        require_live_execution()
        frame,control,postback=entry
        element=frame.locator(CONTROLS).nth(control['index'])
        if postback:
            from applypilot.apply.llm_engine import _css_escape
            element=frame.locator('input.AdvortoApplyNowButton[name="'+_css_escape(control['name'])+'"]')
            if element.count()!=1:
                raise ValueError('Application entry control changed')
            authorized=element.evaluate(r'''el=>{
              const fields=[...document.querySelectorAll('input,textarea,select')].filter(f=>!['hidden','submit','button','reset','image'].includes(f.type)).filter(f=>{const r=f.getBoundingClientRect(),s=getComputedStyle(f);return f.type==='file'||r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';});
              if(!location.hostname.toLowerCase().endsWith('.kallidusrecruit.com')||location.pathname.toLowerCase()!=='/vacancyinformation.aspx'||!document.querySelector('#vacancy-information')?.contains(el)||fields.length||el.disabled||!['apply','apply now'].includes(el.value.toLowerCase().trim()))return false;
              window.__applypilotEntryControl=el;return true;
            }''')
            if not authorized:
                raise ValueError('Application entry control changed')
        try:
            if postback:
                self.operation='navigate'
                element.click(timeout=5000)
                self.page.wait_for_timeout(500)
            else:
                self.click_control(frame,control)
        finally:
            if postback:
                try:
                    frame.evaluate('window.__applypilotEntryControl=null')
                except Exception:
                    pass  # Navigation creates a fresh guarded document.

    def upload(self, snapshots, manual_kind=None):
        from applypilot.apply.safety import require_live_execution
        require_live_execution()
        self.operation='upload'
        for frame, info in snapshots:
            for field in info["fields"]:
                if field["type"] != "file" or field["value"]:
                    continue
                words = " ".join(str(field.get(k) or "") for k in ("label", "name", "id")).lower()
                kinds = [k for k, markers in (("resume", ("resume", "cv")), ("cover", ("cover",)), ("statement", ("supporting", "statement"))) if any(m in words for m in markers)]
                kind = manual_kind or (kinds[0] if len(kinds) == 1 else None)
                if kind not in self.documents or not (field["id"] or field["name"]):
                    continue
                by = "id" if field["id"] else "name"
                from applypilot.apply.llm_engine import _css_escape
                loc = frame.locator(f'[{by}="{_css_escape(field[by])}"]')
                key = (frame.url, by, field[by], kind)
                if key not in self.uploaded or manual_kind:
                    loc.set_input_files(self.documents[kind], timeout=5000)
                    self.uploaded.add(key)
                    if manual_kind:
                        return

    def fill(self, snapshots):
        from applypilot.apply.safety import require_live_execution
        require_live_execution()
        self.operation='plan'
        from applypilot.apply.llm_engine import _llm_plan_fills, _css_escape
        reviewed = {"reviewed_"+kind: self.texts[kind] for kind in ("statement", "cover") if self.texts.get(kind)}
        from applypilot.application_profile import local_fills,empty_field
        for frame, info in snapshots:
            if self.interrupt.is_set():
                return
            for action in local_fills(self.profile,info['fields'],self.verified):
                if self.interrupt.is_set():
                    return
                loc=frame.locator(f'[{action["by"]}="{_css_escape(action["key"])}"]')
                if loc.count()!=1 or not loc.is_visible() or not loc.is_enabled():
                    continue
                if action['method']=='check':
                    loc.check()
                elif action['method']=='select':
                    loc.select_option(action['value'])
                elif not loc.input_value():
                    loc.fill(action['value'])
            # Refresh values so local fills are not overwritten or re-planned.
            current=frame.evaluate(INSPECT)
            fields = [f for f in current["fields"] if empty_field(f) and f["type"] not in {"password", "file", "checkbox", "radio", "hidden", "submit", "button"}]
            if not fields:
                continue
            def plan():
                from applypilot.llm import request_budget
                with workspace_context(self.root), request_budget(45, 1):
                    return _llm_plan_fills(self.job, self.profile.get("personal", {}), fields, verified_answers=self.verified, reviewed_values=reviewed)
            cache_key=hashlib.sha256(json.dumps({'fields':fields,'reviewed':reviewed,'verified':self.verified},sort_keys=True).encode()).hexdigest()
            future = self.planner.submit(plan) if cache_key not in self.plan_cache else None
            deadline = time.monotonic() + 60
            while future and not future.done():
                if self.interrupt.is_set():
                    future.cancel()
                    return
                if time.monotonic() > deadline:
                    raise TimeoutError("Form planning timed out")
                self.capture()
                self.page.wait_for_timeout(25)
            fills = future.result() if future else self.plan_cache[cache_key]
            self.plan_cache[cache_key]=fills
            for fill in fills:
                if self.interrupt.is_set():
                    return
                field = next((f for f in fields if f.get(fill["by"]) == fill["key"]), None)
                if not field:
                    continue
                loc = frame.locator(f'[{fill["by"]}="{_css_escape(fill["key"])}"]')
                if loc.count() != 1 or loc.input_value():
                    continue
                if field["tag"] == "select":
                    match = [o for o in field["options"] if fill["value"].lower() in {o["label"].lower(), o["value"].lower()}]
                    if len(match) == 1:
                        loc.select_option(match[0]["value"])
                else:
                    loc.fill(fill["value"])

    def advance(self):
        if self.interrupt.is_set():
            return
        self.steps += 1
        if self.steps > 20:
            self.transition("manual", "Navigation limit reached. Take over or stop this session.")
            return
        snapshots = self.inspect()
        if self.cookie_controls:
            self.transition('manual','Cookie preferences are blocking the application entry. Review the live panel, then save the current cookie settings or accept all cookies using the controls below. Resume the agent afterwards.')
            return
        entries=listing_entries(snapshots)
        if entries:
            if self.interrupt.is_set():
                return
            self.open_entry(entries[0])
            self.transition("running", "Opened the employer application entry; checking login or form requirements")
            return
        if any(f["type"] == "password" for _, info in snapshots for f in info["fields"]) or any(re.search(r"verify you are human|captcha|security check", info["text"], re.I) for _, info in snapshots):
            self.transition("manual", "Login or a security check needs you. Take over, then resume when ready.")
            return
        self.upload(snapshots)
        try:
            self.fill(snapshots)
        except Exception:
            self.transition("manual", "Form planning could not finish. Check your forms model connection, or complete this page and resume.")
            return
        if self.interrupt.is_set():
            return
        snapshots = self.inspect()
        if any(f["required"] and not f["valid"] for _, info in snapshots for f in info["fields"]):
            self.transition("manual", "This page needs information or consent. Complete the missing fields, then resume.")
            return
        if self.controls(snapshots, FINAL):
            # Next/Continue may coexist with an Apply header link; choose review conservatively.
            self.final_review()
            return
        forward = self.controls(snapshots, FORWARD)
        if len(forward) != 1:
            self.transition("manual", "No unambiguous next step. Navigate to the application form, then resume.")
            return
        before = self.signature(snapshots)
        if self.interrupt.is_set():
            return
        self.click_control(*forward[0])
        if self.signature(self.inspect()) == before:
            self.transition("manual", "The page did not advance. Check validation messages and continue manually.")
        else:
            self.transition("running", "Advanced to the next page; filling from reviewed evidence")

    def confirmation(self, snapshots):
        patterns = [r"(?:your |the )?application (?:has been |was |is )?(?:successfully )?(?:submitted|received)", r"thank you for (?:applying|your application)", r"we have received your application"]
        for _, info in snapshots:
            for line in info["text"].splitlines():
                if any(re.search(p, line, re.I) for p in patterns) and not re.search(r"\b(if|once|when|not|will|after)\b", line, re.I):
                    return line.strip()[:1000]
        return ""

    def confirm(self):
        snapshots = self.inspect()
        evidence = self.confirmation(snapshots)
        if not evidence:
            return False
        text = "\n".join(info["text"] for _, info in snapshots)
        match = re.search(r"(?:application\s+(?:reference|id|number)|reference(?:\s+(?:number|id))?)\s*[:#-]?\s*([A-Z0-9][A-Z0-9/_-]{2,79})", text, re.I)
        self.reference = match[1] if match else ""
        folder = self.root / "browser_sessions" / self.id
        (folder / 'confirmation.png').write_bytes(self.capture_bytes('png'))
        self.capture()
        (folder / "confirmation.json").write_text(json.dumps({"evidence": evidence, "reference": self.reference, "url": self.page.url, "confirmed_at": cc.now()}, indent=2), encoding="utf-8")
        self.conn.execute("UPDATE cc_job_meta SET hiring_status='submitted',submission_method='assisted_agent',reference=?,evidence=? WHERE job_url=?", (self.reference, evidence, self.job["url"]))
        self.transition("confirmed", "Employer confirmation detected" + (": " + self.reference if self.reference else ""))
        return True

    def submit(self, token):
        from applypilot.apply.safety import require_live_execution
        require_live_execution()
        if self.state != "review" or not self.approval or token != self.approval or self.submission_attempted:
            return
        if time.monotonic() >= self.approval_expires:
            self.transition("manual", "Approval expired. Request a fresh final review.")
            return
        current_job = cc.get_job(self.conn, self.job["id"])
        try:
            from applypilot.country_context import check_job
            check_job(current_job, self.root)
            if current_job.get("agent_id") != "browser:" + self.id or current_job.get("apply_status") != "in_progress":
                raise ValueError("Application ownership or state changed")
            if application_binding(self.conn, self.root, current_job, self.documents) != self.packet_binding:
                raise ValueError("Application documents, destination, profile or country answers changed")
        except (ValueError, OSError):
            self.transition("manual", "Application context changed. Stop this session and review the current packet before starting again.")
            return
        if self.signature(self.inspect(stable=True)) != self.review_signature:
            self.transition("manual", "The employer page changed after review. Request a fresh final review.")
            return
        if self.confirmation(self.inspect()):
            self.transition("manual", "This page already contains confirmation text. Reconcile the existing application before submitting.")
            return
        self.submission_attempted = True
        cc.event(self.conn, self.job["url"], "approval", "Candidate approved the current final page for one submission attempt", state="approved", source="candidate",
                 payload={'attempt_id':self.id,'answers':list(self.review_summary),'reviewed_documents':list(self.documents)})
        changed = self.conn.execute("UPDATE jobs SET apply_status='submitting' WHERE url=? AND agent_id=? AND apply_status='in_progress' AND applied_at IS NULL", (self.job["url"], "browser:"+self.id)).rowcount
        if changed != 1:
            self.conn.rollback()
            self.transition("manual", "Another operation changed this application. Reconcile it before submitting.")
            return
        self.transition("submitting", "Approval received; submitting once and checking employer confirmation")
        self.save_attempt_image('approved_review')
        frame, control = self.review_control
        frame.evaluate("window.__applypilotApproved=true")
        try:
            self.click_control(frame, control)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self.confirm():
                    return
                self.capture()
                self.page.wait_for_timeout(500)
        finally:
            try:
                frame.evaluate("window.__applypilotApproved=false")
            except Exception:
                pass
        self.save_attempt_image('confirmation_missing')
        self.transition("uncertain", "Submit was attempted but confirmation was not detected. Check the employer portal/email before retrying; no automatic resubmission.")

    def save_attempt_image(self, stage):
        folder=self.root/'browser_sessions'/self.id
        try:
            self.capture()
            (folder/'last_attempt.jpg').write_bytes(self.image)
            parsed=urlsplit(self.page.url)
            (folder/'attempt.json').write_text(json.dumps({'stage':stage,'at':cc.now(),'page_origin':parsed.scheme+'://'+parsed.netloc,'page_path':parsed.path,'submission_attempted':self.submission_attempted}),encoding='utf-8')
        except Exception:
            log.warning('Attempt image unavailable job=%s stage=%s',self.job['id'],stage)

    def capture(self):
        self.publish_frame()
        if self.screencasting and self._capture_page==self.page:
            return
        started=time.monotonic()
        image=self.capture_bytes('jpeg')
        with self.lock:
            if image!=self.image:
                self.image=image
                self.frame_sequence+=1
                self.frame_command_id=self.last_input_command
                self.frames.notify_all()
        self.last_capture=time.monotonic()
        self.capture_ms=round((self.last_capture-started)*1000,2)

    def publish_frame(self,force=False):
        now=time.monotonic()
        with self.frames:
            if self.pending_frame is None or (not force and self.frame_times and now-self.frame_times[-1]<1/TARGET_FPS):
                return
            self.image=self.pending_frame
            self.pending_frame=None
            self.frame_sequence+=1
            self.frame_command_id=self.last_input_command
            self.frame_times=(self.frame_times+[now])[-30:]
            self.last_capture=now
            self.frames.notify_all()

    def start_frames(self):
        self.select_page()
        # Chromium pushes compositor frames. Input no longer waits on a fresh
        # synchronous screenshot or full DOM inspection after every batch.
        self.capture_bytes('jpeg') if self._capture_page!=self.page else None
        client=self._capture_session
        def frame(message):
            try:
                if not self.screencasting or self._capture_session is not client or self._capture_page!=self.page:
                    return
                image=base64.b64decode(message['data'])
                with self.frames:
                    self.pending_frame=image
                self.publish_frame()
                client.send('Page.screencastFrameAck',{'sessionId':message['sessionId']})
            except Exception:
                self.screencasting=False
        client.on('Page.screencastFrame',frame)
        self._frame_handler=frame
        try:
            self.screencasting=True
            client.send('Page.startScreencast',{'format':'jpeg','quality':65,'maxWidth':WIDTH,'maxHeight':HEIGHT,'everyNthFrame':1})
        except Exception:
            self.screencasting=False

    def stop_frames(self):
        self.screencasting=False
        if self._capture_session and self._frame_handler:
            self._capture_session.remove_listener('Page.screencastFrame',self._frame_handler)
            self._frame_handler=None
        try:
            if self._capture_session:
                self._capture_session.send('Page.stopScreencast')
        except Exception:
            pass

    def capture_bytes(self, fmt):
        self.operation='capture'
        self.select_page()
        # CDP captures the current viewport without waiting on third-party web
        # fonts/animations, which can leave Playwright screenshots timing out.
        if self._capture_page!=self.page:
            self.stop_frames()
            if self._capture_session:
                try:
                    self._capture_session.detach()
                except Exception:
                    pass
            self._capture_session=self.context.new_cdp_session(self.page)
            self._capture_page=self.page
        options={'format':fmt,'captureBeyondViewport':False}
        if fmt=='jpeg':
            options['quality']=65
        return base64.b64decode(self._capture_session.send('Page.captureScreenshot',options)['data'])

    def recover(self, exc):
        if self.state in TERMINAL:
            return
        category=error_category(exc,self.operation)
        diagnostic={'category':category,'operation':self.operation,'exception_type':type(exc).__name__,'at':cc.now()}
        key=(category,self.operation,type(exc).__name__)
        if key!=self._diagnostic_key:
            self._diagnostic_key=key
            self.last_error=diagnostic
            log.warning('Assisted browser job=%s operation=%s category=%s exception=%s',self.job['id'],self.operation,category,type(exc).__name__)
            folder=self.root/'browser_sessions'/self.id
            try:
                (folder/'diagnostic.json').write_text(json.dumps(diagnostic,indent=2),encoding='utf-8')
            except OSError:
                pass
        if self.submission_attempted:
            self.transition('uncertain','Browser interrupted after submit. Check employer confirmation before retrying; no automatic resubmission.')
            return
        connected=hasattr(self,'browser') and self.browser.is_connected()
        live=connected and hasattr(self,'context') and any(not p.is_closed() for p in self.context.pages)
        if not live:
            self.transition('failed','Chromium could not start. Install Playwright Chromium or rebuild the image, then restart the session.' if self.operation=='launch' else 'The application browser/tab closed. Restart a reviewed session; check any employer receipt before retrying.')
            return
        detail={
            'navigation_changed':'The employer page changed while being read. The browser is still open; check the live view and resume or take over.',
            'screenshot_timeout':'The live image could not refresh. The browser is still open; wait for the page, then take over or resume.',
            'navigation_timeout':'The employer page/action took too long. The browser is still open; check its current state before resuming.',
        }.get(category,'The browser action could not finish. Your session is still open; take over, review this page and resume when ready.')
        if self.state!='manual' or self.detail!=detail:
            self.transition('manual',detail)
        else:
            self.approval=''  # A failed inspection always invalidates review.

    def handle(self, action, data):
        if action != "stop":
            from applypilot.apply.safety import require_live_execution
            require_live_execution()
        self.operation='input'
        if action in {'stop','takeover','resume','review'}:
            self.release_mouse()
            self.flush_input_audit(force=True)
        if action == "stop":
            self.transition("uncertain" if self.submission_attempted else "stopped", "Session stopped; check employer confirmation" if self.submission_attempted else "Stopped before submission approval")
        elif action == "takeover":
            self.transition("manual", "You have control. Click the live view and type, or paste text below. Final submission requires review and approval.")
            self.read_focus()
        elif action == "resume":
            if not self.submission_attempted:
                self.interrupt.clear()
                self._diagnostic_key=None
                self.last_error=None
                self.transition("running", "Agent resumed; existing answers are preserved")
        elif action == "review":
            self.final_review()
        elif action == "approve":
            self.submit(str(data.get("approval_token") or ""))
        elif action == "check":
            if self.submission_attempted:
                self.confirm()
        elif action=='cookie' and self.state=='manual':
            labels={'save_settings':'Save my settings','accept_all':'Accept all cookies'}
            name=labels.get(data.get('choice'))
            snapshots=self.inspect()
            matches=[frame for frame,info in snapshots if '/ico-compliance/' in urlsplit(frame.url).path
                     and urlsplit(frame.url).hostname==urlsplit(self.page.url).hostname
                     and any(c['label']==name and not c['disabled'] for c in info['controls'])]
            if len(matches)!=1:
                raise ValueError('Cookie panel changed; refresh the live view')
            matches[0].get_by_role('button',name=name,exact=True).click(timeout=5000)
            self.page.wait_for_timeout(500)
            self.inspect()
            self.transition('manual','Cookie choice sent to the employer site. Check the live view, then resume the agent.')
        elif action=='input' and self.state=='manual':
            started=time.monotonic()
            self.last_input_command=data.get('_command_id',0)
            self.last_queue_ms=round((started-data.get('_queued_at',started))*1000,2)
            for event in data['events']:
                self.apply_input(event)
            self.last_input_at=time.monotonic()
            self.last_dispatch_ms=round((self.last_input_at-started)*1000,2)
            self.last_input_ms=round((time.monotonic()-data.get('_queued_at',time.monotonic()))*1000,2)
        elif action=='read_fields' and self.state=='manual':
            self.read_fields()
        elif action=='select_field' and self.state=='manual':
            loc,field=self.resolve_field(data.get('field_token'))
            loc.scroll_into_view_if_needed(timeout=1500)
            loc.focus(timeout=1500)
            frame=self.targets[field['token']][0]
            self.focused_field=self.remember_field(frame,field)
        elif action=='draft_answer' and self.state=='manual':
            self.request_draft(data)
        elif action=='insert_answer' and self.state=='manual':
            self.insert_answer(data)
        elif self.state == "manual":
            if action == "click":
                self.page.mouse.click(data["x"], data["y"])
            elif action == "text":
                self.page.keyboard.insert_text(data["text"])
            elif action == "key":
                self.page.keyboard.press(data["key"])
            elif action == "scroll":
                self.page.mouse.wheel(0, data["delta"])
            elif action == "upload":
                self.upload(self.inspect(), str(data.get("kind") or ""))

    def apply_input(self,event):
        from applypilot.apply.safety import require_live_execution
        require_live_execution()
        self.operation='input'
        self.select_page()
        if event['type']=='click':
            self.page.mouse.click(event['x'],event['y'])
        elif event['type']=='text':
            self.page.keyboard.insert_text(event['text'])
        elif event['type']=='key':
            self.page.keyboard.press(event['key'])
        elif event['type']=='scroll':
            self.page.mouse.wheel(0,event['delta'])
        elif event['type']=='mouse':
            if event['phase']=='down':
                self.pointer_page=self.page
            target=self.pointer_page or self.page
            if self._input_page!=target:
                if self._input_session:
                    self._input_session.detach()
                self._input_session=self.context.new_cdp_session(target)
                self._input_page=target
            self._input_session.send('Input.dispatchMouseEvent',{
                'type':{'move':'mouseMoved','down':'mousePressed','up':'mouseReleased','cancel':'mouseReleased'}[event['phase']],
                'x':-1 if event['phase']=='cancel' else event['x'],'y':-1 if event['phase']=='cancel' else event['y'],
                'button':event['button'],'buttons':0 if event['phase']=='cancel' else event['buttons'],
                'clickCount':event['click_count'] if event['phase'] in {'down','up'} else 0,'modifiers':event['modifiers']})
            self.pointer_buttons=0 if event['phase']=='cancel' else event['buttons']
            if event['phase']=='cancel' or (event['phase']=='up' and not event['buttons']):
                self.pointer_page=None

    def release_mouse(self):
        if not self.pointer_buttons or not self._input_session:
            return
        try:
            for button,bit in (('left',1),('right',2),('middle',4)):
                if self.pointer_buttons & bit:
                    self._input_session.send('Input.dispatchMouseEvent',{'type':'mouseReleased','x':-1,'y':-1,'button':button,'buttons':0,'clickCount':0})
        except Exception:
            pass  # A closed tab has already released its gesture.
        self.pointer_buttons=0
        self.pointer_page=None

    def run(self):
        from applypilot.apply.safety import require_live_execution
        require_live_execution()
        browser = None
        self.planner = ThreadPoolExecutor(max_workers=1, thread_name_prefix="form-planner")
        self.answer_planner = ThreadPoolExecutor(max_workers=1,thread_name_prefix="question-draft")
        self.audit_writer = ThreadPoolExecutor(max_workers=1,thread_name_prefix="browser-audit")
        with workspace_context(self.root):
            try:
                from playwright.sync_api import sync_playwright
                self.conn = init_db(self.root / "applypilot.db")
                with sync_playwright() as playwright:
                    try:
                        browser = playwright.chromium.launch(headless=True)
                        self.browser=browser
                        self.context = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, accept_downloads=False)
                        self.context.set_default_timeout(5000)
                        self.context.on('page',self.watch_page)
                        self.context.expose_binding('__applypilotFocusChanged',self.focus_changed)
                        self.context.add_init_script(FOCUS_TRACKER)
                        self.focus_tracking=True
                        self.context.add_init_script(GUARD)
                        self.page = self.context.new_page()
                        try:
                            self.operation='open_page'
                            self.page.goto(self.job.get("application_url") or self.job["url"], wait_until="domcontentloaded", timeout=45000)
                            self.capture()
                            self.start_frames()
                            self.transition("running", "Application opened; filling from your reviewed packet")
                        except Exception as exc:
                            self.recover(exc)
                        while self.state not in TERMINAL:
                            if time.monotonic() - self.touched > 900:
                                self.transition("uncertain" if self.submission_attempted else "stopped", "Session expired after 15 minutes without workspace activity")
                                break
                            action,data=None,{}
                            try:
                                self.finish_draft()
                                try:
                                    action,data=self.commands.get_nowait()
                                except queue.Empty:
                                    if self.state=='running':
                                        self.advance()
                                else:
                                    self.handle(action,data)
                                    self.processed(action,data)
                                if self.state not in TERMINAL:
                                    now=time.monotonic()
                                    self.select_page()
                                    self.page_url=self.page.url
                                    if self.state=='manual' and not self.focus_tracking and self.commands.empty() and now-self.last_input_at>.2 and now-self.last_focus_read>=1:
                                        self.read_focus()
                                    if self._capture_page!=self.page:
                                        self.start_frames()
                                    if self.commands.empty() and (self.screencasting or (now-self.last_capture>=0.1 and now-self.last_input_at>.1)):
                                        self.capture()
                                    if self.commands.empty():
                                        self.page.wait_for_timeout(8)  # Pump browser events instead of blocking in queue.get.
                                    self.publish_frame()  # Keep the newest frame, including the last frame of a burst.
                                    self.flush_input_audit()
                            except ValueError as exc:
                                if action:
                                    self.processed(action,data,str(exc))
                                else:
                                    self.recover(exc)
                            except Exception as exc:
                                if action:
                                    self.processed(action,data,'The browser action could not finish. Check the selected field and current page.')
                                self.recover(exc)
                                if self.state not in TERMINAL:
                                    time.sleep(0.3)
                    finally:
                        if browser:
                            try:
                                self.stop_frames()
                                browser.close()
                            except Exception as exc:
                                log.warning('Assisted browser cleanup job=%s exception=%s',self.job['id'],type(exc).__name__)
            except Exception as exc:
                if hasattr(self, "conn"):
                    self.last_error={'category':error_category(exc,self.operation),'operation':self.operation,'exception_type':type(exc).__name__,'at':cc.now()}
                    log.warning('Assisted browser stopped job=%s operation=%s category=%s exception=%s',self.job['id'],self.operation,self.last_error['category'],type(exc).__name__)
                    self.transition("uncertain" if self.submission_attempted else "failed", "Browser interrupted after submit; reconcile confirmation" if self.submission_attempted else "Chromium could not start. Install Playwright Chromium or rebuild the image, then restart the session." if self.operation=='launch' else "Browser session ended unexpectedly. Restart a reviewed session; diagnostic details are available in this view.")
                else:
                    self.state, self.detail = "failed", "Workspace database unavailable"
            finally:
                self.flush_input_audit(force=True)
                self.audit_writer.shutdown(wait=True)
                self.observer.close()
                self.planner.shutdown(wait=False, cancel_futures=True)
                self.answer_planner.shutdown(wait=False,cancel_futures=True)
                close_connection(self.root / "applypilot.db")
