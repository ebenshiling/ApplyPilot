"""AI-powered smart extraction: discovers jobs from arbitrary websites.

Two-phase approach:
  Phase 1: Lightweight intelligence (JSON-LD, API responses, data-testids, DOM stats)
           -> LLM picks the best extraction strategy
  Phase 2: Only for CSS selectors -- Playwright finds repeating card elements,
           extracts 2-3 examples, sends focused HTML to LLM for selector generation.

JSON-LD and API strategies execute directly from stored data -- no LLM needed.

Sites are loaded from config/sites.yaml, with {query_encoded} and {location_encoded}
placeholders replaced from the user's search configuration.
"""

import json
import logging
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from typing import Any, cast
from urllib.parse import quote_plus, urljoin

import yaml
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from applypilot import config
from applypilot.config import CONFIG_DIR
from applypilot.database import get_stats, init_db, normalize_url
from applypilot.discovery.salary_filter import load_salary_preference, salary_text_ok
from applypilot.llm import get_client

log = logging.getLogger(__name__)

# Fix Windows encoding -- prevents charmap errors on emoji/unicode in job titles
if getattr(sys.stdout, "encoding", None) and str(sys.stdout.encoding).lower() != "utf-8":
    try:
        stdout_any = cast(Any, sys.stdout)
        stderr_any = cast(Any, sys.stderr)

        if callable(getattr(stdout_any, "reconfigure", None)):
            stdout_any.reconfigure(encoding="utf-8", errors="replace")
        if callable(getattr(stderr_any, "reconfigure", None)):
            stderr_any.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _env_flag(name: str, default: str = "1") -> bool:
    return str(os.environ.get(name, default)).strip().lower() not in ("0", "false", "no", "off")


def _headful_retry_allowed() -> bool:
    raw = os.environ.get("SMARTE_HEADFUL_RETRY")
    if raw is not None and str(raw).strip() != "":
        return _env_flag("SMARTE_HEADFUL_RETRY", "0")
    # Default to disabled on Linux/server environments without DISPLAY.
    if os.name != "nt" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return False
    return True


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

_CAPTCHA_SIGNALS = (
    "captcha",
    "are you a human",
    "verify you",
    "unusual requests",
    "access denied",
    "please verify",
    "bot detection",
)

_GENERIC_LOCATION_TERMS = {
    "united kingdom",
    "uk",
    "great britain",
    "england",
    "scotland",
    "wales",
    "northern ireland",
    "united states",
    "usa",
    "us",
    "canada",
    "australia",
    "new zealand",
    "europe",
    "global",
    "worldwide",
    "remote",
    "anywhere",
}

_PLACEHOLDER_FIELD_TEXT = {
    "location": {"location"},
    "salary": {"salary"},
}

_UK_OUTWARD_POSTCODE_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\b", re.I)
_DATE_LIKE_TEXT_RE = re.compile(
    r"^\s*\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{4}\b",
    re.I,
)

_SHORT_QUERY_TOKENS = {"it", "qa", "ui", "ux", "bi", "ai", "ml", "sql", "api", "etl", "erp", "euc", "ict"}
_QUERY_STOPWORDS = {"and", "or", "for", "the", "a", "an", "to", "of", "in", "on", "at", "with", "from"}
_QUERY_TOKEN_ALIASES = {"ict": "it", "helpdesk": "support", "desk": "support"}
_GENERIC_OVERLAP_TOKENS = {
    "analyst",
    "engineer",
    "specialist",
    "officer",
    "technician",
    "assistant",
    "developer",
    "consultant",
    "administrator",
}
_FAMILY_NEUTRAL_SUPPORT_TOKENS = _GENERIC_OVERLAP_TOKENS | {"support", "it", "system", "systems"}
_SUPPORT_ROLE_FAMILIES: dict[str, tuple[str, ...]] = {
    "it_support": (
        "it support",
        "service support",
        "service desk",
        "helpdesk",
        "help desk",
        "desktop support",
        "1st line",
        "first line",
        "2nd line",
        "second line",
        "technical support officer",
        "it support officer",
        "user support",
        "end user support",
    ),
    "application_support": (
        "application support",
        "application analyst",
        "applications analyst",
        "technical application support",
        "business application support",
        "software support",
        "production support",
        "platform support",
        "clinical systems",
        "epr support",
        "care record",
    ),
    "technical_systems_support": (
        "technical support",
        "support engineer",
        "technical systems",
        "systems support",
        "system support",
        "cloud support",
        "infrastructure support",
        "product support",
        "euc engineer",
        "end user computing",
    ),
}
_SUPPORT_ROLE_FAMILY_COMPATIBILITY: dict[str, set[str]] = {
    "application_support": {"technical_systems_support"},
    "technical_systems_support": {"application_support"},
}

_JOB_FIELD_HINTS = (
    "job",
    "title",
    "salary",
    "description",
    "location",
    "company",
    "role",
    "vacanc",
    "posting",
    "externalpath",
    "posted",
)

_OBVIOUSLY_IRRELEVANT_API_HINTS = {
    "crossdomain.cookie-script.com": "cookie script endpoint",
    "cookieconsent": "cookie consent endpoint",
    "_portalcookieuserconsent": "cookie consent endpoint",
    "onetrust.com/cookieconsentpub/": "geolocation data",
    "maps.googleapis.com/maps/api/js": "Google Maps API call",
    "maps.googleapis.com/maps/api/mapsjs/gen_204": "Google Maps utility endpoint",
    "googletagmanager.com": "analytics endpoint",
    "google-analytics.com": "analytics endpoint",
    "sentry.io": "telemetry endpoint",
    "/api/gmapi": "utility endpoint",
}

_KNOWN_SITE_SELECTORS: dict[str, dict[str, str | None]] = {
    "GOV.UK Find a job": {
        "job_card": 'div[data-testid^="searchResultCard-"]',
        "title": 'a[data-testid^="jobTitle-"]',
        "salary": "p",
        "description": 'p[data-testid="searchResultCardJobDescription"]',
        "location": 'p[data-testid="searchResultCardEmployer"] span:nth-of-type(2)',
        "url": 'a[data-testid^="jobTitle-"]',
    },
    "Jobs Go Public": {
        "job_card": '[data-testid="jcl-job-teaser-wrapper"]',
        "title": ".jcl-job-teaser-title a",
        "salary": ".jcl-job-teaser-salary",
        "description": ".jcl-job-teaser-description",
        "location": '[data-testid="jcl-job-teaser-location"] .popoverlist-no-list-item',
        "url": ".jcl-job-teaser-title a",
    },
    "LG Jobs": {
        "job_card": '[data-testid="jcl-job-teaser-wrapper"]',
        "title": ".jcl-job-teaser-title a",
        "salary": ".jcl-job-teaser-salary",
        "description": ".jcl-job-teaser-description",
        "location": '[data-testid="jcl-job-teaser-location"] .popoverlist-no-list-item',
        "url": ".jcl-job-teaser-title a",
    },
    "HealthJobsUK": {
        "job_card": "#hj-job-list > ol > li.hj-job",
        "title": ".hj-jobtitle",
        "salary": ".hj-salary",
        "description": None,
        "location": ".hj-locationtown",
        "url": "a[href]",
    },
    "NHS Jobs": {
        "job_card": 'ul.search-results > li[data-test="search-result"]',
        "title": 'a[data-test="search-result-job-title"]',
        "salary": 'li[data-test="search-result-salary"]',
        "description": None,
        "location": '[data-test="search-result-location"] .location-font-size',
        "url": 'a[data-test="search-result-job-title"]',
    },
}


# -- Location filtering -------------------------------------------------------


def _load_location_filter(search_cfg: dict | None = None):
    """Load location accept/reject lists from search config."""
    if search_cfg is None:
        search_cfg = config.load_search_config()

    # Backward/forward compatible config:
    # - old: location_accept / location_reject_non_remote
    # - new: location.accept_patterns / location.reject_patterns
    accept = search_cfg.get("location_accept", []) or []
    reject = search_cfg.get("location_reject_non_remote", []) or []

    loc_cfg = search_cfg.get("location")
    if isinstance(loc_cfg, dict):
        accept_nested = loc_cfg.get("accept_patterns")
        reject_nested = loc_cfg.get("reject_patterns")
        if isinstance(accept_nested, list) and accept_nested:
            accept = accept_nested
        if isinstance(reject_nested, list) and reject_nested:
            reject = reject_nested

    return accept, reject


def _location_country(search_cfg: dict | None = None) -> str:
    if search_cfg is None:
        search_cfg = config.load_search_config()
    return _normalize_space(search_cfg.get("country")).upper()


def _looks_like_uk_location(location: str | None) -> bool:
    normalized = _normalize_space(location)
    if not normalized:
        return False
    lowered = normalized.lower()
    if any(term in lowered for term in ("england", "scotland", "wales", "northern ireland", " uk", "united kingdom")):
        return True
    if _UK_OUTWARD_POSTCODE_RE.search(normalized):
        return True
    comma_parts = [part.strip() for part in normalized.split(",") if part.strip()]
    if len(comma_parts) >= 2:
        return True
    return False


def _location_ok(location: str | None, accept: list[str], reject: list[str]) -> bool:
    """Check if a job location passes the user's location filter."""
    if not location:
        return True
    loc = location.lower()

    # Reject patterns always win (including remote roles like "Remote (US)").
    for r in reject:
        if r and r.lower() in loc:
            return False

    if not accept:
        return True
    for a in accept:
        if a.lower() in loc:
            return True
    if _location_country() == "UK" and _looks_like_uk_location(location):
        return True
    return False


def _is_generic_location(location: str | None) -> bool:
    """Return True when a location is country-wide/non-specific."""
    if not location:
        return False
    normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z]+", " ", location.lower())).strip()
    return normalized in _GENERIC_LOCATION_TERMS


