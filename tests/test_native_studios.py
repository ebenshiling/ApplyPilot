import base64
import io
import json
import os
import threading
import zipfile
from types import SimpleNamespace

import httpx
import pytest

from applypilot import ai_settings, cv_library, database as db, writing_studio as studio
from applypilot.setup_workspace import write_profile, write_resume_text, read_profile
from applypilot.role_routing import route_resume_for_job


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path
    write_profile(root, {"personal": {"full_name": "Fixture Candidate", "email": "candidate@example.com"}, "resume_sections": {"projects": [{"header": "Reporting Project", "bullets": ["Built SQL reporting"], "id": "project-one"}]}})
    write_resume_text(root, "Fixture Candidate\nData Analyst\nLondon\ncandidate@example.com\n\nSUMMARY\nSQL reporting analyst\n\nTECHNICAL SKILLS\nTools: SQL, Power BI\n\nPROJECTS\nReporting Project\n2026 | SQL\n- Built SQL reports")
    conn = db.init_db(root/"applypilot.db")
    conn.execute("INSERT INTO jobs(url,title,company,full_description,site,fit_score) VALUES(?,?,?,?,?,?)", ("https://example.com/jobs/1", "Data Analyst", "Fixture Ltd", "SQL reporting and data quality", "Direct", 8))
    conn.commit()
    yield root, conn
    db.close_connection(root/"applypilot.db")


def item(name, data, **extra):
    return {"name": name, "b64": base64.b64encode(data).decode(), **extra}


def docx(text):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'+text+'</w:t></w:r></w:p></w:body></w:document>')
    return stream.getvalue()


def test_multi_import_has_individual_outcomes_and_collision_safe_sources(workspace):
    root, conn = workspace
    out = cv_library.import_files(conn, root, [item("Data CV.txt", b"Verified SQL evidence", role="Data Analyst"), item("Data CV.txt", b"Second verified SQL evidence"), item("Support.docx", docx("Application support evidence")), item("bad.pdf", b"not a PDF")])
    assert out["imported"] == 3
    assert [r["ok"] for r in out["results"]] == [True, True, True, False]
    assert out["results"][0]["key"] != out["results"][1]["key"]
    assert (root/"resume.txt").read_text().startswith("Fixture Candidate")
    assert len(list((root/"cv_uploads").iterdir())) == 3
    route = route_resume_for_job({"title": "Junior Data Analyst, BI"}, app_dir=root)
    assert route.key == out["results"][0]["key"]


def test_docx_import_does_not_follow_external_relationships():
    assert cv_library.extract("cv.docx", docx("Verified experience")).strip() == "Verified experience"
    with pytest.raises(ValueError):
        cv_library.extract("cv.exe", b"payload")


def test_source_history_restore_default_rename_and_archive(workspace):
    root, conn = workspace
    out = cv_library.import_files(conn, root, [item("Data.txt", b"Original evidence", role="Data Analyst")])
    key = out["results"][0]["key"]
    first = conn.execute("SELECT id FROM cc_cv_revisions WHERE source_key=? ORDER BY id LIMIT 1", (key,)).fetchone()[0]
    from applypilot.setup_workspace import write_resume_variant
    write_resume_variant(root, key, "Updated evidence")
    cv_library.action(conn, root, {"action": "restore_revision", "key": key, "revision_id": first})
    assert cv_library.source_text(root, key).strip() == "Original evidence"
    cv_library.action(conn, root, {"action": "rename", "key": key, "new_key": "data_primary"})
    assert route_resume_for_job({"title": "Data Analyst"}, app_dir=root).key == "data_primary"
    cv_library.action(conn, root, {"action": "default", "key": "data_primary"})
    assert cv_library.source_text(root, "default").strip() == "Original evidence"
    cv_library.action(conn, root, {"action": "archive", "key": "data_primary"})
    assert not (root/"resume_variants/data_primary.txt").exists()
    assert cv_library.sources(conn, root)["archived"]
    assert (root/"cv_archive").is_dir()


def test_literal_profile_portfolio_proposals_and_profile_cv(workspace):
    root, _ = workspace
    proposals = cv_library.suggestions(root, "default")
    assert proposals["personal"]["email"] == "candidate@example.com"
    assert proposals["projects"][0]["header"] == "Reporting Project"
    built = cv_library.build_profile_cv(read_profile(root))
    assert "Fixture Candidate" in built and "Reporting Project" in built
    assert "candidate@example.com" in built


