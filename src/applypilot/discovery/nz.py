"""Phase 2 NZ discovery services, reusing existing campaign/provider adapters.
No accreditation, document generation or application submission is enabled here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote_plus, urlsplit

import yaml

from applypilot import country_context as country, setup_workspace as setup
from applypilot import source_access as access
from applypilot.discovery import observations

CAMPAIGN_ID = "nz-job-search"
_BOARD_URLS = {"indeed": "https://nz.indeed.com", "linkedin": "https://www.linkedin.com", "glassdoor": "https://www.glassdoor.co.nz",
               "google": "https://www.google.com", "zip_recruiter": "https://www.ziprecruiter.com"}


def active():
    return country.current() is not None and country.current().country_code == "NZ"


def preset():
    from applypilot.config import CONFIG_DIR
    return yaml.safe_load((CONFIG_DIR / "searches.nz.example.yaml").read_text(encoding="utf-8"))


def effective(root, campaign):
    """Replace sensitive sections with NZ defaults; never inherit UK filters."""
    value = preset() | campaign
    value["nz_workflow_enabled"] = country.nz_enabled(root)
    value["sites"] = list(value.get("boards") or [])
    value["smart_sources_disabled"] = not bool(value.get("smart_sites"))
    value["defaults"] = preset()["defaults"] | (campaign.get("defaults") or {}) | {"country_indeed": "new zealand"}
    return value


def revision(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def save_campaign(conn, app_dir, data):
    """Lossless edits to a single block-style NZ campaign; no UK reserialization."""
    root = setup.read_searches_dict(app_dir)
    country.require_enabled("NZ", root)
    if data.get("expected_revision") != revision(root):
        raise ValueError("Search configuration changed; reload before saving the NZ campaign")
    proposed = data.get("campaign") or preset()
    if not isinstance(proposed, dict) or proposed.get("id") != CAMPAIGN_ID or country.normalize_country(proposed.get("country_code"), optional=False) != "NZ":
        raise ValueError("Use the dedicated NZ campaign identifier and country")
    campaign = preset() | proposed
    campaign.pop("expected_revision", None)
    if campaign.get("country") != "New Zealand" or campaign.get("location_filter_mode") != "selected":
        raise ValueError("NZ campaigns require New Zealand and explicit selected-location filtering")
    from applypilot.geography import locations
    if any(p.get("provider_country") != "new zealand" for p in locations(campaign)):
        raise ValueError("NZ campaign locations must be in New Zealand")
    campaign["sites"] = list(campaign.get("boards") or [])
    campaign["smart_sources_disabled"] = not bool(campaign.get("smart_sites"))
    with country.operation(country.resolve(app_dir, country_code="NZ", config=effective(root, campaign))):
        setup._validate_searches_dict(campaign, app_dir)
    if any(not isinstance(campaign.get(k), list) for k in ("boards", "smart_sites", "workday_employers")):
        raise ValueError("NZ source selections must be explicit lists (empty means none)")
    if any(not isinstance(v, str) or not v.strip() for k in ("boards", "smart_sites", "workday_employers") for v in campaign[k]):
        raise ValueError("NZ source selections must contain source identifiers/names")
    for key in ("board_search_budget", "smart_target_budget", "workday_target_budget"):
        if isinstance(campaign.get(key), bool) or not isinstance(campaign.get(key), int) or not 1 <= campaign[key] <= 10000:
            raise ValueError("NZ run budgets must be positive bounded integers")
    path = setup.searches_path(app_dir)
    from applypilot.command_center import record_legacy_compatibility
    record_legacy_compatibility(conn,app_dir)
    original_bytes = path.read_bytes() if path.exists() else b""
    original = original_bytes.decode("utf-8")
    newline = "\r\n" if "\r\n" in original else "\n"
    node = yaml.compose(original) if original.strip() else None
    entry = yaml.safe_dump([campaign], sort_keys=False, allow_unicode=True).replace("\n", newline)
    if node is None:
        result = "campaigns:" + newline + entry
    else:
        if not isinstance(node, yaml.MappingNode):
            raise ValueError("Search configuration must be a mapping")
        candidates = [value for key, value in node.value if key.value == "campaigns"]
        if len(candidates) > 1:
            raise ValueError("Duplicate campaigns keys require manual configuration review")
        if not candidates:
            result = original + ("" if original.endswith("\n") else newline) + newline + "campaigns:" + newline + entry
        else:
            sequence = candidates[0]
            if not isinstance(sequence, yaml.SequenceNode) or (sequence.flow_style and sequence.value):
                raise ValueError("NZ scoped editing requires a block-style campaigns list (or an empty list); no UK content was rewritten")
            matches = []
            for item in sequence.value:
                if not isinstance(item, yaml.MappingNode):
                    raise ValueError("Campaigns must be mappings")
                ident = next((value.value for key, value in item.value if key.value == "id"), None)
                if ident == CAMPAIGN_ID:
                    code = next((value.value for key, value in item.value if key.value == "country_code"), None)
                    if code != "NZ":
                        raise ValueError("The NZ campaign identifier is already used by another context")
                    matches.append(item)
            if len(matches) > 1:
                raise ValueError("Duplicate NZ campaign identifiers require review")
            indent = max(0, sequence.value[0].start_mark.column - 2) if sequence.value else 0
            replacement = "".join(" " * indent + line if line.strip() else line for line in entry.splitlines(keepends=True))
            if matches:
                item = matches[0]
                start = original.rfind("\n", 0, item.start_mark.index) + 1
                end = item.end_mark.index
                line_start = original.rfind("\n", 0, end) + 1
                if not original[line_start:end].strip():
                    end = line_start
                result = original[:start] + replacement + original[end:]
            elif sequence.flow_style:
                result = original[:sequence.start_mark.index] + newline + replacement + original[sequence.end_mark.index:]
            else:
                position = sequence.end_mark.index
                line_start = original.rfind("\n", 0, position) + 1
                if not original[line_start:position].strip():
                    position = line_start
                result = original[:position] + ("" if original[:position].endswith("\n") else newline) + replacement + original[position:]
    parsed = yaml.safe_load(result) or {}
    # Preserve list position when editing; compare all other root/campaign values.
    if {k:v for k,v in parsed.items() if k != "campaigns"} != {k:v for k,v in root.items() if k != "campaigns"} or [c for c in parsed.get("campaigns", []) if c.get("id") != CAMPAIGN_ID] != [c for c in root.get("campaigns", []) if c.get("id") != CAMPAIGN_ID]:
        raise ValueError("Scoped campaign edit could not preserve the existing configuration")
    if sum(c.get("id") == CAMPAIGN_ID for c in parsed.get("campaigns", [])) != 1:
        raise ValueError("Scoped campaign edit did not produce one NZ campaign")
    if (path.read_bytes() if path.exists() else b"") != original_bytes:
        raise ValueError("Search configuration changed during the NZ edit; retry")
    setup._atomic_write_bytes(path, result.encode("utf-8"))
    from applypilot.command_center import event
    event(conn, None, "campaign", "NZ discovery campaign saved; existing UK configuration retained", payload={"country_code": "NZ", "campaign_id": CAMPAIGN_ID})
    conn.commit()
    return {"campaign": campaign, "config_revision": revision(parsed)}


def board_source(name, record=None):
    return {"id": "board-" + name, "name": name, "url": _BOARD_URLS[name], "provider_kind": "board"} | (record or {})


def workday_source(key, item):
    return dict(item, id="workday-" + key, _nz_registry_id=key, url=item["base_url"], provider_kind="workday")


def sources(app_dir):
    from applypilot.config import load_sites_config
    from applypilot.source_registry import _workspace_catalog, employers
    output = [dict(s, provider_kind="direct") for s in load_sites_config(app_dir, include_all=True).get("sites", []) if "NZ" in s.get("countries", [])]
    boards = _workspace_catalog(app_dir).get("board_access", {})
    if not isinstance(boards, dict):
        raise ValueError("Workspace board_access must be an object")
    output += [board_source(name, boards.get(name, {})) for name in _BOARD_URLS]
    output += [workday_source(key, item) for key, item in employers(app_dir, include_disabled=True, include_all=True).items() if "NZ" in item.get("countries", [])]
    if len({s["id"] for s in output}) != len(output):
        raise ValueError("NZ source identifiers conflict with reserved board/workday identifiers")
    return output


def source(app_dir, ident):
    matches = [s for s in sources(app_dir) if ident in {s["id"], s["name"], s.get("_nz_registry_id")}]
    if len(matches) != 1:
        raise ValueError("Choose one registered NZ source")
    return matches[0]


def matrix(app_dir, config):
    selected = set(config.get("boards") or []) | set(config.get("smart_sites") or []) | set(config.get("workday_employers") or [])
    output = []
    for item in sources(app_dir):
        method = {"board": "jobspy", "workday": "workday_cxs"}.get(item["provider_kind"], "http")
        reason = access.readiness(item, method=method)
        chosen = bool(selected & {item["id"], item["name"], item.get("_nz_registry_id")})
        output.append({"id": item["id"], "name": item["name"], "kind": item["provider_kind"], "selected": chosen,
                       "ready": reason is None, "reason": reason, "source": item})
    known = {value for item in sources(app_dir) for value in (item["id"], item["name"], item.get("_nz_registry_id"))}
    output += [{"id": name, "name": name, "selected": True, "ready": False, "reason": "Selected source is not registered for NZ", "kind": "unknown"} for name in sorted(selected - known)]
    ready = [row for row in output if row["selected"] and row["ready"]]
    return {"sources": output, "automatic_discovery_available": bool(ready), "mode": "automatic" if ready else "manual",
            "note": "Readiness records permissions and observed samples, not sponsorship or employer accreditation."}


def require_source(ident, *, method="http"):
    from applypilot.config import APP_DIR
    context = country.current()
    if not context or context.country_code != "NZ":
        raise access.SourceAccessError("NZ source retrieval requires an explicit NZ operation")
    country.require_enabled("NZ", context.config)
    country.require_enabled("NZ",setup.read_searches_dict(Path(context.workspace)))
    item = source(Path(context.workspace), ident)
    access.require(item, method=method)
    return item


def _record_validation(app_dir, item, record):
    from applypilot.source_registry import _workspace_catalog
    if access.fingerprint(source(app_dir, item["id"])) != access.fingerprint(item):
        raise ValueError("Source changed during validation; reload and retry")
    kind = item["provider_kind"]
    path = Path(app_dir) / ("employers.yaml" if kind == "workday" else "sites.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}
    if kind == "workday":
        entry = data.setdefault("employers", {}).get(item["_nz_registry_id"])
    elif kind == "board":
        entry = data.setdefault("board_access", {}).get(item["name"])
    else:
        entry = next((s for s in data.get("sites", []) if s.get("id") == item["id"]), None)
    if entry is None:
        raise ValueError("Save a workspace-owned NZ source before recording validation")
    previous = entry.get("_nz_validation") or {}
    record["last_successful_check"] = record["checked_at"] if record["status"] == "validated" else previous.get("last_successful_check")
    entry["_nz_validation"] = record
    setup._atomic_write_text(path, yaml.safe_dump(data, sort_keys=False))


def validate_source(app_dir, ident, query="Software Developer", location="Auckland, New Zealand"):
    country.require_enabled("NZ", setup.read_searches_dict(app_dir))
    item = source(app_dir, ident)
    method = {"board": "jobspy", "workday": "workday_cxs"}.get(item["provider_kind"], "http")
    access.require(item, method=method, validation=False, storage=False)
    record = {"fingerprint": access.fingerprint(item), "method": method, "checked_at": access.now(), "status": "unavailable", "samples": 0}
    try:
        with access.request_scope():
            if method == "jobspy":
                from applypilot.discovery.jobspy import _scrape_with_retry
                access.check_url(item, item["url"], method=method, validation=False, storage=False)
                access.throttle(item)
                frame = _scrape_with_retry({"site_name": [item["name"]], "search_term": query, "location": location,
                    "country_indeed": "new zealand", "results_wanted": 5, "hours_old": 168, "description_format": "markdown", "verbose": 0}, max_retries=0)
                if frame.attrs.get("errors"):
                    raise access.SourceAccessError("Provider reported a request failure; zero results are not verified")
                samples = [{"location": observations.location_text(row.get("location")), "title": row.get("title"), "url": row.get("job_url")} for row in frame.to_dict("records")]
            elif method == "workday_cxs":
                result = workday_payload(item, query=query, validation=False)
                if not isinstance(result.get("jobPostings"), list) or not isinstance(result.get("total"), int):
                    raise ValueError("Unrecognized Workday response")
                samples = []
                from applypilot.discovery.workday import strip_html
                for j in result["jobPostings"][:3]:
                    path = j.get("externalPath") or ""
                    if not path.startswith("/job/") or ".." in path or "?" in path:
                        continue
                    detail = workday_payload(item, path=path, validation=False).get("jobPostingInfo", {})
                    samples.append({"title": j.get("title"), "location": j.get("locationsText"),
                        "url": item["url"] + "/" + item["site_id"] + path, "application_url": detail.get("externalUrl"),
                        "description": strip_html(detail.get("jobDescription") or "")})
            else:
                from applypilot.source_registry import preview
                samples = preview(app_dir, item, query, location)["jobs"]
        valid = []
        from applypilot.source_registry import validate_url
        for j in samples:
            if not j.get("title") or len(observations.text(j.get("description"))) < 200 or observations.advertised_country(j, observations.location_text(j.get("location")))[0] != "NZ":
                continue
            validate_url(j.get("url") or "")
            if j.get("application_url"):
                validate_url(j["application_url"])
            valid.append(j)
        record.update(status="validated" if valid else "no_samples", samples=len(samples), nz_samples=len(valid))
    except Exception:
        record["detail"] = "Read-only source validation unavailable; check permission, network, source schema and NZ samples"
    _record_validation(app_dir, item, record)
    return record


def save_board_access(app_dir, data):
    from applypilot.source_registry import _workspace_catalog
    name = data.get("name")
    if name not in _BOARD_URLS:
        raise ValueError("Unknown supported board adapter")
    metadata = access.validate_metadata(data)
    if metadata.get("countries") != ["NZ"]:
        raise ValueError("Use NZ-scoped board access metadata")
    catalog = _workspace_catalog(app_dir)
    previous = catalog.setdefault("board_access", {}).get(name, {})
    catalog["board_access"][name] = {k:v for k,v in previous.items() if k == "_nz_validation"} | metadata
    setup._atomic_write_text(Path(app_dir) / "sites.yaml", yaml.safe_dump(catalog, sort_keys=False))
    return board_source(name, catalog["board_access"][name])


def save_source(app_dir, data):
    """NZ edits cannot override a packaged or UK source identifier/name."""
    country.require_enabled("NZ", setup.read_searches_dict(app_dir))
    from applypilot import source_registry as registry
    from applypilot.config import load_sites_config
    kind = data.get("kind")
    item = data.get("source")
    if not isinstance(item, dict) or item.get("countries") != ["NZ"]:
        raise ValueError("Provide a source object scoped exclusively to NZ")
    if kind == "board":
        return save_board_access(app_dir, item)
    if kind not in {"direct", "workday"}:
        raise ValueError("Choose a direct, Workday or existing board source")
    known = load_sites_config(app_dir, include_all=True).get("sites", []) if kind == "direct" else [dict(v, id=k) for k,v in registry.employers(app_dir, include_all=True, include_disabled=True).items()]
    for previous in known:
        if previous.get("id") == item.get("id") or previous["name"].casefold() == str(item.get("name") or "").casefold():
            if previous.get("countries") != ["NZ"] or previous.get("origin") != "workspace":
                raise ValueError("Use a distinct NZ source identifier/name; UK and packaged sources are protected")
    if kind == "direct":
        if str(item.get("id") or "").startswith(("board-", "workday-")):
            raise ValueError("Source identifier uses a reserved provider prefix")
        return registry.save_source(app_dir, item)
    return registry.save_employer(app_dir, item)


def workday_payload(item, *, query=None, path=None, limit=10, offset=0, validation=True):
    """Existing CXS read operations through permission/public-host/request bounds."""
    import httpx
    base = item["base_url"].rstrip("/")
    parsed=urlsplit(base)
    if not (parsed.hostname or "").lower().endswith(".myworkdayjobs.com") or parsed.path or parsed.query or parsed.fragment or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,120}",str(item.get(k) or "")) for k in ("tenant","site_id")):
        raise access.SourceAccessError("NZ Workday retrieval requires an actual tenant portal and bounded CXS identifiers")
    if path and (not isinstance(path,str) or not path.startswith("/job/") or ".." in path or "?" in path or "#" in path):
        raise access.SourceAccessError("Use a read-only Workday job-detail path")
    url = f"{base}/wday/cxs/{item['tenant']}/{item['site_id']}" + (path if path else "/jobs")
    access.check_url(item, url, method="workday_cxs", validation=validation, storage=validation)
    access.throttle(item)
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        params = {"json": {"appliedFacets": {}, "limit": min(20, limit), "offset": offset, "searchText": query or ""}} if not path else {}
        with client.stream("GET" if path else "POST", url, **params) as response:
            if response.status_code != 200:
                raise access.SourceAccessError("Workday read-only operation failed; availability is unknown")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 2_000_000:
                    raise access.SourceAccessError("Workday response exceeds 2 MB")
                chunks.append(chunk)
            result = json.loads(b"".join(chunks))
    if not isinstance(result, dict):
        raise access.SourceAccessError("Workday did not return an object")
    return result


def direct_target(name, url):
    item = require_source(name)
    response = access.read_http(item, url)
    if response["http_status"] != 200:
        raise access.SourceAccessError(f"Source returned HTTP {response['http_status']}; not a verified empty result")
    jobs = observations.structured_jobs(response["html"], response["url"])
    return {"name": name, "status": "PASS" if jobs else "PARTIAL", "strategy": "json_ld_no_ai", "jobs": jobs,
            "total": len(jobs), "titles": sum(bool(j.get("title")) for j in jobs), "error": None if jobs else "No structured jobs; use manual capture"}


def capture(conn, app_dir, data):
    root = setup.read_searches_dict(app_dir)
    if data.get("campaign_id") not in {None, CAMPAIGN_ID}:
        raise ValueError("Manual NZ vacancies belong to the dedicated NZ campaign")
    campaign = next((c for c in root.get("campaigns", []) if c.get("id") == CAMPAIGN_ID), preset())
    context = country.resolve(app_dir, country_code="NZ", config=effective(root, campaign), campaign_id=CAMPAIGN_ID)
    url = str(data.get("url") or "").strip()
    from applypilot.source_registry import validate_url
    validate_url(url)  # syntax/public literal only; no DNS or HTTP for manual capture
    description = str(data.get("job_text") or "").strip()
    title = str(data.get("title") or "").strip()
    if not 1 <= len(title) <= 300 or len(description) > 50000:
        raise ValueError("Provide a title and job text of at most 50,000 characters")
    reference_only = data.get("reference_only", True)
    if not isinstance(reference_only, bool):
        raise ValueError("reference_only must be true/false")
    host = urlsplit(url).hostname or ""
    if reference_only and description:
        raise ValueError("Reference-only capture does not retain job-board descriptions; use your own notes separately")
    if not reference_only and (access.restricted_host(host) or data.get("storage_allowed") is not True or not str(data.get("permission_reference") or "").strip()):
        raise ValueError("Job text requires a permitted employer/user-owned source and explicit storage permission; restricted services are reference-only")
    item = {"id": "manual-nz", "name": "Manual NZ", "url": url, "provider_kind": "manual",
        "access": {"storage_allowed": not reference_only, "ai_use_allowed": not reference_only and data.get("ai_use_allowed") is True,
                   "permission_reference": str(data.get("permission_reference") or "")[:2000]}}
    job = {"url": url, "title": title, "company": str(data.get("company") or "")[:500], "description": description,
           "application_url": data.get("application_url"), "location": data.get("location"), "salary": data.get("salary")}
    with country.operation(context):
        new, duplicates = observations.store(conn, [job], item, method="manual", reference_only=reference_only)
    return {"new": new, "duplicates": duplicates, "reference_only": reference_only}


def jobs(conn):
    return [dict(row) | {"vacancy_metadata": observations.metadata(dict(row))} for row in conn.execute("SELECT rowid AS id,* FROM jobs WHERE country_code='NZ' ORDER BY discovered_at DESC")]


def preview(conn, app_dir, config=None):
    root = setup.read_searches_dict(app_dir)
    country.require_enabled("NZ", root)
    campaign = next((c for c in root.get("campaigns", []) if c.get("id") == CAMPAIGN_ID), None)
    cfg = config if config and config.get("country_code") == "NZ" else effective(root, campaign or preset())
    readiness = matrix(app_dir, cfg)
    from applypilot.geography import locations
    selected = [s for s in readiness["sources"] if s["selected"]]
    roles, places = len(cfg.get("queries", [])), len(locations(cfg))
    counts = {kind: sum(s["kind"] == kind and s["ready"] for s in selected) for kind in ("board", "direct", "workday")}
    board_count = counts["board"] * roles * places
    direct_count = counts["direct"] * roles * places
    return {"config": cfg, "config_revision": revision(root), "campaign_installed": campaign is not None,
            "readiness": readiness, "jobs": jobs(conn), "queue": {}, "missing_inputs": [],
            "board_searches": board_count, "board_window": min(board_count, cfg["board_search_budget"]),
            "smart_targets": direct_count, "smart_window": min(direct_count, cfg["smart_target_budget"]),
            "workday_roles": [q["query"] for q in cfg["queries"]] if counts["workday"] else [],
            "geography": {"locations": locations(cfg), "filter_mode": "selected"},
            "preparation_available": False, "automatic_coverage_gate": "available" if readiness["automatic_discovery_available"] else "blocked",
            "note": "Manual discovery remains available. No NZ source entitlement or live reliability has been assumed."}


def run_provider(kind):
    """Bounded NZ branch of each existing discovery entry point; UK paths stay intact."""
    context = country.current()
    if not active() or not context.campaign_id:
        raise ValueError("Choose an explicit NZ campaign")
    country.require_enabled("NZ", context.config)
    cfg = context.config
    from applypilot.database import init_db
    from applypilot.pipeline_store import source_health
    from applypilot.geography import locations, SelectedLocations
    conn = init_db(Path(context.workspace) / "applypilot.db")
    selected = [s for s in matrix(Path(context.workspace), cfg)["sources"] if s["kind"] == kind and s["selected"]]
    result = {"new": 0, "existing": 0, "found": 0, "errors": 0, "queries": 0, "partial": 0,
              "status": "skipped" if not selected else "ok", "restricted_sources": []}
    budget = cfg[{"board": "board_search_budget", "workday": "workday_target_budget", "direct": "smart_target_budget"}[kind]]
    matcher = SelectedLocations(cfg)
    preference = context.preferences.get("search_preferences", {}).get("compensation") or cfg.get("compensation")
    # Interleave source and city across roles to avoid spending the cap on one portal.
    targets = [(s, q["query"], place) for q in cfg.get("queries", []) for place in locations(cfg) for s in selected]
    seen_targets = set()
    with access.request_scope():
        for row, query, place in targets:
            if not row["ready"]:
                if row["id"] not in {v["id"] for v in result["restricted_sources"]}:
                    result["restricted_sources"].append({"id": row["id"], "reason": row["reason"]})
                result["status"] = "partial"
                continue
            item = source(Path(context.workspace), row["id"])  # recheck edits at execution
            if result["queries"] >= budget:
                break
            method = {"board": "jobspy", "workday": "workday_cxs", "direct": "http"}[kind]
            try:
                access.require(item, method=method)
                if kind == "direct":
                    target = item["url"].format(query=quote_plus(query), query_encoded=quote_plus(query),
                        location_encoded=quote_plus(place["location"]), country_encoded="New+Zealand",
                        state_encoded=quote_plus(place.get("state") or ""), city_encoded=quote_plus(place.get("city") or ""))
                    if target in seen_targets:
                        continue
                    seen_targets.add(target)
                    result["queries"] += 1
                    response = direct_target(item["id"], target)
                    batch = response["jobs"]
                    if response["status"] != "PASS":
                        result["partial"] += 1
                    from applypilot.discovery.smartextract import _store_jobs_filtered
                    keep = [j for j in batch if matcher.matches(observations.location_text(j.get("location")), j.get("is_remote"))]
                    new, duplicates = _store_jobs_filtered(conn, keep, item["id"], "json_ld_no_ai", matcher, [], query, preference)
                elif kind == "workday":
                    # CXS search is global per tenant, so don't duplicate it per city.
                    if (item["id"], query) in seen_targets:
                        continue
                    seen_targets.add((item["id"], query))
                    result["queries"] += 1
                    from applypilot.discovery.workday import workday_search, _fetch_one_detail, store_results
                    data = workday_search(item, query, limit=20)
                    postings = data.get("jobPostings")
                    if not isinstance(postings, list) or not isinstance(data.get("total"), int):
                        raise ValueError("Unrecognized Workday search response")
                    if data["total"] > len(postings):
                        result["partial"] += 1  # bounded sample; never claim complete pagination
                    batch = []
                    for j in postings:
                        if not matcher.matches(j.get("locationsText")):
                            continue
                        job = {"title": j.get("title"), "location": j.get("locationsText"), "posted": j.get("postedOn"),
                               "external_path": j.get("externalPath"), "employer_key": item["_nz_registry_id"], "employer_name": item["name"]}
                        batch.append(_fetch_one_detail(item, job))
                    result["partial"] += sum(bool(j.get("detail_error")) for j in batch)
                    new, duplicates = store_results(conn, batch, {item["_nz_registry_id"]: item}, search_query=query)
                else:
                    # Kept fail-closed by readiness until JobSpy has bounded transport.
                    raise access.SourceAccessError("NZ board transport is unavailable")
                result["found"] += len(batch)
                result["new"] += new
                result["existing"] += duplicates
                source_health(conn, "NZ " + item["name"], {"status": "partial" if result["partial"] else "ok", "found": len(batch), "targets": 1}, new, duplicates)
            except Exception:
                result["errors"] += 1
                source_health(conn, "NZ " + item["name"], {"status": "partial", "errors": 1, "targets": 1}, 0, 0)
    if result["errors"] or result["partial"] or result["restricted_sources"]:
        result["status"] = "partial"
    return result


def check_listing(conn, app_dir, job):
    """Separate expiry/listing/application observations; no prohibited fallback fetch."""
    country.resolve(app_dir, job=job, country_code="NZ")
    meta = observations.metadata(job)
    checked = access.now()
    result = {"checked_at": checked, "status": "unavailable", "destinations": [], "source_valid_through": meta.get("source_valid_through")}
    expiry = meta.get("source_valid_through")
    instant = access.moment(expiry)
    expired = bool(expiry and (instant < datetime.now(timezone.utc) if instant else expiry[:10] < checked[:10]))
    try:
        item = source(app_dir, meta.get("source_id"))
        if meta.get("reference_only") or item.get("provider_kind") == "board":
            raise access.SourceAccessError("Reference-only/board records require manual availability checks")
        if item.get("provider_kind") == "workday":
            raise access.SourceAccessError("Workday destination HTML permission is not implied by CXS access; verify manually")
        with access.request_scope():
            for url in dict.fromkeys(filter(None, [meta.get("original_listing_url"), meta.get("application_url_original")])):
                response = access.read_http(item, url)
                code = response["http_status"]
                state = "closed" if code in {404, 410} else "reachable" if code == 200 else "unavailable"
                if code == 200:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(response["html"], "html.parser")
                    for node in soup.select("script,style,noscript"):
                        node.decompose()
                    body = soup.get_text(" ", strip=True).casefold()
                    if any(term in body for term in ("this job is no longer available", "this vacancy is closed", "applications for this vacancy have closed")):
                        state = "possibly_closed"
                result["destinations"].append({"url": url, "resolved_url": response["url"], "status": state, "http_status": code})
        states = [d["status"] for d in result["destinations"]]
        result["status"] = "closed" if "closed" in states else "possibly_closed" if "possibly_closed" in states else "reachable" if states and all(s == "reachable" for s in states) else "unavailable"
    except Exception:
        # Persist truthful unknown even when a recheck fails; do not erase old success.
        result["detail"] = "No permitted automated destination check; verify the original links manually"
    if expired:
        result["status"] = "source_expired"
    result.setdefault("detail", "Source expiry has passed" if expired else "Reachability is not proof that applications remain open")
    previous = meta.get("availability") or {}
    result["last_successful_check"] = checked if result["status"] == "reachable" else previous.get("last_successful_check")
    meta["availability"] = result
    conn.execute("UPDATE jobs SET vacancy_metadata=? WHERE url=?", (json.dumps(meta), job["url"]))
    conn.execute("INSERT INTO cc_listing_checks(job_url,status,detail,checked_at) VALUES(?,?,?,?) ON CONFLICT(job_url) DO UPDATE SET status=excluded.status,detail=excluded.detail,checked_at=excluded.checked_at", (job["url"], result["status"], result["detail"], checked))
    from applypilot.command_center import event
    event(conn, job["url"], "listing", result["detail"], state=result["status"], payload=result)
    conn.commit()
    return result
