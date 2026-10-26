"""Phase 1 invariants: synthetic SQLite/files, no providers or employer browser."""
from contextvars import copy_context
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from applypilot import config, country_context as country, database as db
from test_command_center import http_workspace, workspace  # noqa: F401


@pytest.fixture
def foundation(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "SEARCH_CONFIG_PATH", tmp_path / "searches.yaml")
    monkeypatch.delenv("APPLYPILOT_SEARCHES_PATH", raising=False)
    monkeypatch.delenv("APPLYPILOT_COUNTRY_CODE", raising=False)
    monkeypatch.delenv("APPLYPILOT_NZ_DISABLED", raising=False)
    searches = {"country": "UK", "queries": [{"query": "Analyst", "tier": 1}],
                "locations": [{"location": "London, UK"}], "boards": ["indeed"]}
    (tmp_path / "searches.yaml").write_text(yaml.safe_dump(searches), encoding="utf-8")
    (tmp_path / "profile.json").write_text('{"personal":{"full_name":"Fixture"},"work_authorization":{"legally_authorized_to_work":"Yes"}}', encoding="utf-8")
    conn = db.init_db(tmp_path / "applypilot.db")
    yield tmp_path, conn, searches
    db.close_connection(tmp_path / "applypilot.db")


@pytest.mark.parametrize("value,expected", [("UK", "GB"), ("gb", "GB"), ("United Kingdom", "GB"),
                                           ("NZ", "NZ"), ("New Zealand", "NZ"), (None, None)])
def test_country_codes(value, expected):
    assert country.normalize_country(value) == expected


@pytest.mark.parametrize("value", ["", "unknown", "ZZ", "Canada", 1, False, [], {}])
def test_invalid_country_fails(value):
    with pytest.raises(country.CountryError):
        country.normalize_country(value)


def test_legacy_job_is_not_reassigned_by_selected_tab(foundation):
    root, conn, cfg = foundation
    context = country.resolve(root, job={"country_code": None}, country_code="GB", config=cfg)
    assert context.country_code is None and context.public()["legacy"]
    with pytest.raises(country.CountryError, match="persisted"):
        country.resolve(root, job={"country_code": None}, country_code="NZ", config=cfg | {"nz_workflow_enabled": True})
    with pytest.raises(country.CountryError, match="persisted"):
        country.resolve(root, job={"country_code": "NZ"}, country_code="GB", config=cfg)


def test_immutable_snapshot_and_copied_worker_context_ignore_later_edits(foundation):
    root, conn, cfg = foundation
    ctx = country.resolve(root, country_code="GB", config=cfg, conn=conn)
    cfg["country"] = "NZ"
    ctx.config["country"] = "NZ"
    with pytest.raises(FrozenInstanceError):
        ctx.country_code = "NZ"
    with country.operation(ctx):
        worker = copy_context()
    (root / "searches.yaml").write_text("country: NZ\n", encoding="utf-8")
    with country.operation(country.resolve(root, country_code="NZ", config={"nz_workflow_enabled": True})):
        assert worker.run(config.load_search_config)["country"] == "UK"
        assert worker.run(country.current).country_code == "GB"
    assert country.current() is None


def test_feature_flag_is_strict_and_supports_deployment_enablement(foundation, monkeypatch):
    root, conn, cfg = foundation
    for flag in (None, False, "true", 1):
        with pytest.raises(country.CountryError, match="disabled"):
            country.resolve(root, country_code="NZ", config=cfg | {"nz_workflow_enabled": flag})
    assert country.resolve(root, country_code="NZ", config=cfg | {"nz_workflow_enabled": True}).country_code == "NZ"
    country.require_preparation("NZ")
    monkeypatch.setenv("APPLYPILOT_NZ_ENABLED", "1")
    assert country.resolve(root, country_code="NZ", config=cfg).country_code == "NZ"
    assert not country.nz_enabled({"nz_workflow_enabled": False})
    monkeypatch.setenv("APPLYPILOT_NZ_DISABLED", "1")
    with pytest.raises(country.CountryError, match="disabled"):
        country.resolve(root, country_code="NZ", config=cfg | {"nz_workflow_enabled": True})


