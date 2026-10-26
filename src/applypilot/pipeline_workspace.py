"""Native Pipeline workspace services: configuration, previews and recovery."""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
from pathlib import Path
import re

import yaml

from applypilot import setup_workspace as setup
from applypilot.eligibility import candidates, exclusions, packet
from applypilot.role_routing import route_resume_for_job, score_fingerprint, suggested_routing
from applypilot.read_model import display_read

STAGES = ("discover", "enrich", "score", "tailor", "statement", "cover", "pdf")


def merge_config(app_dir: Path, data: dict) -> dict:
    base = setup.read_searches_dict(app_dir)
    before = copy.deepcopy(base)
    revision = hashlib.sha256(json.dumps(base, sort_keys=True).encode()).hexdigest()
    if data.get("expected_revision") and data["expected_revision"] != revision:
        raise ValueError("Search configuration changed in another tab; reload before saving")
    if data.get("builder_yaml") is not None:
        patch = yaml.safe_load(data["builder_yaml"])
        if not isinstance(patch, dict):
            raise ValueError("Search builder must produce a YAML object")
        tiers = {q["query"].casefold(): q.get("tier", 1) for q in base.get("queries", [])}
        patch["queries"] = [q | {"tier": tiers.get(q["query"].casefold(), 1)} for q in patch.get("queries", [])]
    else:
        patch = data.get("patch", {})
        if not isinstance(patch, dict):
            raise ValueError("Configuration patch must be an object")
    for key, value in patch.items():
        if key in {"defaults", "resume_routing", "board_overrides"} and isinstance(value, dict):
            base[key] = dict(base.get(key) or {}) | value
        else:
            base[key] = value
    if patch.get("smart_sites"):
        base["smart_sources_disabled"] = False
    if "boards" in patch:
        base["sites"] = list(patch["boards"])
    for key, rate in (base.get("ai_prices") or {}).items():
        import math
        if not isinstance(rate, dict) or any(not isinstance(rate.get(k), (float, int)) or not math.isfinite(rate[k]) or rate[k] < 0 for k in ("input_per_million", "output_per_million")):
            raise ValueError(f"Invalid per-million token prices for {key}")
    setup._validate_searches_dict(base, app_dir)
    changed = [k for k in base if before.get(k) != base[k]]
    diff = "".join(difflib.unified_diff(yaml.safe_dump(before, sort_keys=True).splitlines(True), yaml.safe_dump(base, sort_keys=True).splitlines(True), fromfile="saved", tofile="proposed"))
    return {"config": base, "yaml": yaml.safe_dump(base, sort_keys=False), "changed": changed, "diff": diff,
            "preserved": [k for k in before if k not in changed]}


def save_config(conn, app_dir: Path, data: dict) -> dict:
    result = merge_config(app_dir, data)
    from applypilot.command_center import record_legacy_compatibility
    record_legacy_compatibility(conn,app_dir)
    setup.write_searches_yaml(app_dir, result["yaml"])
    result["stale_scores"] = invalidate_scores(conn, app_dir)
    return result


def invalidate_scores(conn, app_dir: Path, ids: list[int] | None = None) -> int:
    """Archive previous assessments, then queue changed inputs without touching CVs."""
    conn.execute("CREATE TABLE IF NOT EXISTS cc_score_history(id INTEGER PRIMARY KEY,job_url TEXT,score INTEGER,reasoning TEXT,fingerprint TEXT,created_at TEXT)")
    from applypilot.pipeline_store import now
    count = 0
    for row in conn.execute("SELECT rowid AS job_id,* FROM jobs WHERE fit_score IS NOT NULL AND applied_at IS NULL").fetchall():
        job = dict(row)
        from applypilot.discovery_scope import processing_allows
        if not processing_allows(conn,job['url']):continue
        from applypilot.country_context import in_legacy_queue
        if not in_legacy_queue(job):
            continue
        if ids is not None and job["job_id"] not in ids:
            continue
        changed = job.get("score_fingerprint") and job["score_fingerprint"] != score_fingerprint(job, app_dir)
        if ids is not None or changed:
            conn.execute("INSERT INTO cc_score_history(job_url,score,reasoning,fingerprint,created_at) VALUES(?,?,?,?,?)",
                         (job["url"], job["fit_score"], job["score_reasoning"], job.get("score_fingerprint"), now()))
            conn.execute("UPDATE jobs SET fit_score=NULL,score_status='stale',score_attempts=0,score_retry_at=NULL WHERE url=?", (job["url"],))
            count += 1
    conn.commit()
    return count


