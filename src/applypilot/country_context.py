"""Country foundation: immutable operation snapshots, not a mutable UI preference.

NULL job country is the legacy cohort. It is never backfilled from a selected
tab/campaign. Shared operations retain their captured country and workspace.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
import json
import os
from pathlib import Path


class CountryError(ValueError):
    pass


_ALIASES = {"gb": "GB", "uk": "GB", "gbr": "GB", "united kingdom": "GB",
            "great britain": "GB", "nz": "NZ", "nzl": "NZ", "new zealand": "NZ"}
_current = ContextVar("applypilot_country_context", default=None)
SECTIONS = frozenset({"search_preferences", "work_authorization", "resume_routing",
                      "document_policy", "sponsorship_evidence", "application_operations"})


def normalize_country(value, *, optional=True):
    if value is None and optional:
        return None
    if not isinstance(value, str) or value.strip().casefold() not in _ALIASES:
        raise CountryError("Country must be GB (United Kingdom) or NZ (New Zealand)")
    return _ALIASES[value.strip().casefold()]


def normalize_campaign_id(value):
    """The UI's blank selection and the CLI's omitted option mean no campaign."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise CountryError("Campaign identifier must be text or omitted")
    return value.strip() or None


def nz_enabled(config=None):
    """Explicit workspace setting or deployment default; service deny wins."""
    denied = os.environ.get("APPLYPILOT_NZ_DISABLED", "").casefold() in {"1", "true", "yes"}
    enabled = os.environ.get("APPLYPILOT_NZ_ENABLED", "").casefold() in {"1", "true", "yes"}
    return not denied and (config or {}).get("nz_workflow_enabled", enabled) is True


def require_enabled(country, config):
    if country == "NZ" and not nz_enabled(config):
        raise CountryError("New Zealand functionality is disabled (nz_workflow_enabled defaults to false)")


def require_preparation(country):
    # Readiness depends on the actual job/documents, not a development phase.
    normalize_country(country)