def _normalize_space(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _normalized_phrase_text(text: str | None) -> str:
    return _normalize_space(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()))


def _query_tokens(text: str | None) -> set[str]:
    tokens: set[str] = set()
    for raw in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        token = _QUERY_TOKEN_ALIASES.get(raw, raw)
        if token in _QUERY_STOPWORDS:
            continue
        if len(token) >= 4 or token in _SHORT_QUERY_TOKENS:
            tokens.add(token)
    return tokens


def _matches_exclude_titles(title: str | None, exclude_titles: list[str] | None) -> bool:
    if not title or not exclude_titles:
        return False
    title_lower = str(title).lower()
    return any(pat and str(pat).strip().lower() in title_lower for pat in exclude_titles)


def _support_role_family_hits(text: str | None) -> set[str]:
    normalized = _normalized_phrase_text(text)
    if not normalized:
        return set()

    padded = f" {normalized} "
    hits: set[str] = set()
    for family, phrases in _SUPPORT_ROLE_FAMILIES.items():
        for phrase in phrases:
            phrase_norm = _normalized_phrase_text(phrase)
            if phrase_norm and f" {phrase_norm} " in padded:
                hits.add(family)
                break
    return hits


def _support_families_compatible(query_families: set[str], title_families: set[str]) -> bool:
    if not query_families or not title_families:
        return False
    if query_families & title_families:
        return True
    for family in query_families:
        compatibles = _SUPPORT_ROLE_FAMILY_COMPATIBILITY.get(family) or set()
        if compatibles & title_families:
            return True
    return False


def _title_matches_query(title: str | None, search_query: str | None) -> bool:
    """Keep obviously relevant titles while dropping generic mismatches."""
    query = _normalize_space(search_query)
    if not query:
        return True

    title_text = _normalize_space(title)
    if not title_text:
        return False

    if query.lower() in title_text.lower():
        return True

    query_families = _support_role_family_hits(query)
    title_families = _support_role_family_hits(title_text)
    if _support_families_compatible(query_families, title_families):
        return True

    query_tokens = _query_tokens(query)
    if not query_tokens:
        return True

    title_tokens = _query_tokens(title_text)
    if not title_tokens:
        return False

    overlap = query_tokens & title_tokens
    if query_families and not _support_families_compatible(query_families, title_families):
        if not overlap or overlap.issubset(_FAMILY_NEUTRAL_SUPPORT_TOKENS):
            return False
    if overlap and overlap.issubset(_GENERIC_OVERLAP_TOKENS):
        return False
    if len(query_tokens) >= 3:
        return len(overlap) >= 2
    return len(overlap) >= 1


def _prepare_site_location(site: dict, default_location: str) -> str:
    """Apply site-specific location shaping before URL expansion."""
    site_location = default_location
    if site.get("omit_generic_location") and _is_generic_location(default_location):
        site_location = ""
    if site.get("location_first_segment") and site_location:
        site_location = site_location.split(",", 1)[0].strip()
    return site_location


def _configured_locations(search_cfg: dict) -> list[str]:
    locations_cfg = search_cfg.get("locations", []) or []
    locations: list[str] = []
    for item in locations_cfg:
        if isinstance(item, dict):
            value = _normalize_space(item.get("location"))
        else:
            value = _normalize_space(item)
        if value:
            locations.append(value)
    return locations or [""]


def _site_location_exclusions(search_cfg: dict | None, site_name: str | None) -> set[str]:
    cfg = (search_cfg or {}).get("site_location_exclusions")
    if not isinstance(cfg, dict):
        return set()

    site_name_norm = _normalize_space(site_name).lower()
    if not site_name_norm:
        return set()

    excluded: set[str] = set()
    for raw_site_name, raw_locations in cfg.items():
        if _normalize_space(raw_site_name).lower() != site_name_norm:
            continue
        if not isinstance(raw_locations, list):
            continue
        for raw_location in raw_locations:
            first_segment = _normalize_space(raw_location).split(",", 1)[0]
            key = _normalized_phrase_text(first_segment)
            if key:
                excluded.add(key)
    return excluded


def _site_query_exclusions(search_cfg: dict | None, site_name: str | None) -> set[str]:
    cfg = (search_cfg or {}).get("site_query_exclusions")
    if not isinstance(cfg, dict):
        return set()

    site_name_norm = _normalize_space(site_name).lower()
    if not site_name_norm:
        return set()

    excluded: set[str] = set()
    for raw_site_name, raw_queries in cfg.items():
        if _normalize_space(raw_site_name).lower() != site_name_norm:
            continue
        if not isinstance(raw_queries, list):
            continue
        for raw_query in raw_queries:
            query_key = _normalize_space(raw_query).lower()
            if query_key:
                excluded.add(query_key)
    return excluded


def _site_query_location_exclusions(search_cfg: dict | None, site_name: str | None) -> set[tuple[str, str]]:
    cfg = (search_cfg or {}).get("site_query_location_exclusions")
    if not isinstance(cfg, dict):
        return set()

    site_name_norm = _normalize_space(site_name).lower()
    if not site_name_norm:
        return set()

    excluded: set[tuple[str, str]] = set()
    for raw_site_name, raw_entries in cfg.items():
        if _normalize_space(raw_site_name).lower() != site_name_norm:
            continue
        if not isinstance(raw_entries, list):
            continue
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                continue
            query_key = _normalize_space(raw_entry.get("query")).lower()
            raw_location = _normalize_space(raw_entry.get("location"))
            location_key = _normalized_phrase_text(raw_location.split(",", 1)[0]) if raw_location else ""
            if query_key:
                excluded.add((query_key, location_key))
    return excluded


def _query_tiers(search_cfg: dict | None) -> dict[str, int]:
    queries_cfg = (search_cfg or {}).get("queries")
    if not isinstance(queries_cfg, list):
        return {}

    tiers: dict[str, int] = {}
    for entry in queries_cfg:
        if not isinstance(entry, dict):
            continue
        query_key = _normalize_space(entry.get("query")).lower()
        if not query_key:
            continue
        try:
            tier = int(entry.get("tier"))
        except Exception:
            continue
        tiers[query_key] = tier
    return tiers


def _site_tier_location_pruning_rules(search_cfg: dict | None, site_name: str | None) -> list[dict[str, object]]:
    cfg = (search_cfg or {}).get("site_tier_location_pruning")
    if not isinstance(cfg, dict):
        return []

    site_name_norm = _normalize_space(site_name).lower()
    if not site_name_norm:
        return []

    rules: list[dict[str, object]] = []
    for raw_site_name, raw_rules in cfg.items():
        if _normalize_space(raw_site_name).lower() != site_name_norm:
            continue
        if not isinstance(raw_rules, list):
            continue
        for raw_rule in raw_rules:
            if isinstance(raw_rule, dict):
                rules.append(raw_rule)
    return rules


def _matches_site_tier_location_pruning(
    search_cfg: dict | None,
    site_name: str | None,
    query: str | None,
    location: str | None,
) -> bool:
    query_key = _normalize_space(query).lower()
    if not query_key:
        return False

    query_tier = _query_tiers(search_cfg).get(query_key)
    if query_tier is None:
        return False

    location_value = _normalize_space(location)
    location_key = _normalized_phrase_text(location_value.split(",", 1)[0])
    for rule in _site_tier_location_pruning_rules(search_cfg, site_name):
        min_tier = rule.get("min_tier")
        if isinstance(min_tier, int) and query_tier < min_tier:
            continue

        if rule.get("location_required") is True and not location_key:
            continue
        if rule.get("location_blank_only") is True and location_key:
            continue

        keep_queries = {_normalize_space(v).lower() for v in (rule.get("keep_queries") or []) if _normalize_space(v)}
        if query_key in keep_queries:
            continue

        drop_queries = {_normalize_space(v).lower() for v in (rule.get("drop_queries") or []) if _normalize_space(v)}
        if drop_queries and query_key not in drop_queries:
            continue

        return True

    return False


def _is_site_target_excluded(
    search_cfg: dict | None,
    site_name: str | None,
    query: str | None,
    location: str | None,
) -> bool:
    query_key = _normalize_space(query).lower()
    if query_key and query_key in _site_query_exclusions(search_cfg, site_name):
        return True
    location_key = _normalized_phrase_text(_normalize_space(location).split(",", 1)[0])
    if query_key and (query_key, location_key) in _site_query_location_exclusions(search_cfg, site_name):
        return True
    if _matches_site_tier_location_pruning(search_cfg, site_name, query, location):
        return True
    return False


def _prepare_site_locations(site: dict, configured_locations: list[str], search_cfg: dict | None = None) -> list[str]:
    prepared: list[str] = []
    seen: set[str] = set()
    excluded_location_keys = _site_location_exclusions(search_cfg, site.get("name"))
    for location in configured_locations or [""]:
        site_location = _prepare_site_location(site, location)
        location_key = _normalized_phrase_text(site_location.split(",", 1)[0])
        if location_key and location_key in excluded_location_keys:
            continue
        key = site_location.lower()
        if key in seen:
            continue
        seen.add(key)
        prepared.append(site_location)
    return prepared or [""]


def _site_uses_location_placeholder(site_url: str) -> bool:
    return "{location_encoded}" in site_url or "{location}" in site_url


def _has_captcha_signal(html: str | None) -> bool:
    if not html:
        return False
    lowered = html.lower()
    return any(s in lowered for s in _CAPTCHA_SIGNALS)


# -- Site configuration from YAML --------------------------------------------


def load_sites() -> list[dict]:
    """Load scraping target sites from config/sites.yaml."""
    path = CONFIG_DIR / "sites.yaml"
    if not path.exists():
        log.warning("sites.yaml not found at %s", path)
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("sites", [])