def campaign_config(app_dir: Path, ident: str | None) -> dict:
    base = setup.read_searches_dict(app_dir)
    if not ident:
        return base
    campaign = next((c for c in base.get("campaigns", []) if c.get("id") == ident), None)
    if not campaign:
        raise ValueError("Campaign not found")
    if campaign.get("country_code") == "NZ":
        from applypilot.discovery.nz import effective
        return effective(base, campaign)
    for key in ("queries", "locations", "boards", "smart_sites", "defaults", "resume_routing", "workday_max_tier", "country", "country_code", "geography_enabled", "location_filter_mode","discovery_defaults","smart_sources_disabled"):
        if key in campaign:
            base[key] = copy.deepcopy(campaign[key])
    return base


def save_campaign(conn, app_dir, data):
    if data.get("country_code") == "NZ" or data.get("id") == "nz-job-search":
        from applypilot.discovery.nz import save_campaign as save_nz
        return save_nz(conn, app_dir, {"campaign": data, "expected_revision": data.get("expected_revision")})
    ident = str(data.get("id") or re.sub(r"[^a-z0-9]+", "-", str(data.get("name", "")).lower()).strip("-"))
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", ident) or not str(data.get("name") or "").strip():
        raise ValueError("Campaign needs a name and stable ID")
    base = setup.read_searches_dict(app_dir)
    campaign = {k: data[k] for k in ("name", "queries", "locations", "boards", "smart_sites", "defaults", "resume_routing", "workday_max_tier", "country", "country_code", "geography_enabled", "location_filter_mode","discovery_defaults","smart_sources_disabled") if k in data} | {"id": ident}
    merged = base | campaign
    setup._validate_searches_dict(merged, app_dir)
    items = [c for c in base.get("campaigns", []) if c.get("id") != ident]
    if not data.get("delete"):
        items.append(campaign)
    return save_config(conn, app_dir, {"patch": {"campaigns": items}})


