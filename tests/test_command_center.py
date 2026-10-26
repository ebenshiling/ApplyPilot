import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from applypilot import command_center as cc
from applypilot.database import close_connection, init_db
from applypilot.professional import enrich_evidence
from applypilot.setup_workspace import read_profile, write_profile


@pytest.fixture
def workspace(tmp_path):
    conn = init_db(tmp_path / "applypilot.db")
    conn.execute("INSERT INTO jobs(url,title,company,site,fit_score,full_description,application_url) VALUES(?,?,?,?,?,?,?)", ("https://careers.example/job/1", "Data Analyst", "Example Ltd", "Direct", 8, "SQL reporting and analytics", "https://careers.example/apply/1"))
    conn.commit()
    yield tmp_path, conn
    close_connection(tmp_path / "applypilot.db")


def test_credentials_edit_delete_and_punctuation(workspace):
    root, conn = workspace
    write_profile(root, {"resume_sections": {"credentials": [{"name": "Reporting, Data & BI", "issuer": "Example", "status": "earned"}]}})
    first = cc.fingerprint(root)
    assert read_profile(root)["resume_sections"]["credentials"][0]["name"] == "Reporting, Data & BI"
    write_profile(root, {"resume_sections": {"credentials": [{"name": "Corrected credential", "status": "in_progress"}]}})
    text, profile = enrich_evidence("Base", read_profile(root), {"title": "Data Analyst"})
    assert "Corrected credential | In progress" in text
    assert "Reporting, Data & BI" not in text
    assert cc.fingerprint(root) != first
    write_profile(root, {"resume_sections": {"credentials": []}})
    assert read_profile(root)["resume_sections"]["credentials"] == []
    assert conn.execute("SELECT COUNT(*) FROM cc_profile_revisions").fetchone()[0] == 3


def test_professional_sections_and_pdf_rendering(workspace):
    root, _ = workspace
    write_profile(root, {"personal": {"full_name": "Example Candidate"}, "resume_sections": {
        "credentials": [{"name": "PL-300", "status": "earned", "family": "data_bi"}, {"name": "ITIL", "status": "in_progress", "family": "support"}],
        "professional_experience": [{"header": "Analyst at Example Ltd", "subtitle": "2026–present", "bullets": ["Built SQL reporting"]}],
        "memberships": ["Example Professional Society"], "professional_development": ["Data quality workshop — 2026"],
    }})
    from applypilot.scoring.tailor import assemble_resume_text
    from applypilot.scoring.pdf import build_html, parse_resume
    text = assemble_resume_text({"title": "Data Analyst", "summary": "Reporting analyst", "experience": [], "projects": [], "skills": {}, "core_skills": ["SQL"]}, read_profile(root), {"title": "Data Analyst"})
    parsed = parse_resume(text)
    assert "Analyst at Example Ltd" in parsed["sections"]["PROFESSIONAL EXPERIENCE"]
    assert "PL-300" in parsed["sections"]["CERTIFICATIONS"]
    assert "ITIL" not in text
    html = build_html(parsed)
    assert "Professional Memberships" in html and "Professional Development" in html
    assert "break-after: avoid" in html


def test_manual_submission_snapshots_document(workspace):
    root, conn = workspace
    source = root / "legacy.txt"
    source.write_text("Original submitted CV", encoding="utf-8")
    conn.execute("UPDATE jobs SET tailored_resume_path=? WHERE rowid=1", (str(source),))
    conn.commit()
    cc.update_job(conn, root, 1, "applied", {"reference": "REF-123", "evidence": "Confirmation email"})
    doc = conn.execute("SELECT * FROM cc_documents").fetchone()
    source.write_text("Later edits", encoding="utf-8")
    assert Path(doc["text_path"]).read_text() == "Original submitted CV"
    meta = conn.execute("SELECT * FROM cc_job_meta").fetchone()
    assert meta["document_id"] == doc["id"] and meta["submission_method"] == "manual"
    with pytest.raises(ValueError, match="Reconcile"):
        cc.update_job(conn, root, 1, "pick", {})


