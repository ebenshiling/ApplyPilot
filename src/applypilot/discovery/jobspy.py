"""JobSpy-based job discovery: searches Indeed, LinkedIn, Glassdoor, ZipRecruiter.

Uses python-jobspy to scrape multiple job boards, deduplicates results,
parses salary ranges, and stores everything in the ApplyPilot database.

Search queries, locations, and filtering rules are loaded from the user's
search configuration YAML (searches.yaml) rather than being hardcoded.
"""

import logging
import re
import sqlite3
import time
from datetime import datetime, timezone

from applypilot import config
from applypilot.country_context import ingestion_country
from applypilot.database import find_existing_job_url, get_connection, init_db, normalize_url
from applypilot.discovery.salary_filter import jobspy_salary_ok, load_salary_preference
from applypilot.discovery_scope import storage_batch,accepts_title,limit_reached,record_new

log = logging.getLogger(__name__)


# -- Proxy parsing -----------------------------------------------------------


def parse_proxy(proxy_str: str) -> dict:
    """Parse host:port:user:pass into components."""
    parts = proxy_str.split(":")
    if len(parts) == 4:
        host, port, user, passwd = parts
        return {
            "host": host,
            "port": port,
            "user": user,
            "pass": passwd,
            "jobspy": f"{user}:{passwd}@{host}:{port}",
            "playwright": {
                "server": f"http://{host}:{port}",
                "username": user,
                "password": passwd,
            },
        }
    elif len(parts) == 2:
        host, port = parts
        return {
            "host": host,
            "port": port,
            "user": None,
            "pass": None,
            "jobspy": f"{host}:{port}",
            "playwright": {"server": f"http://{host}:{port}"},
        }
    else:
        raise ValueError(f"Proxy format not recognized: {proxy_str}. Expected: host:port:user:pass or host:port")


# -- Retry wrapper -----------------------------------------------------------


def _load_scraper():
    try:
        from jobspy import scrape_jobs
        return scrape_jobs
    except ModuleNotFoundError as exc:
        module = exc.name or "unknown"
        if module == "jobspy":
            message = "JobSpy is not installed; install python-jobspy==1.2.0 or skip board discovery"
        else:
            message = f"JobSpy is installed but cannot import required module '{module}'. Rebuild with full python-jobspy==1.2.0 dependencies"
        raise RuntimeError(message) from exc
    except ImportError as exc:
        raise RuntimeError("JobSpy import failed due to incompatible dependencies; rebuild the discovery runtime") from exc


def _scrape_with_retry(kwargs: dict, max_retries: int = 2, backoff: float = 5.0):
    """Call scrape_jobs with retry on transient failures."""
    scrape_jobs = _load_scraper()

    for attempt in range(max_retries + 1):
        try:
            return scrape_jobs(**kwargs)
        except Exception as e:
            err = str(e).lower()
            transient = any(k in err for k in ("timeout", "429", "proxy", "connection", "reset", "refused"))
            if transient and attempt < max_retries:
                wait = backoff * (attempt + 1)
                log.warning("Retry %d/%d in %.0fs: %s", attempt + 1, max_retries, wait, e)
                time.sleep(wait)
            else:
                raise


# -- Location filtering ------------------------------------------------------


def _load_location_config(search_cfg: dict) -> tuple[list[str], list[str]]:
    """Extract accept/reject location lists from search config.

    Falls back to sensible defaults if not defined in the YAML.
    """
    from applypilot.geography import filter_config
    selected=filter_config(search_cfg)
    if selected is not None:
        return selected
    accept = search_cfg.get("location_accept", [])
    reject = search_cfg.get("location_reject_non_remote", [])

    # Also support the nested format used by searches.example.yaml:
    #   location:
    #     accept_patterns: [...]
    #     reject_patterns: [...]
    if (not accept) or (not reject):
        loc_cfg = search_cfg.get("location", {}) or {}
        if not accept:
            accept = loc_cfg.get("accept_patterns", []) or []
        if not reject:
            reject = loc_cfg.get("reject_patterns", []) or []

    return accept, reject