@display_read
def preview(conn, app_dir: Path, data: dict) -> dict:
    from applypilot.config import load_sites_config
    from applypilot.discovery.smartextract import build_scrape_targets, _dedupe_site_query_location_twins, _balanced_target_window
    from applypilot.discovery.jobspy import _normalize_search_cfg
    from applypilot.discovery_scope import effective_config,selection_scope,title_matches,preview_token
    cfg = effective_config(conn,app_dir,data) if data.get('scope_policy') is not None else campaign_config(app_dir, data.get("campaign_id"))
    from applypilot.country_context import current
    if (current() and current().country_code == "NZ") or cfg.get("country_code") == "NZ":
        from applypilot.discovery.nz import preview as nz_preview
        return nz_preview(conn, app_dir, cfg)
    queries = data.get("search_queries")
    if queries is None and data.get("search_query"):
        queries = [x.strip() for x in re.split(r"[,\n]+", data["search_query"]) if x.strip()]
    if queries and data.get('scope_policy') is None:
        cfg["queries"] = [{"query": str(q), "tier": 1} for q in queries]
    normal = _normalize_search_cfg(cfg)
    planned = 0
    coverage = []
    from applypilot.geography import locations as geo_locations, board_scope, public_catalog
    effective_locations=geo_locations(cfg)
    geography_coverage=[]
    for place in effective_locations:
        supported,skipped=board_scope(place,normal.get('sites',[]))
        geography_coverage.append({k:place.get(k) for k in ('label','location','country','state','city','remote','provider_country')} | {'boards':supported,'skipped':skipped})
    geography_conflicts=[]
    if cfg.get('location_filter_mode','legacy')=='legacy':
        from applypilot.discovery.jobspy import _load_location_config, _location_ok
        accept,reject=_load_location_config(cfg)
        geography_conflicts=[f"{p['location']} does not pass the saved location pattern rules. Choose Match selected countries, states and cities or update those patterns." for p in effective_locations if not _location_ok(p['location'],accept,reject)]
    for board in normal.get("sites", []):
        override = (normal.get("board_overrides") or {}).get(board, {})
        roles = [q for q in cfg.get("queries", []) if not override.get("queries") or q["query"] in override["queries"]]
        if override.get("tiers"):
            roles = [q for q in roles if q.get("tier", 1) in override["tiers"]]
        locations = [l for l in effective_locations if (not override.get("location_labels") or l.get("label") in override["location_labels"]) and board in board_scope(l,[board])[0]]
        count = len(roles) * len(locations)
        planned += count
        coverage.append({"source": board, "searches": count, "roles": [r["query"] for r in roles]})
    sites = load_sites_config(app_dir).get("sites", [])
    targets = build_scrape_targets(sites=sites, search_cfg=cfg)
    allow = set(cfg.get("smart_sites") or [])
    targets = [t for t in targets if not allow or t["name"] in allow]
    if cfg.get("smart_sources_disabled"):
        targets = []
    targets = _dedupe_site_query_location_twins(targets)
    cap = int(data.get("smart_target_budget", cfg.get("smart_target_budget", 0)) or 0)
    board_cap = int(data.get("board_search_budget", cfg.get("board_search_budget", 0)) or 0)
    if any(v < 0 or v > 10000 for v in (cap, board_cap, int(data.get("workday_target_budget", cfg.get("workday_target_budget", 0)) or 0))):
        raise ValueError("Source budgets must be between 0 and 10000")
    capped = _balanced_target_window(targets, cap, search_cfg=cfg) if cap else targets
    if cfg.get('_run_scope'):capped=cfg.get('_planned_direct_targets',[])
    picked = bool(data.get("selected_only", False))
    minimum = max(1, min(10, int(data.get("min_score", 7))))
    scope_campaign = data.get("campaign_id")
    members = {r[0] for r in conn.execute("SELECT job_url FROM cc_campaign_jobs WHERE campaign_id=?", (scope_campaign,))} if scope_campaign else set()
    def in_scope(job):
        return not scope_campaign or job.get("campaign_id") == scope_campaign or job["url"] in members
    stages = data.get("stages") or list(STAGES)
    run_scope=cfg.get('_run_scope')
    if run_scope:
        with selection_scope(None,{'saved_job_urls':run_scope.get('saved_job_urls',[])}):
            queue={s:sum(in_scope(j) for j in candidates(conn,s,minimum,picked=picked,app_dir=app_dir)) if s in stages else 0 for s in STAGES if s!='discover'}
    else:
        queue = {s: sum(in_scope(j) for j in candidates(conn, s, minimum, picked=picked, app_dir=app_dir)) if s in stages else 0 for s in STAGES if s != "discover"}
    discover_active = "discover" in stages
    routes = []
    problems = []
    for row in conn.execute("SELECT rowid AS job_id,* FROM jobs ORDER BY discovered_at DESC LIMIT 100").fetchall():
        job = dict(row)
        from applypilot.country_context import in_legacy_queue
        if not in_legacy_queue(job):
            continue
        if not in_scope(job):
            continue
        if run_scope:
            from applypilot.discovery_scope import excluded_title
            if not title_matches(job.get('title'),cfg.get('queries',[]),run_scope['title_match']) or excluded_title(job.get('title'),cfg.get('exclude_titles',[])):continue
        if len(routes) < 30:
            route = route_resume_for_job(job, app_dir=app_dir, cfg=cfg)
            stale = bool(job.get("score_fingerprint") and job["score_fingerprint"] != score_fingerprint(job, app_dir))
            routes.append({"id": job["job_id"], "title": job["title"], "route": route.public(), "packet": packet(conn, job),
                           "score": job.get("fit_score"), "stale_score": stale, "campaign": job.get("campaign_id")})
        for stage, status, detail in (("score", job.get("score_status"), job.get("score_error")),
                                      ("enrich", "partial" if job.get("full_description") else "failed", job.get("detail_error")),
                                      ("tailor", job.get("tailor_status"), job.get("tailor_failure_detail")),
                                      ("cover", job.get("cover_letter_status"), job.get("cover_letter_failure_detail")),
                                      ("statement", job.get("statement_status"), job.get("statement_error")),
                                      ("pdf", job.get("pdf_status"), job.get("pdf_error"))):
            if detail or status in {"failed", "retryable", "needs_route"}:
                problems.append({"id": job["job_id"], "title": job["title"], "stage": stage, "state": status, "detail": str(detail or "")[:600], "excluded": exclusions(conn, job)})
    workday = [q["query"] for q in cfg.get("queries", []) if q.get("tier", 1) <= cfg.get("workday_max_tier", 3)]
    revision=hashlib.sha256(json.dumps(setup.read_searches_dict(app_dir),sort_keys=True).encode()).hexdigest()
    extra={}
    if run_scope:
        from applypilot.source_registry import employers
        portals=employers(app_dir)
        source_plan={'boards':sorted({t['board'] for t in cfg.get('_planned_board_searches',[])}),
                     'direct':[] if data.get('discover_skip_smarte') else sorted({t['name'] for t in capped}),
                     'workday':sorted({t['name'] for t in cfg.get('_planned_workday_searches',[])})}
        workday_targets=len(cfg.get('_planned_workday_searches',[]))
        from applypilot.country_context import in_operation_queue
        saved_count=sum(in_operation_queue(dict(r)) for r in conn.execute('SELECT country_code FROM jobs'))
        examples=[{'id':r['id'],'title':r['title']} for r in routes[:8]]
        scheduled={t['query'] for t in cfg.get('_planned_board_searches',[])+cfg.get('_planned_workday_searches',[])}|{t.get('query') for t in cfg.get('_planned_direct_targets',[])}
        if any(not t.get('query') for t in cfg.get('_planned_direct_targets',[])):scheduled.update(q['query'] for q in cfg['queries'])
        scope_summary={**run_scope,'saved_job_urls':None,'discovering':discover_active,'roles':[q['query'] for q in cfg['queries']],
            'locations':[p['location'] for p in effective_locations],'sources':source_plan,'existing_saved_excluded':saved_count if run_scope['processing_scope']=='new' else max(0,saved_count-len(run_scope['saved_job_urls'])),
            'matching_saved_selected':len(run_scope['saved_job_urls']),'workday_window':workday_targets,'examples':examples,'skipped_boards':cfg.get('_skipped_boards',[]),
            'requests':{'boards':cfg.get('_planned_board_searches',[]),'direct':cfg.get('_planned_direct_targets',[]),'workday':cfg.get('_planned_workday_searches',[])},
            'whole_country_locations':[p['location'] for p in effective_locations if p.get('kind')=='country' and not p.get('remote')],
            'unscheduled_roles':[q['query'] for q in cfg['queries'] if q['query'] not in scheduled] if discover_active else []}
        catalog=[source_plan,[s for s in sites if s['name'] in source_plan['direct']],list(portals.values()) if source_plan['workday'] else []]
        from applypilot.canonical import digest
        token=preview_token(cfg,data,digest([revision,catalog]))
        available=bool((source_plan['boards'] and planned) or (source_plan['direct'] and capped) or workday_targets)
        extra={'scope_summary':scope_summary,'preview_token':token,'can_start':(available if discover_active else bool(sum(queue.values()))),
               'start_problem':None if (available if discover_active else bool(sum(queue.values()))) else 'Choose at least one configured source and a valid role/location, or matching saved jobs.'}
    return {"config": cfg, "config_revision": revision, "queue": queue, "board_searches": planned, "board_window": len(cfg.get('_planned_board_searches',[])) if run_scope else 0 if not discover_active or data.get("discover_skip_jobspy") else min(planned, board_cap) if board_cap else planned, "coverage": coverage,
            "smart_targets": len(targets), "smart_window": 0 if not discover_active or data.get("discover_skip_smarte") else len(capped), "workday_roles": [] if not discover_active or data.get("discover_skip_workday") else workday,
            "routes": routes[:30], "problems": problems, "routing_inferred": not bool(cfg.get("resume_routing")),
            "suggested_routing": suggested_routing(app_dir), "missing_inputs": [name for name in ("profile.json", "resume.txt") if not (app_dir/name).is_file()] if not run_scope or any(s in {'score','tailor','cover','statement'} for s in stages) else [],
            "geography":{'locations':geography_coverage,'filter_mode':cfg.get('location_filter_mode','legacy'),'countries':public_catalog(),'conflicts':geography_conflicts,
                         'note':'Country/state/city text is routed per location. Provider coverage is shown for each target; selected-location filtering replaces legacy accept/reject lists only when explicitly chosen.'},
            "cost_note": "Preview counts work, not guaranteed vacancies. Actual tokens and configured per-model prices determine recorded cost.",**extra}


