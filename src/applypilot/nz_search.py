"""Search-first NZ presentation and owned discovery-run status; no board retrieval."""
import json
from urllib.parse import urlencode

from applypilot import country_context as country
from applypilot.setup_workspace import read_searches_dict

LOCATIONS = ("Auckland", "Wellington", "Christchurch", "Hamilton", "Tauranga", "Dunedin", "New Zealand", "Remote, New Zealand")


def search(app_dir, data):
    country.require_enabled("NZ", read_searches_dict(app_dir))
    query, location = data.get("query"), data.get("location", "Auckland")
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 160 or any(c in query for c in "\r\n"):
        raise ValueError("Enter one job title or a short set of keywords (up to 160 characters)")
    if location not in LOCATIONS:
        raise ValueError("Choose an NZ search location")
    query = query.strip()
    remote = location == "Remote, New Zealand"
    keywords = query + (" remote" if remote else "")
    where = location if "New Zealand" in location else location + ", New Zealand"
    return {"mode": "external_board_results", "query": query, "location": location,
            "detail": "Open a job board's results, then save a vacancy you want to review in ApplyPilot.",
            "links": [
                {"name": "SEEK", "url": "https://www.seek.co.nz/jobs?" + urlencode({"keywords": keywords, "where": "New Zealand" if remote else location})},
                {"name": "Indeed", "url": "https://nz.indeed.com/jobs?" + urlencode({"q": keywords, "l": where})},
                {"name": "LinkedIn", "url": "https://www.linkedin.com/jobs/search/?" + urlencode({"keywords": keywords, "location": where})}],
            "automatic_run_started": False}


def _object(value):
    try:
        parsed = json.loads(value or "{}") if isinstance(value, str) else value
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _is_discovery(config):
    return _object(config.get("country_context")).get("country_code") == "NZ" and config.get("stages") == ["discover"]


def discovery_status(conn, app_dir, runner=None, run_id=None):
    country.require_enabled("NZ", read_searches_dict(app_dir))
    runtime = runner.status() if runner is not None else {}
    runtime = runtime if isinstance(runtime, dict) else {}
    rows = [dict(r) for r in conn.execute("SELECT * FROM cc_pipeline_runs ORDER BY started_at DESC LIMIT 40")]
    records = {r["run_id"]: r for r in rows if _is_discovery(_object(r["config"]))}
    histories = runner.history(limit=40) if runner is not None and hasattr(runner, "history") else []
    for record in histories:
        if isinstance(record, dict) and _is_discovery(record):
            records.setdefault(record["run_id"], record)
    ident = run_id or (runtime.get("run_id") if runtime.get("run_id") in records else next(iter(records), None))
    if ident is not None and ident not in records:
        raise ValueError("NZ discovery run not found in this workspace")
    if ident is None:
        return {"run_id": None, "state": "idle", "active": False, "counts": None,
                "other_workflow_active": bool(runtime.get("running") or runtime.get("starting")),
                "detail": "No automatic import is running. You can search job boards immediately."}
    record = records[ident]
    current = runtime.get("run_id") == ident
    state = record.get("status")
    if state not in {"completed", "partial", "failed"}:
        if current and runtime.get("start_error"):
            state = "failed"
        elif current and (runtime.get("running") or runtime.get("starting")):
            state = "starting" if runtime.get("starting") else "running"
        elif current and runtime.get("exit_code") is not None and runtime["exit_code"] < 0:
            state = "stopped"
        elif current and runtime.get("exit_code") not in (None, 0):
            state = "failed"
        else:
            state = "interrupted"
    result = _object(record.get("result"))
    discovered = next((_object(s.get("result")) for s in result.get("stages", []) if isinstance(s, dict) and s.get("stage") == "discover"), {})
    if not discovered:
        stage = conn.execute("SELECT result FROM cc_pipeline_stages WHERE run_id=? AND stage='discover'", (ident,)).fetchone()
        discovered = _object(stage[0]) if stage else {}
    values = [v for v in _object(discovered.get("sources")).values() if isinstance(v, dict)]
    counts = {key: sum(int(v.get(key) or 0) for v in values) for key in ("new", "existing", "found", "errors")} if values else None
    messages = {
        "starting": "Starting the configured-source import…", "running": "Searching your configured sources. You can stop this import below.",
        "completed": "Import completed. Review your saved NZ jobs.", "partial": "Import finished with partial coverage. Review saved jobs and source details before retrying.",
        "failed": "Import failed. Check source details before starting a new run.", "stopped": "Import stopped. Already saved vacancies are retained.",
        "interrupted": "The import is no longer running and has no completed result. Check source details, then start a new run if needed."}
    return {"run_id": ident, "state": state, "active": state in {"starting", "running"}, "counts": counts,
            "detail": messages[state], "other_workflow_active": bool(not current and (runtime.get("running") or runtime.get("starting")))}
