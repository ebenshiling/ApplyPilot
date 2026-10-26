"""NZ read-only source capabilities. Permission records are operator attestations,
not a grant of rights by ApplyPilot. Validation is a separate server-owned result.
Legacy/UK adapters retain their established behaviour.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading
import time
from urllib.parse import urlsplit

METHODS = {"http", "jobspy", "workday_cxs"}
STATES = {"unreviewed", "permitted", "restricted", "disabled"}
_scope = ContextVar("nz_source_requests", default=None)
_lock = threading.Lock()
_GENERIC_RESTRICTED = ("indeed.com", "linkedin.com", "glassdoor.com", "glassdoor.co.nz", "seek.co.nz", "seek.com", "seek.com.au", "trademe.co.nz")


class SourceAccessError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def moment(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def domain_is(host, domain):
    return host == domain or host.endswith("." + domain)


def restricted_host(host):
    return domain_is(host, "immigration.govt.nz") or any(domain_is(host, item) for item in _GENERIC_RESTRICTED)


def validate_metadata(item):
    """Return bounded catalog metadata; never accept client validation results."""
    from applypilot.country_context import normalize_country
    from applypilot.source_registry import validate_url
    output = {}
    if "countries" in item:
        if not isinstance(item["countries"], list) or not 1 <= len(item["countries"]) <= 2:
            raise SourceAccessError("Source countries must be a GB/NZ list")
        output["countries"] = sorted({normalize_country(v, optional=False) for v in item["countries"]})
    if "access" in item:
        raw = item["access"]
        allowed = {"state", "terms_url", "permission_reference", "reviewed_at", "methods", "storage_allowed", "ai_use_allowed",
                   "application_mode", "min_interval_seconds", "max_requests_per_run", "max_cache_minutes",
                   "review_max_age_days", "validation_max_age_hours"}
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise SourceAccessError("Unsupported source access metadata")
        access = {"state": "unreviewed", "methods": [], "storage_allowed": False, "ai_use_allowed": False,
                  "application_mode": "manual", "min_interval_seconds": 1, "max_requests_per_run": 100,
                  "max_cache_minutes": 0, "review_max_age_days": 30, "validation_max_age_hours": 168} | raw
        if access["state"] not in STATES or access["application_mode"] != "manual":
            raise SourceAccessError("Invalid access state or application mode (live applications remain contained)")
        if not isinstance(access["methods"], list) or any(m not in METHODS for m in access["methods"]):
            raise SourceAccessError("Unsupported source retrieval method")
        for key in ("storage_allowed", "ai_use_allowed"):
            if not isinstance(access[key], bool):
                raise SourceAccessError(f"{key} must be true/false")
        for key, low, high in (("min_interval_seconds", .1, 60), ("max_requests_per_run", 1, 1000),
                               ("max_cache_minutes", 0, 1440), ("review_max_age_days", 1, 365), ("validation_max_age_hours", 1, 720)):
            number = access[key]
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not low <= number <= high:
                raise SourceAccessError(f"Invalid {key} bound")
        for key in ("terms_url", "permission_reference", "reviewed_at"):
            if key in access and (not isinstance(access[key], str) or len(access[key]) > 2000):
                raise SourceAccessError(f"Invalid {key}")
        if access.get("terms_url"):
            validate_url(access["terms_url"])
        if access["state"] == "permitted":
            reviewed = moment(access.get("reviewed_at"))
            if not access.get("terms_url") or not str(access.get("permission_reference") or "").strip() or not reviewed:
                raise SourceAccessError("Permitted access requires terms, an explicit permission reference and a zoned review timestamp")
            if reviewed > datetime.now(timezone.utc) + timedelta(seconds=30):
                raise SourceAccessError("Source review cannot be in the future")
        output["access"] = access
    for key in ("identity_query_keys", "retrieval_hosts", "application_hosts"):
        if key in item:
            values = item[key]
            if not isinstance(values, list) or len(values) > 16 or any(not isinstance(v, str) or not v or len(v) > 160 for v in values):
                raise SourceAccessError(f"Invalid {key}")
            if key == "identity_query_keys":
                import re
                if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", v) for v in values):
                    raise SourceAccessError("Identity query keys must be simple parameter names")
            else:
                for host in values:
                    validate_url("https://" + host)
                    if urlsplit("https://" + host).netloc != host or any(c in host for c in "/?#@:"):
                        raise SourceAccessError("Use exact public hostnames, not URLs or wildcards")
            output[key] = list(dict.fromkeys(values))
    if "currency_convention" in item:
        value = item["currency_convention"]
        if not isinstance(value, dict) or set(value) != {"currency", "evidence"} or value["currency"] not in {"NZD", "AUD", "USD", "GBP", "EUR"} or not isinstance(value["evidence"], str) or not 1 <= len(value["evidence"]) <= 1000:
            raise SourceAccessError("Currency convention needs a supported currency and evidence")
        output["currency_convention"] = dict(value)
    return output


def fingerprint(source):
    fields = ("id", "name", "url", "base_url", "type", "tenant", "site_id", "provider_kind", "countries", "access",
              "identity_query_keys", "retrieval_hosts", "application_hosts", "currency_convention")
    return hashlib.sha256(json.dumps({k: source.get(k) for k in fields}, sort_keys=True, allow_nan=False).encode()).hexdigest()


def readiness(source, *, method="http", validation=True, storage=True):
    try:
        metadata = validate_metadata(source)
        access = metadata.get("access", {})
        host = (urlsplit(source.get("url") or source.get("base_url") or "").hostname or "").lower()
        if domain_is(host, "immigration.govt.nz"):
            return "INZ automation is unavailable; use the official tool manually"
        if method == "http" and restricted_host(host):
            return "Restricted board cannot be fetched through a generic direct-source route"
        if source.get("provider_kind") == "board" and source.get("name") not in {"indeed", "linkedin", "glassdoor"}:
            return "Provider is not an available NZ board adapter"
        if method == "jobspy":
            return "NZ JobSpy execution is disabled: this runtime cannot enforce per-HTTP-request host and rate bounds inside the provider"
        if source.get("disabled") or "NZ" not in metadata.get("countries", []):
            return "Source is disabled or not explicitly scoped to NZ"
        if access.get("state") != "permitted" or method not in access.get("methods", []):
            return "Explicit permission for this retrieval method has not been recorded"
        reviewed = moment(access.get("reviewed_at"))
        if not reviewed or datetime.now(timezone.utc) - reviewed > timedelta(days=access["review_max_age_days"]):
            return "Source permission review is stale"
        if storage and not access.get("storage_allowed"):
            return "Source storage permission is not recorded"
        if validation:
            record = source.get("_nz_validation") or {}
            checked = moment(record.get("checked_at"))
            if record.get("fingerprint") != fingerprint(source) or record.get("status") != "validated" or record.get("method") != method:
                return "A current server-recorded NZ sample validation is required"
            if not checked or checked > datetime.now(timezone.utc) + timedelta(seconds=30) or datetime.now(timezone.utc) - checked > timedelta(hours=access["validation_max_age_hours"]):
                return "Source validation is stale or invalid"
        return None
    except (ValueError, TypeError, KeyError):
        return "Source access metadata is invalid"


def require(source, *, method="http", validation=True, storage=True):
    reason = readiness(source, method=method, validation=validation, storage=storage)
    if reason:
        raise SourceAccessError(reason)


def check_url(source, url, *, method="http", validation=True, storage=True):
    from applypilot.source_registry import validate_url
    require(source, method=method, validation=validation, storage=storage)
    validate_url(url, resolve=True)
    host = (urlsplit(url).hostname or "").lower()
    roots = {urlsplit(source.get(k) or "").hostname for k in ("url", "base_url")}
    roots.update(source.get("retrieval_hosts") or [])
    if host not in roots or domain_is(host, "immigration.govt.nz") or (method == "http" and restricted_host(host)):
        raise SourceAccessError("Redirect/destination is outside this source's permitted retrieval hosts")
    return url


@contextmanager
def request_scope():
    if _scope.get() is not None:
        yield
        return
    token = _scope.set({"counts": {}, "last": {}})
    try:
        yield
    finally:
        _scope.reset(token)


def throttle(source):
    scope = _scope.get()
    if scope is None:
        raise SourceAccessError("NZ network retrieval requires an explicit bounded request scope")
    ident = source.get("id") or source.get("name")
    access = validate_metadata(source)["access"]
    with _lock:
        count = scope["counts"].get(ident, 0)
        if count >= int(access["max_requests_per_run"]):
            raise SourceAccessError("Source request limit reached; retry only in a new explicit operation")
        pause = float(access["min_interval_seconds"]) - (time.monotonic() - scope["last"].get(ident, 0))
        if pause > 0:
            time.sleep(pause)
        scope["counts"][ident] = count + 1
        scope["last"][ident] = time.monotonic()


def read_http(source, url, *, validation=True, storage=True):
    """Shared bounded, redirect-checked GET for NZ preview/discovery/availability.
    No raw-page cache is written (zero is within every configured cache bound).
    """
    import httpx
    from urllib.parse import urljoin
    for _ in range(5):
        check_url(source, url, validation=validation, storage=storage)
        throttle(source)
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            with client.stream("GET", url) as response:
                status = response.status_code
                if status in {301, 302, 303, 307, 308}:
                    if not response.headers.get("location"):
                        raise SourceAccessError("Source redirect has no destination")
                    url = urljoin(url, response.headers["location"])
                    continue
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 2_000_000:
                        raise SourceAccessError("Source response exceeds the 2 MB limit")
                    chunks.append(chunk)
                return {"url": url, "http_status": status, "html": b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")}
    raise SourceAccessError("Source redirected too many times")