def test_scoped_preference_revisions_preserve_shared_profile_and_uk_configuration(foundation):
    root, conn, cfg = foundation
    before = [(root / name).read_bytes() for name in ("profile.json", "searches.yaml")]
    country.save_preferences(conn, "GB", {"work_authorization": {"legally_authorized_to_work": "Yes"}}, 0, cfg)
    nz_cfg = cfg | {"nz_workflow_enabled": True}
    country.save_preferences(conn, "NZ", {"work_authorization": {"require_sponsorship": "Yes"}, "document_policy": {"policy_id": "nz-foundation-v1"}}, 0, nz_cfg)
    assert country.read_preferences(conn, "GB")["config"]["work_authorization"] == {"legally_authorized_to_work": "Yes"}
    assert "legally_authorized_to_work" not in country.read_preferences(conn, "NZ")["config"]["work_authorization"]
    with pytest.raises(country.CountryError, match="changed"):
        country.save_preferences(conn, "NZ", {}, 0, nz_cfg)
    with pytest.raises(country.CountryError):
        country.save_preferences(conn, "NZ", {"personal": {"full_name": "Duplicate"}}, 1, nz_cfg)
    with pytest.raises(country.CountryError, match="credentials"):
        country.save_preferences(conn, "NZ", {"application_operations": {"api_key": "not-allowed"}}, 1, nz_cfg)
    assert before == [(root / name).read_bytes() for name in ("profile.json", "searches.yaml")]


def test_additive_migration_retains_legacy_country_status_and_files(tmp_path):
    path = tmp_path / "applypilot.db"
    with sqlite3.connect(path) as old:
        old.execute("CREATE TABLE jobs(url TEXT PRIMARY KEY,title TEXT,apply_status TEXT,applied_at TEXT)")
        old.execute("INSERT INTO jobs VALUES('https://example.org/legacy','Analyst','applied','2026-01-01')")
    approved = tmp_path / "approved.txt"
    approved.write_bytes(b"Approved historical CV")
    conn = db.init_db(path)
    try:
        for _ in range(2):
            db.init_db(path)
        assert tuple(conn.execute("SELECT url,title,apply_status,applied_at,country_code FROM jobs").fetchone()) == (
            "https://example.org/legacy", "Analyst", "applied", "2026-01-01", None)
        assert approved.read_bytes() == b"Approved historical CV"
        assert conn.execute("SELECT COUNT(*) FROM cc_country_profiles").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO jobs(url,country_code) VALUES('bad','ZZ')")
        conn.rollback()
    finally:
        db.close_connection(path)


@pytest.mark.parametrize("source", ["core", "jobspy", "workday", "direct"])
def test_all_discovery_storage_paths_persist_snapshot_country(foundation, source):
    root, conn, cfg = foundation
    with country.operation(country.resolve(root, country_code="GB", config=cfg)):
        job = {"url": "https://example.org/new", "title": "Analyst", "location": "London, UK"}
        if source == "core":
            db.store_jobs(conn, [job], "Direct", "test")
        elif source == "jobspy":
            import pandas as pd
            from applypilot.discovery.jobspy import store_jobspy_results
            store_jobspy_results(conn, pd.DataFrame([{"job_url": job["url"], "title": "Analyst"}]), "indeed")
        elif source == "workday":
            from applypilot.discovery.workday import store_results
            store_results(conn, [job | {"apply_url": job["url"]}], {})
        else:
            from applypilot.discovery.smartextract import _store_jobs_filtered
            _store_jobs_filtered(conn, [job], "Direct", "test", [], [])
    assert conn.execute("SELECT country_code FROM jobs").fetchone()[0] == "GB"
    # A later campaign/duplicate observation cannot adopt an existing legacy job.
    conn.execute("UPDATE jobs SET country_code=NULL")
    conn.commit()
    with country.operation(country.resolve(root, country_code="GB", config=cfg)):
        db.store_jobs(conn, [job], "Direct", "test")
    assert conn.execute("SELECT country_code FROM jobs").fetchone()[0] is None