def _normalize_search_cfg(cfg: dict) -> dict:
    """Normalize search config across historical formats.

    This keeps the public YAML stable while allowing older/newer keys:
    - `boards` is an alias of `sites`
    - top-level `country` or defaults.country -> defaults.country_indeed
    - nested location.accept_patterns/reject_patterns -> location_accept/location_reject_non_remote
    """
    if not cfg:
        return {}

    out = dict(cfg)

    # boards -> sites alias
    if out.get("sites") is None and out.get("boards") is not None:
        out["sites"] = out.get("boards")

    # defaults normalization
    defaults = dict(out.get("defaults", {}) or {})

    # Country mapping for JobSpy (country_indeed)
    country = defaults.get("country_indeed") or defaults.get("country") or out.get("country")
    if country and not defaults.get("country_indeed"):
        from applypilot.geography import market
        defaults["country_indeed"] = market(country) or str(country).strip().lower()
    out["defaults"] = defaults

    # Location filter aliases
    if out.get('location_filter_mode')!='selected' and (not out.get("location_accept") or not out.get("location_reject_non_remote")):
        accept, reject = _load_location_config(out)
        if accept and not out.get("location_accept"):
            out["location_accept"] = accept
        if reject and not out.get("location_reject_non_remote"):
            out["location_reject_non_remote"] = reject

    # Normalize location labels for workspaces that only define raw location values.
    locs = out.get("locations")
    if isinstance(locs, list):
        normalized_locs = []
        fallback_labels: list[str] = []
        for loc in locs:
            if not isinstance(loc, dict):
                normalized_locs.append(loc)
                continue
            item = dict(loc)
            if out.get('geography_enabled') or any(k in item for k in ('country','state','city')):
                from applypilot.geography import normalize_location
                item = normalize_location(item,out)
            label = item.get("label") or item.get("location")
            if label:
                item["label"] = label
                fallback_labels.append(str(label))
            normalized_locs.append(item)
        out["locations"] = normalized_locs
        if fallback_labels and not out.get("location_labels"):
            out["location_labels"] = fallback_labels

    board_cfg = out.get("board_overrides")
    if isinstance(board_cfg, dict):
        normalized_overrides: dict[str, dict] = {}
        for raw_name, raw_cfg in board_cfg.items():
            name = str(raw_name or "").strip().lower()
            if not name or not isinstance(raw_cfg, dict):
                continue
            item = dict(raw_cfg)
            if "location_labels" not in item and isinstance(item.get("locations"), list):
                labels: list[str] = []
                for loc in item.get("locations") or []:
                    if isinstance(loc, dict):
                        label = loc.get("label") or loc.get("location")
                    else:
                        label = loc
                    if label:
                        labels.append(str(label))
                if labels:
                    item["location_labels"] = labels
            normalized_overrides[name] = item
        out["board_overrides"] = normalized_overrides

    return out


def _location_ok(location: str | None, accept: list[str], reject: list[str]) -> bool:
    """Check if a job location passes the user's location filter.

    Remote jobs are always accepted. Non-remote jobs must match an accept
    pattern and not match a reject pattern.
    """
    from applypilot.geography import SelectedLocations
    if isinstance(accept,SelectedLocations):
        return accept.matches(location)
    if not location:
        return True  # unknown location -- keep it, let scorer decide

    loc = location.lower()

    # Reject patterns always win (including remote roles like "Remote (US)").
    for r in reject:
        if r and r.lower() in loc:
            return False

    # If user didn't configure accept patterns, don't filter by location.
    if not accept:
        return True

    # Require an accept match (including remote, if you included "Remote" in accept_patterns).
    # Accept matches
    for a in accept:
        if a.lower() in loc:
            return True

    # No match -- reject unknown
    return False


# -- DB storage (JobSpy DataFrame -> SQLite) ---------------------------------