def test_worker_updates_create_history_and_conflicts_are_rejected(workspace):
    root, conn = workspace
    cc.update_job(conn, root, 1, "pick", {})
    conn.execute("UPDATE jobs SET apply_status='in_progress',agent_id='llm-worker-1' WHERE rowid=1")
    conn.commit()
    with pytest.raises(ValueError, match="active"):
        cc.update_job(conn, root, 1, "applied", {})
    assert [r[0] for r in conn.execute("SELECT state FROM cc_events WHERE kind='execution'")] == ["selected", "in_progress"]
    cc.update_job(conn, root, 1, "pause", {})
    assert conn.execute("SELECT command FROM cc_controls").fetchone()[0] == "pause"


def test_block_scope_archive_restore_and_unblock(workspace):
    root, conn = workspace
    cc.update_job(conn, root, 1, "block", {"scope": "employer", "reason": "Not suitable"})
    assert cc.blocked(conn, "https://other.example/2", "Example Ltd")
    with pytest.raises(ValueError, match="unblock"):
        cc.update_job(conn, root, 1, "pick", {})
    cc.unblock(conn, 1)
    assert not cc.blocked(conn, "https://other.example/2", "Example Ltd")
    cc.update_job(conn, root, 1, "archive", {})
    assert cc.overview(conn, root)["jobs"][0]["visibility"] == "archived"
    cc.update_job(conn, root, 1, "restore", {})
    cc.update_job(conn, root, 1, "pick", {})


def test_uncertain_submission_cannot_retry(workspace):
    root, conn = workspace
    cc.update_job(conn, root, 1, "failed", {"reason": "Timeout after submit; uncertain result"})
    assert cc.get_job(conn, 1)["apply_status"] == "submission_uncertain"
    with pytest.raises(ValueError, match="Reconcile"):
        cc.update_job(conn, root, 1, "retry", {})


def test_worker_uncertainty_and_dry_run_history(workspace, monkeypatch):
    root, conn = workspace
    from applypilot.apply import llm_engine
    monkeypatch.setattr(llm_engine, "get_connection", lambda: conn)
    job = cc.get_job(conn, 1)
    before = conn.execute("SELECT COUNT(*) FROM cc_events").fetchone()[0]
    llm_engine._worker_event(job, "opening_form")
    assert conn.execute("SELECT COUNT(*) FROM cc_events").fetchone()[0] == before
    conn.execute("UPDATE jobs SET apply_status='in_progress',agent_id='llm-worker-1' WHERE rowid=1")
    conn.commit()
    cc.update_job(conn, root, 1, "pause", {})
    def resume(_):
        conn.execute("UPDATE cc_controls SET command='resume'")
        conn.commit()
    monkeypatch.setattr(llm_engine.time, "sleep", resume)
    llm_engine._checkpoint(job)
    states = [r[0] for r in conn.execute("SELECT state FROM cc_events WHERE kind='progress'")]
    assert states == ["paused", "running"]
    llm_engine._mark_failed(job["url"], "Canceled at checkpoint; verify uncertain submission")
    assert cc.get_job(conn, 1)["apply_status"] == "submission_uncertain"


def test_stopped_attempt_releases_only_its_own_jobs(workspace):
    root, conn = workspace
    token = "a" * 24
    conn.execute("UPDATE jobs SET apply_status='in_progress',agent_id=? WHERE rowid=1", ("llm-worker-0:" + token,))
    conn.execute("INSERT INTO jobs(url,title,apply_status,agent_id) VALUES('https://example.org/other','Other worker','in_progress','llm-worker-0:bbbbbbbbbbbbbbbbbbbbbbbb')")
    conn.commit()
    cc.release_attempt(conn, token)
    assert cc.get_job(conn, 1)["apply_status"] == "submission_uncertain"
    assert cc.get_job(conn, 2)["apply_status"] == "in_progress"


def test_message_matching_requires_review_and_deduplicates(workspace):
    root, conn = workspace
    cc.update_job(conn, root, 1, "applied", {"reference": "REF-123"})
    data = {"subject": "Interview invitation REF-123", "body": "We invite you to interview for Data Analyst at Example Ltd", "sender": "recruiter@example.org"}
    message = cc.import_message(conn, data)
    assert message["job_url"] == "https://careers.example/job/1"
    assert message["proposed_state"] == "interview"
    assert cc.import_message(conn, data)["id"] == message["id"]
    assert cc.overview(conn, root)["jobs"][0]["hiring_status"] == "submitted"
    cc.review_message(conn, root, message["id"], 1, "interview")
    assert cc.overview(conn, root)["metrics"]["interviews"] == 1
    with pytest.raises(ValueError, match="already"):
        cc.review_message(conn, root, message["id"], 1, "rejected")


