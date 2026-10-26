"""Workspace-owned catalogs merged with packaged defaults without global mutation."""
from __future__ import annotations

import copy
import ipaddress
import json
from pathlib import Path
import re
import socket
from string import Formatter
from urllib.parse import urlsplit, urljoin

import yaml

INDEED_TEMPLATE = "https://uk.indeed.com/jobs?q={query_encoded}&l={location_encoded}"


class SourcePreviewUnavailable(RuntimeError):
    """A sanitized site/network preview failure, unrelated to AI configuration."""
    def __init__(self, message, category, *, http_status=None):
        super().__init__(message)
        self.category = category
        self.http_status = http_status


def _workspace_catalog(app_dir):
    path = app_dir / "sites.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    except yaml.YAMLError as exc:
        raise ValueError("Workspace sites.yaml contains invalid YAML; correct the source settings before saving") from exc
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise ValueError("Workspace sites.yaml must be an object")
    entries = data.get("sites", [])
    entries = [] if entries is None else entries
    if not isinstance(entries, list) or any(not isinstance(s, dict) or not isinstance(s.get("name"), str) or not s["name"].strip() for s in entries):
        raise ValueError("Workspace sites.yaml 'sites' must be a list of named source objects")
    bases = data.get("base_urls", {})
    bases = {} if bases is None else bases
    if not isinstance(bases, dict):
        raise ValueError("Workspace sites.yaml 'base_urls' must be an object")
    return data | {"sites": entries, "base_urls": bases}


def validate_source(item):
    if not isinstance(item, dict):
        raise ValueError("Provide a source object with a name, type and public URL")
    name = str(item.get("name") or "").strip()
    ident = str(item.get("id") or source_id(name)).strip()
    if not name or len(name) > 120 or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,119}", ident):
        raise ValueError("Provide a source name and a valid stable identifier")
    url = str(item.get("url") or "").strip()
    if len(url) > 2000:
        raise ValueError("Source URL is too long")
    validate_url(url)
    try:
        fields = [(field, spec, conversion) for _, field, spec, conversion in Formatter().parse(url) if field is not None]
    except ValueError as exc:
        raise ValueError("Source URL has unmatched placeholder braces") from exc
    if any(field not in {"query", "query_encoded", "location_encoded", "country_encoded", "state_encoded", "city_encoded"} or spec or conversion for field, spec, conversion in fields) or "{{" in url or "}}" in url:
        raise ValueError("Supported placeholders: {query_encoded}, {location_encoded}, {country_encoded}, {state_encoded}, {city_encoded}, {query}")
    kind = item.get("type", "search")
    if kind not in {"search", "static"}:
        raise ValueError("Source type must be search or static")
    if kind == "search" and not any(field in {"query", "query_encoded"} for field, _, _ in fields):
        hint = f" For Indeed, use {INDEED_TEMPLATE}." if (urlsplit(url).hostname or "").lower() in {"indeed.com", "www.indeed.com", "uk.indeed.com"} else ""
        raise ValueError("Search URL templates need {query_encoded} or {query}. Enter the site's search-results URL, or select Static listing page for a fixed careers/jobs page." + hint)
    if kind == "static" and any(field not in {'location_encoded','country_encoded','state_encoded','city_encoded'} for field, _, _ in fields):
        raise ValueError("Static listing pages cannot contain role/query placeholders; select Search URL template for a role search")
    base = str(item.get("base_url") or f"{urlsplit(url).scheme}://{urlsplit(url).netloc}").strip()
    validate_url(base)
    if "{" in base or "}" in base:
        raise ValueError("Base URL for relative links must not contain placeholders")
    from applypilot.source_access import validate_metadata
    return {"id": ident, "name": name, "url": url, "type": kind, "disabled": bool(item.get("disabled", False)), "base_url": base} | validate_metadata(item)