def test_legacy_queues_exclude_nz_before_limit_and_bulk_actions_fail_before_writes(foundation, monkeypatch):
    root, conn, cfg = foundation
    conn.executemany("INSERT INTO jobs(url,title,fit_score,country_code) VALUES(?,?,?,?)", [
        ("https://example.org/nz", "NZ", 10, "NZ"), ("https://example.org/uk", "UK", 8, "GB")])
    conn.commit()
    assert [job["title"] for job in db.get_jobs_by_stage(conn, "scored", limit=1)] == ["UK"]
    with pytest.raises(country.CountryError):
        country.validate_request_jobs(conn, root, "/api/center/action", {"ids": [2, 1], "country_code": "GB"})
    monkeypatch.setattr(db, "get_connection", lambda *args, **kwargs: conn)
    with pytest.raises(country.CountryError):
        db.block_job_by_id(1)
    assert all(r[0] is None for r in conn.execute("SELECT apply_status FROM jobs"))


@pytest.mark.parametrize("job,allowed", [
    ({"country_code": "GB"}, True), ({"country_code": "NZ"}, False),
    ({"country_code": "ZZ", "location": "UK"}, False), ({"country": "unknown"}, False),
    ({"location": "Auckland, New Zealand"}, False), ({"location": "Wellington"}, False),
    ({"location": "London, UK"}, True), ({"country_code": "GB", "location": "New Zealand"}, False),
    ({"location": "Wellington, UK"}, True), ({"country_code": "GB", "location": "Wellington"}, True)])
def test_sponsorship_jurisdiction_does_not_default_unknown_to_uk(job, allowed):
    assert country.uk_sponsorship_allowed(job, {"country": "UK"}) is allowed


@pytest.mark.parametrize("cfg", [{"country_code": "ZZ"}, {"country_code": "NZ"}, {"country": "unknown"}])
def test_unknown_or_nz_operation_does_not_enable_legacy_uk_annotation(cfg):
    assert not country.uk_sponsorship_allowed({"location": "London, UK"}, cfg)


@pytest.mark.parametrize("code", [None, "NZ", "ZZ", "unknown"])
def test_non_uk_annotation_never_calls_register(monkeypatch, code):
    from applypilot import uk_sponsorship as sponsors
    lookup = Mock(side_effect=AssertionError("UK lookup is prohibited"))
    monkeypatch.setattr(sponsors, "get_sponsor_index", lookup)
    result = sponsors.annotate_sponsorship(company="Example", text="visa sponsorship available", country_code=code)
    assert result.sponsorship_explicit == "Unknown" and not result.sponsor_source
    assert sponsors.match_licensed_sponsor("Example", country_code=code)[0] == "Unknown"
    assert sponsors.detect_sponsorship_from_text("visa sponsorship available", country_code=code) == ("Unknown", "")
    lookup.assert_not_called()


def test_uk_annotation_keeps_text_and_register_path(monkeypatch):
    from applypilot import uk_sponsorship as sponsors
    monkeypatch.setattr(sponsors, "get_sponsor_index", lambda **kw: {"EXAMPLE": "Example"})
    result = sponsors.annotate_sponsorship(company="Example", text="visa sponsorship available", country_code="GB")
    assert result.sponsorship_explicit == "Yes" and result.sponsor_licensed == "Yes"
    assert result.sponsor_source == "GOVUK_Register_Licensed_Sponsors_Workers"