def recent_runs(conn):
    runs = []
    for row in conn.execute("SELECT * FROM cc_pipeline_runs ORDER BY started_at DESC LIMIT 20"):
        item = dict(row)
        item["config"] = json.loads(item["config"] or "{}")
        item["result"] = json.loads(item["result"] or "{}")
        item["stages"] = [dict(r) | {"result": json.loads(r["result"] or "{}")} for r in conn.execute("SELECT * FROM cc_pipeline_stages WHERE run_id=?", (item["run_id"],))]
        runs.append(item)
    return runs


@display_read
def state(conn, app_dir: Path, bounded=False, campaign_id=None) -> dict:
    from applypilot.config import load_sites_config
    from applypilot.source_registry import employers
    options={}
    if bounded:
        initial=campaign_config(app_dir,campaign_id)
        defaults=initial.get('discovery_defaults') or {}
        options={'scope_policy':{k:defaults.get(k,v) for k,v in {'max_new_jobs':50,'results_per_search':10,'title_match':True,'processing_scope':'new'}.items()},
                 'stages':['discover','enrich','score'],'min_score':7,'workers':1,'selected_only':False,'stream':False,
                 'discover_skip_jobspy':not defaults.get('job_boards',True),'discover_skip_workday':not defaults.get('workday',False),'discover_skip_smarte':not defaults.get('direct',False),
                 **{key:defaults.get(key,6) for key in ('board_search_budget','smart_target_budget','workday_target_budget')}}
        options['campaign_id']=campaign_id
        options['scope_policy']['processing_scope']='new'
        if not initial.get('queries') or not initial.get('locations'):options={}
    return {**preview(conn, app_dir, options), "sources": load_sites_config(app_dir).get("sites", []), "runs": recent_runs(conn),
            "employers": employers(app_dir, include_disabled=True),
            "source_health": [dict(r) for r in conn.execute("SELECT * FROM cc_source_health")],
            "variants": setup.list_resume_variants(app_dir), "campaigns": setup.read_searches_dict(app_dir).get("campaigns", []),
            "insights": insights(conn, app_dir)}


