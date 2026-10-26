"""Additive pipeline observations and recovery metadata."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from contextvars import ContextVar
from contextlib import contextmanager

_usage_scope = ContextVar("pipeline_usage_scope", default=None)


@contextmanager
def usage_context(app_dir, run_id):
    token = _usage_scope.set((app_dir, run_id))
    try:
        yield
    finally:
        _usage_scope.reset(token)


def now():
    return datetime.now(timezone.utc).isoformat()


def init_schema(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS cc_pipeline_runs(run_id TEXT PRIMARY KEY,started_at TEXT,ended_at TEXT,status TEXT,config TEXT,result TEXT);
    CREATE TABLE IF NOT EXISTS cc_pipeline_stages(run_id TEXT,stage TEXT,state TEXT,result TEXT,updated_at TEXT,PRIMARY KEY(run_id,stage));
    CREATE TABLE IF NOT EXISTS cc_source_health(source_id TEXT PRIMARY KEY,name TEXT,last_run TEXT,state TEXT,targets INTEGER DEFAULT 0,found INTEGER DEFAULT 0,new_jobs INTEGER DEFAULT 0,duplicates INTEGER DEFAULT 0,rejected INTEGER DEFAULT 0,errors INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS cc_variant_evaluations(id INTEGER PRIMARY KEY,job_url TEXT,variant TEXT,input_hash TEXT,score INTEGER,reasoning TEXT,provider TEXT,model TEXT,created_at TEXT);
    CREATE TABLE IF NOT EXISTS cc_pipeline_usage(id INTEGER PRIMARY KEY,run_id TEXT,task TEXT,provider TEXT,model TEXT,input_tokens INTEGER,output_tokens INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS cc_pipeline_items(stage TEXT,job_url TEXT,state TEXT,input_hash TEXT,updated_at TEXT,PRIMARY KEY(stage,job_url));
    CREATE TABLE IF NOT EXISTS cc_campaign_jobs(campaign_id TEXT,job_url TEXT,PRIMARY KEY(campaign_id,job_url));
    """)
    columns={r[1] for r in conn.execute("PRAGMA table_info(cc_pipeline_usage)")}
    for key,declaration in {"operation_id":"INTEGER","request_key":"TEXT","country_code":"TEXT","request_status":"TEXT DEFAULT 'unknown'"}.items():
        if key not in columns:conn.execute(f"ALTER TABLE cc_pipeline_usage ADD COLUMN {key} {declaration}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS cc_pipeline_request_key ON cc_pipeline_usage(request_key) WHERE request_key IS NOT NULL")


def stage(conn, run_id, name, state, result=None):
    conn.execute("INSERT INTO cc_pipeline_stages VALUES(?,?,?,?,?) ON CONFLICT(run_id,stage) DO UPDATE SET state=excluded.state,result=excluded.result,updated_at=excluded.updated_at",
                 (run_id, name, state, json.dumps(result or {}, default=str), now()))
    conn.commit()


def source_health(conn, name, result, new=0, duplicates=0):
    from applypilot.source_registry import source_id
    found = int(result.get("found", len(result.get("jobs", []))))
    failed = int(result.get("errors", result.get("status") not in {"PASS", "PARTIAL", "ok"}))
    targets = int(result.get("targets", result.get("queries", 1)))
    conn.execute("INSERT INTO cc_source_health(source_id,name,last_run,state,targets,found,new_jobs,duplicates,rejected,errors) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET last_run=excluded.last_run,state=excluded.state,targets=targets+excluded.targets,found=found+excluded.found,new_jobs=new_jobs+excluded.new_jobs,duplicates=duplicates+excluded.duplicates,rejected=rejected+excluded.rejected,errors=errors+excluded.errors",
                 (source_id(name), name, now(), "partial" if failed else result.get("status", "ok"), targets, found, new, duplicates, max(0, found-new-duplicates), failed))
    conn.commit()


def record_discovery(conn, target, result, new=0, duplicates=0):
    import os
    from applypilot.source_registry import source_id
    from applypilot.database import normalize_url
    source_health(conn, target["name"], result, new, duplicates)
    for job in result.get("jobs", []):
        if job.get("url"):
            conn.execute("UPDATE jobs SET source_id=COALESCE(source_id,?),source_provenance=COALESCE(source_provenance,?),campaign_id=COALESCE(campaign_id,?) WHERE url=?",
                         (source_id(target["name"]), json.dumps({"query": target.get("query"), "strategy": result.get("strategy")}), os.environ.get("APPLYPILOT_CAMPAIGN_ID"), normalize_url(job["url"])))
    conn.commit()


