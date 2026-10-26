"""Durable, workspace-owned opportunity activity and complete analysis exports."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from collections import Counter
from datetime import datetime, timezone

from applypilot import command_center as cc

VERSION = "2"
# Compact analytical facts survive deletion; document contents and credentials do not.
JOB_FIELDS = (
    "url", "title", "company", "site", "location", "salary", "search_query", "strategy",
    "discovered_at", "application_url", "detail_scraped_at", "detail_error",
    "fit_score", "score_confidence", "score_reasoning", "scored_at", "score_status", "score_error",
    "resume_route", "resume_route_override", "tailored_at", "tailor_status", "tailor_attempts",
    "tailor_failure_detail", "tailor_requirement_gaps", "cover_letter_at", "cover_letter_status",
    "cover_attempts", "cover_letter_failure_detail", "supporting_statement_at", "statement_status",
    "statement_attempts", "statement_error", "pdf_status", "pdf_attempts", "pdf_error",
    "applied_at", "apply_status", "apply_error", "apply_attempts", "last_attempted_at",
    "apply_duration_ms", "verification_confidence", "sponsorship_explicit", "sponsor_licensed",
    "source_id", "source_provenance", "campaign_id", "document_requirements", "score_attempts", "score_retry_at",
    "score_provider", "score_model", "tailor_provider", "tailor_model", "tailor_input_hash", "statement_input_hash", "cover_input_hash",
    "country_code","employer_id",
)
META_FIELDS = ("hiring_status", "visibility", "priority", "notes", "due_at", "recruiter",
               "reference", "submission_method", "evidence", "document_id")
MANUAL_TYPES = {"note", "employer_contact", "follow_up", "assessment", "interview",
                "offer", "rejection", "withdrawal", "other"}


def _json_sql(prefix, fields):
    if len(fields) > 50:
        return "json_patch(" + _json_sql(prefix, fields[:50]) + "," + _json_sql(prefix, fields[50:]) + ")"
    return "json_object(" + ",".join(f"'{key}',{prefix}.{key}" for key in fields) + ")"


def init_schema(conn):
    """Triggers also cover CLI/worker writes; migration never invents past actions."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='cc_activity_schema'").fetchone():
        if conn.execute("SELECT 1 FROM cc_activity_schema WHERE version=?", (VERSION,)).fetchone():
            return
    columns = {r[1] for r in conn.execute("PRAGMA table_info(cc_events)")}
    for key, declaration in {"payload": "TEXT NOT NULL DEFAULT '{}'", "occurred_at": "TEXT"}.items():
        if key not in columns:
            conn.execute(f"ALTER TABLE cc_events ADD COLUMN {key} {declaration}")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cc_opportunity_registry(
            job_url TEXT PRIMARY KEY,original_job_id INTEGER,snapshot TEXT NOT NULL,
            registered_at TEXT NOT NULL,updated_at TEXT NOT NULL,deleted_at TEXT);
        CREATE TABLE IF NOT EXISTS cc_activity_schema(version TEXT PRIMARY KEY);
        CREATE INDEX IF NOT EXISTS cc_events_kind_time ON cc_events(kind,created_at,id);
    """)
    available = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    fields = tuple(k for k in JOB_FIELDS if k in available)
    for trigger in ("cc_activity_discovered","cc_activity_snapshot","cc_activity_deleted"):
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for row in conn.execute("SELECT rowid AS id,* FROM jobs").fetchall():
        snapshot = {k: row[k] for k in fields}
        added = conn.execute("INSERT OR IGNORE INTO cc_opportunity_registry VALUES(?,?,?,?,?,NULL)",
                             (row["url"], row["id"], json.dumps(snapshot), cc.now(), cc.now())).rowcount
        if added:
            cc.event(conn, row["url"], "baseline", "Existing opportunity observed when detailed activity tracking was enabled; earlier missing activity is unknown.",
                      source="migration", payload={"history_complete": False, "snapshot": snapshot})
        elif "country_code" in fields:
            conn.execute("UPDATE cc_opportunity_registry SET snapshot=json_set(snapshot,'$.country_code',?) WHERE job_url=?",(row["country_code"],row["url"]))
    for row in conn.execute("SELECT * FROM cc_retired_jobs").fetchall():
        conn.execute("INSERT OR IGNORE INTO cc_opportunity_registry VALUES(?,?,?,?,?,?)",
                     (row["job_url"], row["original_job_id"], row["snapshot"], cc.now(), cc.now(), row["deleted_at"]))
    snapshot_sql = _json_sql("NEW", fields)
    timestamp = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
    conn.executescript(f"""
        CREATE TRIGGER IF NOT EXISTS cc_activity_discovered AFTER INSERT ON jobs BEGIN
          INSERT INTO cc_opportunity_registry(job_url,original_job_id,snapshot,registered_at,updated_at)
          VALUES(NEW.url,NEW.rowid,{snapshot_sql},{timestamp},{timestamp})
          ON CONFLICT(job_url) DO UPDATE SET snapshot=excluded.snapshot,updated_at=excluded.updated_at,deleted_at=NULL;
          INSERT INTO cc_events(job_url,kind,source,state,detail,created_at,occurred_at,payload)
          VALUES(NEW.url,'discovery','database','discovered','Opportunity discovered',{timestamp},COALESCE(NEW.discovered_at,{timestamp}),{snapshot_sql});
        END;
        CREATE TRIGGER IF NOT EXISTS cc_activity_snapshot AFTER UPDATE ON jobs BEGIN
          UPDATE cc_opportunity_registry SET original_job_id=NEW.rowid,snapshot={snapshot_sql},updated_at={timestamp} WHERE job_url=NEW.url;
        END;
        CREATE TRIGGER IF NOT EXISTS cc_activity_deleted AFTER DELETE ON jobs BEGIN
          UPDATE cc_opportunity_registry SET deleted_at={timestamp},updated_at={timestamp} WHERE job_url=OLD.url;
          INSERT INTO cc_events(job_url,kind,source,state,detail,created_at,payload)
          VALUES(OLD.url,'record_removed','database','deleted','Opportunity row removed; analytical identity and activity retained',{timestamp},{_json_sql('OLD', fields)});
        END;
    """)
    groups = {
        "listing_details": ("title", "company", "location", "salary", "application_url", "detail_scraped_at", "detail_error"),
        "scoring": ("fit_score", "score_confidence", "score_reasoning", "scored_at", "score_status", "score_error", "score_attempts", "score_retry_at", "score_provider", "score_model"),
        "resume_routing": ("resume_route", "resume_route_override"),
        "packet_requirements": ("document_requirements", "campaign_id"),
        "cv_preparation": ("tailored_at", "tailor_status", "tailor_attempts", "tailor_failure_detail", "tailor_requirement_gaps"),
        "cover_preparation": ("cover_letter_at", "cover_letter_status", "cover_attempts", "cover_letter_failure_detail"),
        "statement_preparation": ("supporting_statement_at", "statement_status", "statement_attempts", "statement_error"),
        "pdf_preparation": ("pdf_status", "pdf_attempts", "pdf_error"),
        "application_attempt": ("apply_attempts", "last_attempted_at", "apply_duration_ms", "applied_at", "apply_error", "verification_confidence"),
    }
    for kind, names in groups.items():
        names = tuple(k for k in names if k in fields)
        if not names:
            continue
        changed = " OR ".join(f"OLD.{k} IS NOT NEW.{k}" for k in names)
        payload = f"json_object('before',{_json_sql('OLD', names)},'after',{_json_sql('NEW', names)})"
        conn.executescript(f"""CREATE TRIGGER IF NOT EXISTS cc_activity_{kind} AFTER UPDATE OF {','.join(names)} ON jobs
            WHEN {changed} BEGIN
              INSERT INTO cc_events(job_url,kind,source,state,detail,created_at,payload)
              VALUES(NEW.url,'{kind}','database','updated','{kind.replace('_', ' ').capitalize()} changed',{timestamp},{payload});
            END;""")
    for kind, names in {"record_edit": ("priority", "notes", "recruiter", "reference", "evidence", "submission_method", "document_id"),
                        "reminder": ("due_at",), "hiring_change": ("hiring_status",), "visibility_change": ("visibility",)}.items():
        changed = " OR ".join(f"OLD.{k} IS NOT NEW.{k}" for k in names)
        conn.executescript(f"""CREATE TRIGGER IF NOT EXISTS cc_activity_{kind} AFTER UPDATE OF {','.join(names)} ON cc_job_meta
            WHEN {changed} BEGIN
              INSERT INTO cc_events(job_url,kind,source,state,detail,created_at,payload)
              VALUES(NEW.job_url,'{kind}','database','updated','{kind.replace('_', ' ').capitalize()} changed',{timestamp},
                json_object('before',{_json_sql('OLD', names)},'after',{_json_sql('NEW', names)}));
            END;""")
    for verb in ("INSERT", "UPDATE"):
        conn.executescript(f"""CREATE TRIGGER IF NOT EXISTS cc_activity_schedule_{verb.lower()} AFTER {verb} ON cc_schedules BEGIN
            INSERT INTO cc_events(job_url,kind,source,state,detail,created_at,payload)
            VALUES(NEW.job_url,'schedule','database',NEW.state,'Application schedule {verb.lower()}d',{timestamp},
              {_json_sql('NEW', ('id', 'run_at', 'mode', 'state', 'run_id', 'detail'))});
            END;""")
    # Contents stay out of the ledger, but enrichment and approved document
    # versions still produce analytical events even when status is unchanged.
    conn.executescript(f"""
        CREATE TRIGGER IF NOT EXISTS cc_activity_description AFTER UPDATE OF full_description ON jobs
        WHEN OLD.full_description IS NOT NEW.full_description BEGIN
          INSERT INTO cc_events(job_url,kind,source,state,detail,created_at,payload)
          VALUES(NEW.url,'enrichment','database','updated','Job description updated',{timestamp},
            json_object('previous_characters',length(OLD.full_description),'characters',length(NEW.full_description)));
        END;
        CREATE TRIGGER IF NOT EXISTS cc_activity_document AFTER INSERT ON cc_documents BEGIN
          INSERT INTO cc_events(job_url,kind,source,state,detail,created_at,payload)
          VALUES(NEW.job_url,'document_version','database','saved','Document version saved',{timestamp},
            {_json_sql('NEW', ('id','variant','fingerprint','created_at'))});
        END;
    """)
    conn.execute("INSERT OR IGNORE INTO cc_activity_schema VALUES(?)", (VERSION,))
    init_browser_history(conn)
    conn.commit()


def init_browser_history(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS cc_browser_attempt_history(
        id TEXT PRIMARY KEY,job_url TEXT NOT NULL,state TEXT NOT NULL,detail TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='cc_browser_sessions'").fetchone():
        conn.execute("INSERT OR IGNORE INTO cc_browser_attempt_history(id,job_url,state,detail,created_at,updated_at) SELECT id,job_url,state,detail,created_at,updated_at FROM cc_browser_sessions")
        for operation in ("INSERT", "UPDATE"):
            conn.executescript(f"""CREATE TRIGGER IF NOT EXISTS cc_browser_history_{operation.lower()} AFTER {operation} ON cc_browser_sessions BEGIN
                INSERT INTO cc_browser_attempt_history VALUES(NEW.id,NEW.job_url,NEW.state,NEW.detail,NEW.created_at,NEW.updated_at)
                ON CONFLICT(id) DO UPDATE SET state=excluded.state,detail=excluded.detail,updated_at=excluded.updated_at;
            END;""")


def opportunities(conn):
    live = {r["url"]: dict(r) for r in conn.execute("SELECT rowid AS id,* FROM jobs")}
    retired = {r["job_url"]: dict(r) for r in conn.execute("SELECT * FROM cc_retired_jobs")}
    meta = {r["job_url"]: dict(r) for r in conn.execute("SELECT * FROM cc_job_meta")}
    closures = {r["job_url"]: dict(r) for r in conn.execute("SELECT * FROM cc_job_closures")}
    result = []
    for row in conn.execute("SELECT * FROM cc_opportunity_registry ORDER BY original_job_id"):
        url = row["job_url"]
        facts = json.loads(row["snapshot"])
        job = live.get(url, facts)
        removed = retired.get(url, {})
        old_meta = json.loads(removed["snapshot"]) if removed else facts
        current_meta = meta.get(url, old_meta)
        item = {"job_id": job.get("id", row["original_job_id"]), "job_url": url,
                "record_state": "deleted" if url not in live else current_meta.get("visibility") or "active",
                "tracking_started_at": row["registered_at"], "deleted_at": removed.get("deleted_at") or row["deleted_at"],
                "closure_reason": removed.get("reason") or closures.get(url, {}).get("reason"),
                "closure_detail": removed.get("detail") or closures.get(url, {}).get("detail"),
                "history_id": removed.get("id")}
        item.update({k: job.get(k) for k in JOB_FIELDS if k != "url"})
        from applypilot.country_context import in_operation_queue
        if not in_operation_queue(item):continue
        item.update({k: (old_meta.get(k) if url not in live and k in {"notes", "reference", "evidence"} else current_meta.get(k)) for k in META_FIELDS})
        item["status"] = job.get("apply_status") or ("applied" if job.get("applied_at") else "ready")
        if url in live and cc.blocked(conn,url,job.get('company')):
            item['record_state']='blocked'
        result.append(item)
    return result


def _filters(query):
    clauses, args = [], []
    from applypilot.country_context import current
    scope="COALESCE(json_extract(r.snapshot,'$.country_code'),CASE WHEN json_valid(e.payload) THEN json_extract(e.payload,'$.operation_country_code') END,CASE WHEN e.kind LIKE 'nz_%' THEN 'NZ' END)"
    if current() and current().country_code=="NZ":clauses.append(scope+"='NZ'")
    else:clauses.append("("+scope+" IS NULL OR "+scope+"='GB')")
    if '_record_urls' in query:
        clauses.append('e.job_url IN (SELECT value FROM json_each(?))')
        args.append(json.dumps(query['_record_urls']))
    for key in ("job_url", "kind", "source"):
        value = str(query.get(key) or "").strip()
        if value:
            clauses.append(f"e.{key}=?")
            args.append(value)
    for key, operator in (("since", ">="), ("until", "<=")):
        if query.get(key):
            date = datetime.fromisoformat(str(query[key]).replace("Z", "+00:00"))
            if not date.tzinfo:
                raise ValueError("Activity dates must include a time zone")
            clauses.append(f"julianday(COALESCE(e.occurred_at,e.created_at)) {operator} julianday(?)")
            args.append(date.astimezone(timezone.utc).isoformat())
    if query.get("search"):
        clauses.append("(r.snapshot LIKE ? OR e.detail LIKE ?)")
        args += ["%" + str(query["search"])[:200] + "%"] * 2
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), args


def activity(conn, query=None, *, complete=False):
    query = query or {}
    where, args = _filters(query)
    base = " FROM cc_events e LEFT JOIN cc_opportunity_registry r ON r.job_url=e.job_url"
    total = conn.execute("SELECT COUNT(*)" + base + where, args).fetchone()[0]
    offset = max(0, int(query.get("offset") or 0))
    limit = max(1, min(100, int(query.get("limit") or 30)))
    rows = conn.execute("SELECT e.*,r.original_job_id AS job_id,json_extract(r.snapshot,'$.title') AS title,json_extract(r.snapshot,'$.company') AS company" + base + where + " ORDER BY e.id DESC" + ("" if complete else " LIMIT ? OFFSET ?"), args if complete else args + [limit, offset])
    events = []
    for row in rows:
        event = dict(row)
        event["payload"] = json.loads(event.get("payload") or "{}")
        event["occurred_at"] = event["occurred_at"] or event["created_at"]
        events.append(event)
    return {"events": events, "total": total, "offset": offset, "limit": limit}


def summary(conn, query=None, app_dir=None):
    from applypilot.insights_visuals import charts, record_scope, usage_charts
    query=query or {}
    records = opportunities(conn)
    filter_options={'states':sorted({r['record_state'] for r in records}),'sources':sorted({r.get('site') or 'Unknown' for r in records})}
    event_urls=set()
    if query.get('search'):
        event_urls={r[0] for r in conn.execute('SELECT DISTINCT job_url FROM cc_events WHERE detail LIKE ?',('%'+str(query['search'])[:200]+'%',))}
    records=record_scope(records,query,event_urls)
    event_query=dict(query)
    if any(query.get(k) for k in ('record_state','site','milestone','score_band','search','hiring_status','closure_reason')):
        event_query['_record_urls']=[r['job_url'] for r in records]
    counts = Counter(r["record_state"] for r in records)
    kinds = [dict(r) for r in conn.execute("SELECT kind,COUNT(*) AS count FROM cc_events GROUP BY kind ORDER BY count DESC,kind")]
    stages = []
    for label, time_key, status_key in (("Enriched", "detail_scraped_at", None), ("Scored", "scored_at", "score_status"),
                                      ("CV prepared", "tailored_at", "tailor_status"), ("Cover prepared", "cover_letter_at", "cover_letter_status"),
                                      ("Statement prepared", "supporting_statement_at", "statement_status"), ("Submitted", "applied_at", "apply_status")):
        stages.append({"label": label, "completed": sum(bool(r.get(time_key)) for r in records),
                        "failed": sum(r.get(status_key) in {"failed", "rejected", "retryable"} and not (status_key=='apply_status' and r.get('applied_at')) for r in records) if status_key else sum(bool(r.get("detail_error")) for r in records)})
    outcomes = dict(Counter(r.get("hiring_status") or "not_recorded" for r in records))
    reasons = dict(Counter(r["closure_reason"] for r in records if r["closure_reason"]))
    complete=activity(conn,event_query,complete=True)['events']
    visualizations=charts(records,complete,stages)
    prices={}
    if app_dir:
        from applypilot.setup_workspace import read_searches_dict
        from applypilot.ai_settings import environment
        env=environment(app_dir)
        prices=json.loads(env['APPLYPILOT_AI_PRICES']) if env.get('APPLYPILOT_AI_PRICES') else read_searches_dict(app_dir).get('ai_prices',{})
    visualizations['ai_usage']=usage_charts(conn,prices)
    return {"record_count": len(records), "record_states": dict(counts), "stages": stages, "outcomes": outcomes,
            "visualizations":visualizations,"filter_options":filter_options,
            "closure_reasons": reasons, "kinds": kinds, "activity": activity(conn, event_query),
            "opportunities": [{k: r.get(k) for k in ("job_id", "job_url", "title", "company", "record_state", "history_id")} for r in records],
            "coverage": "Detailed changes are recorded from tracking activation. Existing saved events are retained; missing earlier events are unknown, not reconstructed."}


def record_manual(conn, data):
    url = str(data.get("job_url") or "")
    if not conn.execute("SELECT 1 FROM cc_opportunity_registry WHERE job_url=?", (url,)).fetchone():
        raise LookupError("Opportunity not found in this workspace")
    category = str(data.get("category") or "note")
    detail = str(data.get("detail") or "").strip()
    if category not in MANUAL_TYPES or not detail or len(detail) > 4000:
        raise ValueError("Choose an activity type and describe what happened (up to 4,000 characters)")
    occurred = datetime.fromisoformat(str(data.get("occurred_at") or cc.now()).replace("Z", "+00:00"))
    if not occurred.tzinfo or occurred > datetime.now(timezone.utc):
        raise ValueError("Activity time must include a time zone and cannot be in the future")
    reference = str(data.get("reference") or "").strip()
    if len(reference) > 300:
        raise ValueError("Reference must be at most 300 characters")
    cc.event(conn, url, "candidate_activity", detail, state=category, source="candidate",
             occurred_at=occurred.astimezone(timezone.utc).isoformat(), payload={"category": category, "reference": reference, "affects_status": False})
    conn.commit()
    return {"recorded": True, "status_changed": False}


def _table_rows(conn, name):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone():
        return []
    return [dict(r) for r in conn.execute(f"SELECT * FROM {name}")]


def datasets(conn, query=None):
    from applypilot.insights_visuals import record_scope
    records = opportunities(conn)
    query = query or {}
    if query.get("job_url"):
        records = [r for r in records if r["job_url"] == query["job_url"]]
    event_urls={r[0] for r in conn.execute('SELECT DISTINCT job_url FROM cc_events WHERE detail LIKE ?',('%'+str(query['search'])[:200]+'%',))} if query.get('search') else set()
    records=record_scope(records,query,event_urls)
    urls = {r["job_url"] for r in records}
    def owned(name):
        return [r for r in _table_rows(conn, name) if r.get("job_url") in urls]
    event_query=dict(query)
    if any(query.get(k) for k in ('record_state','site','milestone','score_band','hiring_status','closure_reason','search')):event_query['_record_urls']=list(urls)
    return {"opportunities": records, "events": activity(conn, event_query, complete=True)["events"],
            "browser_attempts": list({r["id"]: r for r in owned("cc_browser_attempt_history") + owned("cc_browser_sessions")}.values()), "reconciliations": owned("cc_submission_reconciliations"),
            "schedules": owned("cc_schedules")}


def _safe(value):
    if isinstance(value, (dict, list)):
        return {k: _safe(v) for k, v in value.items()} if isinstance(value, dict) else [_safe(v) for v in value]
    return cc.redact(value) if isinstance(value, str) else value


def csv_bytes(rows, fields=None):
    fields = fields or list(dict.fromkeys(k for row in rows for k in row)) or ["job_url"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        values = {}
        for key in fields:
            value = _safe(row.get(key))
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            values[key] = value
        writer.writerow(values)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def export(conn, query=None):
    query = query or {}
    tables = _safe(datasets(conn, query))
    manifest = {"schema_version": VERSION, "exported_at": cc.now(), "identity_key": "job_url",
                "timestamps": "ISO 8601 with time zone; occurred_at is event time, created_at is recording time",
                "duration_unit": "milliseconds", "missing_values": "Unknown / not recorded; not zero",
                "coverage": "Saved historical events plus detailed tracking from activation; baseline events are observations, not inferred past actions",
                "filters": _safe(query), "counts": {k: len(v) for k, v in tables.items()}}
    fmt = query.get("format", "zip")
    if fmt == "json":
        return json.dumps({"manifest": manifest, **tables}, ensure_ascii=False, indent=2).encode(), "application/json", "opportunity-analysis.json"
    if fmt == "csv":
        dataset = query.get("dataset", "events")
        if dataset not in tables:
            raise ValueError("Choose opportunities, events, browser_attempts, reconciliations or schedules")
        return csv_bytes(tables[dataset]), "text/csv; charset=utf-8", f"opportunity-{dataset}.csv"
    if fmt != "zip":
        raise ValueError("Choose zip, json or csv export format")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        for name, rows in tables.items():
            archive.writestr(name + ".csv", csv_bytes(rows))
    return output.getvalue(), "application/zip", "opportunity-analysis.zip"