@storage_batch
def store_jobspy_results(
    conn: sqlite3.Connection,
    df,
    source_label: str,
    *,
    search_query: str | None = None,
) -> tuple[int, int]:
    """Store JobSpy DataFrame results into the DB. Returns (new, existing)."""
    from applypilot.discovery import nz, observations
    if nz.active():
        new = existing = 0
        for name, group in df.groupby("site"):
            item = nz.require_source(str(name), method="jobspy")
            added, duplicates = observations.store(conn, group.to_dict("records"), item, method="jobspy", query=search_query)
            new += added
            existing += duplicates
        return new, existing
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0

    for _, row in df.iterrows():
        if limit_reached():break
        url = str(row.get("job_url", ""))
        if not url or url == "nan":
            continue

        # Respect user-level URL blocking.
        try:
            from applypilot.database import is_url_blocked

            if is_url_blocked(url, conn=conn):
                existing += 1
                continue
        except Exception:
            pass

        title = str(row.get("title", "")) if str(row.get("title", "")) != "nan" else None
        if not accepts_title(title):continue
        company = str(row.get("company", "")) if str(row.get("company", "")) != "nan" else None
        location_str = str(row.get("location", "")) if str(row.get("location", "")) != "nan" else None

        # Build salary string from min/max
        salary = None
        min_amt = row.get("min_amount")
        max_amt = row.get("max_amount")
        interval = str(row.get("interval", "")) if str(row.get("interval", "")) != "nan" else ""
        currency = str(row.get("currency", "")) if str(row.get("currency", "")) != "nan" else ""
        if min_amt and str(min_amt) != "nan":
            if max_amt and str(max_amt) != "nan":
                salary = f"{currency}{int(float(min_amt)):,}-{currency}{int(float(max_amt)):,}"
            else:
                salary = f"{currency}{int(float(min_amt)):,}"
            if interval:
                salary += f"/{interval}"

        description = str(row.get("description", "")) if str(row.get("description", "")) != "nan" else None
        site_name = str(row.get("site", source_label))
        is_remote = row.get("is_remote", False)

        site_label = f"{site_name}"
        if is_remote:
            location_str = f"{location_str} (Remote)" if location_str else "Remote"

        strategy = "jobspy"

        # If JobSpy gave us a full description, promote it directly
        full_description = None
        detail_scraped_at = None
        if description and len(description) > 200:
            full_description = description
            detail_scraped_at = now

        # Extract apply URL if JobSpy provided it
        apply_url = str(row.get("job_url_direct", "")) if str(row.get("job_url_direct", "")) != "nan" else None

        canonical_url = normalize_url(url) or url
        dup_url = find_existing_job_url(conn, canonical_url)
        if dup_url:
            existing += 1
            from applypilot.pipeline_store import tag_job
            tag_job(conn, dup_url, site_label, search_query)
            if search_query:
                try:
                    conn.execute(
                        "UPDATE jobs SET search_query = COALESCE(NULLIF(search_query, ''), ?) WHERE url = ?",
                        (search_query, dup_url),
                    )
                except Exception:
                    pass
            continue

        try:
            if limit_reached():continue
            cursor=conn.execute(
                "INSERT INTO jobs (url, title, company, search_query, salary, description, location, site, strategy, discovered_at, "
                "full_description, application_url, detail_scraped_at, country_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    canonical_url,
                    title,
                    company,
                    (search_query or None),
                    salary,
                    description,
                    location_str,
                    site_label,
                    strategy,
                    now,
                    full_description,
                    apply_url,
                    detail_scraped_at,
                    ingestion_country({}),
                ),
            )
            if cursor.rowcount!=1:continue
            record_new(conn,canonical_url)
            new += 1
            from applypilot.pipeline_store import tag_job
            tag_job(conn, canonical_url, site_label, search_query)
        except sqlite3.IntegrityError:
            existing += 1
            if search_query:
                try:
                    conn.execute(
                        "UPDATE jobs SET search_query = COALESCE(NULLIF(search_query, ''), ?) WHERE url = ?",
                        (search_query, canonical_url),
                    )
                except Exception:
                    pass

    conn.commit()
    return new, existing


# -- Single search execution -------------------------------------------------