def init_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS cc_country_profiles(
        country_code TEXT PRIMARY KEY CHECK(country_code IN ('GB','NZ')),
        config_json TEXT NOT NULL, revision INTEGER NOT NULL,
        updated_at TEXT NOT NULL)""")


def read_preferences(conn, country):
    code = normalize_country(country, optional=False)
    row = conn.execute("SELECT config_json,revision FROM cc_country_profiles WHERE country_code=?", (code,)).fetchone()
    try:config=json.loads(row[0]) if row else {}
    except (ValueError,TypeError) as exc:raise CountryError("Stored country preferences need validation before use") from exc
    if not isinstance(config,dict):raise CountryError("Stored country preferences must be an object")
    return {"country_code": code, "config": config, "revision": row[1] if row else 0}


def preference_dependency(conn,code,section,*,snapshot=None):
    """Section value plus its audited change epoch; unrelated edits do not stale it."""
    from applypilot.canonical import digest
    snapshot=snapshot or read_preferences(conn,code)
    epoch=None
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='cc_events'").fetchone():
        for row in conn.execute("SELECT id,payload FROM cc_events WHERE kind='country_preferences' ORDER BY id DESC"):
            try:value=json.loads(row["payload"] or "{}")
            except (ValueError,TypeError):continue
            if not isinstance(value,dict) or not isinstance(value.get("revision"),int):continue
            sections=value.get("changed_sections",value.get("sections",[]))
            if value.get("country_code")==code and value["revision"]<=snapshot["revision"] and isinstance(sections,list) and section in sections:
                epoch=row["id"];break
    return digest({"section":section,"value":snapshot["config"].get(section,{}),"change_epoch":epoch})


def _validate_preferences(patch):
    if not isinstance(patch, dict) or set(patch) - SECTIONS:
        raise CountryError("Country preferences must use the supported country-specific sections")
    if any(not isinstance(value, dict) for value in patch.values()):
        raise CountryError("Country preference sections must be objects")
    def check(value, depth=0):
        if depth > 8:
            raise CountryError("Country preferences are nested too deeply")
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str) or len(key) > 100:
                    raise CountryError("Invalid country preference key")
                private_key = key.casefold()
                if private_key in {"password", "api_key", "token", "secret", "access_token", "refresh_token", "approval_token"} or private_key.endswith(("_password", "_api_key", "_secret")):
                    raise CountryError("Country preferences cannot store credentials")
                check(child, depth + 1)
        elif isinstance(value, list):
            if len(value) > 100:
                raise CountryError("Country preference lists are limited to 100 values")
            for child in value:
                check(child, depth + 1)
        elif not (value is None or isinstance(value, (str, bool, int, float))):
            raise CountryError("Country preferences must contain JSON values")
    check(patch)
    try:
        serialized = json.dumps(patch, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise CountryError("Country preferences must contain finite JSON values") from exc
    if len(serialized) > 50000:
        raise CountryError("Country preferences exceed 50,000 characters")


def save_preferences(conn, country, patch, expected_revision, config):
    code = normalize_country(country, optional=False)
    require_enabled(code, config)
    _validate_preferences(patch)
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise CountryError("Provide the expected country preference revision")
    if conn.in_transaction:
        raise CountryError("Finish the current operation before saving country preferences")
    conn.execute("BEGIN IMMEDIATE")
    try:
        before = read_preferences(conn, code)
        if before["revision"] != expected_revision:
            raise CountryError("Country preferences changed; reload before saving")
        merged = before["config"] | patch
        _validate_preferences(merged)
        conn.execute("""INSERT INTO cc_country_profiles(country_code,config_json,revision,updated_at)
            VALUES(?,?,?,?) ON CONFLICT(country_code) DO UPDATE SET
            config_json=excluded.config_json,revision=excluded.revision,updated_at=excluded.updated_at""",
                     (code, json.dumps(merged, sort_keys=True), expected_revision + 1, datetime.now(timezone.utc).isoformat()))
        from applypilot.command_center import event
        event(conn, None, "country_preferences", "Country preference sections updated", source="manual",
              payload={"country_code": code, "sections": sorted(patch), "changed_sections":[k for k in sorted(patch) if before["config"].get(k)!=patch[k]], "revision": expected_revision + 1})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return read_preferences(conn, code)


@dataclass(frozen=True)
class CountryContext:
    country_code: str | None
    workspace: str
    campaign_id: str | None
    config_json: str = field(repr=False)
    preferences_json: str = field(default="{}", repr=False)
    revision: int = 0
    preparation_capability: bool = False

    def __post_init__(self):
        if normalize_country(self.country_code) != self.country_code:
            raise CountryError("Operation snapshots require canonical country codes")

    @property
    def config(self):
        return json.loads(self.config_json)

    @property
    def preferences(self):
        return json.loads(self.preferences_json)

    def public(self):
        return {"country_code": self.country_code, "legacy": self.country_code is None,
                "campaign_id": self.campaign_id, "preferences_revision": self.revision,
                "policy_id": {"NZ": "nz-preparation-v1" if self.preparation_capability else "nz-discovery-v1", "GB": "uk-legacy-v1", None: "legacy-compatibility-v1"}[self.country_code],
                "discovery_available": self.country_code != "NZ" or nz_enabled(self.config),
                "preparation_available": self.country_code != "NZ" or self.preparation_capability}


def resolve(app_dir: Path, *, country_code=None, job=None, config=None, campaign_id=None, conn=None):
    campaign_id = normalize_campaign_id(campaign_id)
    if config is None:
        from applypilot.setup_workspace import read_searches_dict
        config = read_searches_dict(app_dir)
    requested = normalize_country(country_code)
    if job is not None:
        stored = normalize_country(dict(job).get("country_code"))
        if requested is not None and requested != stored and not (stored is None and requested == "GB"):
            raise CountryError("Country does not match this job's persisted jurisdiction")
        code = stored  # legacy NULL is intentionally not adopted by a tab switch
    else:
        configured = normalize_country(config["country_code"], optional=False) if "country_code" in config else None
        if configured and requested and configured != requested:
            raise CountryError("Country does not match the campaign configuration")
        code = requested or configured
    require_enabled(code, config)
    pref = read_preferences(conn, code) if conn is not None and code else {"config": {}, "revision": 0}
    preparation_capability=False
    if code=="NZ" and conn is not None and "country_code" in {r[1] for r in conn.execute("PRAGMA table_info(cc_cv_sources)")}:
        from applypilot.nz_documents import state
        try:preparation_capability=bool(state(conn,Path(app_dir))["candidate_import_complete"])
        except (ValueError,OSError,LookupError):preparation_capability=False
    return CountryContext(code, str(Path(app_dir).resolve()), campaign_id, json.dumps(config, sort_keys=True),
                          json.dumps(pref["config"], sort_keys=True), pref["revision"],preparation_capability)


def current():
    return _current.get()


def export_run_snapshot(context,config):
    from applypilot.canonical import digest
    return {"version":1,"workspace":context.workspace,"country_code":context.country_code,"campaign_id":normalize_campaign_id(context.campaign_id),
            "config_hash":digest(config),"preferences":context.preferences,"revision":context.revision}


def restore_run_snapshot(context,path):
    from applypilot.canonical import digest
    root=Path(context.workspace).resolve()
    file=Path(path).resolve()
    if not file.is_relative_to(root/".country_runs") or not file.is_file() or file.stat().st_size>60000:
        raise CountryError("Country preference snapshot is unavailable in this workspace; no fallback is permitted")
    try:value=json.loads(file.read_bytes().decode("utf-8"))
    except (ValueError,OSError) as exc:raise CountryError("Country preference snapshot is unreadable") from exc
    if not isinstance(value,dict) or value.get("version")!=1 or Path(value.get("workspace") or "").resolve()!=root or normalize_country(value.get("country_code"))!=context.country_code or normalize_campaign_id(value.get("campaign_id"))!=normalize_campaign_id(context.campaign_id) or value.get("config_hash")!=digest(context.config):
        raise CountryError("Country preference snapshot does not match the captured operation")
    revision=value.get("revision")
    if isinstance(revision,bool) or not isinstance(revision,int) or revision<0:raise CountryError("Invalid country preference snapshot revision")
    _validate_preferences(value.get("preferences"))
    return CountryContext(context.country_code,context.workspace,normalize_campaign_id(context.campaign_id),context.config_json,json.dumps(value["preferences"],sort_keys=True),revision,context.preparation_capability)


def resolve_view(app_dir, country_code=None, *, conn=None):
    """The root search's country is not the jurisdiction of a separate UI view."""
    from applypilot.setup_workspace import read_searches_dict
    config = read_searches_dict(app_dir)
    if country_code is not None:
        normalize_country(country_code, optional=False)
        config = {k:v for k,v in config.items() if k != "country_code"}
    return resolve(app_dir, country_code=country_code, config=config, conn=conn)