def _store_jobs_filtered(
    conn: sqlite3.Connection,
    jobs: list[dict],
    site: str,
    strategy: str,
    accept_locs: list[str],
    reject_locs: list[str],
    search_query: str | None = None,
    salary_pref: dict | None = None,
    exclude_titles: list[str] | None = None,
) -> tuple[int, int]:
    """Store jobs with location filtering. Returns (new, existing)."""
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0
    excluded_title_filtered = 0
    title_filtered = 0
    filtered = 0
    salary_filtered = 0

    for job in jobs:
        url = job.get("url")
        if not url:
            continue

        if _matches_exclude_titles(job.get("title"), exclude_titles):
            excluded_title_filtered += 1
            continue

        if search_query and not _title_matches_query(job.get("title"), search_query):
            title_filtered += 1
            continue

        canonical_url = normalize_url(url) or url

        # Respect user-level URL blocking.
        try:
            from applypilot.database import find_existing_job_url, is_url_blocked

            if is_url_blocked(canonical_url, conn=conn):
                existing += 1
                continue

            dup_url = find_existing_job_url(conn, canonical_url)
            if dup_url:
                existing += 1
                if search_query:
                    try:
                        conn.execute(
                            "UPDATE jobs SET search_query = COALESCE(NULLIF(search_query, ''), ?) WHERE url = ?",
                            (search_query, dup_url),
                        )
                    except Exception:
                        pass
                continue
        except Exception:
            pass
        if not _location_ok(job.get("location"), accept_locs, reject_locs):
            filtered += 1
            continue
        if salary_pref is not None and not salary_text_ok(job.get("salary"), salary_pref):
            salary_filtered += 1
            continue
        try:
            conn.execute(
                "INSERT INTO jobs (url, title, search_query, salary, description, location, site, strategy, discovered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    canonical_url,
                    job.get("title"),
                    search_query,
                    job.get("salary"),
                    job.get("description"),
                    job.get("location"),
                    site,
                    strategy,
                    now,
                ),
            )
            new += 1
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

    if excluded_title_filtered or title_filtered or filtered or salary_filtered:
        details: list[str] = []
        if excluded_title_filtered:
            details.append(f"{excluded_title_filtered} excluded title")
        if title_filtered:
            details.append(f"{title_filtered} title mismatch")
        if filtered:
            details.append(f"{filtered} wrong location")
        if salary_filtered:
            details.append(f"{salary_filtered} salary")
        log.info(
            "Filtered %d jobs (%s)",
            excluded_title_filtered + title_filtered + filtered + salary_filtered,
            ", ".join(details),
        )
    conn.commit()
    return new, existing


# -- Page intelligence collector ---------------------------------------------


def collect_page_intelligence(url: str, headless: bool = True) -> dict:
    """Load a page with Playwright and collect every signal a scraping engineer
    would look at in DevTools. Returns a structured intelligence report."""
    intel: dict = {
        "url": url,
        "json_ld": [],
        "api_responses": [],
        "data_testids": [],
        "page_title": "",
        "dom_stats": {},
        "card_candidates": [],
    }

    captured_responses: list[dict] = []

    def on_response(response):
        ct = response.headers.get("content-type", "")
        rurl = response.url
        if any(ext in rurl for ext in [".js", ".css", ".png", ".jpg", ".svg", ".woff", ".ico", ".gif", ".webp"]):
            return
        if "json" in ct or "/api/" in rurl or "algolia" in rurl or "graphql" in rurl:
            try:
                body = response.text()
                try:
                    data = json.loads(body)
                except Exception:
                    data = None
                captured_responses.append(
                    {
                        "url": rurl,
                        "status": response.status,
                        "size": len(body),
                        "data": data,
                    }
                )
            except Exception:
                pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(user_agent=UA)
        page.on("response", on_response)

        page.goto(url, timeout=60000)
        page.wait_for_load_state("networkidle")

        intel["page_title"] = page.title()

        # 1. JSON-LD
        for el in page.query_selector_all('script[type="application/ld+json"]'):
            try:
                data = json.loads(el.inner_text())
                intel["json_ld"].append(data)
            except Exception:
                pass

        # 2. __NEXT_DATA__
        next_data = page.query_selector("script#__NEXT_DATA__")
        if next_data:
            try:
                intel["next_data"] = json.loads(next_data.inner_text())
            except Exception:
                pass

        # 3. data-testid attributes
        intel["data_testids"] = page.evaluate("""
            () => {
                const els = document.querySelectorAll('[data-testid]');
                const results = [];
                els.forEach(el => {
                    results.push({
                        testid: el.getAttribute('data-testid'),
                        tag: el.tagName.toLowerCase(),
                        text: el.innerText?.slice(0, 80) || ''
                    });
                });
                return results.slice(0, 50);
            }
        """)

        # 4. DOM stats
        intel["dom_stats"] = page.evaluate("""
            () => {
                const body = document.body;
                return {
                    total_elements: body.querySelectorAll('*').length,
                    links: body.querySelectorAll('a[href]').length,
                    headings: body.querySelectorAll('h1,h2,h3,h4').length,
                    lists: body.querySelectorAll('ul,ol').length,
                    tables: body.querySelectorAll('table').length,
                    articles: body.querySelectorAll('article').length,
                    has_data_ids: body.querySelectorAll('[data-id]').length,
                };
            }
        """)

        # 5. Find repeating card-like elements
        intel["card_candidates"] = page.evaluate("""
            () => {
                const candidates = [];
                const allParents = document.querySelectorAll('*');

                for (const parent of allParents) {
                    const children = Array.from(parent.children);
                    if (children.length < 3) continue;

                    const tagCounts = {};
                    children.forEach(c => {
                        const key = c.tagName;
                        tagCounts[key] = (tagCounts[key] || 0) + 1;
                    });

                    const dominant = Object.entries(tagCounts).sort((a,b) => b[1]-a[1])[0];
                    if (!dominant || dominant[1] < 3) continue;

                    const repeatingChildren = children.filter(c => c.tagName === dominant[0]);
                    const withText = repeatingChildren.filter(c => c.innerText?.trim().length > 20);
                    if (withText.length < 3) continue;

                    const withLinks = withText.filter(c => c.querySelector('a[href]'));
                    const score = withLinks.length * 2 + withText.length;

                    const parentId = parent.id ? '#' + parent.id : '';
                    const parentClasses = Array.from(parent.classList).filter(c => c.length < 30).slice(0, 3).join('.');
                    const parentTag = parent.tagName.toLowerCase();
                    const parentSelector = parentTag + (parentId || (parentClasses ? '.' + parentClasses : ''));

                    const childTag = dominant[0].toLowerCase();
                    const sampleChild = withText[0];
                    const childClasses = Array.from(sampleChild.classList).filter(c => c.length < 30).slice(0, 3).join('.');
                    const childSelector = childTag + (childClasses ? '.' + childClasses : '');

                    const examples = withText.slice(0, 3).map(c => {
                        const clone = c.cloneNode(true);
                        clone.querySelectorAll('script,style,svg,noscript').forEach(el => el.remove());
                        const html = clone.outerHTML;
                        return html.length > 5000 ? html.slice(0, 5000) + '...' : html;
                    });

                    candidates.push({
                        parent_selector: parentSelector,
                        child_selector: childSelector,
                        child_tag: childTag,
                        total_children: repeatingChildren.length,
                        with_text: withText.length,
                        with_links: withLinks.length,
                        score: score,
                        examples: examples,
                    });
                }

                candidates.sort((a,b) => b.score - a.score);
                return candidates.slice(0, 3);
            }
        """)

        # Capture full rendered HTML
        intel["full_html"] = page.content()

        browser.close()

    # Process API responses
    for resp in captured_responses:
        summary: dict = {
            "url": resp["url"][:200],
            "status": resp["status"],
            "size": resp["size"],
            "_raw_data": resp.get("data"),
        }
        data = resp.get("data")
        if data:
            if isinstance(data, list) and data:
                summary["type"] = f"array[{len(data)}]"
                if isinstance(data[0], dict):
                    summary["first_item_keys"] = list(data[0].keys())[:20]
                    summary["first_item_sample"] = {k: str(v)[:100] for k, v in list(data[0].items())[:8]}
            elif isinstance(data, dict):
                summary["type"] = "object"
                summary["keys"] = list(data.keys())[:20]

                def _explore_nested(obj, path_prefix, depth=0):
                    if depth > 3 or not isinstance(obj, dict):
                        return
                    for key in list(obj.keys())[:15]:
                        val = obj[key]
                        path = f"{path_prefix}.{key}" if path_prefix else key
                        if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                            info = {
                                "count": len(val),
                                "first_item_keys": list(val[0].keys())[:20],
                                "first_item_sample": {k: str(v)[:200] for k, v in list(val[0].items())[:8]},
                            }
                            for subkey in list(val[0].keys())[:10]:
                                subval = val[0][subkey]
                                if isinstance(subval, list) and len(subval) > 0 and isinstance(subval[0], dict):
                                    info[f"first_item.{subkey}"] = {
                                        "count": len(subval),
                                        "first_item_keys": list(subval[0].keys())[:15],
                                        "first_item_sample": {k: str(v)[:100] for k, v in list(subval[0].items())[:8]},
                                    }
                                elif isinstance(subval, dict):
                                    info[f"first_item.{subkey}"] = {
                                        "type": "object",
                                        "keys": list(subval.keys())[:15],
                                        "sample": {k: str(v)[:150] for k, v in list(subval.items())[:8]},
                                    }
                            summary[f"nested_{path}"] = info
                        elif isinstance(val, dict) and depth < 3:
                            _explore_nested(val, path, depth + 1)

                _explore_nested(data, "")
        intel["api_responses"].append(summary)

    return intel