@display_read
def progress(conn, app_dir: Path, new_only=False) -> dict:
    """Live counts/run state without rebuilding source plans or artifact previews."""
    from applypilot.country_context import current, in_operation_queue
    runs=recent_runs(conn)
    run_settings=runs[0]['config'].get('run_scope') if runs else None
    if run_settings:
        from applypilot.discovery_scope import selection_scope
        with selection_scope(runs[0]['run_id'],run_settings):
            queue={s:len(candidates(conn,s,7,picked=False,app_dir=app_dir)) for s in STAGES if s!='discover'}
    else:
        queue = {s: 0 if new_only else len(candidates(conn, s, 7, picked=False, app_dir=app_dir)) for s in STAGES if s != "discover"}
    where = "country_code='NZ'" if current() and current().country_code == "NZ" else "(country_code IS NULL OR country_code='GB')"
    problems = []
    for row in conn.execute(f"SELECT rowid AS job_id,* FROM jobs WHERE {where} ORDER BY discovered_at DESC LIMIT 100"):
        job = dict(row)
        if not in_operation_queue(job):
            continue
        for stage, status, detail in (("score", job.get("score_status"), job.get("score_error")),
                                      ("enrich", "partial" if job.get("full_description") else "failed", job.get("detail_error")),
                                      ("tailor", job.get("tailor_status"), job.get("tailor_failure_detail")),
                                      ("cover", job.get("cover_letter_status"), job.get("cover_letter_failure_detail")),
                                      ("statement", job.get("statement_status"), job.get("statement_error")),
                                      ("pdf", job.get("pdf_status"), job.get("pdf_error"))):
            if detail or status in {"failed", "retryable", "needs_route"}:
                problems.append({"id": job["job_id"], "title": job["title"], "stage": stage, "state": status,
                                 "detail": str(detail or "")[:600], "excluded": exclusions(conn, job)})
    result={"queue":queue,"runs":runs,"problems":problems}
    if run_settings:result['run_queue']=queue
    return result