def test_ai_credentials_are_encrypted_isolated_and_never_returned(workspace, tmp_path, monkeypatch):
    root, _ = workspace
    other = tmp_path/"other"
    other.mkdir()
    monkeypatch.setenv("OPENAI_API_KEY", "global-secret")
    settings = {"credentials": {"OPENAI_API_KEY": "workspace-secret"}, "routes": [{"task": "cover", "provider": "openai", "model": "gpt-4.1-mini", "reasoning_effort": "none"}]}
    out = ai_settings.save(root, settings)
    assert "workspace-secret" not in (root/".ai-settings.json").read_text()
    assert "workspace-secret" not in json.dumps(out)
    from applypilot.ai_routing import resolve_route
    with ai_settings.workspace_context(root):
        assert resolve_route("cover").api_key == "workspace-secret"
    with ai_settings.workspace_context(other):
        assert resolve_route("cover").api_key == "global-secret"
    assert os.environ["OPENAI_API_KEY"] == "global-secret"


def draft_data(**extra):
    return {"kind": "cover", "job_id": 1, "variant": "default", "min_words": 50, "max_words": 300,
            "text": "Dear Hiring Manager,\n\n"+"Verified SQL reporting work. "*20+"\n\nI would welcome a discussion.\n\nBest,\nFixture Candidate", **extra}


def test_manual_draft_versions_preview_and_invalid_publication(workspace):
    root, conn = workspace
    first = studio.save(conn, root, draft_data(text="Too short"))
    assert first["document"]["status"] == "draft"
    assert not first["document"]["validation"]["passed"]
    with pytest.raises(ValueError, match="validation"):
        studio.publish(conn, root, first["document"]["id"])
    second = studio.save(conn, root, draft_data(parent_id=first["document"]["id"]))
    assert first["document"]["family"] == second["document"]["family"]
    assert studio.document(conn, root, first["document"]["id"])["text"] == "Too short"
    assert len(studio.listing(conn, "cover")["documents"]) == 2


def test_publication_rejects_changed_source_and_submitted_jobs(workspace, monkeypatch):
    root, conn = workspace
    monkeypatch.setattr(studio, "validate", lambda *a: {"passed": True, "errors": [], "words": 120})
    saved = studio.save(conn, root, draft_data())
    write_resume_text(root, "Updated CV source")
    with pytest.raises(ValueError, match="CV source changed"):
        studio.publish(conn, root, saved["document"]["id"])
    from document_fixtures import attest_text
    attest_text(conn,root,draft_data()["text"])
    current = studio.save(conn, root, draft_data())
    studio.publish(conn, root, current["document"]["id"])
    path = conn.execute("SELECT cover_letter_path FROM jobs").fetchone()[0]
    assert path == current["document"]["text_path"]
    conn.execute("UPDATE jobs SET applied_at='2026-10-04'")
    conn.commit()
    with pytest.raises(ValueError, match="submitted"):
        studio.publish(conn, root, current["document"]["id"])
    assert conn.execute("SELECT cover_letter_path FROM jobs").fetchone()[0] == path


def test_generation_is_cached_and_failed_requests_reserve_budget(workspace, monkeypatch):
    root, conn = workspace
    from applypilot import ai_routing, llm
    from applypilot.scoring import cover_letter
    calls = []
    monkeypatch.setattr(ai_routing, "route_info", lambda *a: {"configured": True, "provider": "openai", "model": "fixture"})
    monkeypatch.setattr(llm, "get_client", lambda: SimpleNamespace(last_model="fixture"))
    monkeypatch.setattr(cover_letter, "generate_cover_letter", lambda *a, **kw: calls.append(1) or draft_data()["text"])
    first = studio.generate(conn, root, draft_data())
    second = studio.generate(conn, root, draft_data())
    assert second["cached"] and calls == [1]
    assert first["document"]["id"] == second["document"]["id"]
    conn.executemany("INSERT INTO cc_writing_requests(created_at) VALUES(?)", [("2099-01-01",)]*19)
    conn.commit()
    with pytest.raises(ValueError, match="budget"):
        studio.generate(conn, root, draft_data(fresh=True,request_key="explicit_fixture_generation"))


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


