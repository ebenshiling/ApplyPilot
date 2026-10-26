"""NZ observations on the existing jobs ledger; no historical URL rekeys.

Workflow destination, advert-country evidence and work-rights quotes are distinct.
This module does not score immigration suitability or verify accreditation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from applypilot.discovery.salary_filter import normalize_nz_salary, nz_salary_ok


def text(value, limit=50000):
    if value is None:
        return ""
    result = value.isoformat() if hasattr(value, "isoformat") else str(value)
    return "" if result.casefold() in {"nan", "nat", "none", "<na>"} else result[:limit].strip()


def timestamp(value):
    raw = text(value, 200)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            datetime.fromisoformat(raw)
            return raw, "date"
        except ValueError:
            return None, "unknown"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo:
            return parsed.astimezone(timezone.utc).isoformat(), "instant"
    except ValueError:
        pass
    return None, "unknown"


def location_text(value):
    if isinstance(value, list):
        return "; ".join(filter(None, (location_text(v) for v in value)))
    if isinstance(value, dict):
        address = value.get("address", value)
        if isinstance(address, dict):
            return ", ".join(text(address.get(k), 200) for k in ("addressLocality", "addressRegion", "addressCountry") if address.get(k))
        return text(address, 500)
    return text(value, 500)


def advertised_country(job, location):
    from applypilot.geography import market, contains
    explicit = text(job.get("advertised_country") or job.get("country"), 100)
    matches=[]
    for code,names in (("NZ",("New Zealand","NZ")),("GB",("United Kingdom","UK","GB","England","Scotland","Wales")),("US",("United States","USA")),("AU",("Australia",)),("CA",("Canada",))):
        if any(contains(location,name) for name in names):matches.append(code)
    if len(matches)>1:return None,"multiple_countries"
    if explicit:
        name = market(explicit)
        code={"new zealand":"NZ","uk":"GB","usa":"US","australia":"AU","canada":"CA"}.get(name,explicit.upper())
        if matches and code not in matches:return None,"conflicting_country_evidence"
        return code, "provider_country"
    for code, names in (("NZ", ("New Zealand", "NZ")), ("GB", ("United Kingdom", "UK", "GB", "England", "Scotland", "Wales")),
                        ("US", ("United States", "USA")), ("AU", ("Australia",)), ("CA", ("Canada",))):
        if any(contains(location, name) for name in names):
            return code, "advert_location"
    return None, "unavailable"  # a search city/market alone is not proof


def arrangement(job, location):
    raw = " ".join(text(job.get(k), 300) for k in ("work_arrangement", "work_from_home_type", "remote_type")) + " " + location
    if re.search(r"\bhybrid\b", raw, re.I):
        return "hybrid", raw.strip()
    if re.search(r"\bremote\b|work from home|\bwfh\b", raw, re.I) or text(job.get("is_remote")).casefold() == "true":
        return "remote", raw.strip()
    if re.search(r"on[ -]?site|in[ -]?office", raw, re.I):
        return "onsite", raw.strip()
    return "unknown", raw.strip()


def identity_url(url, source):
    """Only discard known tracking keys; unknown/identity/signature queries stay."""
    parts = urlsplit(url)
    keys = source.get("identity_query_keys") or []
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if keys and any(k in keys for k, _ in pairs):
        pairs = [(k, v) for k, v in pairs if k in keys]
    else:
        pairs = [(k, v) for k, v in pairs if not k.casefold().startswith("utm_") and k.casefold() not in {"gclid", "fbclid"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", urlencode(pairs), ""))


def structured_jobs(html, base_url):
    """Shared deterministic JSON-LD extractor for NZ validation/discovery."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    output = []
    def collect(value):
        if isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, dict):
            if "JobPosting" in str(value.get("@type", "")):
                employer = value.get("hiringOrganization") or {}
                identifier = value.get("identifier") or {}
                salary = value.get("baseSalary") or {}
                amount = salary.get("value") or {} if isinstance(salary, dict) else {}
                output.append({"url": urljoin(base_url, text(value.get("url")) or base_url), "title": value.get("title"),
                    "company": employer.get("name") if isinstance(employer, dict) else text(employer),
                    "description": BeautifulSoup(text(value.get("description")), "html.parser").get_text("\n", strip=True),
                    "location": location_text(value.get("jobLocation")), "date_posted": value.get("datePosted"),
                    "valid_through": value.get("validThrough"), "provider_job_id": identifier.get("value") if isinstance(identifier, dict) else text(identifier),
                    "currency": salary.get("currency") if isinstance(salary, dict) else None,
                    "min_amount": amount.get("minValue", amount.get("value")) if isinstance(amount, dict) else None,
                    "max_amount": amount.get("maxValue") if isinstance(amount, dict) else None,
                    "interval": text(amount.get("unitText")).casefold() if isinstance(amount, dict) else None,
                    "is_remote": "TELECOMMUTE" in str(value.get("jobLocationType", ""))})
            collect(value.get("@graph", []))
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            collect(json.loads(script.get_text()))
        except (ValueError, TypeError, RecursionError):
            continue
    return output[:300]