@pytest.mark.parametrize("code,expected", [("GB", "Yes"), ("NZ", None), (None, None)])
def test_enrichment_only_writes_uk_signals_for_confirmed_uk(foundation, monkeypatch, code, expected):
    root, conn, cfg = foundation
    from applypilot.enrichment import detail
    from applypilot import uk_sponsorship as sponsors
    conn.execute("INSERT INTO jobs(url,title,company,location,country_code) VALUES('https://example.org/job','Role','Example',?,?)",
                 ("Auckland, New Zealand" if code != "GB" else "London, UK", code))
    conn.commit()
    browser = SimpleNamespace(new_context=lambda **kw: SimpleNamespace(new_page=lambda: object()), close=lambda: None)
    class PW:
        def __enter__(self): return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw: browser))
        def __exit__(self, *args): pass
    monkeypatch.setattr(detail, "sync_playwright", PW)
    monkeypatch.setattr(detail, "scrape_detail_page", lambda *a: {"status": "ok", "full_description": "visa sponsorship available", "application_url": "https://example.org/apply"})
    monkeypatch.setattr(config, "load_search_config", lambda: cfg)
    monkeypatch.setattr(sponsors, "get_sponsor_index", lambda **kw: {"EXAMPLE": "Example"})
    detail.scrape_site_batch(conn, "Direct", [("https://example.org/job", "Role")], delay=0)
    assert conn.execute("SELECT sponsorship_explicit FROM jobs").fetchone()[0] == expected


def test_nz_disclosures_do_not_fall_back_to_global_uk_answers():
    from applypilot.application_profile import application_values
    profile = {"personal": {"email": "fixture@example.org", "legally_authorized_to_work": "Yes"},
               "work_authorization": {"legally_authorized_to_work": "Yes", "require_sponsorship": "No"}}
    assert application_values(profile)["legally_authorized_to_work"] == "Yes"
    values = application_values(profile, country_code="NZ")
    assert "legally_authorized_to_work" not in values and "require_sponsorship" not in values
    assert profile["work_authorization"]["legally_authorized_to_work"] == "Yes"


def test_failed_enrichment_country_read_never_reuses_previous_uk_job(foundation, monkeypatch):
    root, conn, cfg = foundation
    from applypilot.enrichment import detail
    from applypilot import uk_sponsorship as sponsors
    uk, nz = "https://example.org/uk", "https://example.org/nz"
    conn.executemany("INSERT INTO jobs(url,title,company,location,country_code) VALUES(?,?,?,?,?)", [
        (uk, "Role", "Example", "London, UK", "GB"), (nz, "Role", "Example", "Auckland, NZ", "NZ")])
    conn.commit()
    class FailingCountryRead:
        def execute(self, sql, params=()):
            if sql == "SELECT * FROM jobs WHERE url = ?" and params == (nz,):
                raise sqlite3.OperationalError("Synthetic failed country lookup")
            return conn.execute(sql, params)
        def commit(self): conn.commit()
    browser = SimpleNamespace(new_context=lambda **kw: SimpleNamespace(new_page=lambda: object()), close=lambda: None)
    class PW:
        def __enter__(self): return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw: browser))
        def __exit__(self, *args): pass
    monkeypatch.setattr(detail, "sync_playwright", PW)
    monkeypatch.setattr(detail, "scrape_detail_page", lambda *a: {"status": "ok", "full_description": "visa sponsorship available"})
    monkeypatch.setattr(config, "load_search_config", lambda: cfg)
    monkeypatch.setattr(sponsors, "get_sponsor_index", lambda **kw: {"EXAMPLE": "Example"})
    detail.scrape_site_batch(FailingCountryRead(), "Direct", [(uk, "Role"), (nz, "Role")], delay=0)
    rows = {row["url"]: row["sponsorship_explicit"] for row in conn.execute("SELECT url,sponsorship_explicit FROM jobs")}
    assert rows == {uk: "Yes", nz: None}


def test_http_foundation_preferences_and_nz_write_containment(foundation):
    root, conn, cfg = foundation
    from applypilot import command_api
    handler = SimpleNamespace(_send_json=Mock())
    command_api.post(handler, "/api/center/country/preferences", {"country_code": "NZ", "patch": {}, "expected_revision": 0}, root, None)
    assert handler._send_json.call_args.args[0] == 400
    (root / "searches.yaml").write_text(yaml.safe_dump(cfg | {"nz_workflow_enabled": True}), encoding="utf-8")
    command_api.post(handler, "/api/center/country/preferences", {"country_code": "NZ", "patch": {"document_policy": {}}, "expected_revision": 0}, root, None)
    assert handler._send_json.call_args.args[0] == 200
    command_api.post(handler, "/api/center/studio/generate", {"country_code": "NZ"}, root, None)
    assert handler._send_json.call_args.args[0] == 400
    assert "role title and job description" in handler._send_json.call_args.args[1]["error"]


