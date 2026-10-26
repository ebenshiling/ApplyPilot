"""One effective queue for pipeline workers, counters, previews and recovery."""
from __future__ import annotations

import os
from pathlib import Path


def selected_only() -> bool:
    return any(os.environ.get(k, "").strip().lower() in {"1", "true", "yes", "y", "on"}
               for k in ("APPLYPILOT_SELECTED_ONLY", "APPLYPILOT_APPLY_SELECTED_ONLY"))


def document_requirements(job: dict) -> dict:
    import json
    try:
        explicit = json.loads(job.get("document_requirements") or "{}")
    except (ValueError, TypeError):
        explicit = {}
    text = " ".join(str(job.get(k) or "") for k in ("title", "site", "full_description")).lower()
    statement = any(k in text for k in ("supporting statement", "supporting information", "personal statement", "nhs"))
    cover = not any(k in text for k in ("no cover letter", "cover letter not required", "do not include a cover letter"))
    if job.get("country_code")=="NZ":
        statement=any(k in text for k in ("supporting statement","supporting information","personal statement"))
        cover=("cover letter" in text or "covering letter" in text) and cover
    return {"resume": True, "statement": explicit.get("statement", statement),
            "cover": explicit.get("cover", cover), "answers": explicit.get("answers", [])}


def pending_artifacts(job: dict) -> list[tuple[str, Path]]:
    pending = []
    required = document_requirements(job)
    for kind, field in (("resume", "tailored_resume_path"), ("cover", "cover_letter_path"), ("statement", "supporting_statement_path")):
        if not required[kind]:
            continue
        raw = job.get(field)
        if not raw or not str(raw).lower().endswith(".txt"):
            continue
        path = Path(raw)
        report = path.with_name(path.stem + "_REPORT.json")
        pdf = path.with_suffix(".pdf")
        try:
            stamp = max(path.stat().st_mtime_ns, report.stat().st_mtime_ns if report.exists() else 0)
            if not pdf.exists() or pdf.stat().st_mtime_ns < stamp:
                pending.append((field, path))
        except OSError:
            pending.append((field, path))
    return pending


def exclusions(conn, job: dict, *,preparation=True) -> list[str]:
    from applypilot.command_center import blocked, preference_exclusions
    reasons = []
    if job.get("country_code")=="NZ":
        from applypilot.preparation import require_job
        from applypilot.country_context import workspace_for_connection
        try:
            if preparation:require_job(job,workspace_for_connection(conn))
            else:
                from applypilot.country_context import require_enabled
                from applypilot.setup_workspace import read_searches_dict
                require_enabled("NZ",read_searches_dict(workspace_for_connection(conn)))
        except ValueError as exc:reasons.append(str(exc))
    elif job.get("country_code") not in {None,"GB"}:
        reasons.append("Unsupported job country")
    if job.get("apply_status") in {"skipped", "blocked", "in_progress", "running", "submitting"}:
        reasons.append("Job is skipped, blocked or being applied to")
    if job.get("applied_at"):
        reasons.append("Already submitted")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='cc_job_meta'").fetchone():
        row = conn.execute("SELECT visibility FROM cc_job_meta WHERE job_url=?", (job["url"],)).fetchone()
        if row and row[0] != "active":
            reasons.append("Archived or blocked")
    if blocked(conn, job["url"], job.get("company")):
        reasons.append("Matches a block rule")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='blocked_urls'").fetchone():
        if conn.execute("SELECT 1 FROM blocked_urls WHERE ? LIKE prefix||'%'", (job["url"],)).fetchone():
            reasons.append("Blocked URL")
    return reasons + preference_exclusions(conn, job)