def test_stale_detection_tracks_profile_and_variants(workspace):
    root, conn = workspace
    text = root / "cv.txt"
    text.write_text("CV text")
    cc.register_document(conn, root, cc.get_job(conn, 1), text, None, root / "report.json")
    conn.execute("UPDATE jobs SET tailored_resume_path=? WHERE rowid=1", (str(text),))
    conn.commit()
    assert not cc.overview(conn, root)["jobs"][0]["document_stale"]
    (root / "resume_variants").mkdir()
    (root / "resume_variants" / "data.txt").write_text("New source facts")
    assert cc.overview(conn, root)["jobs"][0]["document_stale"]


def test_targeted_regeneration_preserves_previous_version(workspace, monkeypatch):
    root, conn = workspace
    import applypilot.config as config
    import applypilot.scoring.tailor as tailor
    import applypilot.scoring.pdf as pdf
    old = root / "old.txt"
    old.write_text("Old approved CV")
    base = root / "resume.txt"
    base.write_text("User supplied resume")
    conn.execute("UPDATE jobs SET tailored_resume_path=? WHERE rowid=1", (str(old),))
    conn.commit()
    monkeypatch.setenv("APPLYPILOT_REGENERATE_IDS", "1")
    monkeypatch.setattr(config, "APP_DIR", root)
    monkeypatch.setattr(tailor, "RESUME_PATH", base)
    monkeypatch.setattr(tailor, "TAILORED_DIR", root / "tailored_resumes")
    monkeypatch.setattr(tailor, "get_connection", lambda: conn)
    monkeypatch.setattr(tailor, "load_profile", lambda: {"personal": {"full_name": "Candidate"}})
    monkeypatch.setattr(tailor, "_load_skip_titles", lambda: [])
    monkeypatch.setattr(tailor, "route_resume_for_job", lambda job: SimpleNamespace(text=base.read_text(), key="data"))
    monkeypatch.setattr(tailor, "tailor_resume", lambda *a, **kw: ("New CV with CERTIFICATIONS", {"status": "approved", "attempts": 1}))
    from document_fixtures import attest_text
    attest_text(conn,root,"New CV with CERTIFICATIONS")
    monkeypatch.setattr(pdf, "convert_to_pdf", lambda path, **kw: path.with_suffix(".pdf"))
    assert tailor.run_tailoring()["approved"] == 1
    new = Path(cc.get_job(conn, 1)["tailored_resume_path"])
    assert new != old and new.read_text() == "New CV with CERTIFICATIONS"
    assert old.read_text() == "Old approved CV"
    assert conn.execute("SELECT variant FROM cc_documents").fetchone()[0] == "data"


def test_scheduler_does_not_replay_unknown_running_attempt(workspace):
    root, conn = workspace
    from applypilot.scheduler import schedule, tick
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    schedule(conn, 1, when, "prepare")
    conn.execute("UPDATE cc_schedules SET state='running',run_id='old-server-run'")
    conn.commit()
    runner = SimpleNamespace(status=lambda: {"running": False, "starting": False, "run_id": None})
    tick(conn, runner)
    assert conn.execute("SELECT state FROM cc_schedules").fetchone()[0] == "needs_review"


def test_preferences_do_not_treat_unknown_location_as_onsite(workspace):
    root, conn = workspace
    cc.save_preferences(conn, {"excluded_title_terms": ["Senior"], "excluded_locations": ["Manchester"], "remote_only": True})
    assert cc.preference_exclusions(conn, {"title": "Analyst", "location": None}) == []
    assert cc.preference_exclusions(conn, {"title": "Analyst", "location": "Remote"}) == []
    assert cc.preference_exclusions(conn, {"title": "Senior Analyst", "location": "Manchester"})
    conn.execute("UPDATE jobs SET location='Manchester' WHERE rowid=1")
    conn.commit()
    with pytest.raises(ValueError, match="preferences"):
        cc.update_job(conn, root, 1, "pick", {})