def test_runner_snapshots_country_config_and_rejects_invalid_or_nz(foundation, monkeypatch):
    root, conn, cfg = foundation
    from applypilot import dashboard_server as server
    runner = server._PipelineRunner(root)
    for payload in ({"stages": ["apply"]}, {"stages": " APPLY "}, {"country_code": "NZ"}, {"country_code": "ZZ"}):
        ok, _ = runner.start(payload)
        assert not ok
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    snapshot = country.resolve(root, country_code="GB", config=cfg)
    (root / "searches.yaml").write_text("country: NZ\n", encoding="utf-8")
    path, meta = runner._write_search_override_if_needed({"_country_snapshot": snapshot.config, "_country_context": snapshot.public()}, run_id="fixture", log_dir=logs)
    assert yaml.safe_load(Path(path).read_text())["country"] == "UK"
    assert meta["country_context"]["country_code"] == "GB"


def test_streaming_stage_threads_keep_captured_country_when_workspace_settings_change(foundation, monkeypatch):
    import threading
    from applypilot import pipeline
    root, conn, cfg = foundation
    changed = threading.Event()
    observed = []
    def stage(name, tracker, *args):
        if name == "discover":
            (root / "searches.yaml").write_text("country: NZ\n", encoding="utf-8")
            changed.set()
        assert changed.wait(5)
        observed.append((country.current().country_code, config.load_search_config()["country"]))
        tracker.mark_done(name, {"status": "ok"})
    monkeypatch.setattr(pipeline, "_run_stage_streaming", stage)
    monkeypatch.setattr(pipeline, "_append_metric", lambda *args: None)
    with country.operation(country.resolve_run(root, cfg, "GB")):
        out = pipeline._run_streaming(["discover", "enrich"], 7)
    assert not out["errors"] and observed == [("GB", "UK"), ("GB", "UK")]


def test_nz_foundation_read_does_not_return_uk_jobs_or_documents(foundation):
    from applypilot import command_api
    root, conn, cfg = foundation
    conn.execute("INSERT INTO jobs(url,title,country_code) VALUES('https://example.org/uk','UK record','GB')")
    conn.commit()
    (root / "searches.yaml").write_text(yaml.safe_dump(cfg | {"nz_workflow_enabled": True}), encoding="utf-8")
    handler = SimpleNamespace(_send_json=Mock())
    command_api.get(handler, "/api/center", "country_code=NZ", root)
    assert handler._send_json.call_args.args[1]["jobs"] == []
    assert "messages" not in handler._send_json.call_args.args[1]
    command_api.get(handler, "/api/center/studio", "country_code=NZ", root)
    assert handler._send_json.call_args.args[0] == 200
    assert handler._send_json.call_args.args[1]["documents"] == []
    command_api.get(handler, "/api/center/detail", "country_code=NZ&id=1", root)
    assert handler._send_json.call_args.args[0] == 400
    command_api.get(handler, "/api/center", "country_code=", root)
    assert handler._send_json.call_args.args[0] == 400


def test_direct_nz_writers_are_blocked_even_when_foundation_is_enabled(foundation):
    from applypilot.scoring.scorer import score_job
    from applypilot.scoring.tailor import tailor_resume
    from applypilot.scoring.cover_letter import generate_cover_letter
    from applypilot.scoring.supporting_statement import generate_supporting_statement
    from applypilot.apply.llm_engine import _llm_plan_fills
    from applypilot.apply.question_assistant import draft_answer
    root, conn, cfg = foundation
    (root / "searches.yaml").write_text(yaml.safe_dump(cfg | {"nz_workflow_enabled": True}), encoding="utf-8")
    job = {"country_code": "NZ", "title": "Fixture", "site": "Fixture"}
    for call in (lambda: score_job("", job), lambda: tailor_resume("", job, {}),
                 lambda: generate_cover_letter("", job, {}), lambda: generate_supporting_statement("", job, {}),
                 lambda: _llm_plan_fills(job, {}, []), lambda: draft_answer(job, {}, {}, [], {})):
        with pytest.raises(country.CountryError, match="preparation"):
            call()