def inspect_job(conn, app_dir: Path, ident: int) -> dict:
    row = conn.execute("SELECT rowid AS job_id,* FROM jobs WHERE rowid=?", (ident,)).fetchone()
    if not row:
        raise LookupError("Job not found")
    job = dict(row)
    def parsed(value, default):
        try:
            return json.loads(value or json.dumps(default))
        except ValueError:
            return default
    return {"route": route_resume_for_job(job, app_dir=app_dir).public(), "packet": packet(conn, job),
            "override": job.get("resume_route_override"), "score_reasoning": job.get("score_reasoning"),
            "gaps": parsed(job.get("tailor_requirement_gaps"), {}), "responsibility_map": parsed(job.get("tailor_responsibility_map"), []),
            "source": parsed(job.get("source_provenance"), {"name": job.get("site")}), "variants": setup.list_resume_variants(app_dir),
            "evaluations": [dict(r) for r in conn.execute("SELECT variant,score,reasoning,provider,model,created_at FROM cc_variant_evaluations WHERE job_url=? ORDER BY id DESC LIMIT 10", (job["url"],))]}


def retry(conn, app_dir: Path, data: dict) -> list[int]:
    stage = data.get("stage")
    ids = data.get("ids")
    if stage not in {"score", "enrich", "tailor", "statement", "cover", "pdf"} or not isinstance(ids, list) or not 1 <= len(ids) <= 100:
        raise ValueError("Choose a stage and 1–100 job IDs")
    for ident in ids:
        job = conn.execute("SELECT rowid AS job_id,* FROM jobs WHERE rowid=?", (int(ident),)).fetchone()
        if not job or exclusions(conn, dict(job)):
            raise ValueError(f"Job #{ident} is unavailable or excluded")
        if stage == "score":
            invalidate_scores(conn, app_dir, [int(ident)])
            conn.execute("UPDATE jobs SET score_status=NULL,score_attempts=0,score_retry_at=NULL,score_error=NULL WHERE rowid=?", (int(ident),))
        elif stage in {"tailor", "statement", "cover"}:
            conn.execute(f"UPDATE jobs SET {stage}_attempts=0 WHERE rowid=?", (int(ident),))
        elif stage == "enrich":
            conn.execute("UPDATE jobs SET detail_scraped_at=NULL,detail_error=NULL WHERE rowid=?", (int(ident),))
        elif stage == "pdf":
            conn.execute("UPDATE jobs SET pdf_attempts=0,pdf_status=NULL,pdf_error=NULL WHERE rowid=?", (int(ident),))
    conn.commit()
    return [int(x) for x in ids]