def tag_job(conn, url, source, query=None):
    import os
    from applypilot.source_registry import source_id
    columns = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    if "source_id" not in columns:
        return  # legacy callers which have not run additive migrations
    campaign = os.environ.get("APPLYPILOT_CAMPAIGN_ID")
    conn.execute("UPDATE jobs SET source_id=COALESCE(source_id,?),source_provenance=COALESCE(source_provenance,?),campaign_id=COALESCE(campaign_id,?) WHERE url=?",
                 (source_id(source), json.dumps({"source": source, "query": query}), campaign, url))
    if campaign and conn.execute("SELECT 1 FROM jobs WHERE url=?", (url,)).fetchone():
        conn.execute("INSERT OR IGNORE INTO cc_campaign_jobs VALUES(?,?)", (campaign, url))


def target_offset(app_dir, names):
    """Read a rotating cursor without creating a database during previews/tests."""
    import sqlite3
    from applypilot.source_registry import source_id
    path = app_dir / "applypilot.db"
    if not path.is_file():
        return 0
    ids = {source_id(n) for n in names}
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            return sum(row[1] for row in conn.execute("SELECT source_id,targets FROM cc_source_health") if row[0] in ids)
    except sqlite3.Error:
        return 0


def record_usage(client):
    """Capture only pipeline task usage; interactive assistants have their own ledger."""
    import os
    from applypilot.ai_routing import current_task
    task = current_task()
    scope = _usage_scope.get()
    from applypilot.operations import current
    operation=current()
    request_key=getattr(getattr(client,"_usage_local",None),"operation_request_key",None)
    if operation is not None and request_key is None:return
    run_id = scope[1] if scope else "operation:"+str(operation["record"]["id"]) if operation else os.environ.get("APPLYPILOT_RUN_ID")
    if not run_id or (operation is None and task not in {"score", "extract", "summary", "tailor", "statement", "cover", "review"}):
        return
    from applypilot.database import get_connection
    usage = client.last_usage or {}
    conn = get_connection(scope[0]/"applypilot.db" if scope else operation["root"]/"applypilot.db" if operation else None)
    values=(client.last_model,usage.get("input_tokens",usage.get("prompt_tokens")),usage.get("output_tokens",usage.get("completion_tokens")))
    if request_key:
        conn.execute("UPDATE cc_pipeline_usage SET model=?,input_tokens=?,output_tokens=?,request_status=? WHERE request_key=?",(*values,"completed" if usage else "unknown",request_key))
        if operation is not None:
            operation.setdefault("receipts",{})[task or "default"]={"provider":client.provider,"model":client.last_model,"request_key":request_key}
    else:
        conn.execute("INSERT INTO cc_pipeline_usage(run_id,task,provider,model,input_tokens,output_tokens,created_at) VALUES(?,?,?,?,?,?,?)",
                     (run_id, task, client.provider,*values,now()))
    conn.commit()


def request_intent(conn,client,operation,key):
    import os
    from applypilot.ai_routing import current_task
    scope=_usage_scope.get()
    run_id=scope[1] if scope else os.environ.get("APPLYPILOT_RUN_ID") or "operation:"+str(operation["record"]["id"])
    conn.execute("INSERT OR IGNORE INTO cc_pipeline_usage(run_id,task,provider,model,created_at,operation_id,request_key,country_code,request_status) VALUES(?,?,?,?,?,?,?,?,'dispatch_intent')",
        (run_id,current_task() or "default",client.provider,client.last_model,now(),operation["record"]["id"],key,operation["record"]["country_code"]))


def usage_report(conn,prices,*,country_code=None):
    """Known correlation keys deduplicate ledgers; missing usage is never zero."""
    import math
    from collections import defaultdict
    pipeline=[dict(r) for r in conn.execute("SELECT * FROM cc_pipeline_usage")]
    keys={r.get("request_key") for r in pipeline if r.get("request_key")}
    rows=pipeline+[dict(r) for r in conn.execute("SELECT * FROM cc_ai_usage") if not r["request_key"] or r["request_key"] not in keys]
    def grouped(items):
        groups=defaultdict(list)
        for row in items:groups[(row.get("provider"),row.get("model"))].append(row)
        output=[]
        for (provider,model),values in groups.items():
            known_input=sum(r["input_tokens"] for r in values if r.get("input_tokens") is not None)
            known_output=sum(r["output_tokens"] for r in values if r.get("output_tokens") is not None)
            unknown_input=sum(r.get("input_tokens") is None for r in values)
            unknown_output=sum(r.get("output_tokens") is None for r in values)
            rate=prices.get(f"{provider}/{model}") if isinstance(prices,dict) and provider and model else None
            price_known=isinstance(rate,dict) and all(isinstance(rate.get(k),(int,float)) and not isinstance(rate[k],bool) and math.isfinite(rate[k]) and rate[k]>=0 for k in ("input_per_million","output_per_million"))
            complete=not unknown_input and not unknown_output
            estimate=(known_input*rate["input_per_million"]+known_output*rate["output_per_million"])/1e6 if price_known and complete else None
            output.append({"provider":provider,"model":model,"requests":len(values),"input_tokens":known_input if not unknown_input else None,"output_tokens":known_output if not unknown_output else None,
                "recorded_input_tokens":known_input,"recorded_output_tokens":known_output,"unknown_input_requests":unknown_input,"unknown_output_requests":unknown_output,
                "uncorrelated_requests":sum(not r.get("request_key") for r in values),"country_codes":sorted({r.get("country_code") or "legacy_unknown" for r in values}),
                "price_known":price_known,"estimated_usd":estimate,"cost_status":"estimated_from_reported_usage" if estimate is not None else "usage_unknown" if not complete else "price_unknown"})
        return output
    selected=[r for r in rows if (r.get("country_code")=="NZ" if country_code=="NZ" else r.get("country_code") in {None,"GB"})]
    return {"usage":grouped(selected),"workspace_usage":grouped(rows),"correlation_note":"Known request keys deduplicate interactive/pipeline records. Unknown delivery, usage, country or prices remain unknown; estimates are not a billing guarantee."}


