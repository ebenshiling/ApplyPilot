import json
import os
import threading
from types import SimpleNamespace

import httpx
import pytest
import yaml

from applypilot import database as db, pipeline_workspace as pw, source_registry as sources
from applypilot.pipeline_store import persist_document, record_discovery
from applypilot.role_routing import route_resume_for_job, score_fingerprint
from applypilot.setup_workspace import write_searches_yaml, read_searches_dict


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path
    (root / "profile.json").write_text('{"personal":{"full_name":"Fixture Candidate"}}')
    (root / "resume.txt").write_text("Fixture Candidate\nSUMMARY\nSQL reporting analyst\nSKILLS\nSQL, Power BI")
    (root / "resume_variants").mkdir()
    (root / "resume_variants" / "data_analyst.txt").write_text("Verified SQL evidence")
    config = {"queries": [{"query": "Data Analyst", "tier": 3}], "locations": [{"location": "London", "remote": False}],
              "boards": ["indeed"], "smart_sites": ["NHS Jobs"], "defaults": {"hours_old": 48},
              "board_overrides": {"indeed": {"hours_old": 72}}, "workday_max_tier": 3,
              "resume_routing": {"variants": {"data_analyst": "resume_variants/data_analyst.txt"}, "families": [{"name": "data", "variant": "data_analyst", "keywords": ["data analyst"]}]}}
    write_searches_yaml(root, yaml.safe_dump(config))
    conn = db.init_db(root / "applypilot.db")
    conn.execute("INSERT INTO jobs(url,title,company,location,full_description,fit_score,site,discovered_at) VALUES(?,?,?,?,?,?,?,?)",
                 ("https://careers.example.com/jobs/1", "Data Analyst", "Fixture Ltd", "London", "SQL reporting", 8, "Direct", "2026-10-04"))
    conn.commit()
    yield root, conn
    db.close_connection(root / "applypilot.db")


def test_builder_roundtrip_preserves_routing_priorities_and_overrides(workspace):
    root, conn = workspace
    before = read_searches_dict(root)
    result = pw.save_config(conn, root, {"builder_yaml": "queries:\n- query: Data Analyst\n  tier: 1\n- query: IT Support Analyst\n  tier: 3\nlocations:\n- location: Bristol\n"})
    assert result["config"]["resume_routing"] == before["resume_routing"]
    assert result["config"]["board_overrides"] == before["board_overrides"]
    assert result["config"]["workday_max_tier"] == 3
    assert result["config"]["queries"] == [{"query": "Data Analyst", "tier": 3}, {"query": "IT Support Analyst", "tier": 1}]


def test_custom_catalog_is_workspace_owned_and_used_for_targets(workspace, tmp_path):
    root, _ = workspace
    item = sources.save_source(root, {"name": "Fixture Careers", "url": "https://careers.example.org/jobs?q={query_encoded}", "type": "search"})
    from applypilot.config import load_sites_config
    from applypilot.discovery.smartextract import build_scrape_targets
    first = load_sites_config(root)
    other = load_sites_config(tmp_path / "other")
    assert any(s["name"] == item["name"] for s in first["sites"])
    assert not any(s["name"] == item["name"] for s in other["sites"])
    targets = build_scrape_targets(first["sites"], {"queries": [{"query": "Data Analyst"}]})
    assert any(t["name"] == item["name"] and "Data+Analyst" in t["url"] for t in targets)
    sources.save_source(root, item | {"disabled": True})
    assert not any(t["name"] == item["name"] for t in build_scrape_targets(load_sites_config(root)["sites"], {"queries": [{"query": "Data Analyst"}]}))


@pytest.mark.parametrize("url", ["http://127.0.0.1/jobs", "http://localhost/jobs", "http://192.168.1.5/jobs", "file:///tmp/jobs", "https://user:pass@example.com/jobs", "http://foo.internal/jobs"])
def test_source_rejects_private_or_credential_urls(url):
    with pytest.raises(ValueError):
        sources.validate_url(url)