@contextmanager
def operation(context):
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)


def check_job(job, app_dir=None, *, country_code=None, preparation=False):
    from applypilot.config import APP_DIR
    active = current()
    root=Path(app_dir or (active.workspace if active else APP_DIR))
    if active and root.resolve()!=Path(active.workspace):
        raise CountryError("Workspace is outside the operation's captured owner")
    context = resolve(root, country_code=country_code, job=job)
    if active and active.workspace == context.workspace:
        allowed = {None, "GB"} if active.country_code in {None, "GB"} else {active.country_code}
        if context.country_code not in allowed:
            raise CountryError("Job is outside the operation's captured country")
    if preparation:
        if context.country_code=="NZ":
            from applypilot.preparation import require_job
            require_job(dict(job),root)
        else:require_preparation(context.country_code)
    if active and active.workspace==context.workspace:
        return active  # operation snapshot stays immutable; the job row is not adopted
    if context.country_code=="NZ":
        if active and active.workspace==context.workspace and active.country_code=="NZ":
            return active
        from applypilot.database import get_connection
        database=root/"applypilot.db"
        if database.is_file():
            context=resolve(root,job=job,country_code=country_code,conn=get_connection(database))
    return context


def workspace_for_connection(conn):
    from applypilot.config import APP_DIR
    row = next((row for row in conn.execute("PRAGMA database_list") if row[1] == "main"), None)
    return Path(row[2]).parent if row and row[2] else APP_DIR


