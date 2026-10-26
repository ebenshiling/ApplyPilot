"""Bounded preparation requests on the existing writing-request/usage ledgers.

An expired or failed operation is held for explicit retry, never silently replayed.
No provider credentials or approval capabilities enter request keys/results.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime,timedelta,timezone
import json
import hashlib
import os
from pathlib import Path
import threading
import uuid

from applypilot import canonical,country_context as country,setup_workspace as setup

_current=ContextVar("preparation_request",default=None)
_lock=threading.RLock()


class OperationHeld(ValueError):
    pass


def current():return _current.get()


def now():return datetime.now(timezone.utc).isoformat()


def routing_revision(app_dir):
    """In-memory only: detect credential/routing edits without logging values."""
    h=hashlib.sha256()
    selected={k:v for k,v in os.environ.items() if k.startswith(("LLM_","OPENAI_","GEMINI_","ANTHROPIC_"))}
    h.update(json.dumps(selected,sort_keys=True).encode())
    for name in (".env",".ai-settings.json"):
        path=Path(app_dir)/name
        h.update(path.read_bytes() if path.is_file() else b"")
    return h.hexdigest()


def init_schema(conn):
    columns={r[1] for r in conn.execute("PRAGMA table_info(cc_writing_requests)")}
    for key,definition in {"country_code":"TEXT","operation_key":"TEXT","job_url":"TEXT","origin":"TEXT","owner_token":"TEXT","lease_until":"TEXT","result_json":"TEXT","result_id":"INTEGER","transport_attempts":"INTEGER NOT NULL DEFAULT 0","status_detail":"TEXT","source_hash":"TEXT","target_hash":"TEXT","disclosure_hash":"TEXT"}.items():
        if key not in columns:conn.execute(f"ALTER TABLE cc_writing_requests ADD COLUMN {key} {definition}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS cc_preparation_operation_key ON cc_writing_requests(operation_key) WHERE operation_key IS NOT NULL")


def reserve(conn,app_dir,kind,input_hash,code,*,origin="pipeline",job_url=None,request_key=None,fresh=False,source_hash=None,target_hash=None,disclosure_hash=None):
    from applypilot.source_access import moment
    code=country.normalize_country(code)
    operation_key=canonical.digest(["preparation-operation-v1",code,kind,input_hash])
    if fresh:
        import re
        if not isinstance(request_key,str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}",request_key):
            raise ValueError("A fresh generation needs a stable request_key; retries of that request reuse its reservation")
        operation_key=canonical.digest([operation_key,request_key])
    cfg=setup.read_searches_dict(app_dir)
    global_limit=cfg.get("preparation_daily_budget",100)
    from applypilot.ai_settings import environment
    studio_limit=environment(app_dir).get("APPLYPILOT_STUDIO_DAILY_BUDGET",cfg.get("studio_daily_budget",20))
    if isinstance(studio_limit,str) and studio_limit.isdecimal():studio_limit=int(studio_limit)
    for value in (global_limit,studio_limit):
        if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=1000:raise ValueError("Preparation request budgets must be bounded positive integers")
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing=conn.execute("SELECT * FROM cc_writing_requests WHERE operation_key=?",(operation_key,)).fetchone()
        if existing is None:
            existing=conn.execute("SELECT * FROM cc_writing_requests WHERE kind=? AND input_hash=? AND country_code IS ? AND state='reserved' ORDER BY id LIMIT 1",(kind,input_hash,code)).fetchone()
        if existing:
            result=dict(existing)
            if result["state"]=="reserved" and (not moment(result["lease_until"]) or moment(result["lease_until"])<=datetime.now(timezone.utc)):
                conn.execute("UPDATE cc_writing_requests SET state='needs_review',status_detail='Reservation expired; an explicit new request is required' WHERE id=?",(result["id"],))
                result["state"]="needs_review"
            conn.commit()
            return result|{"new":False}
        since=now()[:10]
        used=conn.execute("SELECT COUNT(*) FROM cc_writing_requests WHERE created_at>=?",(since,)).fetchone()[0]
        studio_used=conn.execute("SELECT COUNT(*) FROM cc_writing_requests WHERE created_at>=? AND (origin='studio' OR origin IS NULL)",(since,)).fetchone()[0]
        if used>=global_limit or (origin=="studio" and studio_used>=studio_limit):
            raise OperationHeld("Daily preparation generation budget reached; saved drafts and cached results remain available")
        owner=uuid.uuid4().hex
        lease=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()
        ident=conn.execute("INSERT INTO cc_writing_requests(kind,input_hash,state,created_at,country_code,operation_key,job_url,origin,owner_token,lease_until,source_hash,target_hash,disclosure_hash) VALUES(?,?,'reserved',?,?,?,?,?,?,?,?,?,?)",
            (kind,input_hash,now(),code,operation_key,job_url,origin,owner,lease,source_hash,target_hash,disclosure_hash)).lastrowid
        conn.commit()
        return dict(conn.execute("SELECT * FROM cc_writing_requests WHERE id=?",(ident,)).fetchone())|{"new":True}
    except Exception:
        conn.rollback()
        raise


@contextmanager
def scope(app_dir,record):
    if current() is not None:
        yield current()
        return
    cfg=setup.read_searches_dict(app_dir)
    limits={"max_requests_per_operation":12,"max_transport_requests_per_day":120,"max_prompt_characters":120000,"max_output_tokens_per_call":80000}|(cfg.get("ai_request_limits") or {})
    bounds={"max_requests_per_operation":(1,30),"max_transport_requests_per_day":(1,10000),"max_prompt_characters":(1000,600000),"max_output_tokens_per_call":(128,128000)}
    for key,(low,high) in bounds.items():
        if isinstance(limits.get(key),bool) or not isinstance(limits.get(key),int) or not low<=limits[key]<=high:
            raise ValueError("Invalid bounded AI request limit: "+key)
    value={"root":Path(app_dir),"record":record,"limits":limits,"calls":0,"routing_revision":record.get("_routing_revision") or routing_revision(app_dir)}
    token=_current.set(value)
    try:
        from applypilot.llm import request_budget,_request_budget
        if _request_budget.get() is not None:yield value
        else:
            with request_budget(timeout_seconds=60,max_attempts=2):yield value
    finally:_current.reset(token)


def before_provider(payload,client):
    value=current()
    serialized=json.dumps(payload,ensure_ascii=False,default=str)
    output=payload.get("max_completion_tokens",payload.get("max_tokens",(payload.get("generationConfig") or {}).get("maxOutputTokens",0)))
    baseline=value["limits"] if value else {"max_prompt_characters":120000,"max_output_tokens_per_call":80000}
    if len(serialized)>baseline["max_prompt_characters"] or not isinstance(output,int) or not 0<output<=baseline["max_output_tokens_per_call"]:
        raise OperationHeld("AI request exceeds the configured input/output bound; shorten the source or choose explicit bounded settings")
    if value is None:return
    from applypilot.database import get_connection
    record,limits=value["record"],value["limits"]
    if value["routing_revision"]!=routing_revision(value["root"]):
        raise OperationHeld("AI routing/credentials changed during this request; no new provider request was sent")
    if record["country_code"]=="NZ":
        country.require_enabled("NZ",setup.read_searches_dict(value["root"]))
        if record.get("job_url"):
            from applypilot.preparation import require_job
            from applypilot.database import get_connection
            row=get_connection(value["root"]/"applypilot.db").execute("SELECT * FROM jobs WHERE url=?",(record["job_url"],)).fetchone()
            if not row:raise OperationHeld("NZ source job is no longer available")
            require_job(dict(row),value["root"],ai=True)
            from applypilot.discovery.observations import metadata
            if record.get("source_hash")!=metadata(dict(row)).get("content_hash") or record.get("target_hash")!=canonical.digest({k:row[k] for k in ("url","title","company")}):
                raise OperationHeld("NZ source/target changed after reservation; no stale provider request was sent")
            if record.get("disclosure_hash")!=country.preference_dependency(get_connection(value["root"]/"applypilot.db"),"NZ","work_authorization"):
                raise OperationHeld("NZ disclosures changed after reservation; reload before generation")
    with _lock:
        if value["calls"]>=limits["max_requests_per_operation"]:raise OperationHeld("Preparation operation exhausted its model-request cap; no further provider calls were made")
        conn=get_connection(value["root"]/"applypilot.db")
        conn.execute("BEGIN IMMEDIATE")
        try:
            total=conn.execute("SELECT COALESCE(SUM(transport_attempts),0) FROM cc_writing_requests WHERE created_at>=?",(now()[:10],)).fetchone()[0]
            if total>=limits["max_transport_requests_per_day"]:raise OperationHeld("Workspace transport budget reached across GB and NZ")
            changed=conn.execute("UPDATE cc_writing_requests SET transport_attempts=transport_attempts+1,lease_until=? WHERE id=? AND owner_token=? AND state='reserved'",
                ((datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),record["id"],record["owner_token"])).rowcount
            if changed!=1:raise OperationHeld("Preparation reservation is no longer owned by this operation")
            key=f"{record['id']}:{value['calls']+1}"
            from applypilot.pipeline_store import request_intent
            request_intent(conn,client,value,key)
            conn.commit()
        except Exception:
            conn.rollback();raise
        value["calls"]+=1
        client._usage_local.operation_id=record["id"]
        client._usage_local.operation_request_key=f"{record['id']}:{value['calls']}"
        client._usage_local.operation_country=record["country_code"]


def complete(conn,record,result,*,document_id=None):
    envelope={"type":"tuple" if isinstance(result,tuple) else "value","value":result}
    serialized=json.dumps(envelope,allow_nan=False,default=str)
    if len(serialized)>2_000_000:serialized=json.dumps({"type":"document" if document_id else "unavailable","value":document_id})
    conn.execute("UPDATE cc_writing_requests SET state='completed',result_json=?,result_id=?,owner_token=NULL WHERE id=? AND owner_token=? AND state='reserved'",(serialized,document_id,record["id"],record["owner_token"]))
    conn.commit()


def fail(conn,record):
    conn.execute("UPDATE cc_writing_requests SET state='failed',owner_token=NULL,status_detail='Generation did not complete; explicit retry required, provider usage may be unknown' WHERE id=? AND owner_token=? AND state='reserved'",(record["id"],record["owner_token"]))
    conn.commit()


def cached(record):
    if record["state"]!="completed":raise OperationHeld("An identical operation is "+record["state"]+"; reuse its status or explicitly start a new request after review")
    envelope=json.loads(record.get("result_json") or "null")
    if not isinstance(envelope,dict) or envelope.get("type") in {"document","unavailable"}:raise OperationHeld("Cached result requires its saved document or an explicit new request")
    return tuple(envelope["value"]) if envelope["type"]=="tuple" else envelope["value"]


def invalidate(conn,kind,job_url):
    active=conn.execute("SELECT 1 FROM cc_writing_requests WHERE kind=? AND job_url=? AND state='reserved'",(kind,job_url)).fetchone()
    if active:raise OperationHeld("An operation for this job is still running; do not replay it")
    conn.execute("UPDATE cc_writing_requests SET operation_key=NULL,status_detail='Explicit retry requested; prior reservation retained' WHERE kind=? AND job_url=? AND state IN ('failed','needs_review','completed')",(kind,job_url))
    conn.commit()


def invoke(function,values,context):
    name=function.__name__
    kinds={"score_job":"score","tailor_resume":"tailor","generate_cover_letter":"cover","generate_supporting_statement":"statement"}
    if name not in kinds or current() is not None:return function(**values)
    root=Path(context.workspace)
    if not (root/"applypilot.db").is_file():return function(**values)
    from applypilot.database import get_connection
    conn=get_connection(root/"applypilot.db")
    if "operation_key" not in {r[1] for r in conn.execute("PRAGMA table_info(cc_writing_requests)")}:return function(**values)
    projected=dict(values)
    job=dict(projected.get("job") or {})
    if kinds[name]=="score" and context.country_code=="NZ":
        from applypilot.preparation import technical_job
        projected["job"]=technical_job(job)
        projected["resume_text"]=values["resume_text"]
    else:
        projected["job"]={k:v for k,v in job.items() if k in {"url","title","company","site","location","full_description","country_code","_nhs_plan","_studio_instructions","_studio_structure","_statement_avoid"}}
    if isinstance(projected.get("profile"),dict):projected["profile"]={k:v for k,v in projected["profile"].items() if k!="canonical_facts"}
    from applypilot.ai_routing import route_info
    revision=routing_revision(root)
    info=route_info(kinds[name])
    if revision!=routing_revision(root):raise OperationHeld("AI configuration changed while capturing the request; reload")
    key=canonical.digest([name,projected,info])
    source_hash=None
    if context.country_code=="NZ":
        from applypilot.discovery.observations import metadata
        source_hash=metadata(job).get("content_hash")
    record=reserve(conn,root,kinds[name],key,context.country_code,job_url=job.get("url"),source_hash=source_hash,
        target_hash=canonical.digest({k:job.get(k) for k in ("url","title","company")}) if context.country_code=="NZ" else None,
        disclosure_hash=country.preference_dependency(conn,"NZ","work_authorization",snapshot={"config":context.preferences,"revision":context.revision}) if context.country_code=="NZ" else None)
    if not record["new"]:return cached(record)
    record["_routing_revision"]=revision
    try:
        with scope(root,record):result=function(**values)
        failed=(isinstance(result,dict) and result.get("score")==0) or (isinstance(result,tuple) and len(result)==2 and isinstance(result[1],dict) and result[1].get("status")!="approved")
        if failed:
            fail(conn,record)
        else:complete(conn,record,result)
        return result
    except Exception:
        fail(conn,record);raise


def status(conn,ident,code):
    row=conn.execute("SELECT id,kind,state,country_code,result_id,status_detail,created_at,lease_until FROM cc_writing_requests WHERE id=?",(int(ident),)).fetchone()
    code=country.normalize_country(code)
    if not row or row["country_code"] not in ({"NZ"} if code=="NZ" else {None,"GB"}):
        raise ValueError("Preparation request is outside this country view")
    return dict(row)


def transport(client,messages,temperature,max_tokens,response_mime_type,thinking_budget,call):
    """Meter otherwise interactive/extraction calls without a second usage ledger."""
    if current() is not None:return call()
    from applypilot import ai_settings,config
    from applypilot.database import get_connection
    from applypilot.llm import request_budget,_request_budget
    from applypilot.ai_routing import current_task
    active=country.current()
    root=Path(active.workspace if active else ai_settings.current_workspace() or config.APP_DIR)
    if not (root/"applypilot.db").is_file():
        if _request_budget.get() is not None:return call()
        with request_budget(timeout_seconds=60,max_attempts=2):return call()
    conn=get_connection(root/"applypilot.db")
    if "operation_key" not in {r[1] for r in conn.execute("PRAGMA table_info(cc_writing_requests)")}:
        if _request_budget.get() is not None:return call()
        with request_budget(timeout_seconds=60,max_attempts=2):return call()
    key=canonical.digest([client.provider,client.model,messages,temperature,max_tokens,response_mime_type,thinking_budget])
    record=reserve(conn,root,"provider:"+(current_task() or "default"),key,active.country_code if active else None,origin="interactive",fresh=True,request_key=uuid.uuid4().hex)
    # Separate interactive invocations are intentional; concurrent identical
    # in-flight calls still share a reservation and never create a second call.
    if not record["new"]:return cached(record)
    try:
        with scope(root,record):result=call()
        complete(conn,record,result)
        return result
    except Exception:
        fail(conn,record);raise