def test_assistant_cache_and_request_budget(workspace, monkeypatch):
    root, conn = workspace
    import applypilot.llm as llm
    calls = []
    client = SimpleNamespace(provider="test", model="test-model", last_usage={"input_tokens": 12, "output_tokens": 5}, chat=lambda *a, **kw: calls.append(a) or "Evidence-based answer")
    monkeypatch.setattr(llm, "get_client", lambda: client)
    monkeypatch.setenv("APPLYPILOT_AI_DAILY_REQUESTS", "1")
    assert cc.assistant(conn, root, {"id": 1, "task": "explain"})["cached"] is False
    assert cc.assistant(conn, root, {"id": 1, "task": "explain"})["cached"] is True
    assert len(calls) == 1
    with pytest.raises(ValueError, match="budget"):
        cc.assistant(conn, root, {"id": 1, "task": "interview"})


def test_legacy_blocks_can_be_unblocked_without_destroying_history(workspace):
    root, conn = workspace
    conn.execute("INSERT INTO blocked_urls(prefix,reason,created_at) VALUES(?,?,?)", ("https://careers.example/job/1", "user_deleted", cc.now()))
    conn.execute("UPDATE jobs SET apply_status='skipped',apply_error='user_deleted' WHERE rowid=1")
    conn.commit()
    cc.init_schema(conn)
    rule = conn.execute("SELECT id FROM cc_blocks WHERE scope='legacy'").fetchone()[0]
    assert cc.blocked(conn, "https://careers.example/job/1")
    cc.unblock(conn, rule)
    assert not cc.blocked(conn, "https://careers.example/job/1")
    assert cc.get_job(conn, 1)["apply_status"] is None
    assert conn.execute("SELECT COUNT(*) FROM cc_events").fetchone()[0] >= 2


def test_anthropic_messages_contract_and_usage(monkeypatch):
    from applypilot.llm import LLMClient, _detect_provider
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert _detect_provider()[0] == "anthropic"
    client = LLMClient("anthropic", "https://api.anthropic.com/v1", "test-model", "test-api-key")
    def mock(request):
        body = json.loads(request.content)
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert request.headers["x-api-key"] == "test-api-key"
        assert body["system"].startswith("Be factual")
        assert body["messages"] == [{"role": "user", "content": "Explain"}]
        return httpx.Response(200, json={"content": [{"type": "text", "text": "Answer"}], "usage": {"input_tokens": 10, "output_tokens": 3}, "stop_reason": "end_turn"})
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(mock))
    try:
        assert client.chat([{"role": "system", "content": "Be factual"}, {"role": "user", "content": "Explain"}]) == "Answer"
        assert client.last_usage["input_tokens"] == 10
    finally:
        client._client.close()