def preparation_task(function):
    """Pure/direct writer calls must not bypass country-policy containment."""
    import inspect
    signature = inspect.signature(function)
    @wraps(function)
    def wrapped(*args, **kwargs):
        values = signature.bind(*args, **kwargs).arguments
        job = values.get("job")
        if job is not None:
            context=check_job(job, preparation=True)
            if context.country_code=="NZ":
                from copy import deepcopy
                from applypilot.preparation import require_job,country_profile
                require_job(dict(job),Path(context.workspace),ai=True)
                values["job"]=deepcopy(dict(job))
                if isinstance(values.get("profile"),dict):
                    values["profile"]=country_profile(values["profile"],context.preferences)
                if function.__name__=="score_job":
                    from applypilot.preparation import technical_resume
                    from applypilot.nz_documents import route
                    from applypilot.database import get_connection
                    routed=route(values["job"],Path(context.workspace))
                    values["resume_text"]=technical_resume(get_connection(Path(context.workspace)/"applypilot.db"),Path(context.workspace),routed)
                    values["job"]["_technical_resume_key"]=routed.key
                    values["job"]["_technical_resume_frozen"]=True
            values["job"]=dict(values["job"],_workspace_dir=context.workspace)
            from applypilot.operations import invoke
            from applypilot.ai_settings import workspace_context
            with operation(context),workspace_context(Path(context.workspace)):
                return invoke(function,values,context)
        return function(*args, **kwargs)
    return wrapped


def pipeline_operation(function):
    import inspect
    signature = inspect.signature(function)
    @wraps(function)
    def wrapped(*args, **kwargs):
        from applypilot import config
        cfg = config.load_search_config()
        requested = kwargs.get("country_code")
        if requested is None:
            requested = os.environ.get("APPLYPILOT_COUNTRY_CODE") or None
        values = signature.bind(*args, **kwargs).arguments
        campaign = normalize_campaign_id(values.get("campaign_id") or os.environ.get("APPLYPILOT_CAMPAIGN_ID"))
        override=(os.environ.get("APPLYPILOT_SEARCHES_PATH") or "").strip()
        if campaign and cfg.get("id") != campaign and not (override and Path(override).is_file()):
            from applypilot.pipeline_workspace import campaign_config
            cfg = campaign_config(config.APP_DIR, campaign)
        context = resolve_run(config.APP_DIR, cfg, requested, campaign, stages=values.get("stages"))
        if os.environ.get("APPLYPILOT_COUNTRY_SNAPSHOT"):
            context=restore_run_snapshot(context,os.environ["APPLYPILOT_COUNTRY_SNAPSHOT"])
        with operation(context):
            ids={int(v) for name in ("APPLYPILOT_JOB_IDS","APPLYPILOT_REGENERATE_IDS") for v in os.environ.get(name,"").split(",") if v.strip().isdigit()}
            if ids:
                from applypilot.database import get_connection
                validate_request_jobs(get_connection(config.APP_DIR/"applypilot.db"),config.APP_DIR,"/api/center/pipeline/start",{"country_code":context.country_code,"job_ids":sorted(ids),"stages":values.get("stages") or ["all"]})
            from applypilot.source_access import request_scope
            with request_scope():
                return function(*args, **kwargs)
    return wrapped


def resolve_run(app_dir, config, country_code=None, campaign_id=None, *, stages=None):
    requested = country_code
    if requested is None and "country_code" not in config:
        requested = _ALIASES.get(str(config.get("country") or "").strip().casefold())
    from applypilot.database import init_db
    context = resolve(app_dir,country_code=requested,config=config,campaign_id=campaign_id)
    if context.country_code is not None:
        context=resolve(app_dir,country_code=requested,config=config,campaign_id=campaign_id,conn=init_db(Path(app_dir)/"applypilot.db"))
    if context.country_code == "NZ":
        if config.get("country_code") != "NZ" or not campaign_id:
            raise CountryError("NZ discovery requires an explicit NZ campaign and configuration")
        selected=["all"] if stages is None else stages
        allowed={"discover","enrich","score","tailor","statement","cover","pdf","all","apply"}
        if not isinstance(selected,list) or not selected or any(s not in allowed for s in selected):
            raise CountryError("Choose supported NZ workflow stages")
        if selected not in (["discover"], ["apply"]):
            from applypilot.preparation import require_ready
            require_ready(app_dir)
    with operation(context):
        if stages != ["apply"]:
            guard_discovery(config)
    return context


def in_legacy_queue(job):
    """Legacy queues never pick new-country or invalid-jurisdiction records."""
    active = current()
    return (not active or active.country_code != "NZ") and dict(job).get("country_code") in {None, "GB"}


def in_operation_queue(job):
    active=current()
    return dict(job).get("country_code")=="NZ" if active and active.country_code=="NZ" else dict(job).get("country_code") in {None,"GB"}