def _run_one_search(
    search: dict,
    sites: list[str],
    results_per_site: int,
    hours_old: int,
    proxy_config: dict | None,
    defaults: dict,
    max_retries: int,
    accept_locs: list[str],
    reject_locs: list[str],
    glassdoor_map: dict,
    exclude_titles: list[str] | None = None,
    salary_pref: dict | None = None,
) -> dict:
    """Run a single search query and store results in DB."""
    from applypilot.discovery import nz
    if nz.active():
        raise ValueError("Use the bounded NZ campaign discovery entry point")
    s = search
    from applypilot.geography import board_scope, market, SelectedLocations
    from applypilot.country_context import guard_discovery
    guard_discovery({"country": s.get("country") or defaults.get("country_indeed"), "locations": []})
    selected_country=s.get('provider_country') or market(s.get('country')) or market(defaults.get('country_indeed')) or defaults.get('country_indeed','usa')
    if s.get('country') and not market(s['country']):
        selected_country='worldwide'
    sites,skipped=board_scope(s,sites)
    for skip in skipped:
        log.info('Skipping %s: %s',skip['board'],skip['reason'])
    label = f'"{s["query"]}" in {s["location"]} {"(remote)" if s.get("remote") else ""}'
    if "tier" in s:
        label += f" [tier {s['tier']}]"

    # Split sites: Glassdoor needs simplified location, others use original
    gd_location = glassdoor_map.get(s["location"], s["location"].split(",")[0])
    has_glassdoor = "glassdoor" in sites
    other_sites = [si for si in sites if si != "glassdoor"]

    all_dfs = []

    # Run non-Glassdoor sites with original location
    if other_sites:
        kwargs = {
            "site_name": other_sites,
            "search_term": s["query"],
            "location": s["location"],
            "results_wanted": results_per_site,
            "hours_old": hours_old,
            "description_format": "markdown",
            "country_indeed": selected_country,
            "verbose": 0,
        }
        if s.get("remote"):
            kwargs["is_remote"] = True
        if proxy_config:
            kwargs["proxies"] = [proxy_config["jobspy"]]
        if "linkedin" in other_sites:
            kwargs["linkedin_fetch_description"] = True
        try:
            df = _scrape_with_retry(kwargs, max_retries=max_retries)
            all_dfs.append(df)
        except Exception as e:
            log.error("[%s] (non-gd): %s", label, e)

    # Run Glassdoor separately with simplified location
    if has_glassdoor:
        gd_kwargs = {
            "site_name": ["glassdoor"],
            "search_term": s["query"],
            "location": gd_location,
            "results_wanted": results_per_site,
            "hours_old": hours_old,
            "description_format": "markdown",
            "verbose": 0,
            "country_indeed": selected_country,
        }
        if s.get("remote"):
            gd_kwargs["is_remote"] = True
        if proxy_config:
            gd_kwargs["proxies"] = [proxy_config["jobspy"]]
        try:
            gd_df = _scrape_with_retry(gd_kwargs, max_retries=max_retries)
            all_dfs.append(gd_df)
        except Exception as e:
            log.error("[%s] (glassdoor): %s", label, e)

    if not all_dfs:
        log.error("[%s]: all sites failed", label)
        return {"new": 0, "existing": 0, "errors": 1, "filtered": 0, "total": 0, "label": label}

    import pandas as pd
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        df = pd.concat(all_dfs, ignore_index=True) if len(all_dfs) > 1 else all_dfs[0]

    if len(df) == 0:
        log.info("[%s] 0 results", label)
        return {"new": 0, "existing": 0, "errors": 0, "filtered": 0, "total": 0, "label": label}

    from applypilot.discovery_scope import policy as current_policy
    scoped_settings=current_policy()
    if scoped_settings:df=df.iloc[:scoped_settings['results_per_search']]

    # Filter by excluded titles (before storing)
    excluded = 0
    if exclude_titles and "title" in df.columns:
        pats = [p.strip().lower() for p in exclude_titles if str(p).strip()]
        if pats:
            exclude_re = "|".join(re.escape(p) for p in pats)
            mask = df["title"].astype(str).str.lower().str.contains(exclude_re, na=False)
            excluded = int(mask.sum())
            if excluded:
                df = df[~mask]

    # Filter by location before storing
    before = len(df)
    df = df[
        df.apply(
            lambda row: accept_locs.matches(str(row.get('location','')) if str(row.get('location',''))!='nan' else None, remote=str(row.get('is_remote','')).casefold() in {'true','1','yes'}) if isinstance(accept_locs,SelectedLocations) else _location_ok(
                str(row.get("location", "")) if str(row.get("location", "")) != "nan" else None,
                accept_locs,
                reject_locs,
            ),
            axis=1,
        )
    ]
    filtered = before - len(df)

    salary_filtered = 0
    if salary_pref is not None:
        before_salary = len(df)
        df = df[df.apply(lambda row: jobspy_salary_ok(row, salary_pref), axis=1)]
        salary_filtered = before_salary - len(df)

    from applypilot.discovery_scope import policy,run_connection
    conn = run_connection() if policy() else get_connection()
    new, existing = store_jobspy_results(conn, df, s["query"], search_query=s.get("query"))

    msg = f"[{label}] {before} results -> {new} new, {existing} dupes"
    if excluded:
        msg += f", {excluded} filtered (title)"
    if filtered:
        msg += f", {filtered} filtered (location)"
    if salary_filtered:
        msg += f", {salary_filtered} filtered (salary)"
    log.info(msg)

    return {
        "new": new,
        "existing": existing,
        "errors": 0,
        "filtered": filtered + salary_filtered,
        "total": before,
        "label": label,
    }