def persist_document(conn, stage_name, result, *,manual_owned_text=False):
    """Commit each item's result without clearing previously approved paths."""
    from applypilot.country_context import check_job, workspace_for_connection
    job = conn.execute("SELECT * FROM jobs WHERE url=?", (result["url"],)).fetchone()
    if job:
        check_job(dict(job), workspace_for_connection(conn), preparation=False)
        if job["country_code"]=="NZ":
            from applypilot.preparation import require_job
            require_job(dict(job),workspace_for_connection(conn),text_required=not manual_owned_text)
        if job["applied_at"] or job["apply_status"] in {"in_progress","running","submitting","submission_uncertain"}:
            raise ValueError("Cannot publish documents for an active, submitted or uncertain application; retain a draft")
    else:
        raise ValueError("Document publication requires a saved workspace job")
    fields = {"tailor": ("tailored_resume_path", "tailored_at", "tailor_status", "tailor_failure_detail", "tailor_report_path"),
              "cover": ("cover_letter_path", "cover_letter_at", "cover_letter_status", "cover_letter_failure_detail", "cover_letter_report_path"),
              "statement": ("supporting_statement_path", "supporting_statement_at", "statement_status", "statement_error", None)}
    path_field, time_field, status_field, error_field, report_field = fields[stage_name]
    approved = bool(result.get("path")) and result.get("status", "approved") == "approved"
    if approved:
        from applypilot.canonical import finalize, FactReviewRequired
        try:
            result["factual_finalisation"] = finalize(conn, workspace_for_connection(conn), result["path"], dict(job), kind="resume" if stage_name=="tailor" else stage_name)
        except FactReviewRequired as exc:
            approved = False
            result["status"] = "needs_fact_review"
            result["failure_detail"] = str(exc)
    status = result.get("status", "approved" if approved else "failed")
    sql = f"UPDATE jobs SET {path_field}=CASE WHEN ? THEN ? ELSE {path_field} END,{time_field}=CASE WHEN ? THEN ? ELSE {time_field} END,{status_field}=?,{error_field}=?,{stage_name}_attempts=COALESCE({stage_name}_attempts,0)+1"
    args = [approved, result.get("path"), approved, now(), status, None if approved else str(result.get("failure_detail") or result.get("error") or "Generation failed")[:1200]]
    if report_field:
        sql += f",{report_field}=?"
        args.append(result.get("report_path"))
    if stage_name == "tailor":
        sql += ",tailor_requirement_gaps=?,tailor_responsibility_map=?,tailor_provider=?,tailor_model=?"
        writer = result.get("writer") or {}
        args += [json.dumps(result.get("requirement_gaps") or {}), json.dumps(result.get("responsibility_map") or []), writer.get("provider"), writer.get("model")]
    elif stage_name == "cover":
        sql += ",cover_letter_diagnostics=?"
        args.append(json.dumps(result.get("diagnostics") or {}))
    if approved:
        sql += ",pdf_attempts=0,pdf_error=NULL,pdf_status=NULL"
        sql += f",{stage_name}_input_hash=" + ("score_fingerprint" if stage_name == "tailor" else "tailor_input_hash")
    conn.execute(sql + " WHERE url=?", (*args, result["url"]))
    row = conn.execute("SELECT score_fingerprint FROM jobs WHERE url=?", (result["url"],)).fetchone()
    conn.execute("INSERT INTO cc_pipeline_items VALUES(?,?,?,?,?) ON CONFLICT(stage,job_url) DO UPDATE SET state=excluded.state,input_hash=excluded.input_hash,updated_at=excluded.updated_at",
                 (stage_name, result["url"], status, row[0] if row else None, now()))
    conn.commit()