def validate_request_jobs(conn, app_dir, path, data):
    """Validate the entire target set before a bulk or legacy action can write."""
    requested = data.get("country_code")
    resolve_view(app_dir, country_code=requested)
    single_paths = {"/api/center/action", "/api/center/apply", "/api/center/regenerate", "/api/center/schedule",
                    "/api/center/listing/check", "/api/center/browser/start", "/api/center/browser/prepare",
                    "/api/center/pipeline/job/save", "/api/center/pipeline/evaluate", "/api/center/assistant",
                    "/api/center/messages/review", "/api/center/lifecycle/close", "/api/center/lifecycle/delete"}
    keys = ["ids", "job_ids", "regenerate_ids"]
    if path in single_paths or path.startswith("/api/jobs/"):
        keys.append("id")
    if path.startswith("/api/center/studio/"):
        keys.append("job_id")
    for key in keys:
        raw = data.get(key)
        if raw is None:
            continue
        ids = raw if isinstance(raw, list) else [raw]
        if len(ids) > 100:
            raise CountryError("Choose at most 100 job records")
        for ident in ids:
            if isinstance(ident, bool) or not isinstance(ident, (int, str)):
                raise CountryError("Job identifiers must be integers")
            try:
                number = int(ident)
            except ValueError as exc:
                raise CountryError("Job identifiers must be integers") from exc
            row = conn.execute("SELECT * FROM jobs WHERE rowid=?", (number,)).fetchone()
            if row:
                context = resolve(app_dir, country_code=requested, job=dict(row))
                if context.country_code=="NZ" and path=="/api/center/pipeline/start":
                    check_job(dict(row),app_dir,country_code=requested,preparation=(data.get("stages") not in (["discover"],["enrich"])))
                else:require_preparation(context.country_code)


def guard_discovery(config):
    """NZ geography requires explicit, enabled, captured NZ discovery context."""
    from applypilot.geography import locations, market
    code = normalize_country(config["country_code"], optional=False) if "country_code" in config else None
    if code == "NZ" or market(config.get("country")) == "new zealand" or any(p.get("provider_country") == "new zealand" for p in locations(config)):
        active = current()
        if not active or active.country_code != "NZ" or not active.campaign_id:
            raise CountryError("NZ discovery requires an explicit NZ campaign operation")
        require_enabled("NZ", active.config)


def ingestion_country(job):
    """Persist explicit new-record jurisdiction only; never rewrite duplicates."""
    active = current()
    code = normalize_country(job.get("country_code"))
    if code and active and active.country_code and code != active.country_code:
        raise CountryError("Discovered job is outside the operation's country")
    code = code or (active.country_code if active else None)
    if code == "NZ":
        raise CountryError("NZ ingestion requires the vacancy-observation adapter to retain source provenance")
    return code


def uk_sponsorship_allowed(job, config=None):
    """No default-to-UK for an unknown explicit country or NZ location."""
    from applypilot.geography import market, contains
    job = dict(job)
    location = str(job.get("location") or "")
    nz_location = any(contains(location, term) for term in ("New Zealand", "NZ"))
    uk_location = any(contains(location, term) for term in ("United Kingdom", "UK", "GB", "Great Britain", "England", "Scotland", "Wales", "Northern Ireland"))
    explicit = job.get("country_code")
    if explicit is not None:
        try:
            if normalize_country(explicit, optional=False) != "GB":
                return False
            if market(job.get("country")) == "new zealand" or nz_location:
                return False
            return True
        except CountryError:
            return False
    raw = job.get("country")
    if raw:
        return market(raw) == "uk" and not nz_location
    configured = (config or {}).get("country_code")
    if configured is not None:
        try:
            if normalize_country(configured, optional=False) != "GB":
                return False
        except CountryError:
            return False
    configured_geography = (config or {}).get("country")
    if configured_geography and market(configured_geography) != "uk":
        return False
    if nz_location:
        return False
    if uk_location:
        return True
    if any(contains(location, term) for term in ("Auckland", "Wellington", "Christchurch")):
        return False  # ambiguous bare city is not adequate UK jurisdiction evidence
    if configured is not None:
        return True  # validated GB above; conflicting NZ location already rejected
    # Compatibility for existing UK search configs, never arbitrary unknown codes.
    return market((config or {}).get("country")) == "uk"