def test_country_foundation_ui_switches_only_preferences_and_keeps_uk_data(http_workspace):
    if os.environ.get("APPLYPILOT_RUN_COUNTRY_UI") != "1":
        pytest.skip("Set APPLYPILOT_RUN_COUNTRY_UI=1 for local-only country UI verification")
    from playwright.sync_api import sync_playwright
    base, root, conn = http_workspace
    profile = root / "profile.json"
    profile.write_text('{"personal":{"full_name":"UI Fixture"}}', encoding="utf-8")
    original = profile.read_bytes()
    search_path = root / "searches.yaml"
    search_path.write_text("queries:\n- query: Analyst\nlocations:\n- location: London, UK\n", encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(base + "/workspace")
            page.wait_for_function("document.querySelector('#job-search') !== null")
            assert page.locator('#workspace-country option[value="NZ"]').evaluate("el => el.disabled"), page.evaluate("({option:document.querySelector('#workspace-country option[value=NZ]').outerHTML,flag:state.data.nz_workflow_enabled})")
            search_path.write_text(search_path.read_text(encoding="utf-8") + "nz_workflow_enabled: true\n", encoding="utf-8")
            expected_search = search_path.read_bytes()
            page.locator("#refresh").click()
            page.wait_for_function("!document.querySelector('#workspace-country option[value=NZ]').disabled")
            page.locator("#workspace-country").select_option("NZ")
            page.locator("#country-preferences").wait_for(state="attached")
            page.get_by_text("Country-specific preferences", exact=True).click()
            page.wait_for_function("document.querySelector('#country-preferences').value.trim() === '{}'")
            assert "apply with the agent" in page.locator("#page-description").inner_text()
            assert page.locator("#job-search").count() == 0
            page.locator("#country-preferences").fill('{"search_preferences":{"locations":["Auckland"]}}')
            page.locator("#save-country-preferences").click()
            page.wait_for_function("document.querySelector('#toast').textContent.includes('Country preferences saved')")
            assert country.read_preferences(conn, "NZ")["revision"] == 1
            page.locator('.rail [data-view="jobs"]').click()
            page.locator('#nz-capture-form').wait_for(state='attached')
            page.wait_for_timeout(16000)
            assert page.locator('#nz-capture-form').count() == 1
            assert page.locator('#job-search').count() == 0
            page.locator("#workspace-country").select_option("GB")
            page.wait_for_function("document.querySelector('#page-title').textContent === 'Make it yours.'")
            assert profile.read_bytes() == original and search_path.read_bytes() == expected_search
            assert conn.execute("SELECT country_code,applied_at FROM jobs").fetchone()[:] == (None, None)
            assert not errors
        finally:
            browser.close()


def test_snapshot_write_failure_never_falls_back_to_mutable_workspace_configuration(foundation, monkeypatch):
    root, conn, cfg = foundation
    from applypilot import dashboard_server as server
    runner = server._PipelineRunner(root)
    monkeypatch.setattr(runner, "_write_search_override_if_needed", lambda *a, **kw: (None, {"search_override_error": "fixture failure"}))
    spawn = Mock(side_effect=AssertionError("Snapshot failure must not start a process"))
    monkeypatch.setattr(server.subprocess, "Popen", spawn)
    ok, result = runner.start({"stages": ["score"], "country_code": "GB"})
    assert not ok and "snapshot" in result["error"]
    spawn.assert_not_called()
    assert not runner.status()["starting"]


def test_operation_diagnostics_do_not_disclose_preference_or_proxy_values(foundation):
    root, conn, cfg = foundation
    country.save_preferences(conn, "GB", {"work_authorization": {"disclosure": "private-disclosure-fixture"}}, 0, cfg)
    context = country.resolve(root, country_code="GB", config=cfg | {"proxy": "private-proxy-fixture"}, conn=conn)
    diagnostics = repr(context) + json.dumps(context.public())
    assert "private-disclosure-fixture" not in diagnostics
    assert "private-proxy-fixture" not in diagnostics