# -- Judge: filter API responses ---------------------------------------------

JUDGE_PROMPT = """You are filtering intercepted API responses from a job listings website.
Decide if this API response contains actual job listing data (titles, companies, locations, etc).

API Response Summary:
  URL: {url}
  Status: {status}
  Size: {size} chars
  Type: {type}
  Keys/Fields: {fields}
  Sample: {sample}

Is this job listing data? Answer in under 10 words. Return ONLY valid JSON:
{{"relevant": true, "reason": "job objects with title/company"}}
or
{{"relevant": false, "reason": "auth endpoint"}}

No explanation, no markdown, no thinking."""


def _api_response_has_job_hints(resp: dict) -> bool:
    fields: list[str] = []

    for key in ("first_item_keys", "keys"):
        values = resp.get(key)
        if isinstance(values, list):
            fields.extend(str(v) for v in values if v)

    for key, value in resp.items():
        if not key.startswith("nested_") or not isinstance(value, dict):
            continue
        fields.append(key.replace("nested_", ""))
        for nested_key in ("first_item_keys", "keys"):
            nested_values = value.get(nested_key)
            if isinstance(nested_values, list):
                fields.extend(str(v) for v in nested_values if v)

    normalized = [re.sub(r"[^a-z0-9]+", "", field.lower()) for field in fields if field]
    return any(any(hint in field for hint in _JOB_FIELD_HINTS) for field in normalized)


def _obvious_irrelevant_api_reason(resp: dict) -> str | None:
    url = str(resp.get("url") or "").lower()
    for needle, reason in _OBVIOUSLY_IRRELEVANT_API_HINTS.items():
        if needle in url:
            return reason

    size = int(resp.get("size") or 0)
    if size <= 2 and not _api_response_has_job_hints(resp):
        return "empty/no structured data"

    return None


def judge_api_responses(api_responses: list[dict]) -> list[dict]:
    """Use the LLM to filter API responses, keeping only job-relevant ones."""
    if not api_responses:
        return []

    # Optional: disable judge to avoid LLM rate limiting.
    # When disabled, we keep all responses (safe fallback).
    if not _env_flag("SMARTE_JUDGE", "1"):
        return api_responses

    client = get_client()
    relevant: list[dict] = []

    for resp in api_responses:
        irrelevant_reason = _obvious_irrelevant_api_reason(resp)
        if irrelevant_reason:
            log.info("Judge: %s -> DROP (%s)", resp.get("url", "?")[:80], irrelevant_reason)
            continue

        fields = ""
        sample = ""
        resp_type = resp.get("type", "unknown")
        if "first_item_keys" in resp:
            fields = str(resp["first_item_keys"])
            sample = json.dumps(resp.get("first_item_sample", {}), indent=2)[:500]
        elif "keys" in resp:
            fields = str(resp["keys"])
            for k, v in resp.items():
                if k.startswith("nested_"):
                    fields += f"\n  .{k.replace('nested_', '')}: {v.get('count', '?')} items, keys={v.get('first_item_keys', '?')}"
                    sample = json.dumps(v.get("first_item_sample", {}), indent=2)[:500]
        else:
            fields = "no structured data"

        prompt = JUDGE_PROMPT.format(
            url=resp.get("url", "?")[:200],
            status=resp.get("status", "?"),
            size=resp.get("size", "?"),
            type=resp_type,
            fields=fields,
            sample=sample or "n/a",
        )

        try:
            raw = client.ask(prompt, temperature=0.0, max_tokens=1024)
            verdict = extract_json(raw)
            is_relevant = verdict.get("relevant", False)
            reason = verdict.get("reason", "?")
            log.info("Judge: %s -> %s (%s)", resp.get("url", "?")[:80], "KEEP" if is_relevant else "DROP", reason)
            if is_relevant:
                relevant.append(resp)
        except Exception as e:
            keep = _api_response_has_job_hints(resp)
            log.warning(
                "Judge ERROR for %s: %s -- %s",
                resp.get("url", "?")[:80],
                e,
                "keeping" if keep else "dropping likely noise",
            )
            if keep:
                relevant.append(resp)

    return relevant


# -- Phase 1: strategy selection ---------------------------------------------


def format_strategy_briefing(intel: dict) -> str:
    """Lightweight briefing for strategy selection. No raw DOM."""
    sections: list[str] = []
    sections.append(f"PAGE: {intel['url']}")
    sections.append(f"TITLE: {intel['page_title']}")

    # JSON-LD
    if intel["json_ld"]:
        job_postings = [j for j in intel["json_ld"] if isinstance(j, dict) and j.get("@type") == "JobPosting"]
        other = [j for j in intel["json_ld"] if not (isinstance(j, dict) and j.get("@type") == "JobPosting")]
        if job_postings:
            sections.append(f"\nJSON-LD: {len(job_postings)} JobPosting entries found (usable!)")
            sections.append(f"First JobPosting:\n{json.dumps(job_postings[0], indent=2)[:3000]}")
        else:
            sections.append("\nJSON-LD: NO JobPosting entries (json_ld strategy will NOT work)")
        if other:
            types = [j.get("@type", "?") if isinstance(j, dict) else "?" for j in other]
            sections.append(f"Other JSON-LD types (NOT job data): {types}")
    else:
        sections.append("\nJSON-LD: none")

    # API responses
    if intel["api_responses"]:
        sections.append(f"\nAPI RESPONSES INTERCEPTED: {len(intel['api_responses'])} calls")
        for resp in intel["api_responses"]:
            sections.append(f"\n  URL: {resp['url']}")
            sections.append(
                f"  Status: {resp['status']} | Size: {resp['size']:,} chars | Type: {resp.get('type', '?')}"
            )
            if "first_item_keys" in resp:
                sections.append(f"  Item keys: {resp['first_item_keys']}")
                sections.append(f"  Sample: {json.dumps(resp.get('first_item_sample', {}), indent=2)[:1000]}")
            if "keys" in resp:
                sections.append(f"  Object keys: {resp['keys']}")
            for k, v in resp.items():
                if k.startswith("nested_"):
                    arr_name = k.replace("nested_", "")
                    sections.append(f"  .{arr_name}: array of {v['count']} items")
                    sections.append(f"    Item keys: {v['first_item_keys']}")
                    sections.append(f"    Sample: {json.dumps(v.get('first_item_sample', {}), indent=2)[:1000]}")
                    for sk, sv in v.items():
                        if sk.startswith("first_item.") and isinstance(sv, dict):
                            sub_name = sk.replace("first_item.", "")
                            if "count" in sv:
                                sections.append(f"    .{arr_name}[0].{sub_name}: array of {sv['count']} items")
                                sections.append(f"      Item keys: {sv['first_item_keys']}")
                                sections.append(
                                    f"      Sample: {json.dumps(sv.get('first_item_sample', {}), indent=2)[:1500]}"
                                )
                            elif "keys" in sv:
                                sections.append(f"    .{arr_name}[0].{sub_name}: object with keys {sv['keys']}")
                                sections.append(f"      Sample: {json.dumps(sv.get('sample', {}), indent=2)[:1500]}")
    else:
        sections.append("\nAPI RESPONSES: none intercepted")

    # data-testid
    if intel["data_testids"]:
        sections.append(f"\nDATA-TESTID ATTRIBUTES: {len(intel['data_testids'])} elements")
        for dt in intel["data_testids"][:15]:
            text_preview = dt["text"].replace("\n", " ")[:60]
            sections.append(f'  <{dt["tag"]} data-testid="{dt["testid"]}"> {text_preview}')
    else:
        sections.append("\nDATA-TESTID: none found")

    # DOM stats
    stats = intel.get("dom_stats", {})
    sections.append(
        f"\nDOM STATS: {stats.get('total_elements', '?')} elements, "
        f"{stats.get('links', '?')} links, {stats.get('headings', '?')} headings, "
        f"{stats.get('tables', '?')} tables, {stats.get('articles', '?')} articles, "
        f"{stats.get('has_data_ids', '?')} data-id elements"
    )

    # Card candidates
    if intel["card_candidates"]:
        sections.append(f"\nREPEATING ELEMENTS DETECTED: {len(intel['card_candidates'])} candidate groups")
        for i, cand in enumerate(intel["card_candidates"]):
            sections.append(
                f"  [{i}] parent={cand['parent_selector']} child={cand['child_selector']} "
                f"count={cand['total_children']} with_text={cand['with_text']} with_links={cand['with_links']}"
            )
    else:
        sections.append("\nREPEATING ELEMENTS: none detected")

    return "\n".join(sections)


STRATEGY_PROMPT = """You are analyzing a job listings page to pick the best extraction strategy.

Below is a lightweight intelligence briefing -- JSON-LD data, intercepted API responses, data-testid attributes, and DOM statistics. NO raw DOM HTML is included.

Pick the BEST strategy:

1. "json_ld" -- ONLY if briefing shows JobPosting JSON-LD entries (it will say "usable!")
2. "api_response" -- ONLY if an intercepted API response has job-like fields (name, title, salary, description, location, slug)
3. "css_selectors" -- when neither JSON-LD nor API data has job data

HOW TO THINK:
- If the briefing says "JSON-LD: NO JobPosting entries" or "json_ld strategy will NOT work", do NOT pick json_ld.
- For api_response: "url_pattern" must be a substring that matches one of the INTERCEPTED API URLs listed above (not the page URL!). Copy a unique part of the API URL.
- For api_response: "items_path" must point to the ARRAY of items, not a single item. Use dot notation with [n] ONLY for traversing into a specific index to reach an inner array. Example: if data is {{"results": [{{"hits": [...]}}]}}, items_path is "results[0].hits" to reach the hits array.
- For api_response: field paths (title, salary, etc.) are RELATIVE TO EACH ITEM in the array. If items are nested objects like {{"_source": {{"Title": "..."}}}}, use "_source.Title" for the title field.
- For css_selectors: just return {{"strategy":"css_selectors","reasoning":"...","extraction":{{}}}} -- selectors will be generated in a separate focused step.

Return ONLY valid JSON:

For json_ld:
{{"strategy":"json_ld","reasoning":"...","extraction":{{"title":"title","salary":"baseSalary_path_or_null","description":"description","location":"jobLocation[0].address.addressCountry","url":"url_field"}}}}

For api_response:
{{"strategy":"api_response","reasoning":"...","extraction":{{"url_pattern":"actual.url.substring","items_path":"path.to.the.array","title":"field_in_each_item","salary":"salary_field_or_null","description":"description_field_or_null","location":"location_path","url":"url_field"}}}}

For css_selectors:
{{"strategy":"css_selectors","reasoning":"...","extraction":{{}}}}

Keep reasoning under 20 words. No explanation, no markdown, no code fences.

INTELLIGENCE BRIEFING:
{briefing}"""