def source_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def catalog(app_dir: Path, packaged: dict, *, include_all=False) -> dict:
    output = copy.deepcopy(packaged)
    custom = _workspace_catalog(app_dir)
    entries = {str(s.get("id") or source_id(s["name"])): dict(s, origin="packaged") for s in output.get("sites", [])}
    for item in custom.get("sites", []):
        ident = str(item.get("id") or source_id(item["name"]))
        entries[ident] = dict(entries.get(ident, {})) | item | {"origin": "workspace"}
    output["base_urls"] = dict(output.get("base_urls", {})) | dict(custom.get("base_urls", {}))
    output["sites"] = [item | {"id": ident, "base_url": item.get("base_url") or output["base_urls"].get(item["name"])} for ident, item in entries.items()]
    if not include_all:
        from applypilot.country_context import current
        nz = current() is not None and current().country_code == "NZ"
        visible = [s for s in output["sites"] if ("NZ" in s.get("countries", []) if nz else s.get("countries") != ["NZ"])]
        hidden_names = {s["name"] for s in output["sites"]} - {s["name"] for s in visible}
        output["base_urls"] = {k:v for k,v in output["base_urls"].items() if k not in hidden_names}
        output["sites"] = visible
    return output


def validate_url(url: str, *, resolve: bool = False) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Source URL must be a public HTTP(S) URL without embedded credentials")
    if any(c.isspace() for c in parsed.netloc):
        raise ValueError("Source hostname must not contain whitespace")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Source URL needs a valid HTTP(S) port")
    if parsed.hostname.lower() == "localhost" or parsed.hostname.lower().endswith((".localhost", ".local", ".internal")):
        raise ValueError("Local/private source URLs are not supported")
    if "{" in parsed.netloc or "}" in parsed.netloc:
        raise ValueError("Source placeholders belong in the path/query, not the hostname")
    try:
        literal = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal = None
    if literal and not literal.is_global:
        raise ValueError("Source URL cannot point to a private address")
    if resolve:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError("Source resolves to a private or unavailable address")
    return url


def save_source(app_dir: Path, item: dict) -> dict:
    from applypilot.setup_workspace import _atomic_write_text
    clean = validate_source(item)
    name, ident = clean["name"], clean["id"]
    from applypilot.config import load_sites_config
    existing = load_sites_config(app_dir, include_all=True).get("sites", [])
    if any((s["name"].casefold() == name.casefold() and s["id"] != ident) or (s["id"] == ident and not item.get("id")) for s in existing):
        raise ValueError("A source with this name/identifier already exists. Choose Edit / test to update it")
    path = app_dir / "sites.yaml"
    data = _workspace_catalog(app_dir)
    previous = next((s for s in data["sites"] if str(s.get("id") or source_id(s["name"])) == ident), {})
    stored = previous | clean
    data["sites"] = [s for s in data["sites"] if str(s.get("id") or source_id(s["name"])) != ident] + [stored]
    data["base_urls"][name] = clean["base_url"]
    _atomic_write_text(path, yaml.safe_dump(data, sort_keys=False))
    return clean


def remove_source(app_dir: Path, ident: str) -> None:
    from applypilot.setup_workspace import _atomic_write_text
    path = app_dir / "sites.yaml"
    if not path.exists():
        raise ValueError("This source has no workspace override")
    data = _workspace_catalog(app_dir)
    removed = [s for s in data.get("sites", []) if str(s.get("id") or source_id(s["name"])) == ident]
    if not removed:
        raise ValueError("Workspace source not found")
    data["sites"] = [s for s in data.get("sites", []) if s not in removed]
    for s in removed:
        data.get("base_urls", {}).pop(s["name"], None)
    _atomic_write_text(path, yaml.safe_dump(data, sort_keys=False))