def test_structured_preview_preserves_employer_without_storage_or_ai(workspace, monkeypatch):
    root, conn = workspace
    html = '<script type="application/ld+json">{"@type":"JobPosting","title":"Data Analyst","hiringOrganization":{"name":"Employer Ltd"},"description":"SQL","url":"https://example.org/job"}</script>'
    monkeypatch.setattr(sources, "validate_url", lambda url, **kw: url)
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url): return httpx.Response(200, text=html, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "Client", Client)
    out = sources.preview(root, {"name": "Fixture", "type": "static", "url": "https://example.org/jobs"}, "Analyst", "London")
    assert out["jobs"][0]["company"] == "Employer Ltd"
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_saved_route_and_fingerprint_follow_actual_scoring_inputs(workspace):
    root, conn = workspace
    job = dict(conn.execute("SELECT * FROM jobs").fetchone())
    initial = score_fingerprint(job, root)
    pw.save_config(conn, root, {"patch": {"locations": [{"location": "Bristol"}]}})
    assert score_fingerprint(job, root) == initial
    (root / "resume_variants/data_analyst.txt").write_text("Updated verified SQL evidence")
    assert score_fingerprint(job, root) != initial
    pw.save_job(conn, root, {"id": 1, "variant": "default"})
    assert route_resume_for_job(dict(conn.execute("SELECT * FROM jobs").fetchone()), app_dir=root).key == "default"


def test_failed_regeneration_keeps_approved_path_and_checkpoint(workspace):
    root, conn = workspace
    approved = str(root / "approved.txt")
    conn.execute("UPDATE jobs SET tailored_resume_path=?", (approved,))
    persist_document(conn, "tailor", {"url": "https://careers.example.com/jobs/1", "path": None, "status": "error", "error": "fixture outage"})
    assert conn.execute("SELECT tailored_resume_path FROM jobs").fetchone()[0] == approved
    assert conn.execute("SELECT state FROM cc_pipeline_items").fetchone()[0] == "error"


def test_employer_metadata_and_source_health_survive_storage(workspace):
    _, conn = workspace
    from applypilot.discovery.smartextract import _store_jobs_filtered
    job = {"url": "https://example.org/job", "title": "Data Analyst", "company": "Actual Employer", "location": "London"}
    new, old = _store_jobs_filtered(conn, [job], "Fixture Careers", "json_ld", [], [])
    record_discovery(conn, {"name": "Fixture Careers", "query": "Data Analyst"}, {"status": "PASS", "jobs": [job], "strategy": "json_ld"}, new, old)
    row = conn.execute("SELECT company,source_id FROM jobs WHERE url=?", (job["url"],)).fetchone()
    assert tuple(row) == ("Actual Employer", "fixture-careers")
    assert conn.execute("SELECT new_jobs FROM cc_source_health").fetchone()[0] == 1


def test_second_opinions_are_cached_and_budgeted(workspace, monkeypatch):
    root, conn = workspace
    from applypilot.scoring import scorer
    from applypilot import llm
    from applypilot import ai_routing
    calls = []
    monkeypatch.setattr(scorer, "score_job", lambda *a: calls.append(1) or {"score": 8, "reasoning": "Verified SQL", "keywords": "SQL", "confidence": .9})
    monkeypatch.setattr(llm, "get_client", lambda: SimpleNamespace(last_model="fixture-model"))
    monkeypatch.setattr(ai_routing, "route_info", lambda *a: {"provider": "gemini", "model": "fixture", "configured": True})
    data = {"id": 1, "variants": ["data_analyst"]}
    first = pw.evaluate_variants(conn, root, data)
    second = pw.evaluate_variants(conn, root, data)
    assert first["evaluations"][0]["score"] == 8
    assert second["evaluations"][0]["cached"] and calls == [1]
    conn.execute("UPDATE cc_variant_evaluations SET input_hash='old'")
    conn.executemany("INSERT INTO cc_variant_evaluations(created_at) VALUES(?)", [("2099-01-01",)] * 9)
    conn.commit()
    with pytest.raises(ValueError, match="budget"):
        pw.evaluate_variants(conn, root, data)
    conn.rollback()
    assert calls == [1]


def test_packet_answers_and_optional_cover(workspace):
    root, conn = workspace
    cv = root / "ready.txt"
    cv.write_text("CV")
    cv.with_suffix(".pdf").write_bytes(b"pdf")
    conn.execute("UPDATE jobs SET tailored_resume_path=?", (str(cv),))
    pw.save_job(conn, root, {"id": 1, "requirements": {"cover": False, "statement": False, "answers": ["Why this role?"]}})
    from applypilot.eligibility import packet
    job = dict(conn.execute("SELECT * FROM jobs").fetchone())
    from document_fixtures import finalized_document
    finalized_document(conn,root,cv,job)
    assert packet(conn, job)["missing"] == ["Answer: Why this role?"]
    conn.execute("INSERT INTO cc_answers(question,answer,updated_at) VALUES(?,?,?)", ("Why this role?", "Verified motivation", "2026-10-04"))
    assert packet(conn, job)["ready"]