# -- Single query search -----------------------------------------------------


def search_jobs(
    query: str,
    location: str,
    sites: list[str] | None = None,
    remote_only: bool = False,
    results_per_site: int = 50,
    hours_old: int = 72,
    proxy: str | None = None,
    country_indeed: str = "usa",
) -> dict:
    """Run a single job search via JobSpy and store results in DB."""
    from applypilot.country_context import guard_discovery
    guard_discovery({"country": country_indeed, "locations": [{"location": location}]})
    from applypilot.discovery import nz
    if nz.active():
        raise ValueError("Use the bounded NZ campaign discovery entry point")
    if sites is None:
        sites = ["indeed", "linkedin", "zip_recruiter"]

    proxy_config = parse_proxy(proxy) if proxy else None

    log.info('Search: "%s" in %s | sites=%s | remote=%s', query, location, sites, remote_only)

    kwargs = {
        "site_name": sites,
        "search_term": query,
        "location": location,
        "results_wanted": results_per_site,
        "hours_old": hours_old,
        "description_format": "markdown",
        "country_indeed": country_indeed,
        "verbose": 2,
    }

    if remote_only:
        kwargs["is_remote"] = True

    if proxy_config:
        kwargs["proxies"] = [proxy_config["jobspy"]]

    if "linkedin" in sites:
        kwargs["linkedin_fetch_description"] = True

    try:
        df = _scrape_with_retry(kwargs)
    except Exception as e:
        log.error("JobSpy search failed: %s", e)
        return {"error": str(e), "total": 0, "new": 0, "existing": 0}

    total = len(df)
    log.info("JobSpy returned %d results", total)

    if total == 0:
        return {"total": 0, "new": 0, "existing": 0}

    if "site" in df.columns:
        site_counts = df["site"].value_counts()
        for site, count in site_counts.items():
            log.info("  %s: %d", site, count)

    conn = init_db()
    new, existing = store_jobspy_results(conn, df, query, search_query=query)
    log.info("Stored: %d new, %d already in DB", new, existing)

    db_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE detail_scraped_at IS NULL").fetchone()[0]
    log.info("DB total: %d jobs, %d pending detail scrape", db_total, pending)

    return {"total": total, "new": new, "existing": existing}


# -- Full crawl (all queries x all locations) --------------------------------