def normalize(job, source, *, method, query=None, seen_at=None, reference_only=False):
    from applypilot.source_registry import validate_url
    seen_at = seen_at or datetime.now(timezone.utc).isoformat()
    listing = text(job.get("original_listing_url") or job.get("url") or job.get("job_url"), 2000)
    listing = urljoin(source.get("base_url") or source.get("url") or listing, listing)
    validate_url(listing)
    application = text(job.get("application_url") or job.get("job_url_direct") or job.get("apply_url"), 2000)
    application = urljoin(listing, application) if application else None
    if application:
        validate_url(application)
    location = location_text(job.get("location"))
    country, evidence = advertised_country(job, location)
    description = "" if reference_only else text(job.get("full_description") or job.get("description"))
    if not text(job.get("title"), 300):
        raise ValueError("A vacancy title is required")
    digest = hashlib.sha256(description.encode()).hexdigest() if description else None
    posted, precision = timestamp(job.get("date_posted") or job.get("posted_at"))
    expiry, expiry_precision = timestamp(job.get("valid_through") or job.get("validThrough") or job.get("expires_at"))
    salary_fields = {key: text(job.get(key), 100) or None for key in ("currency", "min_amount", "max_amount", "interval", "hours_per_week")}
    salary = normalize_nz_salary(text(job.get("salary"), 3000), salary_fields, source.get("currency_convention"))
    mode, mode_evidence = arrangement(job, location)
    host = urlsplit(application or listing).hostname
    root_host = urlsplit(source.get("url") or "").hostname
    destination = "employer" if source.get("provider_kind") == "direct" and host == root_host else "employer_ats" if host in (source.get("application_hosts") or []) else "board" if source.get("provider_kind") == "board" and host == root_host else "unknown"
    quotes = []
    for line in re.split(r"(?<=[.!?])\s+|\n", description):
        if re.search(r"work rights|right to work|work authori[sz]|sponsorship|overseas applicants|existing.*visa|unrestricted.*(?:work|residen)", line, re.I):
            quotes.append({"quote": line.strip()[:600], "source_hash": digest, "classification": "unassessed"})
        if len(quotes) >= 8:
            break
    metadata = {"version": 1, "source_id": source["id"], "provider_kind": source.get("provider_kind", "direct"),
        "provider_job_id": text(job.get("provider_job_id") or job.get("id") or job.get("job_req_id"), 200) or None,
        "original_listing_url": listing, "application_url_original": application,
        "application_destination_kind": destination, "search_query": query, "search_country": "NZ",
        "advertised_country": country, "country_evidence": evidence,
        "country_status": "confirmed" if country == "NZ" else "out_of_scope" if country else "ambiguous" if evidence in {"multiple_countries","conflicting_country_evidence"} else "unknown",
        "posted_at": posted, "posting_date_precision": precision, "posting_date_raw": text(job.get("date_posted") or job.get("posted_at") or job.get("posted"), 200),
        "source_valid_through": expiry, "expiry_precision": expiry_precision,
        "last_seen_at": seen_at, "first_discovered_at": seen_at,
        "last_description_fetch_at": seen_at if description and method != "manual" else None,
        "description_recorded_at": seen_at if description else None, "description_state": "source_text" if description else "missing",
        "content_hash": digest, "extraction_method": method, "work_arrangement": mode, "work_arrangement_evidence": mode_evidence,
        "salary": salary, "work_rights_evidence": quotes, "reference_only": reference_only,
        "storage_allowed": bool(source.get("access", {}).get("storage_allowed")),
        "ai_use_allowed": not reference_only and bool(source.get("access", {}).get("ai_use_allowed")),
        "permission_reference": text(source.get("access", {}).get("permission_reference"), 2000),
        "observations": [{"source_id": source["id"], "provider_job_id": text(job.get("provider_job_id") or job.get("job_req_id") or job.get("id"), 200), "listing_url": listing, "query": query, "seen_at": seen_at}]}
    from applypilot.nz_evidence import extract
    metadata["recruitment_evidence"] = extract(description, metadata)
    return {"url": identity_url(listing, source), "title": text(job.get("title"), 300),
            "company": text(job.get("company") or job.get("employer_name"), 500), "location": location,
            "description": description, "application_url": application, "metadata": metadata}