# -- Card HTML cleaning (allowlist approach) ----------------------------------

_ALLOWED_ATTRS = {
    "id",
    "href",
    "data-testid",
    "data-id",
    "data-type",
    "data-slug",
    "role",
    "aria-label",
    "aria-labelledby",
    "type",
    "name",
    "for",
}
_ALLOWED_PREFIXES = ("data-", "aria-")
_UTILITY_CLASS_RE = re.compile(
    r"^("
    r"[a-z]{1,2}-\d+|"
    r"[a-z]{1,3}-[a-z]{1,3}-\d+|"
    r"col-\d+|"
    r"d-\w+|"
    r"align-\w+|justify-\w+|"
    r"flex-\w+|order-\d+|"
    r"text-\w+|font-\w+|"
    r"bg-\w+|border-\w+|"
    r"rounded-?\w*|shadow-?\w*|"
    r"w-\d+|h-\d+|"
    r"position-\w+|overflow-\w+|"
    r"float-\w+|clearfix|"
    r"visible-\w+|invisible|"
    r"sr-only|"
    r"css-[a-z0-9]+|"
    r"sc-[a-zA-Z]+|"
    r"sc-[a-f0-9]+-\d+"
    r")$"
)


def clean_card_html(html: str) -> str:
    """Strip layout noise from card HTML, keep only what the LLM needs for selectors."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(True):
        new_attrs: dict = {}
        for attr, val in list(tag.attrs.items()):
            if attr in _ALLOWED_ATTRS or any(attr.startswith(p) for p in _ALLOWED_PREFIXES):
                new_attrs[attr] = val
            elif attr == "class":
                classes = val if isinstance(val, list) else val.split()
                kept = [c for c in classes if not _UTILITY_CLASS_RE.match(c)]
                if kept:
                    new_attrs["class"] = kept
        tag.attrs = new_attrs

    return str(soup)


def clean_page_html(html: str, max_chars: int = 150_000) -> str:
    """Strip full page HTML to essential structure for LLM card detection."""
    soup = BeautifulSoup(html, "html.parser")

    main = soup.find("main") or soup.find(attrs={"role": "main"})
    if main and len(str(main)) > 1000:
        soup = BeautifulSoup(str(main), "html.parser")

    for tag in soup.find_all(["script", "style", "svg", "noscript", "iframe", "link", "meta", "head", "footer", "nav"]):
        tag.decompose()

    for tag in soup.find_all(True):
        new_attrs: dict = {}
        for attr, val in list(tag.attrs.items()):
            if attr in _ALLOWED_ATTRS or any(attr.startswith(p) for p in _ALLOWED_PREFIXES):
                new_attrs[attr] = val
            elif attr == "class":
                classes = val if isinstance(val, list) else val.split()
                kept = [c for c in classes if not _UTILITY_CLASS_RE.match(c)]
                if kept:
                    new_attrs["class"] = kept
        tag.attrs = new_attrs

    for tag in soup.find_all(True):
        if not tag.get_text(strip=True) and not tag.find("img") and not tag.find("a"):
            tag.decompose()

    result = str(soup)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n<!-- TRUNCATED -->"
    return result


# -- Phase 2: CSS selector generation ----------------------------------------

FULL_PAGE_SELECTOR_PROMPT = """You are a senior web scraping engineer. Below is the cleaned HTML of a job listings page.

Your task:
1. Find the repeating HTML elements that represent individual job listings
2. Generate CSS selectors to extract data from them

Return a JSON object:
- "job_card": CSS selector matching each job card (MUST match ALL cards on the page)
- "title": selector RELATIVE to the card for the job title
- "salary": selector relative to card for salary, or null
- "description": selector relative to card for description snippet, or null
- "location": selector relative to card for location, or null
- "url": selector relative to card for the link (<a> tag) to the job detail page

Selector rules:
- SIMPLEST wins. A single attribute selector like [data-testid="job-card"] is better than a multi-level path like li > div > [data-testid="job-card"]. Do NOT add parent/ancestor selectors unless the target is ambiguous without them.
- For data-testid/data-id with DYNAMIC values (e.g. data-testid="card-123"), use prefix matching: [data-testid^="card-"]
- For data-testid with STATIC values (e.g. data-testid="job-card"), use exact: [data-testid="job-card"]
- Prefer semantic HTML: article, section, h2, h3 over div
- NEVER use hashed/generated classes: sc-*, css-*, random 5-8 char strings like "fJyWhK"
- Max 2 levels deep. One level is best.
- The "url" selector should target an <a> element (we extract its href attribute)
- If the page has NO job listings visible, return {{"error": "no job listings found"}}

Return ONLY valid JSON, no explanation, no markdown.