def save_job(conn, app_dir: Path, data: dict):
    ident = int(data.get("id") or 0)
    row = conn.execute("SELECT rowid AS job_id,* FROM jobs WHERE rowid=?", (ident,)).fetchone()
    if not row or dict(row).get("apply_status") in {"running", "submitting", "in_progress"}:
        raise ValueError("Job is unavailable or active")
    from applypilot.country_context import check_job
    check_job(dict(row), app_dir, country_code=data.get("country_code"), preparation=True)
    if "variant" in data:
        key = str(data["variant"])
        if key not in {v["key"] for v in setup.list_resume_variants(app_dir)} | {"default", ""}:
            raise ValueError("Unknown resume variant")
        if (key or None) != row["resume_route_override"]:
            conn.execute("UPDATE jobs SET resume_route_override=?,score_status=NULL WHERE rowid=?", (key or None, ident))
            invalidate_scores(conn, app_dir, [ident])
    if "requirements" in data:
        req = data["requirements"]
        if not isinstance(req, dict) or any(k not in {"cover", "statement", "answers"} for k in req):
            raise ValueError("Invalid packet requirements")
        if any(k in req and not isinstance(req[k], bool) for k in ("cover", "statement")):
            raise ValueError("Cover/statement requirements must be true or false")
        answers = req.get("answers", [])
        if not isinstance(answers, list) or len(answers) > 50 or any(not isinstance(q, str) or not q.strip() or len(q) > 1000 for q in answers):
            raise ValueError("Required answers must be up to 50 question strings")
        conn.execute("UPDATE jobs SET document_requirements=? WHERE rowid=?", (json.dumps(req), ident))
    conn.commit()


def evaluate_variants(conn, app_dir: Path, data: dict) -> dict:
    """User-triggered second opinions; cached, two variants maximum, daily budget."""
    from applypilot.scoring.scorer import score_job
    from applypilot.ai_routing import route_info
    from applypilot.pipeline_store import now
    from applypilot.professional import enrich_evidence
    row = conn.execute("SELECT rowid AS job_id,* FROM jobs WHERE rowid=?", (int(data.get("id") or 0),)).fetchone()
    keys = data.get("variants", [])
    if not row or not row["full_description"] or exclusions(conn, dict(row)) or not isinstance(keys, list) or not 1 <= len(keys) <= 2:
        raise ValueError("Choose an active enriched job and one or two resume variants")
    cfg = setup.read_searches_dict(app_dir)
    route_cfg = cfg.get("resume_routing") or suggested_routing(app_dir)
    outputs = []
    task = "review" if data.get("use_reviewer") is True else "score"
    info = route_info(task)
    if not info.get("configured"):
        raise ValueError("Configure the selected AI task before requesting a second opinion")
    for key in dict.fromkeys(keys):
        job = dict(row) | {"resume_route_override": key}
        routed = route_resume_for_job(job, app_dir=app_dir, cfg=cfg | {"resume_routing": route_cfg})
        inputs = score_fingerprint(job, app_dir) + routed.fingerprint + json.dumps(info, sort_keys=True)
        cached = conn.execute("SELECT * FROM cc_variant_evaluations WHERE job_url=? AND variant=? AND input_hash=? AND score IS NOT NULL ORDER BY id DESC LIMIT 1", (job["url"], key, inputs)).fetchone()
        if cached:
            outputs.append(dict(cached) | {"cached": True})
            continue
        conn.execute("BEGIN IMMEDIATE")
        used = conn.execute("SELECT COUNT(*) FROM cc_variant_evaluations WHERE created_at>=?", (now()[:10],)).fetchone()[0]
        if used >= 10:
            raise ValueError("Daily second-opinion budget reached (10 evaluations)")
        # Reserve budget before inference, including failed calls.
        reservation = conn.execute("INSERT INTO cc_variant_evaluations(job_url,variant,input_hash,provider,model,created_at) VALUES(?,?,?,?,?,?)",
                                   (job["url"], key, inputs, info["provider"], info["model"], now())).lastrowid
        conn.commit()
        evidence, _ = enrich_evidence(routed.text, setup.read_profile(app_dir), job)
        from applypilot.pipeline_store import usage_context
        from applypilot.ai_routing import task_context
        from applypilot.llm import get_client
        with usage_context(app_dir, "evaluation:" + str(reservation)), task_context(task):
            evaluator = getattr(score_job, "__wrapped__", score_job) if task == "review" else score_job
            result = evaluator(evidence, job)
            info["model"] = get_client().last_model
        if not result["score"]:
            raise ValueError("Second opinion unavailable; budget reservation retained")
        conn.execute("UPDATE cc_variant_evaluations SET score=?,reasoning=?,model=? WHERE id=?", (result["score"], result["reasoning"], info["model"], reservation))
        conn.commit()
        outputs.append({"variant": key, **result, **info, "cached": False})
    return {"evaluations": outputs, "note": "Comparative scores are evidence, not a measured interview-outcome guarantee. Choose a route explicitly."}