def _full_crawl(
    search_cfg: dict,
    tiers: list[int] | None = None,
    locations: list[str] | None = None,
    sites: list[str] | None = None,
    results_per_site: int = 100,
    hours_old: int = 72,
    proxy: str | None = None,
    max_retries: int = 2,
    salary_pref: dict | None = None,
) -> dict:
    """Run all search queries from search config across all locations."""
    if sites is None:
        sites = ["indeed", "linkedin", "zip_recruiter"]

    # Build search combinations from config
    queries = search_cfg.get("queries", [])
    locs = search_cfg.get("locations", [])
    defaults = search_cfg.get("defaults", {})
    glassdoor_map = search_cfg.get("glassdoor_location_map", {})
    accept_locs, reject_locs = _load_location_config(search_cfg)
    exclude_titles = search_cfg.get("exclude_titles") or []

    if tiers:
        queries = [q for q in queries if q.get("tier") in tiers]
    if locations:
        locs = [loc for loc in locs if loc.get("label") in locations]

    searches = []
    from applypilot.geography import normalize_location
    for q in queries:
        for loc in locs:
            place=normalize_location(loc,search_cfg) if 'provider_country' not in loc else loc
            searches.append(
                {
                    "query": q["query"],
                    "location": place["location"],
                    "remote": place.get("remote", False),
                    "country":place.get('country'),"provider_country":place.get('provider_country'),
                    "city":place.get('city'),"state":place.get('state'),
                    "tier": q.get("tier", 0),
                }
            )

    proxy_config = parse_proxy(proxy) if proxy else None

    log.info("Full crawl: %d search combinations", len(searches))
    log.info("Sites: %s | Results/site: %d | Hours old: %d", ", ".join(sites), results_per_site, hours_old)

    # Ensure DB schema is ready
    budget = int(search_cfg.get("board_search_budget", 0) or 0)
    if budget > 0:
        searches = [searches[q*len(locs)+l] for l in range(len(locs)) for q in range(len(queries))]
    from applypilot.geography import board_scope
    searches=[s for s in searches if board_scope(s,sites)[0]]
    if budget > 0:
        from applypilot.pipeline_store import target_offset
        offset = int(search_cfg.get("board_search_offset", target_offset(config.APP_DIR, sites)) or 0) % max(1, len(searches))
        searches = (searches[offset:] + searches[:offset])[:budget]
    init_db()

    total_new = 0
    total_existing = 0
    total_errors = 0
    completed = 0

    for s in searches:
        if limit_reached():break
        result = _run_one_search(
            s,
            sites,
            results_per_site,
            hours_old,
            proxy_config,
            defaults,
            max_retries,
            accept_locs,
            reject_locs,
            glassdoor_map,
            exclude_titles=exclude_titles,
            salary_pref=salary_pref,
        )
        completed += 1
        total_new += result["new"]
        total_existing += result["existing"]
        total_errors += result["errors"]

        if completed % 5 == 0 or completed == len(searches):
            log.info(
                "Progress: %d/%d queries done (%d new, %d dupes, %d errors)",
                completed,
                len(searches),
                total_new,
                total_existing,
                total_errors,
            )

    # Final stats
    conn = get_connection()
    db_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    log.info(
        "Full crawl complete: %d new | %d dupes | %d errors | %d total in DB",
        total_new,
        total_existing,
        total_errors,
        db_total,
    )

    from applypilot.pipeline_store import source_health
    source_health(conn, sites[0] if len(sites) == 1 else "JobSpy", {"status": "ok", "errors": total_errors, "found": total_new+total_existing, "queries": len(searches)}, total_new, total_existing)
    return {
        "new": total_new,
        "existing": total_existing,
        "errors": total_errors,
        "db_total": db_total,
        "queries": len(searches),
    }


# -- Public entry point ------------------------------------------------------


