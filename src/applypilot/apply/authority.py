"""Owned synthetic submit authority; live workflows use the assisted browser.

Synthetic receipts remain distinct from real employer confirmations.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime,timedelta,timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit
import uuid

from applypilot import canonical,country_context as country,document_review

BOOT_ID=secrets.token_hex(32)
_fixtures=ContextVar("owned_synthetic_submit_adapter",default=None)
_EXECUTOR_KEY=object()
_lock=threading.RLock()
MODEL_TOOLS=frozenset({"inspect","propose_field_value"})


def clock():return datetime.now(timezone.utc)


def init_schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS cc_browser_sessions(id TEXT PRIMARY KEY,job_url TEXT NOT NULL,state TEXT NOT NULL,detail TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)")
    columns={r[1] for r in conn.execute("PRAGMA table_info(cc_browser_sessions)")}
    for key,declaration in {"country_code":"TEXT","review_json":"TEXT","approval_json":"TEXT","approval_revision":"INTEGER NOT NULL DEFAULT 0","authority_boot":"TEXT"}.items():
        if key not in columns:conn.execute(f"ALTER TABLE cc_browser_sessions ADD COLUMN {key} {declaration}")


class SyntheticAdapter:
    """Owned fixture actuator: virtual state only, no URL fetch/HTTP/browser API."""
    def __init__(self,target,fields=None):
        if not (urlsplit(target).hostname or "").endswith(".applypilot.invalid"):
            raise ValueError("Synthetic adapter only accepts non-routable applypilot.invalid fixture targets")
        self.target=target;self.fields=fields or {};self.revision=0;self.transactions=0;self.failure=None;self._receipts={}

    def snapshot(self):
        return {"adapter":"owned-synthetic-v1","target":self.target,"revision":self.revision,"fields":dict(self.fields),"submitted":self.transactions>0}

    def change(self,key,value):
        self.fields[key]=value;self.revision+=1

    def action(self,action,**values):
        if action=="inspect":return {"adapter":"owned-synthetic-v1","revision":self.revision,"field_keys":sorted(self.fields),"values_available_via_owned_references":True,"submitted":self.transactions>0}
        if action=="propose_field_value":return {"proposal":dict(values),"requires_human_review":True,"mutated":False}
        raise ValueError("Uncontrolled pointer/keyboard/frame/popup/JavaScript/network/tool actions are not available; explicit owned execution is required")

    def _commit(self,key,dispatch_id,expected):
        if key is not _EXECUTOR_KEY:raise ValueError("Only the owned executor can actuate a supported target")
        with _lock:
            if dispatch_id in self._receipts:return self._receipts[dispatch_id]
            if self.transactions:raise ValueError("The synthetic requisition already received an application")
            if canonical.digest(self.snapshot())!=expected:raise ValueError("Synthetic field state changed")
            if self.failure=="before_dispatch":raise RuntimeError("Synthetic interruption after durable intent")
            self.transactions+=1
            if self.failure=="crash_after_dispatch":raise SystemExit("Synthetic crash after dispatch")
            if self.failure=="lost_receipt":return None
            receipt={"verified":True,"reference":"SYNTHETIC-"+dispatch_id,"dispatch_id":dispatch_id,"target":self.target}
            self._receipts[dispatch_id]=receipt
            return receipt


@contextmanager
def synthetic_scope(adapter):
    if not isinstance(adapter,SyntheticAdapter):raise ValueError("Only the owned non-network fixture adapter is supported")
    token=_fixtures.set(adapter)
    try:yield adapter
    finally:_fixtures.reset(token)


def _adapter(target):
    adapter=_fixtures.get()
    return adapter if isinstance(adapter,SyntheticAdapter) and adapter.target==target else None


def _job(conn,app_dir,ident,code=None):
    from applypilot.command_center import get_job
    job=get_job(conn,int(ident))
    country.check_job(job,app_dir,country_code=code)
    return job


def _snapshot(conn,app_dir,job,adapter,*,allow_intent=False):
    from applypilot.apply.application_packet import status
    from applypilot.command_center import classify_failure
    packet=status(conn,app_dir,job["id"])
    if job.get("applied_at") or job.get("apply_status")=="submission_uncertain" or classify_failure(job.get("apply_error") or "")["category"]=="uncertain":
        raise ValueError("Reconcile the existing or uncertain application before a new review")
    if not allow_intent and not packet["application_ready"]:
        raise ValueError("Current factual artifacts, exact reviewed documents and required answers are needed before submit review")
    if allow_intent and job.get("apply_status")!="submitting":raise ValueError("Durable submitting intent changed")
    if adapter.snapshot()["submitted"]:raise ValueError("This supported requisition has already received an application")
    fields=adapter.snapshot()["fields"]
    if set(fields)-{"candidate_name","consent","answers"} or not isinstance(fields.get("candidate_name"),str) or not fields["candidate_name"].strip() or fields.get("consent") is not True:
        raise ValueError("Supported fixture requires complete reviewed candidate fields and explicit consent; unknown fields stay unsupported")
    from applypilot.setup_workspace import read_profile
    name=(read_profile(app_dir).get("personal") or {}).get("full_name")
    if fields["candidate_name"]!=name or not canonical.check(conn,app_dir,name or "",job,kind="answer")["passed"]:
        raise ValueError("Supported candidate identity must match the current verified professional profile")
    if job.get("country_code")=="NZ":
        from applypilot.preparation import require_job
        if not allow_intent:require_job(job,app_dir,text_required=False)
    review=document_review.current(conn,app_dir,job["id"],packet=packet)
    if not allow_intent and not review["approved"]:raise ValueError("Final-document review is not current")
    code=job.get("country_code") or "GB"
    from applypilot.country_answers import reviewed
    answers=[]
    for question in packet["required"].get("answers",[]):
        answer=reviewed(conn,code,question,job=job)
        if not answer:raise ValueError("Review the exact country/question answer before submit approval")
        if answer["manual_review_only"]:raise ValueError("Conditional/negated/foreign question semantics require manual handoff for this adapter")
        answers.append({"id":answer["id"],"question":answer["question"],"answer":answer["answer"],"jurisdiction":answer["question_jurisdiction"],"manual_review_only":answer["manual_review_only"]})
    expected={answer["question"]:answer["answer"] for answer in answers}
    if fields.get("answers",{})!=expected:raise ValueError("Complete supported field values must match the exact reviewed country answers")
    return {"version":1,"country_code":job.get("country_code"),"job_url":job["url"],"target":adapter.target,"title":job.get("title"),"company":job.get("company"),
        "packet_token":packet["packet_token"],"document_review_token":review["review_token"],"document_approval_id":review["approval_id"] if not allow_intent else None,
        "rights_dependency":country.preference_dependency(conn,code,"work_authorization"),"answers":answers,"form":adapter.snapshot(),"form_hash":canonical.digest(adapter.snapshot())}


def review(conn,app_dir,job_id,*,actor,session_binding,country_code=None):
    from applypilot.command_center import event,now
    from applypilot.source_registry import validate_url
    job=_job(conn,app_dir,job_id,country_code)
    target=job.get("application_url") or job["url"]
    validate_url(target)
    adapter=_adapter(target)
    if adapter is None:
        from applypilot.apply.safety import status
        if status()["live_execution_enabled"]:
            return {"mode":"assisted_browser","supported":True,"target":target,"job_id":job["id"],"detail":"Open Apply with agent to review your packet, fill the employer form and approve its final submission."}
        return {"mode":"manual_handoff","supported":False,"target":target,"detail":status()["reason"]}
    document_review.require(conn,app_dir,job["id"],actor=actor)
    snapshot=_snapshot(conn,app_dir,job,adapter)
    ident="review_"+uuid.uuid4().hex
    binding=hashlib.sha256(str(session_binding).encode()).hexdigest()
    snapshot.update(actor=actor,session_hash=binding,boot=BOOT_ID,review_id=ident)
    snapshot["review_hash"]=canonical.digest(snapshot)
    conn.execute("UPDATE cc_browser_sessions SET state='stopped',detail='Superseded by a new current review',approval_json=NULL,approval_revision=approval_revision+1,updated_at=? WHERE job_url=? AND state IN ('review_ready','approved') AND authority_boot IS NOT NULL",(now(),job["url"]))
    conn.execute("INSERT INTO cc_browser_sessions(id,job_url,state,detail,created_at,updated_at,country_code,review_json,approval_revision,authority_boot) VALUES(?,?,'review_ready','Exact supported fixture review awaits fresh candidate approval',?,?,?,?,1,?)",
        (ident,job["url"],now(),now(),job.get("country_code"),json.dumps(snapshot),BOOT_ID))
    event(conn,job["url"],"submit_review","Exact actor-bound supported-target review created; no submit authority granted",source="candidate",payload={"review_id":ident,"actor":actor,"review_hash":snapshot["review_hash"]})
    conn.commit()
    return {"mode":"controlled_synthetic","supported":True,"review_id":ident,"review_hash":snapshot["review_hash"],"review":snapshot,"approval_required":True}


def _record(conn,app_dir,ident,actor,session_binding):
    row=conn.execute("SELECT * FROM cc_browser_sessions WHERE id=? AND authority_boot IS NOT NULL",(str(ident),)).fetchone()
    if not row:raise ValueError("Review not found in this workspace")
    job_row=conn.execute("SELECT rowid AS id FROM jobs WHERE url=?",(row["job_url"],)).fetchone()
    if not job_row:raise ValueError("Reviewed job no longer exists")
    job=_job(conn,app_dir,job_row["id"])
    value=json.loads(row["review_json"] or "{}")
    binding=hashlib.sha256(str(session_binding).encode()).hexdigest()
    if row["authority_boot"]!=BOOT_ID or value.get("boot")!=BOOT_ID or value.get("actor")!=actor or not hmac.compare_digest(value.get("session_hash",""),binding):
        raise ValueError("Review actor/session/server instance changed; obtain a fresh review")
    adapter=_adapter(value.get("target"))
    if adapter is None:raise ValueError("Adapter is unsupported in this executor; use manual handoff")
    return dict(row),job,value,adapter


def _unchanged(conn,app_dir,job,value,adapter,*,allow_intent=False):
    fresh=_snapshot(conn,app_dir,job,adapter,allow_intent=allow_intent)
    for key in ("country_code","job_url","target","title","company","packet_token","document_review_token","rights_dependency","answers","form_hash"):
        if fresh.get(key)!=value.get(key):raise ValueError("Review context changed; no old approval can execute it")


def approve(conn,app_dir,ident,*,actor,session_binding,expected_review_hash,human_approved,seconds=300):
    from applypilot.command_center import event,now
    if human_approved is not True:raise ValueError("Fresh explicit candidate approval is required")
    if isinstance(seconds,bool) or not isinstance(seconds,int) or not 30<=seconds<=300:raise ValueError("Approval expiry must be bounded to at most five minutes")
    row,job,value,adapter=_record(conn,app_dir,ident,actor,session_binding)
    if row["state"]!="review_ready" or expected_review_hash!=value["review_hash"]:raise ValueError("Review is stale, already approved or superseded")
    _unchanged(conn,app_dir,job,value,adapter)
    token=secrets.token_urlsafe(32)
    approval={"token_hash":hashlib.sha256(token.encode()).hexdigest(),"actor":actor,"session_hash":value["session_hash"],"review_hash":value["review_hash"],"boot":BOOT_ID,"expires_at":(clock()+timedelta(seconds=seconds)).isoformat(),"consumed_at":None}
    changed=conn.execute("UPDATE cc_browser_sessions SET state='approved',approval_json=?,updated_at=?,approval_revision=approval_revision+1 WHERE id=? AND state='review_ready' AND approval_revision=?",
        (json.dumps(approval),now(),ident,row["approval_revision"])).rowcount
    if changed!=1:conn.rollback();raise ValueError("Another actor changed this review")
    event(conn,job["url"],"submit_approval","Fresh single-use submit approval recorded as hash only",source="candidate",payload={"review_id":ident,"actor":actor,"expires_at":approval["expires_at"]})
    conn.commit()
    return {"review_id":ident,"approval_token":token,"expires_at":approval["expires_at"],"single_use":True}


def execute(conn,app_dir,ident,token,*,actor,session_binding):
    from applypilot.command_center import event,now,snapshot_document
    row,job,value,adapter=_record(conn,app_dir,ident,actor,session_binding)
    if row["state"]!="approved":raise ValueError("Approval is absent, consumed, superseded or uncertain")
    approval=json.loads(row["approval_json"] or "{}")
    from applypilot.source_access import moment
    if not isinstance(token,str) or not 20<=len(token)<=200 or not hmac.compare_digest(approval.get("token_hash",""),hashlib.sha256(token.encode()).hexdigest()) or approval.get("consumed_at") or not moment(approval.get("expires_at")) or moment(approval["expires_at"])<=clock():
        raise ValueError("Submit approval is invalid, expired or already consumed")
    _unchanged(conn,app_dir,job,value,adapter)
    conn.execute("BEGIN IMMEDIATE")
    try:
        approval["consumed_at"]=now()
        changed=conn.execute("UPDATE cc_browser_sessions SET state='submitting',approval_json=?,updated_at=? WHERE id=? AND state='approved' AND approval_revision=? AND approval_json=?",
            (json.dumps(approval),now(),ident,row["approval_revision"],row["approval_json"])).rowcount
        if changed!=1:raise ValueError("Approval was concurrently consumed")
        conn.execute("UPDATE jobs SET apply_status='submitting',apply_task_id=?,last_attempted_at=?,apply_attempts=COALESCE(apply_attempts,0)+1 WHERE url=?",(ident,now(),job["url"]))
        event(conn,job["url"],"submission_intent","Single-use authority consumed before owned actuation",source="candidate",payload={"review_id":ident,"actor":actor})
        conn.commit()
    except Exception:conn.rollback();raise
    try:
        current_job=_job(conn,app_dir,job["id"])
        _unchanged(conn,app_dir,current_job,value,adapter,allow_intent=True)
        receipt=adapter._commit(_EXECUTOR_KEY,ident,value["form_hash"])
        if not receipt or receipt.get("verified") is not True or receipt.get("dispatch_id")!=ident or receipt.get("target")!=value["target"]:raise RuntimeError("No verified supported-target receipt")
        doc_id=snapshot_document(conn,Path(app_dir),job)
        conn.execute("UPDATE jobs SET applied_at=COALESCE(applied_at,?),apply_status='applied',apply_error=NULL,agent_id=NULL WHERE url=?",(now(),job["url"]))
        conn.execute("INSERT OR IGNORE INTO cc_job_meta(job_url) VALUES(?)",(job["url"],))
        conn.execute("UPDATE cc_job_meta SET hiring_status='submitted',submission_method='controlled_synthetic',reference=?,document_id=? WHERE job_url=?",(receipt["reference"],doc_id,job["url"]))
        conn.execute("UPDATE cc_browser_sessions SET state='confirmed',detail='Verified synthetic receipt; no real employer contacted',updated_at=? WHERE id=?",(now(),ident))
        event(conn,job["url"],"submission","Verified owned synthetic receipt",state="submitted",source="controlled_executor",payload={"review_id":ident,"reference":receipt["reference"]})
        conn.commit()
        return {"submitted":True,"synthetic":True,"receipt":receipt}
    except Exception:
        conn.rollback();_uncertain(conn,job["url"],ident)
        return {"submitted":False,"state":"submission_uncertain","retry_allowed":False}


def _uncertain(conn,url,ident):
    from applypilot.command_center import event,now
    conn.execute("UPDATE jobs SET apply_status='submission_uncertain',apply_error='Consumed attempt has no verified receipt; reconcile before a new review' WHERE url=? AND applied_at IS NULL",(url,))
    conn.execute("UPDATE cc_browser_sessions SET state='uncertain',detail='Consumed attempt requires explicit reconciliation; never replay',updated_at=? WHERE id=?",(now(),ident))
    event(conn,url,"submission_uncertain","Consumed owned attempt lacks a verified receipt; no automatic retry",source="controlled_executor",payload={"review_id":ident})
    conn.commit()


def recover(conn):
    from applypilot.command_center import now
    for row in conn.execute("SELECT * FROM cc_browser_sessions WHERE authority_boot IS NOT NULL AND state IN ('submitting','approved','review_ready')").fetchall():
        if row["state"]=="submitting":_uncertain(conn,row["job_url"],row["id"])
        elif row["authority_boot"]!=BOOT_ID:
            conn.execute("UPDATE cc_browser_sessions SET state='stopped',approval_json=NULL,detail='Server restart invalidated pending approval',updated_at=? WHERE id=?",(now(),row["id"]))
    conn.commit()


def cancel(conn,app_dir,ident,*,actor,session_binding):
    from applypilot.command_center import now
    row,job,value,adapter=_record(conn,app_dir,ident,actor,session_binding)
    if row["state"] not in {"review_ready","approved"}:raise ValueError("Consumed or uncertain attempts cannot be reset; reconcile them")
    conn.execute("UPDATE cc_browser_sessions SET state='stopped',approval_json=NULL,approval_revision=approval_revision+1,detail='Candidate cancelled current review',updated_at=? WHERE id=?",(now(),ident))
    conn.commit()
    return {"cancelled":True}