def metadata(job):
    try:
        raw=job.get("vacancy_metadata")
        value = raw if isinstance(raw,dict) else json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


from applypilot.discovery_scope import storage_batch,accepts_title,limit_reached,record_new


@storage_batch
def store(conn, jobs, source, *, method, query=None, reference_only=False, salary_preference=None, stats=None):
    from applypilot.country_context import current, nz_enabled
    from applypilot.command_center import blocked, event
    from applypilot.source_access import require
    context = current()
    if not context or context.country_code != "NZ" or not nz_enabled(context.config):
        raise ValueError("NZ ingestion requires an enabled, explicitly captured NZ context")
    from applypilot.country_context import require_enabled
    from applypilot.setup_workspace import read_searches_dict
    from pathlib import Path
    require_enabled("NZ",read_searches_dict(Path(context.workspace)))
    if method != "manual":
        require(source, method="jobspy" if method == "jobspy" else "workday_cxs" if method == "workday_api" else "http")
    new = duplicates = rejected = errors = 0
    for raw in jobs:
        if limit_reached():break
        try:
            item = normalize(raw, source, method=method, query=query, reference_only=reference_only)
        except (ValueError, TypeError):
            rejected += 1
            errors += 1
            continue
        meta = item["metadata"]
        if not accepts_title(item['title']):
            rejected+=1
            continue
        if method != "manual" and (meta["country_status"] == "out_of_scope" or not nz_salary_ok(meta["salary"], salary_preference)):
            rejected += 1
            continue
        urls = {item["url"], meta["original_listing_url"], item["application_url"]} - {None}
        if any(blocked(conn, u, item["company"]) or conn.execute("SELECT 1 FROM blocked_urls WHERE ? LIKE prefix||'%'", (u,)).fetchone() for u in urls):
            rejected += 1
            continue
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='cc_retired_jobs'").fetchone() and conn.execute("SELECT 1 FROM cc_retired_jobs WHERE job_url=?", (item["url"],)).fetchone():
            duplicates += 1
            continue
        existing = conn.execute("SELECT * FROM jobs WHERE url=?", (item["url"],)).fetchone()
        if not existing:
            for row in conn.execute("SELECT * FROM jobs WHERE country_code='NZ'"):
                old = metadata(dict(row))
                old_urls = {row["url"], row["application_url"], old.get("original_listing_url"), old.get("application_url_original")} - {None, ""}
                if urls & old_urls or (meta["provider_job_id"] and any(o.get("source_id") == source["id"] and o.get("provider_job_id") == meta["provider_job_id"] for o in old.get("observations", []))):
                    existing = row
                    break
        if not existing and meta["provider_job_id"]:
            for row in conn.execute("SELECT * FROM jobs WHERE country_code='NZ' AND source_id=?", (source["id"],)):
                if metadata(dict(row)).get("provider_job_id") == meta["provider_job_id"]:
                    existing = row
                    break
        if existing:
            duplicates += 1
            url = existing["url"]
            if existing["country_code"] != "NZ":
                # Never adopt, tag or mutate a legacy/GB application through NZ.
                rejected += 1
                continue
            if existing["country_code"] == "NZ":
                old = metadata(dict(existing))
                meta["first_discovered_at"] = old.get("first_discovered_at") or existing["discovered_at"]
                meta["observations"] = (old.get("observations", []) + meta["observations"])[-20:]
                if not item["description"]:
                    for key in ("last_description_fetch_at", "description_recorded_at", "description_state", "content_hash", "work_rights_evidence", "recruitment_evidence"):
                        meta[key] = old.get(key, meta[key])
                    for key in ("reference_only", "storage_allowed", "ai_use_allowed", "permission_reference"):
                        meta[key] = old.get(key, meta[key])
                for key in ("posted_at", "source_valid_through", "application_url_original"):
                    if meta.get(key) is None:
                        meta[key] = old.get(key)
                if not meta["posted_at"]:
                    meta["posting_date_precision"] = old.get("posting_date_precision", "unknown")
                meta = old | meta
                mutable = not existing["applied_at"] and existing["apply_status"] not in {"in_progress", "running", "submitting", "submission_uncertain"}
                conn.execute("UPDATE jobs SET vacancy_metadata=?,full_description=CASE WHEN ? AND ?!='' THEN ? ELSE full_description END,application_url=CASE WHEN ? AND ? IS NOT NULL THEN ? ELSE application_url END WHERE url=?",
                    (json.dumps(meta, allow_nan=False), mutable, item["description"], item["description"], mutable, item["application_url"], item["application_url"], url))
        else:
            if limit_reached():continue
            url = item["url"]
            fuzzy = conn.execute("SELECT url FROM jobs WHERE country_code='NZ' AND lower(title)=lower(?) AND lower(company)=lower(?) LIMIT 5", (item["title"], item["company"])).fetchall()
            meta["possible_duplicates"] = [r[0] for r in fuzzy]  # warning only; never fuzzy-merge applications
            cursor=conn.execute("""INSERT INTO jobs(url,title,company,location,salary,description,full_description,application_url,
                site,strategy,discovered_at,detail_scraped_at,country_code,source_id,source_provenance,campaign_id,vacancy_metadata)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (url, item["title"], item["company"], item["location"], meta["salary"]["raw"] or None,
                 item["description"][:500] or None, item["description"] if len(item["description"]) > 200 else None,
                 item["application_url"], source["name"], method, meta["first_discovered_at"],
                 meta["last_description_fetch_at"] if len(item["description"]) > 200 else None, "NZ", source["id"],
                 json.dumps({"source_id": source["id"], "query": query, "country_code": "NZ"}), context.campaign_id, json.dumps(meta, allow_nan=False)))
            if cursor.rowcount!=1:continue
            record_new(conn,url)
            new += 1
            conn.execute("UPDATE jobs SET search_query=? WHERE url=?", (query, url))
        if context.campaign_id:
            conn.execute("INSERT OR IGNORE INTO cc_campaign_jobs(campaign_id,job_url) VALUES(?,?)", (context.campaign_id, url))
        event(conn, url, "discovery_observation", "NZ source observation retained; original application state preserved",
              payload={"country_code": "NZ", "source_id": source["id"], "duplicate": bool(existing), "query": query})
        from applypilot.nz_evidence import assess_job
        stored = conn.execute("SELECT * FROM jobs WHERE url=?", (url,)).fetchone()
        assessment = assess_job(conn, context.workspace, dict(stored), persist=False)
        conn.execute("UPDATE jobs SET immigration_assessment=? WHERE url=?", (json.dumps(assessment, allow_nan=False), url))
    conn.commit()
    if stats is not None:
        stats.update(rejected=rejected, errors=errors)
    return new, duplicates