def test_studio_cv_and_portfolio_http_flows(server):
    base, root, _ = server
    with httpx.Client(base_url=base, timeout=15) as client:
        result = client.post("/api/center/cv/import", json={"files": [item("Data.txt", b"Verified reporting evidence")]}).json()
        assert result["imported"] == 1
        assert client.get("/api/center/cv").json()["sources"]
        assert client.get("/api/center/cv/source?key=default&format=html").headers["content-type"].startswith("text/html")
        saved = client.post("/api/center/studio/save", json=draft_data()).json()
        assert saved["ok"]
        ident = saved["document"]["id"]
        assert client.get(f"/api/center/studio/document?id={ident}&format=txt").text.startswith("Dear")
        assert client.get("/api/center/studio?kind=cover").json()["documents"][0]["id"] == ident
        photo = client.post("/api/center/portfolio/upload", json={"project_id": "project-one", "name": "evidence.txt", "b64": base64.b64encode(b"Verified supporting evidence").decode()}).json()
        assert photo["ok"]
        assert client.get('/api/center/portfolio/asset?id='+str(photo["id"])).text == "Verified supporting evidence"
        assert client.post("/api/center/portfolio/upload", json={"project_id": "unknown", "name": "evidence.txt", "b64": "QQ=="}).status_code == 400


def test_ui_multi_upload_profile_portfolio_and_writing_versions(server):
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Enable Playwright")
    from playwright.sync_api import sync_playwright
    base, root, conn = server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base+'/workspace#cv')
        page.locator('#cv-multi-files').wait_for()
        page.locator('#cv-multi-files').set_input_files([{"name":"data_cv.txt","mimeType":"text/plain","buffer":b"Verified data reporting evidence"},{"name":"support_cv.txt","mimeType":"text/plain","buffer":b"Verified service desk evidence"}])
        page.locator('#cv-import-files').click()
        page.wait_for_function("document.querySelector('#cv-upload-results')?.textContent.includes('Imported as')")
        assert len(list((root/'resume_variants').glob('*.txt'))) == 2
        page.locator('[data-view=settings]').first.click()
        page.locator('#native-profile-save').wait_for()
        page.locator('[data-profile-key=city]').fill('Bristol')
        page.locator('#native-profile-save').click()
        page.wait_for_function("document.querySelector('#toast')?.textContent.includes('Professional profile saved')")
        assert read_profile(root)['personal']['city'] == 'Bristol'
        page.locator('[data-view=portfolio]').first.click()
        page.locator('#portfolio-save').wait_for()
        page.locator('[data-project-field=header]').fill('Updated Reporting Project')
        page.locator('#portfolio-save').click()
        page.wait_for_function("document.querySelector('#toast')?.textContent.includes('Portfolio saved')")
        assert read_profile(root)['resume_sections']['projects'][0]['header'] == 'Updated Reporting Project'
        page.locator('[data-view=cover]').first.click()
        page.locator('#writing-output').wait_for()
        page.locator('#writing-job').select_option('1')
        page.wait_for_function("document.querySelector('#writing-title')?.value === 'Data Analyst'")
        page.locator('#writing-output').fill('Manual draft to revise later')
        page.locator('#writing-save').click()
        page.wait_for_function("document.querySelector('#toast')?.textContent.includes('New draft version saved')")
        page.frame_locator('#writing-preview').get_by_text('Manual draft to revise later', exact=True).wait_for()
        assert conn.execute('SELECT COUNT(*) FROM cc_writing_documents').fetchone()[0] == 1
        if os.environ.get('APPLYPILOT_STUDIO_SCREENSHOTS'):
            from pathlib import Path
            output = Path(os.environ['APPLYPILOT_STUDIO_SCREENSHOTS'])
            assert output.is_dir()
            page.screenshot(path=str(output/'cover-desktop.png'), full_page=True)
        page.set_viewport_size({"width":390,"height":844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if os.environ.get('APPLYPILOT_STUDIO_SCREENSHOTS'):
            page.screenshot(path=str(output/'cover-mobile.png'), full_page=True)
        assert not errors
        browser.close()


def test_workspace_backup_captures_wal_and_rebases_documents(workspace, tmp_path):
    root, conn = workspace
    from applypilot.workspace_transfer import export_workspace, import_workspace
    saved = studio.save(conn, root, draft_data())
    archive = root/"backup.zip"
    export_workspace(root, archive, include_generated=True)
    restored = tmp_path/"restored"
    out = import_workspace(archive, restored)
    assert "applypilot.db" in out["restored"]
    moved = db.init_db(restored/"applypilot.db")
    row = moved.execute("SELECT text_path FROM cc_writing_documents WHERE id=?", (saved["document"]["id"],)).fetchone()
    assert str(restored) in row[0]
    assert studio.document(moved, restored, saved["document"]["id"])["text"].startswith("Dear")
    db.close_connection(restored/"applypilot.db")