@display_read
def insights(conn, app_dir: Path) -> dict:
    from applypilot.ai_settings import environment
    env = environment(app_dir)
    prices = json.loads(env["APPLYPILOT_AI_PRICES"]) if env.get("APPLYPILOT_AI_PRICES") else setup.read_searches_dict(app_dir).get("ai_prices", {})
    from applypilot.pipeline_store import usage_report
    from applypilot.country_context import current,in_operation_queue
    usage=usage_report(conn,prices,country_code=current().country_code if current() else None)
    cohorts = {}
    rows = conn.execute("SELECT j.*,m.hiring_status,d.variant submitted_variant,d.fingerprint submitted_revision,d.report_path submitted_report FROM jobs j LEFT JOIN cc_job_meta m ON m.job_url=j.url LEFT JOIN cc_documents d ON d.id=m.document_id WHERE j.applied_at IS NOT NULL").fetchall()
    for row in rows:
        job = dict(row)
        if not in_operation_queue(job):continue
        try:
            resume = json.loads(job.get("resume_route") or "{}").get("key", "untracked")
        except ValueError:
            resume = "untracked"
        writer = {}
        report_path = Path(job["submitted_report"]) if job.get("submitted_report") else None
        if report_path and report_path.resolve().is_relative_to(app_dir.resolve()) and report_path.is_file():
            try:
                writer = (json.loads(report_path.read_text(encoding="utf-8")).get("ai_routes") or {}).get("writer", {})
            except (ValueError, OSError):
                pass
        model = writer.get("model") or job.get("tailor_model") or "untracked"
        resume = job.get("submitted_variant") or resume
        revision = (job.get("submitted_revision") or "untracked")[:12]
        key = (job.get("campaign_id") or "unassigned", job.get("site") or "unknown", resume, model, revision, job["applied_at"][:7])
        cohort = cohorts.setdefault(key, {"campaign": key[0], "source": key[1], "resume": key[2], "model": key[3], "revision": key[4], "month": key[5], "submitted": 0, "responses": 0, "interviews": 0, "offers": 0})
        cohort["submitted"] += 1
        outcome = job.get("hiring_status")
        cohort["responses"] += outcome in {"acknowledged", "assessment", "interview", "offer", "rejected"}
        cohort["interviews"] += outcome in {"interview", "offer"}
        cohort["offers"] += outcome == "offer"
    return {**usage, "cohorts": list(cohorts.values()), "note": "Small samples describe recorded outcomes; they do not establish a best model or resume. "+usage["correlation_note"]}