def test_campaign_configuration_and_outcome_cohorts(workspace):
    root, conn = workspace
    pw.save_campaign(conn, root, {"name": "Data & BI", "queries": [{"query": "BI Analyst", "tier": 1}]})
    assert pw.campaign_config(root, "data-bi")["queries"][0]["query"] == "BI Analyst"
    conn.execute("UPDATE jobs SET campaign_id='data-bi',applied_at='2026-10-04',resume_route=?", (json.dumps({"key": "data_analyst"}),))
    conn.execute("INSERT INTO cc_job_meta(job_url,hiring_status) VALUES(?,?)", ("https://careers.example.com/jobs/1", "interview"))
    cohort = pw.insights(conn, root)["cohorts"][0]
    assert cohort["submitted"] == cohort["responses"] == cohort["interviews"] == 1
    assert cohort["campaign"] == "data-bi" and cohort["resume"] == "data_analyst"


@pytest.fixture
def server(workspace, monkeypatch):
    root, conn = workspace
    import applypilot.dashboard_server as ds
    monkeypatch.setattr(ds, "APP_DIR", root)
    monkeypatch.setattr(ds, "_MULTI_USER_MODE", False)
    monkeypatch.setattr(ds, "_PIPELINE", ds._PipelineRunner(root))
    httpd = ds.ThreadingHTTPServer(("127.0.0.1", 0), ds._Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", root, conn
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=3)


def test_pipeline_http_config_source_and_preview(server):
    base, root, _ = server
    with httpx.Client(base_url=base, timeout=15) as client:
        out = client.get("/api/center/pipeline").json()
        assert out["ok"] and out["routes"][0]["route"]["key"] == "data_analyst"
        assert client.get("/api/center/pipeline/detail?id=1").json()["packet"]["required"]["resume"]
        result = client.post("/api/center/pipeline/config/save", json={"patch": {"locations": [{"location": "Bristol"}]}}).json()
        assert result["ok"] and "resume_routing" in result["preserved"]
        client.post("/api/center/pipeline/source/save", json={"name": "Custom Careers", "url": "https://careers.example.org/?q={query_encoded}", "type": "search"}).raise_for_status()
        assert (root/"sites.yaml").exists()
        result = client.post("/api/center/pipeline/preview", json={"search_query": "Data Analyst, IT Support", "smart_target_budget": 1, "board_search_budget": 1}).json()
        assert len(result["config"]["queries"]) == 2 and result["board_window"] == 1
        assert client.post("/api/center/pipeline/source/save", json={"name": "Private", "url": "http://127.0.0.1/jobs", "type": "static"}).status_code == 400


def test_native_pipeline_browser_configuration_and_mobile(server):
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1")
    from playwright.sync_api import sync_playwright
    base, root, _ = server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/workspace#pipeline")
        page.locator("#native-query-editor").wait_for(state="attached")
        assert page.locator("#classic-frame").count() == 0
        page.locator("#native-queries").fill("Data Analyst\nIT Support Analyst")
        page.locator("#native-preview").click()
        page.locator("#native-config").evaluate("el => el.open = true")
        page.locator("#native-locations").fill("Bristol")
        page.locator("#native-config-save").click()
        page.wait_for_function("document.querySelector('#toast')?.textContent.includes('Searches and resume mappings saved.')")
        page.locator("#native-query-editor").wait_for(state="attached")
        assert "resume_routing" in read_searches_dict(root)
        assert read_searches_dict(root)["locations"][0]["location"] == "Bristol"
        page.set_viewport_size({"width": 390, "height": 844})
        overflow = page.evaluate("Array.from(document.querySelectorAll('#pipeline-shell *')).filter(el=>el.getBoundingClientRect().right>innerWidth+1).map(el=>el.tagName+'#'+el.id+'.'+el.className)")
        dimensions = page.evaluate("['body','.workspace','main','#content','#pipeline-shell'].map(s=>{const el=document.querySelector(s),r=el.getBoundingClientRect();return [s,r.x,r.width,getComputedStyle(el).minWidth]})")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (dimensions, overflow)
        page.locator("[data-workflow=prepare]").click()
        assert page.locator("#native-picked").is_checked()
        assert page.locator("#native-custom").is_hidden()
        if os.environ.get('APPLYPILOT_STUDIO_SCREENSHOTS'):
            from pathlib import Path
            output = Path(os.environ['APPLYPILOT_STUDIO_SCREENSHOTS'])
            assert output.is_dir()
            page.screenshot(path=str(output/'pipeline-mobile.png'), full_page=True)
            page.set_viewport_size({"width":1280,"height":900})
            page.screenshot(path=str(output/'pipeline-desktop.png'), full_page=True)
        assert not errors
        browser.close()