def run_discovery(cfg: dict | None = None) -> dict:
    """Main entry point for JobSpy-based job discovery.

    Loads search queries and locations from the user's search config YAML,
    then runs a full crawl across all configured job boards.

    Args:
        cfg: Override the search configuration dict. If None, loads from
             the user's searches.yaml file.

    Returns:
        Dict with stats: new, existing, errors, db_total, queries.
    """
    if cfg is None:
        cfg = config.load_search_config()

    from applypilot.discovery import nz
    if nz.active():
        return nz.run_provider("board")

    cfg = _normalize_search_cfg(cfg)
    from applypilot.country_context import guard_discovery
    guard_discovery(cfg)
    if cfg.get('_run_scope'):
        from applypilot.discovery_scope import budget_active,run_connection
        if not budget_active():raise ValueError('Use the captured pipeline budget for scoped discovery')
        try:salary_pref=load_salary_preference(config.load_profile())
        except Exception:salary_pref=None
        accept,reject=_load_location_config(cfg);defaults=cfg.get('defaults',{})
        combined={'new':0,'existing':0,'errors':0,'queries':0,'db_total':0}
        for target in cfg.get('_planned_board_searches',[]):
            if limit_reached():break
            settings=(cfg.get('board_overrides') or {}).get(target['board'],{})
            result=_run_one_search(target,[target['board']],min(defaults['results_per_site'],int(settings.get('results_per_site') or defaults['results_per_site'])),
                int(settings.get('hours_old') or defaults.get('hours_old',72)),parse_proxy(cfg['proxy']) if cfg.get('proxy') else None,
                defaults,1,accept,reject,cfg.get('glassdoor_location_map',{}),exclude_titles=cfg.get('exclude_titles',[]),salary_pref=salary_pref)
            for key in ('new','existing','errors'):combined[key]+=result.get(key,0)
            combined['queries']+=1
            from applypilot.pipeline_store import source_health
            source_health(run_connection(),target['board'],{'status':'ok','found':result.get('total',0),'errors':result.get('errors',0),'targets':1},result['new'],result['existing'])
        combined['db_total']=run_connection().execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        return combined

    if not cfg:
        log.warning("No search configuration found. Run `applypilot init` to create one.")
        return {"new": 0, "existing": 0, "errors": 0, "db_total": 0, "queries": 0}
    _load_scraper()  # fail once, before expanding board/query combinations

    proxy = cfg.get("proxy")
    sites = list(cfg.get("sites") or [])
    defaults = cfg.get("defaults", {}) or {}
    results_per_site = defaults.get("results_per_site", 100)
    hours_old = defaults.get("hours_old", 72)
    tiers = cfg.get("tiers")
    locations = cfg.get("location_labels")
    board_overrides = cfg.get("board_overrides") or {}
    try:
        salary_pref = load_salary_preference(config.load_profile())
    except Exception:
        salary_pref = None

    if not sites:
        return _full_crawl(
            search_cfg=cfg,
            tiers=tiers,
            locations=locations,
            sites=sites,
            results_per_site=results_per_site,
            hours_old=hours_old,
            proxy=proxy,
            salary_pref=salary_pref,
        )

    combined = {"new": 0, "existing": 0, "errors": 0, "db_total": 0, "queries": 0}
    for board_index, site in enumerate(sites):
        if limit_reached():break
        total_budget = int(cfg.get("board_search_budget", 0) or 0)
        if total_budget and combined["queries"] >= total_budget:
            break
        override = board_overrides.get(str(site or "").strip().lower(), {})
        site_cfg = dict(cfg)
        if total_budget:
            remaining = total_budget - combined["queries"]
            site_cfg["board_search_budget"] = max(1, remaining // (len(sites)-board_index))
        site_cfg["sites"] = [site]
        if "tiers" in override:
            site_cfg["tiers"] = override.get("tiers")
        if "location_labels" in override:
            site_cfg["location_labels"] = override.get("location_labels")
        if "queries" in override:
            keep_queries = {str(q).strip() for q in (override.get("queries") or []) if str(q).strip()}
            if keep_queries:
                site_cfg["queries"] = [q for q in (cfg.get("queries") or []) if str(q.get("query") or "").strip() in keep_queries]
        site_defaults = dict(defaults)
        if "results_per_site" in override:
            site_defaults["results_per_site"] = override.get("results_per_site")
        if "hours_old" in override:
            site_defaults["hours_old"] = override.get("hours_old")
        site_cfg["defaults"] = site_defaults
        result = _full_crawl(
            search_cfg=site_cfg,
            tiers=site_cfg.get("tiers"),
            locations=site_cfg.get("location_labels"),
            sites=[site],
            results_per_site=site_defaults.get("results_per_site", 100),
            hours_old=site_defaults.get("hours_old", 72),
            proxy=proxy,
            salary_pref=salary_pref,
        )
        combined["new"] += result.get("new", 0)
        combined["existing"] += result.get("existing", 0)
        combined["errors"] += result.get("errors", 0)
        combined["queries"] += result.get("queries", 0)
        combined["db_total"] = result.get("db_total", combined["db_total"])

    return combined