def test_oauth_state_is_workspace_scoped_and_tokens_encrypted(workspace, monkeypatch):
    root, conn = workspace
    from applypilot import mail_connectors as mail
    monkeypatch.setenv("GMAIL_CLIENT_ID", "client-id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "client-secret")
    url = mail.connect(conn, "gmail")
    params = parse_qs(urlparse(url).query)
    assert params["code_challenge_method"] == ["S256"]
    monkeypatch.setattr(mail.httpx, "post", lambda *a, **kw: httpx.Response(200, json={"access_token": "private-access-token", "refresh_token": "private-refresh-token", "expires_in": 3600}, request=httpx.Request("POST", a[0])))
    mail.callback(conn, root, params["state"][0], "authorization-code")
    stored = bytes(conn.execute("SELECT tokens FROM cc_mail").fetchone()[0])
    assert b"private-access-token" not in stored
    assert mail.access_token(conn, root, "gmail") == "private-access-token"
    assert "tokens" not in mail.connections(conn)[0]
    with pytest.raises(ValueError, match="expired"):
        mail.callback(conn, root, params["state"][0], "authorization-code")
    mail.disconnect(conn, "gmail")
    assert mail.connections(conn) == []


@pytest.fixture
def http_workspace(workspace, monkeypatch):
    root, conn = workspace
    import applypilot.dashboard_server as ds
    monkeypatch.setattr(ds, "APP_DIR", root)
    monkeypatch.setattr(ds, "_MULTI_USER_MODE", False)
    monkeypatch.setattr(ds, "_PIPELINE", ds._PipelineRunner(root))
    httpd = ds.ThreadingHTTPServer(("127.0.0.1", 0), ds._Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", root, conn
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=3)


def test_command_center_http_roundtrip(http_workspace):
    base, root, conn = http_workspace
    with httpx.Client(base_url=base, timeout=10) as client:
        assert client.get("/workspace").status_code == 200
        assert client.get("/assets/workspace.js").status_code == 200
        assert client.get("/api/center").json()["metrics"]["total"] == 1
        result = client.post("/api/center/action", json={"ids": [1], "action": "pick"}).json()
        assert result["results"][0]["ok"]
        assert any(e["kind"] == "execution" and e["state"] == "selected" for e in client.get("/api/center/events").json()["events"])
        assert client.get("/api/center/export").headers["content-type"].startswith("text/csv")
        assert client.get("/api/center/document?id=999").status_code == 404
        routes = client.get("/api/center/ai/routes").json()["routes"]
        assert {r["task"] for r in routes} == {"score", "summary", "tailor", "statement", "cover", "review", "assistant", "forms", "extract", "email"}
        assert all("api_key" not in r and "base_url" not in r for r in routes)
        client.post("/api/center/action", json={"ids": [1], "action": "notes", "due_at": "2026-11-01T12:00:00+00:00", "notes": "Interview, prepare questions"}).raise_for_status()
        calendar = client.get("/api/center/calendar?id=1")
        assert "DTSTART:20261101T120000Z" in calendar.text
        assert "Interview\\, prepare questions" in calendar.text


def test_existing_cv_is_adopted_once_without_changing_user_files(http_workspace):
    base, root, conn = http_workspace
    source = root / "existing-cv.txt"
    original = "Candidate\nData Analyst\ncandidate@example.org\n\nSUMMARY\nOriginal existing CV\n"
    source.write_text(original, encoding="utf-8")
    conn.execute("UPDATE jobs SET tailored_resume_path=? WHERE rowid=1", (str(source),))
    conn.commit()
    with httpx.Client(base_url=base) as client:
        first = client.get("/api/center/detail?id=1").json()
        second = client.get("/api/center/detail?id=1").json()
        assert len(first["documents"]) == len(second["documents"]) == 1
        document = first["documents"][0]
        assert document["fingerprint"] == "legacy-untracked" and document["stale"]
        assert document["id"] == second["documents"][0]["id"]
        assert Path(document["text_path"]) != source
        assert source.read_text(encoding="utf-8") == original
        assert cc.get_job(conn, 1)["tailored_resume_path"] == str(source)
        assert "Original existing CV" in client.get(f"/api/center/document?id={document['id']}").text


@pytest.mark.parametrize("outside", [False, True])
def test_unavailable_legacy_cv_has_explicit_notice(workspace, outside):
    root, conn = workspace
    source = root.parent / "outside-cv.txt" if outside else root / "missing-cv.txt"
    if outside:
        source.write_text("Outside-workspace content", encoding="utf-8")
    conn.execute("UPDATE jobs SET tailored_resume_path=? WHERE rowid=1", (str(source),))
    conn.commit()
    notice = cc.adopt_legacy_document(conn, root, 1)
    assert "outside this workspace" in notice if outside else "could not be found" in notice
    assert conn.execute("SELECT COUNT(*) FROM cc_documents").fetchone()[0] == 0
    assert not conn.in_transaction


def test_command_center_multiuser_isolation(http_workspace, monkeypatch):
    base, root, _ = http_workspace
    import applypilot.dashboard_server as ds
    monkeypatch.setenv("APPLYPILOT_MULTI_ROOT", str(root / "multi"))
    monkeypatch.setattr(ds, "_MULTI_USER_MODE", True)
    ds.init_auth_db()
    with httpx.Client(base_url=base) as alice, httpx.Client(base_url=base) as bob:
        assert alice.get("/api/center").status_code == 401
        assert alice.get("/api/center/stream").status_code == 401
        for client, username in ((alice, "centeralice"), (bob, "centerbob")):
            assert client.post("/api/auth/register", json={"username": username, "email": username + "@example.org", "password": "TestPassword123", "full_name": username}).status_code == 200
        alice_dir = root / "multi" / "workspaces" / "centeralice"
        conn = init_db(alice_dir / "applypilot.db")
        conn.execute("INSERT INTO jobs(url,title,company) VALUES('https://example.org/private','Alice private opportunity','Private employer')")
        conn.commit()
        assert alice.get("/api/center").json()["metrics"]["total"] == 1
        assert bob.get("/api/center").json()["metrics"]["total"] == 0
        assert bob.get("/api/center/detail?id=1").status_code == 404
        assert alice.post("/api/center/action", json={"action": "pick", "ids": [1]}).status_code == 403
        close_connection(alice_dir / "applypilot.db")
        close_connection(root / "multi" / "workspaces" / "centerbob" / "applypilot.db")


def test_workspace_browser_editors_and_actions(http_workspace):
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1 for browser verification")
    from playwright.sync_api import sync_playwright
    base, root, conn = http_workspace
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/workspace")
        page.locator('[data-action="pick"]').first.click()
        page.get_by_role("button", name="Unpick", exact=True).wait_for()
        page.locator('[data-view="cv"]').click()
        page.locator("#add-credential").click()
        page.locator('[data-field="name"]').fill("Reporting, Data & BI")
        page.locator("#save-professional").click()
        page.wait_for_function("document.querySelector('#save-state').textContent==='Saved'")
        assert read_profile(root)["resume_sections"]["credentials"][0]["name"] == "Reporting, Data & BI"
        page.locator('[data-remove-credential="0"]').click()
        page.locator("#save-professional").click()
        page.wait_for_function("document.querySelector('#save-state').textContent==='Saved'")
        assert read_profile(root)["resume_sections"]["credentials"] == []
        for width in (375, 768, 1440):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            size = page.locator("#education").bounding_box()
            parent = page.locator("#cv-editor").bounding_box()
            assert size["width"] >= parent["width"] - 4
        page.locator('[data-view="settings"]').click()
        page.locator("#native-profile-save").wait_for(state="visible")
        assert page.locator("#classic-frame").count() == 0
        page.locator('[data-view="cv"]').first.click()
        page.locator('[data-cv-tab="resume"]').click()
        page.locator("#source-resume").wait_for(state="visible")
        print_page = browser.new_page()
        from applypilot.scoring.pdf import build_html, parse_resume
        long_text = "Candidate\nData Analyst\ncandidate@example.org\n\nSUMMARY\nReporting analyst\n\nPROFESSIONAL EXPERIENCE\n"
        long_text += "\n".join(f"Analyst at Employer {i}\n2020–2026\n- Built reporting systems and supported data quality investigations\n- Worked with stakeholders on recurring analytical requirements\n" for i in range(20))
        long_text += "\nCERTIFICATIONS\n- Reporting, Data & BI | Earned 2026\n\nEDUCATION\nMSc Data Science\n\nPROFESSIONAL MEMBERSHIPS\n- Professional Society\n"
        print_page.set_content(build_html(parse_resume(long_text)))
        pdf_path = root / "verified-multipage-cv.pdf"
        print_page.pdf(path=str(pdf_path), format="A4")
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        assert len(reader.pages) > 1
        printed = "\n".join(p.extract_text() for p in reader.pages)
        assert "Reporting, Data & BI" in printed and "PROFESSIONAL MEMBERSHIPS" in printed
        assert not errors
        browser.close()


def test_cv_studio_selectors_preserve_choices_and_preview_versions(http_workspace):
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1 for browser verification")
    from playwright.sync_api import sync_playwright
    from applypilot.setup_workspace import write_resume_text, write_resume_variant
    base, root, conn = http_workspace
    write_resume_text(root, "Default source resume")
    write_resume_variant(root, "data_analyst", "Role-specific source resume")
    for version in (1, 2):
        text = root / f"cv-{version}.txt"
        text.write_text(f"Candidate\nData Analyst\ncandidate@example.org\n\nSUMMARY\nSaved CV content version {version}\n\nCERTIFICATIONS\n- PL-300\n", encoding="utf-8")
        cc.register_document(conn, root, cc.get_job(conn, 1), text, None, root / "report.json")
    conn.commit()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(base + "/workspace#cv")
        page.locator("#preview-job").select_option("1")
        assert page.locator("#preview-job option").count() == 2
        assert page.locator("#preview-job").input_value() == "1"
        page.frame_locator("#cv-preview").get_by_text("Saved CV content version 2", exact=True).wait_for()
        assert page.locator("#preview-version option").count() == 2
        page.locator("#preview-version").select_option("1")
        page.frame_locator("#cv-preview").get_by_text("Saved CV content version 1", exact=True).wait_for()
        page.locator('[data-cv-tab="resume"]').click()
        assert page.locator("#preview-job").input_value() == "1"
        page.frame_locator("#cv-preview").get_by_text("Saved CV content version 1", exact=True).wait_for()
        page.locator("#resume-variant").select_option("data_analyst")
        page.wait_for_function("document.querySelector('#source-resume').value.trim()==='Role-specific source resume'")
        assert page.locator("#resume-variant option").count() == 2
        assert page.locator("#resume-variant").input_value() == "data_analyst"
        assert page.locator("#save-state").inner_text() != "Unsaved CV changes"
        page.locator("#source-resume").fill("Updated role-specific source resume")
        page.locator("#save-resume").click()
        page.wait_for_function("document.querySelector('#save-state').textContent==='Saved'")
        assert (root / "resume.txt").read_text().strip() == "Default source resume"
        assert (root / "resume_variants" / "data_analyst.txt").read_text().strip() == "Updated role-specific source resume"
        browser.close()


def test_cv_preview_empty_error_and_recovery_states(http_workspace):
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1 for browser verification")
    from playwright.sync_api import sync_playwright
    base, root, conn = http_workspace
    conn.execute("INSERT INTO jobs(url,title,company) VALUES('https://example.org/no-cv','Job without CV','Example Ltd')")
    conn.commit()
    missing = root / "missing-version.txt"
    cc.register_document(conn, root, cc.get_job(conn, 1), missing, None, root / "report.json")
    conn.commit()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(base + "/workspace#cv")
        page.locator("#preview-job").wait_for()
        assert page.locator("#cv-preview").is_hidden()
        assert "Choose a job" in page.locator("#cv-preview-state").inner_text()
        page.locator("#preview-job").select_option("1")
        page.locator('#cv-preview-state[data-state="error"]').wait_for()
        assert "unavailable" in page.locator("#cv-preview-state").inner_text()
        assert page.locator("#cv-preview").is_hidden()
        page.locator("#preview-job").select_option("2")
        page.locator('#cv-preview-state[data-state="empty"]').wait_for()
        assert "No saved CV available" in page.locator("#cv-preview-state").inner_text()
        assert page.locator("#preview-version").count() == 0
        missing.write_text("Candidate\nAnalyst\ncandidate@example.org\n\nSUMMARY\nRecovered CV preview\n", encoding="utf-8")
        page.locator("#preview-job").select_option("1")
        page.frame_locator("#cv-preview").get_by_text("Recovered CV preview", exact=True).wait_for()
        assert page.locator("#cv-preview-state").is_hidden()
        assert page.locator("#preview-job option").count() == 3
        page.locator("#preview-job").select_option("")
        page.locator('#cv-preview-state[data-state="empty"]').wait_for()
        assert page.locator("#cv-preview").is_hidden()
        browser.close()


def test_workspace_displays_routing_and_actual_assistant_model(http_workspace):
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1 for browser verification")
    from playwright.sync_api import sync_playwright
    base, _, _ = http_workspace
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/api/center/assistant", lambda route: route.fulfill(json={"ok": True, "answer": "Evidence-based fit explanation", "provider": "gemini", "model": "gemini-2.5-flash", "routing_task": "score", "cached": False}))
        page.goto(base + "/workspace", wait_until="domcontentloaded")
        page.locator('[data-detail="1"]').first.click()
        page.locator('[data-ai="explain"]').click()
        page.locator("#ai-detail").get_by_text("Evidence-based fit explanation", exact=True).wait_for()
        assert "gemini-2.5-flash" in page.locator(".ai-response-meta").inner_text()
        assert "score" in page.locator(".ai-response-meta").inner_text()
        page.locator("#close-dialog").click()
        page.locator('[data-view="settings"]').click()
        page.get_by_role("heading", name="AI preferences.", exact=True).wait_for()
        assert page.locator('[data-ai-task]').count() == 10
        browser.close()