def candidates(conn, stage: str, min_score: int = 7, *, picked: bool | None = None, limit: int = 0, app_dir: Path | None = None) -> list[dict]:
    from applypilot.database import get_jobs_by_stage
    names = {"enrich": "pending_detail", "score": "pending_score", "tailor": "pending_tailor", "pdf": "pending_pdf"}
    picked = selected_only() if picked is None else picked
    jobs = get_jobs_by_stage(conn, names.get(stage, stage), min_score=min_score, limit=0,
                              selected_only=picked and stage not in {"enrich", "score"})
    from applypilot.discovery_scope import policy,title_matches,excluded_title
    from applypilot.country_context import current
    settings=policy()
    if stage == "score":
        from applypilot.role_routing import score_fingerprint,score_inputs_changed
        stale = [j for j in get_jobs_by_stage(conn, "enriched", limit=0) if j.get("fit_score") is not None and
                 j.get("score_fingerprint") and score_inputs_changed(j,app_dir)]
        seen = {j["url"] for j in jobs}
        jobs += [j for j in stale if j["url"] not in seen]
    if settings and current() and not settings.get('explicit_picks'):
        cfg=current().config
        jobs=[j for j in jobs if title_matches(j.get('title'),cfg.get('queries',[]),settings['title_match']) and not excluded_title(j.get('title'),cfg.get('exclude_titles',[]))]
    scope = {int(x) for x in os.environ.get("APPLYPILOT_JOB_IDS", "").split(",") if x.strip().isdigit()}
    jobs = [j for j in jobs if not exclusions(conn, j,preparation=not(stage=="enrich" and j.get("country_code")=="NZ")) and (not scope or j["job_id"] in scope)]
    if stage=="enrich":
        from applypilot.preparation import enrichment_eligible
        from applypilot.country_context import workspace_for_connection
        jobs=[j for j in jobs if j.get("country_code")!="NZ" or enrichment_eligible(j,app_dir or workspace_for_connection(conn))]
    campaign = os.environ.get("APPLYPILOT_CAMPAIGN_ID") if app_dir is None else None
    if campaign:
        members = {r[0] for r in conn.execute("SELECT job_url FROM cc_campaign_jobs WHERE campaign_id=?", (campaign,))}
        jobs = [j for j in jobs if j.get("campaign_id") == campaign or j["url"] in members]
    if stage == "enrich":
        from applypilot.enrichment.detail import SKIP_DETAIL_SITES
        jobs = [j for j in jobs if j.get("site") not in SKIP_DETAIL_SITES]
    if stage == "score":
        jobs = [j for j in jobs if j.get("score_status") != "needs_route"]
    if stage == "tailor":
        from applypilot.role_routing import score_fingerprint
        jobs = [j for j in jobs if not j.get("score_fingerprint") or j["score_fingerprint"] == score_fingerprint(j, app_dir)]
        from applypilot.role_routing import route_resume_for_job
        jobs = [j for j in jobs if not route_resume_for_job(j, app_dir=app_dir).ambiguous]
    if stage == "tailor":
        from applypilot.scoring.tailor import (_load_skip_titles, _matches_skip_titles, _strict_title_filter_enabled,
                                             _load_focus_roles, _title_matches_focus_roles)
        from applypilot.config import load_profile
        try:
            if app_dir:
                from applypilot.setup_workspace import read_profile, read_searches_dict
                profile = read_profile(app_dir)
                search_cfg = read_searches_dict(app_dir)
            else:
                profile = load_profile()
                search_cfg = None
        except (FileNotFoundError, ValueError):
            profile = {}
            search_cfg = None
        skip = search_cfg.get("skip_titles", []) if search_cfg is not None else _load_skip_titles()
        focus = _load_focus_roles(profile, search_cfg) if not picked and _strict_title_filter_enabled(profile) else []
        jobs = [j for j in jobs if not _matches_skip_titles(j.get("title"), skip)
                and (not focus or _title_matches_focus_roles(j.get("title"), focus))]
    if stage in {"statement", "cover"}:
        forced = os.environ.get("APPLYPILOT_FORCE_DOCUMENTS", "").split(",")
        jobs = [j for j in jobs if stage in forced or document_requirements(j)[stage]]
    if stage == "pdf":
        jobs = [j for j in jobs if pending_artifacts(j)]
    return jobs[:limit] if limit > 0 else jobs


def packet(conn, job: dict) -> dict:
    required = document_requirements(job)
    missing = []
    for kind, field in (("resume", "tailored_resume_path"), ("cover", "cover_letter_path"), ("statement", "supporting_statement_path")):
        if required[kind] and (not job.get(field) or not Path(job[field]).is_file()):
            missing.append(kind)
        elif required[kind]:
            from applypilot.canonical import require_final,FactReviewRequired
            from applypilot.country_context import workspace_for_connection
            try:
                require_final(conn,workspace_for_connection(conn),job[field],job,kind=kind)
            except FactReviewRequired:
                missing.append(kind+" factual review")
    if pending_artifacts(job):
        missing.append("PDF rendering")
    if job.get("tailor_input_hash") and job.get("score_fingerprint") != job["tailor_input_hash"]:
        missing.append("CV inputs changed")
    for kind in ("cover", "statement"):
        if required[kind] and job.get("tailor_input_hash") and job.get(kind+"_input_hash") != job["tailor_input_hash"]:
            missing.append(kind + " inputs changed")
    for question in required.get("answers", []):
        if job.get("country_code")=="NZ":
            from applypilot.country_answers import reviewed
            if reviewed(conn,"NZ",question,job=job) is None:missing.append("Answer: "+question)
            continue
        table = conn.execute("SELECT 1 FROM sqlite_master WHERE name='cc_answers'").fetchone()
        if not table or not conn.execute("SELECT 1 FROM cc_answers WHERE lower(trim(question))=lower(trim(?)) AND trim(answer)!=''", (question,)).fetchone():
            missing.append("Answer: " + question)
    reasons = exclusions(conn, job,preparation=False)
    if not (job.get("application_url") or job.get("url")):
        reasons.append("No application link")
    return {"required": required, "missing": missing, "excluded": reasons, "ready": not missing and not reasons}
