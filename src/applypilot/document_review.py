"""Human final-document review, distinct from factual proof and submit approval."""
from __future__ import annotations

import json
from pathlib import Path
import uuid

from applypilot import canonical,country_context as country


def context(conn,app_dir,job_id,*,packet=None):
    from applypilot.command_center import get_job
    from applypilot.apply.application_packet import status
    job=get_job(conn,int(job_id))
    packet=packet if packet is not None else status(conn,app_dir,int(job_id),include_review=False)
    code=job.get("country_code") or "GB"
    manifest={"version":1,"country_code":job.get("country_code"),"target":{k:job.get(k) for k in ("url","title","company","application_url","employer_id")},
        "packet_token":packet["packet_token"],"requirements":packet.get("required",{}),"rights_dependency":country.preference_dependency(conn,code,"work_authorization")}
    if job.get("country_code")=="NZ":
        from applypilot.nz_evidence import assess_job
        assessment=assess_job(conn,app_dir,job)
        employer=assessment.get("employer_evidence")
        manifest["recruitment_evidence"]={"code":assessment["code"],"identity_confirmed":assessment["identity_confirmed"],
            "employer":None if not employer else {"id":employer["employer"]["id"],"revision":employer["employer"]["revision"],"result":employer["accreditation_result"],"state":employer["evidence_state"],"evidence_ids":employer["evidence_ids"]}}
    ready=not packet.get("excluded") and all(d["pdf_state"]=="ready" for d in packet["documents"] if d["required"] or d["included"])
    return {"review_token":canonical.digest(manifest),"manifest":manifest,"documents_ready":ready,"does_not_authorize_submission":True}


def current(conn,app_dir,job_id,*,packet=None):
    context_value=context(conn,app_dir,job_id,packet=packet)
    from applypilot.command_center import get_job
    job=get_job(conn,int(job_id))
    row=conn.execute("SELECT document_approval_json FROM cc_job_meta WHERE job_url=?",(job["url"],)).fetchone()
    try:approval=json.loads(row[0] or "{}") if row else {}
    except (ValueError,TypeError):approval={}
    valid=context_value["documents_ready"] and approval.get("review_token")==context_value["review_token"] and canonical._audit_valid(conn,approval.get("audit_event_id"),"final_documents_reviewed",context_value["review_token"])
    return context_value|{"approved":bool(valid),"approval_id":approval.get("id") if valid else None,"actor":approval.get("actor") if valid else None}


def approve(conn,app_dir,data,*,actor):
    from applypilot.command_center import get_job,event,now
    ident=int(data.get("id") or 0)
    job=get_job(conn,ident)
    country.check_job(job,app_dir,country_code=data.get("country_code"))
    if data.get("human_reviewed") is not True:raise ValueError("Review the actual current TXT/PDF documents explicitly")
    if job.get("applied_at") or job.get("apply_status") in {"in_progress","running","submitting","submission_uncertain"}:
        raise ValueError("Document review is unavailable for active, submitted or uncertain applications")
    conn.execute("BEGIN IMMEDIATE")
    try:
        value=context(conn,app_dir,ident)
        if not value["documents_ready"] or data.get("expected_review_token")!=value["review_token"]:
            raise ValueError("Documents, facts, target or country context changed; reopen the current review")
        approval={"id":uuid.uuid4().hex,"review_token":value["review_token"],"actor":actor,"reviewed_at":now(),"country_code":job.get("country_code")}
        event(conn,job["url"],"final_documents_reviewed","Candidate reviewed exact current factual TXT/PDF artifacts; no submit authority granted",source="candidate",payload={"proof_hash":value["review_token"],"actor":actor,"approval_id":approval["id"],"country_code":job.get("country_code")})
        approval["audit_event_id"]=conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO cc_job_meta(job_url) VALUES(?)",(job["url"],))
        conn.execute("UPDATE cc_job_meta SET document_approval_json=? WHERE job_url=?",(json.dumps(approval),job["url"]))
        conn.commit()
    except Exception:
        conn.rollback();raise
    return {"approved":True,"approval_id":approval["id"],"does_not_authorize_submission":True}


def require(conn,app_dir,job_id,*,actor=None):
    value=current(conn,app_dir,job_id)
    if not value["approved"] or (actor is not None and value["actor"]!=actor):
        raise ValueError("Current actor-bound final-document review is required")
    return value