def preview(app_dir: Path, item: dict, query: str, location: str) -> dict:
    """Bounded read-only structured extraction; no inference or DB insertion."""
    import httpx
    from bs4 import BeautifulSoup
    from applypilot.discovery.smartextract import build_scrape_targets
    explicit_base = str(item.get('base_url') or '').strip() if isinstance(item,dict) else ''
    item = validate_source(item)
    from applypilot.country_context import current
    if "NZ" in item.get("countries", []) or (current() and current().country_code == "NZ"):
        from applypilot.source_access import read_http, request_scope
        from applypilot.country_context import require_enabled
        from applypilot.setup_workspace import read_searches_dict
        require_enabled("NZ", read_searches_dict(app_dir))
        from applypilot.discovery.observations import structured_jobs
        from urllib.parse import quote_plus
        target = item["url"].replace("{query_encoded}", quote_plus(query)).replace("{query}", quote_plus(query)).replace("{location_encoded}", quote_plus(location))
        target = target.replace("{country_encoded}", "New+Zealand").replace("{state_encoded}", "").replace("{city_encoded}", quote_plus(location.split(",")[0]))
        with request_scope():
            response = read_http(item, target, validation=False, storage=False)
        if response["http_status"] != 200:
            raise SourcePreviewUnavailable(f"NZ source returned HTTP {response['http_status']}; availability/extraction is unknown", "site_error", http_status=response["http_status"])
        jobs = structured_jobs(response["html"], response["url"])
        return {"url": response["url"], "strategy": "structured JSON-LD (no AI)", "jobs": jobs[:25], "candidate_links": [],
                "message": "Structured samples found" if jobs else "No structured samples; manual capture or a supported extractor is needed"}
    item['base_url'] = explicit_base or None
    search_cfg={"queries": [{"query": query or "Analyst"}], "locations": [{"location": location or "London"}]}
    if any('{'+k+'_encoded}' in item['url'] for k in ('country','state','city')):
        from applypilot.geography import default_country, source_country
        from applypilot.setup_workspace import read_searches_dict
        search_cfg.update({'geography_enabled':True,'country':source_country(item) or default_country(read_searches_dict(app_dir))})
    targets = build_scrape_targets(sites=[item], search_cfg=search_cfg)
    if not targets:
        raise ValueError("Enable this source to test extraction")
    url = targets[0]["url"]
    try:
        return _preview_targets(httpx, BeautifulSoup, item, url)
    except SourcePreviewUnavailable:
        raise
    except httpx.TimeoutException as exc:
        raise SourcePreviewUnavailable("The source preview timed out. The source can still be saved; try the preview again later.", "timeout") from exc
    except (httpx.HTTPError, OSError) as exc:
        raise SourcePreviewUnavailable("Could not connect to the source for extraction preview. Check its public URL and retry; saving does not require a successful preview.", "network") from exc


def _preview_targets(httpx, BeautifulSoup, item, url):
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        for _ in range(5):
            validate_url(url, resolve=True)
            response = client.get(url)
            if response.is_redirect:
                destination = response.headers.get("location")
                if not destination:
                    raise SourcePreviewUnavailable("The source returned a redirect without a destination. Check its public URL and retry.", "redirect", http_status=response.status_code)
                url = urljoin(url, destination)
                continue
            status = response.status_code
            host = urlsplit(url).hostname
            if status >= 400:
                category = "blocked" if status in {401,403} else "rate_limited" if status == 429 else "not_found" if status == 404 else "site_error"
                detail = "blocked the automated preview" if category == "blocked" else "rate-limited the preview" if category == "rate_limited" else "did not find this page" if category == "not_found" else "could not serve this preview"
                raise SourcePreviewUnavailable(f"{host} {detail} (HTTP {status}). The source can still be saved. Check the search-results URL or retry later; this preview uses no AI provider.", category, http_status=status)
            if len(response.content) > 2_000_000:
                raise ValueError("Preview page exceeds the 2 MB limit")
            soup = BeautifulSoup(response.text, "html.parser")
            if re.search(r"verify you are human|checking your browser|just a moment|captcha challenge", soup.get_text(" ", strip=True)[:5000], re.I):
                raise SourcePreviewUnavailable(f"{host} returned a browser verification page. The source can still be saved; this read-only preview cannot extract listings from that page.", "challenge", http_status=status)
            jobs = []
            def collect(value):
                if isinstance(value, list):
                    for child in value:
                        collect(child)
                elif isinstance(value, dict):
                    if "JobPosting" in str(value.get("@type", "")):
                        employer = value.get("hiringOrganization") or {}
                        jobs.append({"title": value.get("title"), "company": employer.get("name") if isinstance(employer, dict) else employer,
                                     "url": urljoin(item.get("base_url") or url, value.get("url") or url), "description": BeautifulSoup(str(value.get("description") or ""), "html.parser").get_text(" ")[:1000]})
                    collect(value.get("@graph", []))
            for script in soup.select('script[type="application/ld+json"]'):
                try:
                    collect(json.loads(script.get_text()))
                except ValueError:
                    continue
            links = [{"title": a.get_text(" ", strip=True)[:150], "url": urljoin(item.get("base_url") or url, a["href"])} for a in soup.select("a[href]") if a.get_text(strip=True) and any(k in a["href"].lower() for k in ("job", "career", "vacan"))]
            return {"url": url, "strategy": "structured JSON-LD / candidate links (no AI)", "jobs": jobs[:25], "candidate_links": links[:25],
                    "message": "Structured jobs found" if jobs else "No structured jobs found; inspect candidate links. Browser/AI extraction may be needed during discovery."}
    raise SourcePreviewUnavailable("The source redirected too many times. Check its public search/listing URL and retry.", "redirect")


