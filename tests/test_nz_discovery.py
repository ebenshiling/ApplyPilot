"""Synthetic Phase 2 acceptance. No live source rights/reliability are implied."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from applypilot import config, country_context as country, database as db, source_access as access, source_registry as registry
from applypilot.discovery import nz, observations as obs
from applypilot.discovery.salary_filter import normalize_nz_salary, nz_salary_ok
from test_country_foundation import foundation  # noqa: F401
from test_command_center import http_workspace, workspace  # noqa: F401


@pytest.fixture
def nz_workspace(foundation, monkeypatch):
    root, conn, cfg = foundation
    cfg = cfg | {"nz_workflow_enabled": True}
    (root / "searches.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    network = Mock(side_effect=AssertionError("External sockets are prohibited in fixture tests"))
    monkeypatch.setattr(socket.socket, "connect", network)
    yield root, conn, cfg
    network.assert_not_called()


def permitted(root, *, kind="direct", validated=True):
    now = access.now()
    item = {"id": "nz-fixture", "name": "NZ fixture", "type": "static", "url": "https://careers.example.nz/jobs", "countries": ["NZ"],
        "identity_query_keys": ["id"], "application_hosts": ["apply.example.nz"],
        "access": {"state": "permitted", "terms_url": "https://careers.example.nz/terms", "permission_reference": "Synthetic permitted fixture, not real entitlement",
                   "reviewed_at": now, "methods": ["http"], "storage_allowed": True, "ai_use_allowed": False, "min_interval_seconds": .1, "max_requests_per_run": 3}}
    if kind == "workday":
        item |= {"id": "nz_workday", "name": "NZ Workday fixture", "base_url": "https://fixture.myworkdayjobs.com", "tenant": "fixture", "site_id": "Careers"}
        item["access"]["methods"] = ["workday_cxs"]
        nz.save_source(root, {"kind": "workday", "source": item})
        result = nz.source(root, "workday-nz_workday")
    else:
        nz.save_source(root, {"kind": "direct", "source": item})
        result = nz.source(root, "nz-fixture")
    if validated:
        method = "workday_cxs" if kind == "workday" else "http"
        nz._record_validation(root, result, {"fingerprint": access.fingerprint(result), "method": method, "status": "validated", "checked_at": now, "samples": 1, "nz_samples": 1})
    return nz.source(root, result["id"])


def context(root, cfg, conn, campaign=None):
    effective = nz.effective(cfg, campaign or nz.preset())
    return country.resolve(root, country_code="NZ", config=effective, campaign_id=nz.CAMPAIGN_ID, conn=conn)


def vacancy(**patch):
    return {"url": "https://careers.example.nz/job?id=123&utm_source=fixture", "title": "Software Developer", "company": "Fixture NZ", "location": "Auckland, New Zealand",
            "description": "A permitted synthetic description. " * 10 + "Existing work rights are required.",
            "application_url": "https://apply.example.nz/apply?id=123&signature=a%2Bb&token=keep", "date_posted": "2026-10-01", "provider_job_id": "REQ123"} | patch


def test_campaign_append_edit_preserve_uk_bytes_and_semantics(nz_workspace):
    root, conn, cfg = nz_workspace
    original = "# UK owner comments\nnz_workflow_enabled: true\ncountry: UK\nqueries: [{query: Analyst, tier: 1}]\nlocations: [{location: 'London, UK'}]\nboards: [indeed]\ncampaigns:\n  - id: uk-search\n    name: UK search # keep me\n    queries: [{query: Analyst}]\nowner_extension: {preserve: true}\n"
    path = root / "searches.yaml"
    path.write_text(original, encoding="utf-8")
    root_before = yaml.safe_load(original)
    out = nz.save_campaign(conn, root, {"expected_revision": nz.revision(root_before)})
    saved = path.read_text(encoding="utf-8")
    assert "# UK owner comments" in saved and "name: UK search # keep me" in saved
    assert saved[:saved.index("  - id: nz-job-search")] == original[:original.index("owner_extension:")]
    assert saved.endswith("owner_extension: {preserve: true}\n")
    changed = out["campaign"] | {"queries": [{"query": "Data Analyst", "tier": 1}]}
    nz.save_campaign(conn, root, {"expected_revision": out["config_revision"], "campaign": changed})
    after = path.read_text(encoding="utf-8")
    assert after[:after.index("  - id: nz-job-search")] == saved[:saved.index("  - id: nz-job-search")]
    parsed = yaml.safe_load(after)
    assert parsed["campaigns"][0] == root_before["campaigns"][0]
    assert {k:v for k,v in parsed.items() if k != "campaigns"} == {k:v for k,v in root_before.items() if k != "campaigns"}
    with pytest.raises(ValueError, match="changed"):
        nz.save_campaign(conn, root, {"expected_revision": "stale"})


@pytest.mark.parametrize("campaigns", ["campaigns: []\n", "", "campaigns:\n- id: uk\n  name: UK\n"])
def test_campaign_install_empty_missing_and_block_list(nz_workspace, campaigns):
    root, conn, cfg = nz_workspace
    path = root / "searches.yaml"
    before = yaml.safe_dump(cfg) + campaigns
    path.write_text(before, encoding="utf-8")
    nz.save_campaign(conn, root, {"expected_revision": nz.revision(yaml.safe_load(before))})
    assert sum(c["id"] == nz.CAMPAIGN_ID for c in yaml.safe_load(path.read_text())["campaigns"]) == 1


def test_campaign_cannot_inherit_uk_filters_sources_or_documents(nz_workspace):
    root, conn, cfg = nz_workspace
    effective = nz.effective(cfg | {"exclude_titles": ["Developer"], "smart_sites": ["NHS Jobs"], "proxy": "private", "compensation": {"salary_currency": "GBP"}}, nz.preset())
    assert effective["boards"] == effective["smart_sites"] == effective["workday_employers"] == []
    assert effective["exclude_titles"] == [] and "proxy" not in effective and "compensation" not in effective
    assert effective["defaults"]["country_indeed"] == "new zealand"
    assert {p["city"] for p in effective["locations"]} == {"Auckland", "Wellington", "Christchurch"}
    assert len(effective["career_categories"]) == 3


def test_manual_mode_empty_sources_has_no_provider_calls(nz_workspace, monkeypatch):
    root, conn, cfg = nz_workspace
    from applypilot.discovery import jobspy, workday, smartextract
    calls = Mock(side_effect=AssertionError("No adapter should retrieve"))
    monkeypatch.setattr(jobspy, "_load_scraper", calls)
    monkeypatch.setattr(workday, "workday_search", calls)
    monkeypatch.setattr(smartextract, "collect_page_intelligence", calls)
    with country.operation(context(root, cfg, conn)):
        for f in (jobspy.run_discovery, workday.run_workday_discovery, smartextract.run_smart_extract):
            out = f()
            assert out["queries"] == 0 and out["status"] == "skipped"
    out = nz.preview(conn, root)
    assert out["readiness"]["mode"] == "manual" and out["automatic_coverage_gate"] == "blocked"
    calls.assert_not_called()


@pytest.mark.parametrize("change,reason", [({"disabled": True}, "disabled"), ({"countries": ["GB"]}, "NZ"), ({"access": {}}, "permission"), ({"access": {"state": "restricted"}}, "permission")])
def test_permission_fail_closed(nz_workspace, change, reason):
    root, _, _ = nz_workspace
    item = permitted(root)
    item |= change
    assert reason.lower() in access.readiness(item).lower()


def test_permission_and_validation_are_separate_stale_fingerprinted_records(nz_workspace):
    root, _, _ = nz_workspace
    item = permitted(root, validated=False)
    assert access.readiness(item, validation=False) is None
    assert "validation" in access.readiness(item)
    item = permitted(root)
    assert access.readiness(item) is None
    item["access"]["permission_reference"] = "Changed agreement"
    assert "validation" in access.readiness(item)
    item = permitted(root)
    item["access"]["reviewed_at"] = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    assert "stale" in access.readiness(item)
    with pytest.raises(ValueError):
        access.validate_metadata({"access": {"state": "permitted", "methods": ["http"], "reviewed_at": "2026-01-01"}})


@pytest.mark.parametrize("url", ["https://nz.indeed.com/jobs", "https://www.linkedin.com/jobs", "https://seek.co.nz/jobs", "https://www.immigration.govt.nz/jobs"])
def test_restricted_services_no_generic_preview(nz_workspace, url):
    root, _, _ = nz_workspace
    item = permitted(root) | {"url": url, "base_url": url}
    with pytest.raises(ValueError):
        with access.request_scope():
            access.read_http(item, url, validation=False)


def test_board_geographic_support_never_proves_access(nz_workspace):
    root, _, _ = nz_workspace
    board = nz.board_source("indeed", {"countries": ["NZ"], "access": permitted(root)["access"] | {"methods": ["jobspy"]}})
    assert "per-HTTP-request" in access.readiness(board, method="jobspy", validation=False)
    for name in ("google", "zip_recruiter"):
        assert "not an available" in access.readiness(nz.board_source(name), method="jobspy")


def test_preview_uses_bounded_http_no_ai_and_validation_needs_real_nz_text(nz_workspace, monkeypatch):
    root, _, _ = nz_workspace
    item = permitted(root, validated=False)
    payload = {"@type": "JobPosting", "title": "Developer", "url": "https://careers.example.nz/job?id=1", "description": "Synthetic permitted text. " * 20,
               "jobLocation": {"address": {"addressLocality": "Auckland", "addressCountry": "NZ"}}}
    monkeypatch.setattr(access, "read_http", lambda *a, **kw: {"url": item["url"], "http_status": 200, "html": '<script type="application/ld+json">' + json.dumps(payload) + '</script>'})
    result = nz.validate_source(root, item["id"])
    assert result["status"] == "validated" and access.readiness(nz.source(root, item["id"])) is None
    payload["description"] = "short"
    result = nz.validate_source(root, item["id"])
    assert result["status"] == "no_samples" and result["last_successful_check"]
    assert access.readiness(nz.source(root, item["id"]))


def test_request_caps_private_redirects_and_oversize_are_enforced(nz_workspace, monkeypatch):
    root, _, _ = nz_workspace
    item = permitted(root)
    monkeypatch.setattr(access.time, "sleep", lambda _: None)
    with pytest.raises(ValueError, match="scope"):
        access.throttle(item)
    with access.request_scope():
        for _ in range(3): access.throttle(item)
        with pytest.raises(ValueError, match="limit"):
            access.throttle(item)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(None, None, None, None, ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="private"):
        access.check_url(item, item["url"])
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(None, None, None, None, ("8.8.8.8", 443))])
    with pytest.raises(ValueError, match="hosts"):
        access.check_url(item, "https://other.example.nz/job")
    import httpx
    @contextmanager
    def fixture_stream(*a, **kw):
        yield SimpleNamespace(status_code=200, encoding="utf-8", iter_bytes=lambda: iter([b"x" * 2_000_001]))
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        stream = staticmethod(fixture_stream)
    monkeypatch.setattr(httpx, "Client", Client)
    with access.request_scope(), pytest.raises(ValueError, match="2 MB"):
        access.read_http(item, item["url"])


def test_manual_capture_no_dns_or_network_and_restricted_reference_only(nz_workspace, monkeypatch):
    root, conn, cfg = nz_workspace
    dns = Mock(side_effect=AssertionError("Manual capture never resolves DNS"))
    monkeypatch.setattr(socket, "getaddrinfo", dns)
    out = nz.capture(conn, root, {"url": "https://seek.co.nz/job?id=11&token=preserve", "title": "Developer", "location": "Auckland, NZ"})
    assert out["new"] == 1 and out["reference_only"]
    row = dict(conn.execute("SELECT * FROM jobs").fetchone())
    meta = obs.metadata(row)
    assert "token=preserve" in meta["original_listing_url"] and not meta["ai_use_allowed"] and row["full_description"] is None
    with pytest.raises(ValueError, match="restricted"):
        nz.capture(conn, root, {"url": row["url"], "title": "Developer", "reference_only": False, "job_text": "Forbidden", "storage_allowed": True, "permission_reference": "No real permission"})
    dns.assert_not_called()


def test_metadata_identity_dates_duplicates_preserve_application_and_uk(nz_workspace):
    root, conn, cfg = nz_workspace
    item = permitted(root)
    with country.operation(context(root, cfg, conn)):
        assert obs.store(conn, [vacancy()], item, method="json_ld_no_ai", query="Developer") == (1, 0)
        row = dict(conn.execute("SELECT * FROM jobs").fetchone())
        meta = obs.metadata(row)
        assert row["url"] == "https://careers.example.nz/job?id=123"
        assert meta["original_listing_url"].endswith("&utm_source=fixture")
        assert "signature=a%2Bb&token=keep" in row["application_url"]
        assert meta["posted_at"] == "2026-10-01" and meta["posting_date_precision"] == "date"
        assert meta["posted_at"] != meta["first_discovered_at"]
        assert meta["work_rights_evidence"][0]["classification"] == "unassessed"
        conn.execute("UPDATE jobs SET applied_at='2026-10-02',apply_status='applied',full_description='Submitted advert snapshot'")
        conn.commit()
        assert obs.store(conn, [vacancy(url="https://careers.example.nz/another?id=123", application_url="https://apply.example.nz/new", description="Changed" * 50)], item, method="json_ld_no_ai") == (0, 1)
        after = dict(conn.execute("SELECT * FROM jobs").fetchone())
        assert after["full_description"] == "Submitted advert snapshot" and after["application_url"] == row["application_url"]
        assert after["applied_at"] == "2026-10-02" and obs.metadata(after)["first_discovered_at"] == meta["first_discovered_at"]
        assert conn.execute("SELECT COUNT(*) FROM cc_campaign_jobs").fetchone()[0] == 1
        # Distinct query identities without shared provider IDs are separate jobs.
        obs.store(conn, [vacancy(url="https://careers.example.nz/job?id=456", provider_job_id="REQ456", application_url=None)], item, method="json_ld_no_ai")
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
        assert obs.metadata(dict(conn.execute("SELECT * FROM jobs WHERE url LIKE '%456'").fetchone()))["possible_duplicates"]
        conn.execute("INSERT INTO jobs(url,title,country_code) VALUES('https://careers.example.nz/job?id=999','UK existing','GB')")
        conn.commit()
        obs.store(conn, [vacancy(url="https://careers.example.nz/job?id=999", provider_job_id=None, application_url=None)], item, method="json_ld_no_ai")
        assert conn.execute("SELECT country_code FROM jobs WHERE title='UK existing'").fetchone()[0] == "GB"
        assert conn.execute("SELECT COUNT(*) FROM cc_campaign_jobs WHERE job_url LIKE '%999'").fetchone()[0] == 0


def test_wrong_country_rejected_unknown_kept_and_hybrid_not_remote(nz_workspace):
    root, conn, cfg = nz_workspace
    item = permitted(root)
    with country.operation(context(root, cfg, conn)):
        assert obs.store(conn, [vacancy(location="Auckland, United States")], item, method="json_ld_no_ai") == (0, 0)
        assert obs.store(conn, [vacancy(location=None)], item, method="json_ld_no_ai") == (1, 0)
    assert obs.metadata(dict(conn.execute("SELECT * FROM jobs").fetchone()))["country_status"] == "unknown"
    from applypilot.geography import SelectedLocations
    matcher = SelectedLocations({"country": "New Zealand", "locations": [{"location": "Remote, New Zealand", "country": "New Zealand", "remote": True}]})
    assert not matcher.matches("Wellington, NZ (Hybrid)")
    assert matcher.matches("Remote, New Zealand")


@pytest.mark.parametrize("raw,currency,minimum,maximum,interval", [
    ("NZ$80k–100k base per year + KiwiSaver", "NZD", 80000, 100000, "year"),
    ("NZD 80-100k gross p.a.", "NZD", 80000, 100000, "year"),
    ("NZD 40–50 per hour base", "NZD", 40, 50, "hour"),
    ("AUD 600 per day", "AUD", 600, 600, "day"),
    ("$90k–$110k per year", None, 90000, 110000, "year"),
    ("Competitive", None, None, None, None)])
def test_salary_evidence_units_and_k_ranges(raw, currency, minimum, maximum, interval):
    s = normalize_nz_salary(raw)
    assert (s["currency"], s["min"], s["max"], s["interval"]) == (currency, minimum, maximum, interval)


def test_salary_no_implicit_conversion_unknown_basis_or_currency():
    pref = {"min": 80000, "currency": "NZD", "interval": "year", "basis": "base"}
    hourly = normalize_nz_salary("NZD 40 per hour base; 40 hours per week")
    assert nz_salary_ok(hourly, pref) and hourly["annualisation_basis"] is None
    assert hourly["stated_hours_per_week"] == 40
    assert nz_salary_ok(normalize_nz_salary("NZD 20k per year"), pref)
    assert nz_salary_ok(normalize_nz_salary("GBP 20k base per year"), pref)
    assert not nz_salary_ok(normalize_nz_salary("NZD 20k base per year"), pref)
    assert nz_salary_ok(normalize_nz_salary("$20k base per year"), pref)
    assert normalize_nz_salary("$20k per year", convention={"currency": "NZD", "evidence": "Source agreement"})["currency_basis"] == "source_convention"
    assert normalize_nz_salary("USD 50k per year", {"currency": "NZD"})["parse_status"] == "unknown"


def test_workday_and_direct_ingestion_hooks_share_envelope(nz_workspace):
    root, conn, cfg = nz_workspace
    direct = permitted(root)
    workday = permitted(root, kind="workday")
    from applypilot.discovery.smartextract import _store_jobs_filtered
    from applypilot.discovery.workday import store_results
    with country.operation(context(root, cfg, conn)):
        assert _store_jobs_filtered(conn, [vacancy()], direct["id"], "fixture", [], [], "Developer") == (1, 0)
        wd = {"employer_key": "nz_workday", "external_path": "/job/Auckland/Developer_REQ1", "title": "Python Developer", "location": "Auckland, New Zealand", "job_req_id": "WD1", "full_description": "Workday permitted description. " * 20, "apply_url": "https://fixture.myworkdayjobs.com/apply?token=keep", "date_posted": "2026-10-01"}
        assert store_results(conn, [wd], {"nz_workday": workday}, search_query="Python") == (1, 0)
    metadata = [obs.metadata(dict(r)) for r in conn.execute("SELECT * FROM jobs")]
    assert {m["extraction_method"] for m in metadata} == {"json_ld_no_ai", "workday_api"}
    assert all(m["search_country"] == "NZ" and m["content_hash"] and m["posted_at"] for m in metadata)


@pytest.mark.parametrize("http_status,expected", [(200, "reachable"), (403, "unavailable"), (404, "closed"), (410, "closed")])
def test_listing_checks_original_destinations_and_truthful_states(nz_workspace, monkeypatch, http_status, expected):
    root, conn, cfg = nz_workspace
    item = permitted(root)
    with country.operation(context(root, cfg, conn)):
        obs.store(conn, [vacancy()], item, method="json_ld_no_ai")
    job = dict(conn.execute("SELECT * FROM jobs").fetchone())
    read = Mock(side_effect=lambda s,u: {"url":u, "http_status":http_status, "html":"Applications open"})
    monkeypatch.setattr(access, "read_http", read)
    out = nz.check_listing(conn, root, job)
    assert out["status"] == expected and read.call_count == 2
    assert read.call_args.args[1].endswith("signature=a%2Bb&token=keep")
    meta = obs.metadata(dict(conn.execute("SELECT * FROM jobs").fetchone()))
    assert meta["availability"]["checked_at"] != meta["posted_at"]


def test_reference_checks_and_legacy_enrichment_cannot_fetch(nz_workspace, monkeypatch):
    root, conn, cfg = nz_workspace
    nz.capture(conn, root, {"url": "https://www.linkedin.com/jobs/view/1", "title": "Reference"})
    job = dict(conn.execute("SELECT * FROM jobs").fetchone())
    read = Mock(side_effect=AssertionError("No retrieval"))
    monkeypatch.setattr(access, "read_http", read)
    assert nz.check_listing(conn, root, job)["status"] == "unavailable"
    from applypilot.enrichment import detail
    monkeypatch.setattr(detail, "sync_playwright", read)
    assert detail.scrape_site_batch(conn, "LinkedIn", [(job["url"], job["title"])])["processed"] == 0
    read.assert_not_called()


def test_discovery_only_context_preserves_generation_and_legacy_guards(nz_workspace):
    root, conn, cfg = nz_workspace
    effective = nz.effective(cfg, nz.preset())
    ctx = country.resolve_run(root, effective, "NZ", nz.CAMPAIGN_ID, stages=["discover"])
    assert ctx.country_code == "NZ"
    for stages in (None, ["all"], ["enrich"], ["discover", "score"], ["tailor"]):
        with pytest.raises(country.CountryError, match="preparation"):
            country.resolve_run(root, effective, "NZ", nz.CAMPAIGN_ID, stages=stages)
    with pytest.raises(country.CountryError):
        country.guard_discovery(effective)
    with country.operation(ctx):
        with pytest.raises(country.CountryError):
            db.store_jobs(conn, [vacancy()], "Unsafe generic ingestion", "generic")


def test_nz_api_is_scoped_default_off_and_rejects_wrong_country_jobs(nz_workspace):
    root, conn, cfg = nz_workspace
    from applypilot import command_api
    handler = SimpleNamespace(_send_json=Mock())
    command_api.post(handler, "/api/center/nz/capture", {"country_code": "NZ", "url": vacancy()["url"], "title": "Manual"}, root, None)
    assert handler._send_json.call_args.args[0] == 200
    command_api.get(handler, "/api/center/nz/discovery", "country_code=NZ", root)
    assert len(handler._send_json.call_args.args[1]["jobs"]) == 1
    command_api.get(handler, "/api/center/nz/discovery", "country_code=GB", root)
    assert handler._send_json.call_args.args[0] == 400
    conn.execute("INSERT INTO jobs(url,country_code) VALUES('https://uk.example.org/job','GB')")
    conn.commit()
    ident = conn.execute("SELECT rowid FROM jobs WHERE country_code='GB'").fetchone()[0]
    command_api.post(handler, "/api/center/nz/listing/check", {"country_code": "NZ", "id": ident}, root, None)
    assert handler._send_json.call_args.args[0] == 400
    for path in ("/api/center/studio/generate", "/api/center/browser/start"):
        command_api.post(handler, path, {"country_code": "NZ"}, root, None)
        assert handler._send_json.call_args.args[0] == 400
    (root / "searches.yaml").write_text(yaml.safe_dump(cfg | {"nz_workflow_enabled": False}), encoding="utf-8")
    command_api.get(handler, "/api/center/nz/discovery", "country_code=NZ", root)
    assert handler._send_json.call_args.args[0] == 400


def test_nz_source_addition_does_not_change_uk_catalog(nz_workspace):
    root, conn, cfg = nz_workspace
    before = config.load_sites_config(root)
    permitted(root)
    assert config.load_sites_config(root) == before
    with country.operation(context(root, cfg, conn)):
        assert {s["id"] for s in config.load_sites_config(root)["sites"]} == {"nz-fixture"}
    previous = config.load_sites_config(root)["sites"][0]
    with pytest.raises(ValueError, match="protected"):
        nz.save_source(root, {"kind": "direct", "source": previous | {"countries": ["NZ"]}})


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_lossless_campaign_bytes_and_unknown_root_country_keys(nz_workspace, newline):
    root, conn, cfg = nz_workspace
    original = ("# untouched UK bytes\ncountry_code: GB\n" + yaml.safe_dump(cfg)).replace("\n", newline).encode()
    (root / "searches.yaml").write_bytes(original)
    revision = nz.revision(yaml.safe_load(original))
    nz.save_campaign(conn, root, {"expected_revision": revision})
    saved = (root / "searches.yaml").read_bytes()
    assert saved.startswith(original)
    assert b"\r\r\n" not in saved
    assert country.resolve_view(root, "NZ", conn=conn).country_code == "NZ"
    assert nz.capture(conn, root, {"title": "Reference", "url": "https://employer.example.nz/job?id=1"})["new"] == 1
    with pytest.raises(ValueError, match="dedicated"):
        nz.capture(conn, root, {"title": "Bad membership", "url": "https://employer.example.nz/job?id=2", "campaign_id": "uk"})


def test_direct_campaign_execution_promotes_metadata_and_current_source_disable(nz_workspace, monkeypatch):
    root, conn, cfg = nz_workspace
    item = permitted(root)
    campaign = nz.preset() | {"smart_sites": [item["name"]]}
    payload = {"@type":"JobPosting", "title":"Software Developer", "url":vacancy()["url"], "description":vacancy()["description"],
               "identifier":{"value":"123"}, "datePosted":"2026-10-01", "jobLocation":{"address":{"addressLocality":"Auckland","addressCountry":"NZ"}}}
    read = Mock(return_value={"url": item["url"], "http_status": 200, "html": '<script type="application/ld+json">' + json.dumps(payload) + '</script>'})
    monkeypatch.setattr(access, "read_http", read)
    with country.operation(context(root, cfg, conn, campaign)):
        result = nz.run_provider("direct")
    assert result["new"] == 1 and result["queries"] == 1 and result["errors"] == 0
    row = dict(conn.execute("SELECT * FROM jobs").fetchone())
    assert row["detail_scraped_at"] and row["country_code"] == "NZ" and row["full_description"]
    assert row["sponsor_licensed"] is None and row["sponsorship_explicit"] is None
    registry.save_source(root, item | {"disabled":True})
    read.reset_mock()
    with country.operation(context(root, cfg, conn, campaign)):
        result = nz.run_provider("direct")
    assert result["status"] == "partial" and result["restricted_sources"]
    read.assert_not_called()


@pytest.mark.parametrize("state", ["sample", "empty", "schema_error", "timeout", "detail_error"])
def test_workday_execution_distinguishes_partial_failures_empty_and_dates(nz_workspace, monkeypatch, state):
    root, conn, cfg = nz_workspace
    item = permitted(root, kind="workday")
    campaign = nz.preset() | {"workday_employers":["nz_workday"], "queries":[{"query":"Developer","tier":1}]}
    def payload(source, **kwargs):
        if state == "timeout": raise TimeoutError("synthetic timeout")
        if kwargs.get("path"):
            if state == "detail_error": raise ValueError("synthetic detail unavailable")
            return {"jobPostingInfo":{"jobDescription":"<p>" + vacancy()["description"] + "</p>", "externalUrl":vacancy()["application_url"], "jobReqId":"WD1", "startDate":"2026-10-01", "endDate":"2027-01-01", "country":{"descriptor":"New Zealand"}}}
        if state == "schema_error": return {"error":"Denied"}
        return {"total":0,"jobPostings":[]} if state == "empty" else {"total":1,"jobPostings":[{"title":"Developer","locationsText":"Auckland, New Zealand","externalPath":"/job/Auckland/Developer_WD1","postedOn":"Posted Today"}]}
    monkeypatch.setattr(nz, "workday_payload", payload)
    with country.operation(context(root, cfg, conn, campaign)):
        out = nz.run_provider("workday")
    assert out["queries"] == 1
    if state == "empty":
        assert out["status"] == "ok" and out["found"] == out["errors"] == 0
    elif state in {"timeout", "schema_error"}:
        assert out["status"] == "partial" and out["errors"] == 1
    elif state == "detail_error":
        assert out["status"] == "partial" and out["new"] == 1
    else:
        assert out["status"] == "ok" and out["new"] == 1
        row = dict(conn.execute("SELECT * FROM jobs").fetchone())
        meta = obs.metadata(row)
        assert meta["posted_at"] == "2026-10-01" and meta["source_valid_through"] == "2027-01-01"
        assert "token=keep" in row["application_url"]


def test_jobspy_fixture_envelope_without_enabling_opaque_board_transport(nz_workspace):
    root, conn, cfg = nz_workspace
    item = nz.board_source("indeed", {"countries":["NZ"], "access":permitted(root)["access"]})
    raw = {"job_url":"https://nz.indeed.com/viewjob?jk=abc", "job_url_direct":vacancy()["application_url"], "title":"Developer", "id":"indeed-abc", "location":"Wellington, New Zealand", "date_posted":"2026-10-01", "description":vacancy()["description"], "currency":"NZD", "min_amount":80000, "max_amount":100000, "interval":"yearly"}
    result = obs.normalize(raw, item, method="jobspy")
    assert result["metadata"]["provider_job_id"] == "indeed-abc"
    assert result["metadata"]["salary"]["currency"] == "NZD"
    assert "token=keep" in result["application_url"]
    import pandas as pd
    from applypilot.discovery.jobspy import store_jobspy_results
    with country.operation(context(root, cfg, conn)), pytest.raises(ValueError):
        store_jobspy_results(conn, pd.DataFrame([raw | {"site":"indeed"}]), "indeed")
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_migration_rerun_and_legacy_url_helpers_preserve_nz_identity(tmp_path):
    path = tmp_path / "applypilot.db"
    with sqlite3.connect(path) as old:
        old.execute("CREATE TABLE jobs(url TEXT PRIMARY KEY,title TEXT,apply_status TEXT,applied_at TEXT)")
        old.execute("INSERT INTO jobs VALUES('https://employer.example/old?token=original','Historical','applied','2026-01-01')")
    conn = db.init_db(path)
    try:
        before = tuple(conn.execute("SELECT url,title,apply_status,applied_at,country_code,vacancy_metadata FROM jobs").fetchone())
        db.init_db(path)
        assert tuple(conn.execute("SELECT url,title,apply_status,applied_at,country_code,vacancy_metadata FROM jobs").fetchone()) == before
        conn.execute("INSERT INTO jobs(url,application_url,title,country_code) VALUES('/nz-unresolved?token=keep','/apply?token=keep','NZ','NZ')")
        conn.commit()
        from applypilot.enrichment.detail import resolve_all_urls
        resolve_all_urls(conn)
        assert conn.execute("SELECT url,application_url FROM jobs WHERE country_code='NZ'").fetchone()[:] == ('/nz-unresolved?token=keep','/apply?token=keep')
        assert before[0].endswith("token=original") and before[-2:] == (None, None)
    finally:
        db.close_connection(path)


def test_nz_local_ui_installs_campaign_captures_reference_and_returns_to_uk(http_workspace):
    if os.environ.get("APPLYPILOT_RUN_COUNTRY_UI") != "1":
        pytest.skip("Set APPLYPILOT_RUN_COUNTRY_UI=1 for local-only NZ discovery UX")
    from playwright.sync_api import sync_playwright
    base, root, conn = http_workspace
    original = b"# UK search stays intact\nnz_workflow_enabled: true\ncountry: UK\nqueries: [{query: Analyst}]\nlocations: [{location: 'London, UK'}]\n"
    (root / "searches.yaml").write_bytes(original)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width":1400,"height":1000})
            page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(base + "/workspace")
            page.wait_for_function("document.querySelector('#job-search') !== null")
            page.locator("#workspace-country").select_option("NZ")
            page.locator("#nz-search-form").wait_for()
            page.locator("#nz-advanced > summary").click()
            page.locator("#nz-campaign-form").wait_for()
            assert page.locator("#nz-start").is_disabled()
            assert page.locator("[data-nz-source]:checked").count() == 0
            page.locator("#nz-campaign-form button[type=submit]").click()
            page.wait_for_function("document.querySelector('#nz-campaign-form button[type=submit]')?.textContent === 'Save NZ campaign'")
            assert (root / "searches.yaml").read_bytes().startswith(original)
            page.locator("#nz-show-save").click()
            page.locator("#nz-url").fill("https://seek.co.nz/job?id=123&token=preserve")
            page.locator("#nz-title").fill("Reference vacancy <safe>")
            page.locator("#nz-vacancy-details > summary").click()
            page.locator("#nz-location").fill("Auckland, New Zealand")
            page.locator("#nz-capture-form button[type=submit]").click()
            page.wait_for_function("document.querySelector('#nz-capture-form') && [...document.querySelectorAll('#content h3')].some(n => n.textContent === 'Reference vacancy <safe>')")
            assert conn.execute("SELECT COUNT(*) FROM jobs WHERE country_code='NZ'").fetchone()[0] == 1
            assert page.locator('a[href="https://seek.co.nz/job?id=123&token=preserve"]').count() == 1
            page.locator("#workspace-country").select_option("GB")
            page.wait_for_function("document.querySelector('#page-title').textContent === 'Make it yours.'")
            assert conn.execute("SELECT COUNT(*) FROM jobs WHERE country_code IS NULL").fetchone()[0] == 1
            assert not errors
        finally:
            browser.close()