PAGE HTML:
{page_html}"""


# -- LLM helpers -------------------------------------------------------------


def ask_llm(prompt: str) -> tuple[str, float, dict]:
    """Send prompt to LLM. Returns (response_text, seconds_taken, metadata)."""
    client = get_client()
    t0 = time.time()
    text = client.ask(prompt, temperature=0.0, max_tokens=4096)
    elapsed = time.time() - t0
    meta = {
        "finish_reason": "stop",
        "prompt_chars": len(prompt),
        "response_chars": len(text),
    }
    return text, elapsed, meta


def extract_json(text: str) -> dict:
    """Extract JSON from LLM response, handling think tags and code fences."""
    if "<think>" in text:
        after = text.split("</think>")[-1].strip()
        if after:
            text = after
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    text = text.strip()
    text = re.sub(r'\\([^"\\\/bfnrtu])', r"\1", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    while text.endswith("}") or text.endswith("]"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            text = text[:-1].rstrip()
    raise json.JSONDecodeError("Could not parse JSON", text, 0)


def _normalize_selector_for_bs4(selector: str | None) -> str | None:
    """Normalize LLM CSS selectors to SoupSieve-supported equivalents."""
    if not selector:
        return selector
    return re.sub(r"(?<!-soup):contains\(", ":-soup-contains(", selector)


def _extract_visible_text(el) -> str:
    clone = BeautifulSoup(str(el), "html.parser")
    for node in clone.select("script, style, svg, noscript, iframe"):
        node.decompose()
    return _normalize_space(clone.get_text(" ", strip=True))


def _is_placeholder_field_text(field: str, text: str | None) -> bool:
    normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z]+", " ", str(text or "").lower())).strip()
    if not normalized:
        return True
    return normalized in _PLACEHOLDER_FIELD_TEXT.get(field, set())


def _extract_field_text(card, el, field: str) -> str | None:
    text = _extract_visible_text(el)

    if field in _PLACEHOLDER_FIELD_TEXT and _is_placeholder_field_text(field, text):
        parent = getattr(el, "parent", None)
        hops = 0
        while parent is not None and hops < 3:
            candidate = _extract_visible_text(parent)
            if candidate and not _is_placeholder_field_text(field, candidate):
                text = candidate
                break
            if parent == card:
                break
            parent = getattr(parent, "parent", None)
            hops += 1

    text = _normalize_space(text)
    if field == "salary":
        text = re.sub(r"^\s*salary\b[:\s-]*", "", text, flags=re.I)
    elif field == "location":
        text = re.sub(r"^\s*location\b[:\s-]*", "", text, flags=re.I)
    return text or None


def _salary_text_looks_valid(text: str | None) -> bool:
    normalized = _normalize_space(text).lower()
    if not normalized:
        return False
    if normalized in {"salary", "location", "description"}:
        return False
    if any(phrase in normalized for phrase in ("competitive", "negotiable", "depends on experience", "market rate")):
        return True
    if not re.search(r"\d", normalized):
        return False
    if _DATE_LIKE_TEXT_RE.match(normalized):
        return False
    if re.search(r"[£$€]", normalized):
        return True
    if re.search(r"\b(?:gbp|usd|eur|cad|aud)\b", normalized):
        return True
    if re.search(
        r"\b(?:per\s+(?:year|annum|month|week|day|hour)|annual|annually|monthly|weekly|daily|hourly|p\.?a\.?|salary)\b",
        normalized,
    ):
        return True
    if re.search(r"\b\d{2,3}(?:,\d{3})*(?:\.\d+)?\s*(?:-|to)\s*\d{2,3}(?:,\d{3})*(?:\.\d+)?\b", normalized):
        return True
    return bool(re.search(r"\b\d{2,3}(?:\.\d+)?k\b", normalized))


def _select_field_text(card, selector: str, field: str) -> str | None:
    try:
        matches = card.select(selector)
    except Exception:
        return None

    if not matches:
        return None

    for el in matches:
        candidate = _extract_field_text(card, el, field)
        if not candidate:
            continue
        if field in _PLACEHOLDER_FIELD_TEXT and _is_placeholder_field_text(field, candidate):
            continue
        if field == "salary" and not _salary_text_looks_valid(candidate):
            continue
        if field == "location":
            candidate = re.sub(r"^\s*[-–—]+\s*", "", candidate).strip()
        return candidate
    return None


def _resolve_extracted_url(
    raw_url: str | None, *, page_url: str | None = None, site_name: str | None = None
) -> str | None:
    raw = str(raw_url or "").strip()
    if not raw:
        return None
    if raw.startswith(("http://", "https://")):
        return raw
    if page_url:
        return urljoin(page_url, raw)
    if site_name:
        base = config.load_base_urls().get(site_name)
        if base:
            return urljoin(base, raw)
    return raw


def _extract_jobs_with_selectors(
    full_html: str,
    selectors: dict,
    *,
    site_name: str = "",
    page_url: str = "",
) -> list[dict]:
    soup = BeautifulSoup(full_html, "html.parser")
    card_sel = selectors.get("job_card", "")
    cards = soup.select(card_sel) if card_sel else []

    jobs: list[dict] = []
    for card in cards:
        job: dict = {}
        for field in ["title", "salary", "description", "location", "url"]:
            sel = selectors.get(field)
            if not sel or sel == "null":
                job[field] = None
                continue
            if field == "url":
                try:
                    el = card.select_one(sel)
                except Exception:
                    job[field] = None
                    continue
                if not el:
                    job[field] = None
                    continue
                job[field] = _resolve_extracted_url(el.get("href"), page_url=page_url, site_name=site_name)
            else:
                job[field] = _select_field_text(card, str(sel), field)
        jobs.append(job)
    return jobs


def execute_known_site_selectors(name: str, intel: dict) -> tuple[dict, list[dict]]:
    """Use stable built-in selectors for known sites to avoid flaky LLM extraction."""
    selectors = _KNOWN_SITE_SELECTORS.get(name)
    if not selectors:
        return {}, []
    full_html = intel.get("full_html", "")
    if not full_html:
        return dict(selectors), []
    return dict(selectors), _extract_jobs_with_selectors(
        full_html,
        selectors,
        site_name=name,
        page_url=str(intel.get("url") or ""),
    )


# -- JSON path resolution ---------------------------------------------------


def resolve_json_path_raw(data, path: str):
    """Navigate a JSON path and return whatever is there (including lists/dicts)."""
    if not path or not data:
        return None
    try:
        current = data
        for part in path.replace("[", ".[").split("."):
            if not part:
                continue
            if part.startswith("[") and part.endswith("]"):
                idx = int(part[1:-1])
                current = current[idx]
            else:
                current = current[part]
        return current
    except (KeyError, IndexError, TypeError):
        return None


def resolve_json_path(data, path: str):
    """Simple JSON path resolver with type coercion for display."""
    if not path or not data:
        return None
    try:
        current = data
        for part in path.replace("[", ".[").split("."):
            if not part:
                continue
            if part.startswith("[") and part.endswith("]"):
                idx = int(part[1:-1])
                current = current[idx]
            else:
                current = current[part]
        if isinstance(current, (str, int, float)):
            return str(current) if not isinstance(current, str) else current
        elif isinstance(current, dict):
            return current.get("name", current.get("text", str(current)[:100]))
        elif isinstance(current, list):
            if current and isinstance(current[0], dict):
                return ", ".join(str(item.get("name", item.get("text", ""))) for item in current[:3])
            return ", ".join(str(x) for x in current[:3])
        return str(current) if current else None
    except (KeyError, IndexError, TypeError):
        return None


# -- Extraction executors ----------------------------------------------------


def execute_json_ld(intel: dict, plan: dict) -> list[dict]:
    """Extract jobs from JSON-LD JobPosting entries."""
    ext = plan["extraction"]
    jobs: list[dict] = []
    for entry in intel["json_ld"]:
        if not isinstance(entry, dict) or entry.get("@type") != "JobPosting":
            continue
        job: dict = {}
        for field in ["title", "salary", "description", "location", "url"]:
            path = ext.get(field)
            if not path or path == "null":
                job[field] = None
                continue
            job[field] = resolve_json_path(entry, path)
        jobs.append(job)
    return jobs


def execute_api_response(intel: dict, plan: dict) -> list[dict]:
    """Extract jobs from intercepted API response data."""
    ext = plan["extraction"]
    url_pattern = ext.get("url_pattern", "")

    target_data = None
    for resp in intel["api_responses"]:
        if url_pattern in resp.get("url", ""):
            target_data = resp.get("_raw_data")
            break

    if not target_data:
        log.warning("Could not find stored API response matching: %s", url_pattern)
        return []

    items_path = ext.get("items_path", "")
    items = resolve_json_path_raw(target_data, items_path)
    if not isinstance(items, list):
        log.warning("items_path '%s' did not resolve to a list (got %s)", items_path, type(items).__name__)
        return []

    jobs: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        job: dict = {}
        for field in ["title", "salary", "description", "location", "url"]:
            path = ext.get(field)
            if not path or path == "null":
                job[field] = None
                continue
            job[field] = resolve_json_path(item, path)
        jobs.append(job)
    return jobs


def execute_css_selectors(intel: dict, site_name: str | None = None) -> tuple[dict, list[dict]]:
    """Phase 2: Send full cleaned page HTML to LLM for card detection + selector generation.
    Returns (selectors, jobs)."""
    full_html = intel.get("full_html", "")
    if not full_html:
        log.warning("No page HTML captured")
        return {}, []

    cleaned = clean_page_html(full_html)
    log.info("Page HTML: %s -> %s chars", f"{len(full_html):,}", f"{len(cleaned):,}")

    prompt = FULL_PAGE_SELECTOR_PROMPT.format(page_html=cleaned)

    try:
        raw, elapsed, meta = ask_llm(prompt)
    except Exception as e:
        log.error("LLM_ERROR in Phase 2: %s", e)
        return {}, []

    log.info("Phase 2 LLM: %d chars, %.1fs", meta["response_chars"], elapsed)

    try:
        selectors = extract_json(raw)
    except Exception as e:
        log.error("PARSE_ERROR in Phase 2: %s | raw: %s", e, raw[:500])
        return {}, []

    if "error" in selectors:
        log.warning("LLM: %s", selectors["error"])
        return selectors, []

    for field in ["job_card", "title", "salary", "description", "location", "url"]:
        value = selectors.get(field)
        if not isinstance(value, str):
            continue
        normalized = _normalize_selector_for_bs4(value)
        if normalized != value:
            selectors[field] = normalized

    log.info("Selectors: %s", selectors)

    try:
        jobs = _extract_jobs_with_selectors(
            full_html,
            selectors,
            site_name=str(site_name or ""),
            page_url=str(intel.get("url") or ""),
        )
    except Exception as e:
        log.error("Selector extraction failed: %s", e)
        return selectors, []

    log.info("Matched %d cards", len(jobs))
    return selectors, jobs


def _summarize_site_result(name: str, strategy: str, plan: dict, jobs: list[dict]) -> dict:
    titles = sum(1 for j in jobs if j.get("title"))
    total = len(jobs)
    status = "PASS" if total > 0 and titles / max(total, 1) >= 0.8 else "FAIL" if total == 0 else "PARTIAL"
    urls = sum(1 for j in jobs if j.get("url"))
    salaries = sum(1 for j in jobs if j.get("salary"))
    descs = sum(1 for j in jobs if j.get("description"))

    log.info(
        "RESULT: %s -- %d jobs, %d titles, %d urls, %d salaries, %d descriptions",
        status,
        total,
        titles,
        urls,
        salaries,
        descs,
    )
    for j in jobs[:3]:
        log.info(
            "  - %s | loc: %s | salary: %s",
            str(j.get("title") or "?")[:55],
            str(j.get("location") or "?")[:25],
            str(j.get("salary") or "-")[:20],
        )

    return {
        "name": name,
        "status": status,
        "strategy": strategy,
        "total": total,
        "titles": titles,
        "plan": plan,
        "jobs": jobs,
        "sample": jobs[:5],
    }


# -- Main per-site extraction ------------------------------------------------


def _run_one_site(name: str, url: str) -> dict:
    """Run full smart extraction pipeline on one site URL."""
    log.info("=" * 60)
    log.info("%s: %s", name, url)

    # Step 1: Collect intelligence
    log.info("[1] Collecting page intelligence...")
    t0 = time.time()
    intel = collect_page_intelligence(url)
    collect_time = time.time() - t0
    log.info(
        "Done in %.1fs | JSON-LD: %d | API: %d | testids: %d | cards: %d",
        collect_time,
        len(intel["json_ld"]),
        len(intel["api_responses"]),
        len(intel["data_testids"]),
        len(intel["card_candidates"]),
    )

    # Headful retry if page content is tiny
    full_html = intel.get("full_html", "")
    cleaned_check = clean_page_html(full_html) if full_html else ""
    _is_captcha = _has_captcha_signal(full_html)
    if _is_captcha:
        retry_captcha = _env_flag("SMARTE_CAPTCHA_RETRY", "1")
        if not retry_captcha:
            log.warning("CAPTCHA/rate-limit detected -- skipping retries")
            return {"name": name, "status": "CAPTCHA", "error": "captcha/rate-limit detected"}

        try:
            retry_delay = float(str(os.environ.get("SMARTE_CAPTCHA_RETRY_DELAY", "6")).strip() or "0")
        except Exception:
            retry_delay = 0.0
        if retry_delay > 0:
            time.sleep(retry_delay)

        if _headful_retry_allowed():
            log.warning("CAPTCHA/rate-limit detected -- retrying once in headful mode")
            try:
                intel = collect_page_intelligence(url, headless=False)
                collect_time = time.time() - t0
                full_html = intel.get("full_html", "")
                cleaned_check = clean_page_html(full_html) if full_html else ""
                if _has_captcha_signal(full_html):
                    log.warning("CAPTCHA persists after retry")
                    return {"name": name, "status": "CAPTCHA", "error": "captcha/rate-limit detected"}
                log.info(
                    "Headful retry done in %.1fs | JSON-LD: %d | API: %d",
                    collect_time,
                    len(intel["json_ld"]),
                    len(intel["api_responses"]),
                )
            except Exception as e:
                log.warning("Headful retry failed, continuing with headless result: %s", e)
        else:
            log.warning("CAPTCHA/rate-limit detected, but headful retry is disabled in this environment")
    elif len(cleaned_check) < 5000 and full_html:
        if _headful_retry_allowed():
            log.info("Cleaned HTML only %s chars -- retrying headful...", f"{len(cleaned_check):,}")
            try:
                intel = collect_page_intelligence(url, headless=False)
                collect_time = time.time() - t0
                log.info(
                    "Headful done in %.1fs | JSON-LD: %d | API: %d",
                    collect_time,
                    len(intel["json_ld"]),
                    len(intel["api_responses"]),
                )
            except Exception as e:
                log.warning("Headful retry failed, continuing with headless result: %s", e)
        else:
            log.info(
                "Cleaned HTML only %s chars -- skipping headful retry in this environment", f"{len(cleaned_check):,}"
            )

    # Step 1.75: Built-in selectors for stable sites.
    preset_selectors, preset_jobs = execute_known_site_selectors(name, intel)
    if preset_selectors:
        log.info("[1.75] Using built-in selectors for %s", name)
        log.info("Selectors: %s", preset_selectors)
        log.info("Matched %d cards", len(preset_jobs))
        if preset_jobs:
            plan = {
                "strategy": "site_preset",
                "reasoning": "built-in selectors",
                "extraction": preset_selectors,
            }
            return _summarize_site_result(name, "site_preset", plan, preset_jobs)
        log.warning("Built-in selectors matched no jobs -- falling back to LLM")

    # Step 1.5: Judge filters API responses
    if intel["api_responses"]:
        log.info("[1.5] Judge filtering API responses...")
        intel["api_responses"] = judge_api_responses(intel["api_responses"])
        log.info("Kept %d relevant responses", len(intel["api_responses"]))

    # Step 2: Strategy selection
    briefing = format_strategy_briefing(intel)
    log.info("[2] Phase 1: Strategy selection (%s chars briefing)", f"{len(briefing):,}")

    # Optional: cool down between LLM calls to avoid 429s
    try:
        cooldown = float(str(os.environ.get("LLM_COOLDOWN", "0")).strip() or "0")
    except Exception:
        cooldown = 0.0
    if cooldown > 0:
        time.sleep(cooldown)

    prompt = STRATEGY_PROMPT.format(briefing=briefing)
    try:
        raw, elapsed, meta = ask_llm(prompt)
    except Exception as e:
        log.error("LLM_ERROR: %s", e)
        return {"name": name, "status": "LLM_ERROR", "error": str(e)}

    log.info("LLM: %d chars, %.1fs", meta["response_chars"], elapsed)

    try:
        plan = extract_json(raw)
    except Exception as e:
        log.error("PARSE_ERROR: %s | raw: %s", e, raw[:500])
        return {"name": name, "status": "PARSE_ERROR", "error": str(e), "raw": raw}

    strategy = plan.get("strategy", "?")
    reasoning = plan.get("reasoning", "?")
    log.info("Strategy: %s | Reasoning: %s", strategy, reasoning)

    # Step 3: Execute
    log.info("[3] Executing %s...", strategy)
    try:
        if strategy == "json_ld":
            log.info("Extraction plan: %s", json.dumps(plan.get("extraction", {}))[:300])
            jobs = execute_json_ld(intel, plan)
        elif strategy == "api_response":
            log.info("Extraction plan: %s", json.dumps(plan.get("extraction", {}))[:300])
            jobs = execute_api_response(intel, plan)
        elif strategy == "css_selectors":
            log.info("-> Phase 2: Generating selectors from card examples...")
            selectors, jobs = execute_css_selectors(intel, site_name=name)
            plan["extraction"] = selectors
        else:
            log.warning("Unknown strategy: %s", strategy)
            jobs = []
    except Exception as e:
        log.error("EXECUTION_ERROR: %s", e)
        return {"name": name, "status": "EXEC_ERROR", "error": str(e), "plan": plan}

    # Step 4: Report
    return _summarize_site_result(name, strategy, plan, jobs)


# -- Target building --------------------------------------------------------


def build_scrape_targets(
    sites: list[dict] | None = None,
    search_cfg: dict | None = None,
) -> list[dict]:
    """Build the full list of (name, url) targets from sites + search config queries.

    - "search" sites get expanded: 1 URL per query/location pair from search config
    - "static" sites get scraped once as-is

    Placeholders in URLs:
      {query_encoded} -> URL-encoded search query
      {location_encoded} -> URL-encoded location
      {query} -> raw search query (for simple substitution)
    """
    if sites is None:
        sites = load_sites()
    if search_cfg is None:
        search_cfg = config.load_search_config()

    queries_cfg = search_cfg.get("queries", [])
    queries = [_normalize_space(q.get("query")) for q in queries_cfg if _normalize_space(q.get("query"))]
    configured_locations = _configured_locations(search_cfg)

    targets: list[dict] = []

    for site in sites:
        if bool(site.get("disabled")):
            log.info("Skipping disabled smart site: %s", site.get("name", "Unknown"))
            continue
        site_url = site.get("url", "")
        site_name = site.get("name", "Unknown")
        site_type = site.get("type", "static")

        if site_type == "search" and queries:
            if _site_uses_location_placeholder(site_url):
                site_locations = _prepare_site_locations(site, configured_locations, search_cfg)
                # Interleave queries across locations so capped runs still sample a useful mix.
                for location_round in range(len(site_locations)):
                    for query_index, query in enumerate(queries):
                        site_location = site_locations[(query_index + location_round) % len(site_locations)]
                        expanded_url = site_url
                        expanded_url = expanded_url.replace("{query_encoded}", quote_plus(query))
                        expanded_url = expanded_url.replace("{query}", quote_plus(query))
                        expanded_url = expanded_url.replace("{location_encoded}", quote_plus(site_location))
                        if _is_site_target_excluded(search_cfg, site_name, query, site_location):
                            continue
                        targets.append(
                            {
                                "name": site_name,
                                "url": expanded_url,
                                "query": query,
                                "location": site_location,
                            }
                        )
            else:
                for query in queries:
                    expanded_url = site_url
                    expanded_url = expanded_url.replace("{query_encoded}", quote_plus(query))
                    expanded_url = expanded_url.replace("{query}", quote_plus(query))
                    if _is_site_target_excluded(search_cfg, site_name, query, ""):
                        continue
                    targets.append(
                        {
                            "name": site_name,
                            "url": expanded_url,
                            "query": query,
                            "location": "",
                        }
                    )
        else:
            site_location = _prepare_site_locations(site, configured_locations, search_cfg)[0]
            expanded_url = site_url
            expanded_url = expanded_url.replace("{location_encoded}", quote_plus(site_location))
            targets.append(
                {
                    "name": site_name,
                    "url": expanded_url,
                    "query": None,
                    "location": site_location,
                }
            )

    return targets


def _balanced_target_window(
    targets: list[dict], max_targets: int, offset: int = 0, search_cfg: dict | None = None
) -> list[dict]:
    """Interleave higher-value site groups first, then take a rotating capped window."""
    if max_targets <= 0 or len(targets) <= max_targets:
        return list(targets)

    grouped_by_bucket: dict[int, list[dict]] = defaultdict(list)
    for target in targets:
        grouped_by_bucket[_site_window_bucket(target.get("name"))].append(target)

    def _interleave_by_site(items: list[dict]) -> list[dict]:
        by_site: dict[str, list[dict]] = defaultdict(list)
        site_order: list[str] = []
        for t in items:
            name = str(t.get("name") or "").strip() or "unknown"
            if name not in by_site:
                site_order.append(name)
            by_site[name].append(t)

        site_priority_overrides = {
            _normalize_space(k).lower(): int(v)
            for k, v in ((search_cfg or {}).get("smart_window_site_priority") or {}).items()
            if _normalize_space(k)
        }

        ordered: list[dict] = []
        idx = 0
        while len(ordered) < len(items):
            added_this_round = False
            round_site_order = sorted(
                site_order,
                key=lambda n: (site_priority_overrides.get(_normalize_space(n).lower(), 999), site_order.index(n)),
            )
            for name in round_site_order:
                site_items = by_site.get(name) or []
                if idx < len(site_items):
                    ordered.append(site_items[idx])
                    added_this_round = True
            if not added_this_round:
                break
            idx += 1
        return ordered

    balanced: list[dict] = []
    for bucket in sorted(grouped_by_bucket):
        balanced.extend(_interleave_by_site(grouped_by_bucket[bucket]))

    if not balanced:
        return []

    site_caps: dict[str, int] = {}
    for raw_name, raw_cap in ((search_cfg or {}).get("smart_window_site_caps") or {}).items():
        name_key = _normalize_space(raw_name).lower()
        if not name_key:
            continue
        try:
            cap = int(raw_cap)
        except Exception:
            continue
        if cap < 0:
            continue
        site_caps[name_key] = cap

    start = offset % len(balanced)
    rotated = [balanced[(start + i) % len(balanced)] for i in range(len(balanced))]
    if not site_caps:
        return rotated[: min(max_targets, len(rotated))]

    window: list[dict] = []
    selected_by_site: dict[str, int] = defaultdict(int)
    for target in rotated:
        site_name = _normalize_space(target.get("name")).lower()
        cap = site_caps.get(site_name)
        if cap is not None and selected_by_site[site_name] >= cap:
            continue
        window.append(target)
        selected_by_site[site_name] += 1
        if len(window) >= max_targets:
            break
    return window


def _site_window_bucket(name: str | None) -> int:
    normalized = str(name or "").strip().lower()
    bucket_map = {
        "gov.uk find a job": 0,
        "nhs jobs": 0,
        "healthjobsuk": 0,
        "jobs go public": 1,
        "lg jobs": 1,
        "moj jobs": 2,
    }
    return bucket_map.get(normalized, 1)


def _site_priority(name: str | None) -> tuple[int, str]:
    normalized = str(name or "").strip().lower()
    priority_map = {
        "gov.uk find a job": 0,
        "nhs jobs": 1,
        "healthjobsuk": 2,
        "jobs go public": 3,
        "lg jobs": 4,
        "moj jobs": 5,
    }
    return (priority_map.get(normalized, 50), normalized)


def _target_slot_key(target: dict) -> tuple[str, str] | None:
    query = _normalize_space(target.get("query"))
    location = _normalize_space(target.get("location"))
    if not query or not location:
        return None
    location_key = _normalized_phrase_text(location.split(",", 1)[0])
    if not location_key:
        return None
    return (query.lower(), location_key)


def _dedupe_site_query_location_twins(targets: list[dict]) -> list[dict]:
    """Prefer the stronger site when multiple sites cover the same query/location slot."""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    ordered_entries: list[tuple[str, object]] = []
    seen_slots: set[tuple[str, str]] = set()

    for target in targets:
        slot_key = _target_slot_key(target)
        if slot_key is None:
            ordered_entries.append(("target", target))
            continue
        if slot_key not in seen_slots:
            seen_slots.add(slot_key)
            ordered_entries.append(("slot", slot_key))
        grouped[slot_key].append(target)

    prioritized: list[dict] = []
    deferred_groups: list[list[dict]] = []

    for entry_type, value in ordered_entries:
        if entry_type == "target":
            prioritized.append(cast(dict, value))
            continue
        slot_key = cast(tuple[str, str], value)
        items = sorted(grouped.get(slot_key, []), key=lambda t: _site_priority(t.get("name")))
        if not items:
            continue
        prioritized.append(items[0])
        if len(items) > 1:
            deferred_groups.append(items[1:])

    deferred = [item for group in deferred_groups for item in group]
    return prioritized + deferred


# -- Run all sites -----------------------------------------------------------


def _run_all(
    targets: list[dict],
    accept_locs: list[str],
    reject_locs: list[str],
    workers: int = 1,
    salary_pref: dict | None = None,
    exclude_titles: list[str] | None = None,
) -> dict:
    """Run smart extract on all targets.

    Sequential by default. When workers > 1, scrapes multiple sites in parallel
    using ThreadPoolExecutor. DB storage is still serialized after each result.
    """
    conn = init_db()
    pre_stats = get_stats(conn)
    log.info(
        "Database: %d jobs already stored, %d pending detail scrape", pre_stats["total"], pre_stats["pending_detail"]
    )

    results: list[dict] = []
    total_new = 0
    total_existing = 0

    def _process_result(r: dict, target: dict) -> None:
        nonlocal total_new, total_existing
        jobs = r.get("jobs", [])
        if jobs:
            new, existing = _store_jobs_filtered(
                conn,
                jobs,
                target["name"],
                r.get("strategy", "?"),
                accept_locs,
                reject_locs,
                search_query=(target.get("query") if target.get("query") else None),
                salary_pref=salary_pref,
                exclude_titles=exclude_titles,
            )
            total_new += new
            total_existing += existing
            log.info("DB: +%d new, %d already existed", new, existing)

    if workers > 1 and len(targets) > 1:
        # Parallel mode
        with ThreadPoolExecutor(max_workers=min(workers, len(targets))) as pool:
            future_to_target = {pool.submit(_run_one_site, target["name"], target["url"]): target for target in targets}
            for future in as_completed(future_to_target):
                target = future_to_target[future]
                r = future.result()
                results.append(r)
                _process_result(r, target)
    else:
        # Sequential mode (default)
        for i, target in enumerate(targets):
            label = target["name"]
            details = [str(v) for v in (target.get("query"), target.get("location")) if str(v or "").strip()]
            if details:
                label = f"{target['name']} [{' | '.join(details)}]"
            log.info("[%d/%d] %s", i + 1, len(targets), label)

            r = _run_one_site(target["name"], target["url"])
            results.append(r)
            _process_result(r, target)

    # Summary
    for r in results:
        strategy = r.get("strategy", "?")
        if r["status"] in ("PASS", "PARTIAL", "FAIL"):
            detail = f"{r['total']} jobs, {r['titles']} titles, strategy={strategy}"
        else:
            detail = r.get("error", "")[:60]
        log.info("%-10s | %-25s | %s", r["status"], r["name"], detail)

    passed = sum(1 for r in results if r["status"] == "PASS")
    log.info("%d/%d PASS", passed, len(results))

    return {"total_new": total_new, "total_existing": total_existing, "passed": passed, "total": len(results)}


# -- Public entry point ------------------------------------------------------


def run_smart_extract(
    sites: list[dict] | None = None,
    workers: int = 1,
) -> dict:
    """Main entry point for AI-powered smart extraction.

    Loads sites from config/sites.yaml and search queries from the user's
    search config, then runs the extraction pipeline on all targets.

    Args:
        sites: Override the site list. If None, loads from YAML.
        workers: Number of parallel threads for site scraping. Default 1 (sequential).

    Returns:
        Dict with stats: total_new, total_existing, passed, total.
    """
    if not _env_flag("SMARTE_ENABLED", "1"):
        log.info("Smart extract disabled via SMARTE_ENABLED=0")
        return {"total_new": 0, "total_existing": 0, "passed": 0, "total": 0}

    search_cfg = config.load_search_config()
    accept_locs, reject_locs = _load_location_filter(search_cfg)
    try:
        salary_pref = load_salary_preference(config.load_profile())
    except Exception:
        salary_pref = None
    exclude_titles = search_cfg.get("exclude_titles") or []

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    # Optional: filter targets by site name / query (handy for focused runs).
    def _env_csv(name: str) -> set[str]:
        raw = str(os.environ.get(name, "") or "").strip()
        if not raw:
            return set()
        return {p.strip().lower() for p in raw.split(",") if p.strip()}

    allow_sites = _env_csv("SMARTE_SITE_ALLOW")
    if not allow_sites:
        cfg_sites = search_cfg.get("smart_sites")
        if isinstance(cfg_sites, list):
            allow_sites = {str(s or "").strip().lower() for s in cfg_sites if str(s or "").strip()}
    block_sites = _env_csv("SMARTE_SITE_BLOCK")
    allow_queries = _env_csv("SMARTE_QUERY_ALLOW")
    block_queries = _env_csv("SMARTE_QUERY_BLOCK")

    # Convenience: focus on UK sources only.
    if _env_flag("SMARTE_ONLY_UK", "0"):
        uk_names = {
            "reed",
            "nhs jobs",
            "adzuna uk",
            "guardian jobs",
            "jobs.ac.uk",
        }

        def _is_uk_target(t: dict) -> bool:
            n = str(t.get("name") or "").strip().lower()
            u = str(t.get("url") or "").strip().lower()
            if n in uk_names:
                return True
            return any(
                host in u
                for host in (
                    "reed.co.uk",
                    "adzuna.co.uk",
                    "jobs.theguardian.com",
                    "jobs.nhs.uk",
                    "jobs.ac.uk",
                )
            )

        targets = [t for t in targets if _is_uk_target(t)]

    if allow_sites:
        targets = [t for t in targets if str(t.get("name") or "").strip().lower() in allow_sites]
    if block_sites:
        targets = [t for t in targets if str(t.get("name") or "").strip().lower() not in block_sites]

    if allow_queries:
        targets = [
            t
            for t in targets
            if (t.get("query") is None) or (str(t.get("query") or "").strip().lower() in allow_queries)
        ]
    if block_queries:
        targets = [
            t
            for t in targets
            if (t.get("query") is None) or (str(t.get("query") or "").strip().lower() not in block_queries)
        ]

    targets = _dedupe_site_query_location_twins(targets)

    # Optional: cap number of targets (useful while tuning rate limits)
    try:
        max_targets = int(str(os.environ.get("SMARTE_MAX_TARGETS", "0")).strip() or "0")
    except Exception:
        max_targets = 0
    try:
        target_offset = int(str(os.environ.get("SMARTE_TARGET_OFFSET", "0")).strip() or "0")
    except Exception:
        target_offset = 0
    if max_targets > 0 and len(targets) > max_targets:
        log.info("Capping smart extract targets: %d -> %d (SMARTE_MAX_TARGETS)", len(targets), max_targets)
        if target_offset:
            log.info("Rotating capped targets by %d (SMARTE_TARGET_OFFSET)", target_offset)
        targets = _balanced_target_window(targets, max_targets=max_targets, offset=target_offset, search_cfg=search_cfg)

    if not targets:
        log.warning("No scrape targets configured. Create config/sites.yaml and searches.yaml.")
        return {"total_new": 0, "total_existing": 0, "passed": 0, "total": 0}

    search_sites = sum(1 for s in (sites or load_sites()) if s.get("type") == "search")
    static_sites = sum(1 for s in (sites or load_sites()) if s.get("type") != "search")
    log.info(
        "Sites: %d searchable, %d static | Total targets: %d (workers=%d)",
        search_sites,
        static_sites,
        len(targets),
        workers,
    )

    return _run_all(
        targets,
        accept_locs,
        reject_locs,
        workers=workers,
        salary_pref=salary_pref,
        exclude_titles=exclude_titles,
    )