def employers(app_dir: Path, *, include_disabled=False, include_all=False) -> dict:
    from applypilot.config import CONFIG_DIR
    path = CONFIG_DIR / "employers.yaml"
    base = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    result = {k: dict(v, origin="packaged") for k, v in base.get("employers", {}).items()}
    local = app_dir / "employers.yaml"
    custom = yaml.safe_load(local.read_text(encoding="utf-8")) if local.exists() else {}
    for key, value in (custom or {}).get("employers", {}).items():
        result[key] = dict(result.get(key, {})) | value | {"origin": "workspace"}
    if not include_all:
        from applypilot.country_context import current
        nz = current() is not None and current().country_code == "NZ"
        result = {k: v for k, v in result.items() if ("NZ" in v.get("countries", []) if nz else v.get("countries") != ["NZ"])}
    return result if include_disabled else {k: v for k, v in result.items() if not v.get("disabled")}


def save_employer(app_dir: Path, item: dict) -> dict:
    from applypilot.setup_workspace import _atomic_write_text
    key = str(item.get("id") or source_id(str(item.get("name") or "")))
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", key):
        raise ValueError("Invalid employer ID")
    clean = {k: str(item.get(k) or "").strip() for k in ("name", "tenant", "site_id", "base_url")}
    if not clean["name"] or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", clean[k]) for k in ("tenant", "site_id")):
        raise ValueError("Employer needs a name, tenant and Workday site ID")
    validate_url(clean["base_url"])
    if not (urlsplit(clean["base_url"]).hostname or "").endswith(".myworkdayjobs.com"):
        raise ValueError("Workday employers must use a myworkdayjobs.com base URL")
    clean["disabled"] = bool(item.get("disabled"))
    from applypilot.source_access import validate_metadata
    clean.update(validate_metadata(item))
    path = app_dir / "employers.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = data or {}
    if item.get("delete"):
        data.setdefault("employers", {}).pop(key, None)
    else:
        previous = data.setdefault("employers", {}).get(key, {})
        data["employers"][key] = {k:v for k,v in previous.items() if k == "_nz_validation"} | clean
    _atomic_write_text(path, yaml.safe_dump(data, sort_keys=False))
    return clean


def preview_employer(item: dict, query: str) -> dict:
    import httpx
    base = str(item.get("base_url") or "").rstrip("/")
    from applypilot.country_context import current
    if "NZ" in item.get("countries", []) or (current() and current().country_code == "NZ"):
        from applypilot.discovery.nz import workday_payload
        from applypilot.source_access import request_scope
        with request_scope():
            data = workday_payload(item, query=query, validation=False)
        return {"strategy": "Workday CXS (no AI)", "total": data.get("total"), "jobs": data.get("jobPostings", [])[:10]}
    validate_url(base, resolve=True)
    if not (urlsplit(base).hostname or "").endswith(".myworkdayjobs.com"):
        raise ValueError("Use a Workday employer portal")
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", str(item.get(k) or "")) for k in ("tenant", "site_id")):
        raise ValueError("Invalid Workday tenant/site")
    response = httpx.post(f"{base}/wday/cxs/{item['tenant']}/{item['site_id']}/jobs", json={"appliedFacets": {}, "limit": 10, "offset": 0, "searchText": query}, timeout=15, follow_redirects=False)
    response.raise_for_status()
    return {"strategy": "Workday CXS (no AI)", "total": response.json().get("total"), "jobs": response.json().get("jobPostings", [])[:10]}
