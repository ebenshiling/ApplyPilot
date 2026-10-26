import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from applypilot import command_center as cc
from applypilot.apply import assisted_browser as ab
from applypilot.apply import llm_engine
from test_command_center import workspace, http_workspace  # noqa: F401, F811


def packet(root, conn, target="https://example.org/application"):
    cv = root / "cv.txt"
    cv.write_text("Reviewed candidate CV", encoding="utf-8")
    cv.with_suffix(".pdf").write_bytes(b"%PDF-1.4\nReviewed CV")
    statement = root / "statement.txt"
    statement.write_text("Reviewed NHS evidence: resolved incidents using documented diagnostics.", encoding="utf-8")
    statement.with_suffix(".pdf").write_bytes(b"%PDF-1.4\nReviewed statement")
    (root / "profile.json").write_text(json.dumps({"personal": {"full_name": "Example Candidate", "email": "candidate@example.org"}}), encoding="utf-8")
    conn.execute("UPDATE jobs SET tailored_resume_path=?,supporting_statement_path=?,application_url=?,document_requirements=? WHERE rowid=1", (str(cv), str(statement), target, json.dumps({"cover": False, "statement": True})))
    conn.commit()
    from document_fixtures import attest_text, finalized_document
    attest_text(conn, root, cv.read_text() + "\n" + statement.read_text())
    job = cc.get_job(conn, 1)
    finalized_document(conn, root, cv, job, pdf=True)
    finalized_document(conn, root, statement, job, kind="statement", pdf=True)


def wait_state(root, ident, desired, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        out = ab.status(root, ident)["session"]
        if out["state"] in desired:
            return out
        if out["state"] in ab.TERMINAL:
            pytest.fail(str(out))
        time.sleep(0.1)
    pytest.fail("Browser state wait timed out: " + str(ab.status(root, ident)))


@pytest.fixture
def portal():
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1 for browser verification")
    submissions = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/job":
                html = '<h1>Example role</h1><a href="/start">Apply now</a>'
            elif self.path == "/start":
                html = '''<form action="/second"><label>Name<input id="name" name="name" required></label><label>CV<input id="cv" type="file" required></label><button>Next</button></form>'''
            elif self.path.startswith("/second"):
                html = '''<h1>Application answers</h1><iframe src="/questions" style="width:1100px;height:750px;border:0"></iframe>'''
            elif self.path == "/questions":
                html = '''<form action="/done" method="post" target="_top"><label>Supporting information<textarea id="statement" name="statement" required></textarea></label><label>I confirm accuracy<input id="consent" type="checkbox" required></label><button id="final">Submit application</button></form>'''
            elif self.path.startswith("/login"):
                html = '<form action="/job"><input id="password" type="password" required><button>Sign in</button></form>'
            elif self.path == "/stale":
                html = '''<form method="post" action="/done"><textarea id="notes" name="notes">Reviewed answer</textarea><button>Submit application</button></form><script>setTimeout(()=>document.querySelector('#notes').value='Changed after review',2500)</script>'''
            elif self.path == "/uncertain":
                html = '<form method="post" action="/no-confirmation"><button>Submit application</button></form>'
            elif self.path == "/old":
                html = '<h1>Application received</h1><form action="/done" method="post"><button>Submit application</button></form>'
            else:
                html = '<p>Unknown page</p>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode())

        def do_POST(self):
            submissions.append((self.path, self.rfile.read(int(self.headers.get("Content-Length", 0)))))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>We are processing your request</h1>" if self.path == "/no-confirmation" else b"<h1>Your application has been submitted</h1><p>Application reference: REF-12345</p>")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", submissions
    server.shutdown()
    server.server_close()
    thread.join(3)


@pytest.fixture
def planner(monkeypatch):
    calls = []
    def plan(job, personal, fields, **kwargs):
        calls.append((personal, fields, kwargs))
        values = {"name": personal["full_name"], "statement": kwargs["reviewed_values"]["reviewed_statement"]}
        return [{"by": "id", "key": f["id"], "value": values[f["id"]]} for f in fields if f["id"] in values]
    monkeypatch.setattr(llm_engine, "_llm_plan_fills", plan)
    return calls


@pytest.fixture(autouse=True)
def end_sessions():
    yield
    sessions = list(ab._sessions.values())
    for session in sessions:
        if session.state not in ab.TERMINAL:
            ab.command(session.root, {"session_id": session.id, "action": "stop"})
    deadline = time.monotonic() + 10
    while any(s.state not in ab.TERMINAL for s in sessions) and time.monotonic() < deadline:
        time.sleep(0.1)
    with ab._lock:
        ab._sessions.clear()


def test_approval_and_workspace_ownership(workspace, monkeypatch):
    root, conn = workspace
    packet(root, conn)
    monkeypatch.setattr(ab.Session, "run", lambda self: None)
    with pytest.raises(ValueError, match="approve the application documents"):
        ab.start(conn, root, {"id": 1})
    assert not (root / "browser_sessions").exists()
    assert cc.get_job(conn, 1)["apply_status"] is None
    started = ab.start(conn, root, {"id": 1, "documents_approved": True})["session"]
    assert started["id"] and cc.get_job(conn, 1)["apply_status"] == "in_progress"
    ab._sessions[started["id"]].state = "stopped"
    # Historical/session ownership remains protected without launching a browser.
    owned = ab.Session(root.resolve(), "ownership-fixture", cc.get_job(conn, 1), {}, {}, {}, [])
    owned.state = "stopped"
    ab._sessions[owned.id] = owned
    session = {"id": owned.id}
    for operation in (lambda: ab.status(root.parent, session["id"]), lambda: ab.screenshot(root.parent, session["id"]), lambda: ab.command(root.parent, {"session_id": session["id"], "action": "stop"})):
        with pytest.raises(LookupError):
            operation()
    with pytest.raises(ValueError, match="ended"):
        ab.command(root, {"session_id": session["id"], "action": "approve", "approval_token": "invented"})


@pytest.mark.parametrize("state,expected", [("review", "prepared"), ("running", "prepared"), ("submitting", "submission_uncertain")])
def test_interrupted_session_is_recovered_conservatively(workspace, state, expected):
    root, conn = workspace
    ab._schema(conn)
    conn.execute("INSERT INTO cc_browser_sessions(id,job_url,state,detail,created_at,updated_at) VALUES('interrupted',?,?,?,?,?)", (cc.get_job(conn, 1)["url"], state, "", cc.now(), cc.now()))
    conn.execute("UPDATE jobs SET apply_status='in_progress',agent_id='browser:interrupted' WHERE rowid=1")
    conn.commit()
    assert ab.current(conn, root, 1)["session"]["state"] == ("uncertain" if state == "submitting" else "stopped")
    assert cc.get_job(conn, 1)["apply_status"] == expected


def test_planner_uses_only_reviewed_values_and_never_passwords(monkeypatch):
    class Client:
        def ask(self, prompt, **kwargs):
            assert '"reviewed_statement": "Actual reviewed evidence"' in prompt
            assert '"id": "secret"' not in prompt
            return json.dumps({"fills": [{"by": "id", "key": "statement", "value_key": "reviewed_statement"}, {"by": "id", "key": "secret", "value_key": "email"}, {"by": "id", "key": "statement", "value_key": "invented"}]})
    monkeypatch.setattr(llm_engine, "get_client", lambda: Client())
    result = llm_engine._llm_plan_fills({}, {"email": "a@example.org"}, [{"id": "statement", "tag": "textarea"}, {"id": "secret", "type": "password"}], verified_answers=[], reviewed_values={"reviewed_statement": "Actual reviewed evidence"})
    assert result == [{"by": "id", "key": "statement", "value": "Actual reviewed evidence"}]


def test_multiframe_application_takeover_guard_and_confirmation(workspace, portal, planner):
    root, conn = workspace
    base, submissions = portal
    packet(root, conn, base + "/job")
    ident = ab.start(conn, root, {"id": 1, "documents_approved": True})["session"]["id"]
    wait_state(root, ident, {"manual"})
    assert planner[0][0]["email"] == "candidate@example.org"
    assert any("Reviewed NHS evidence" in c[2]["reviewed_values"]["reviewed_statement"] for c in planner)
    assert ab.screenshot(root, ident).startswith(b"\xff\xd8")
    # Tab into iframe, toggle required consent without introducing new evidence.
    for action in ("Tab", "Space", "Tab", "Enter"):
        ab.command(root, {"session_id": ident, "action": "key", "key": action})
    time.sleep(0.8)
    ab.command(root, {"session_id": ident, "action": "review"})
    review = wait_state(root, ident, {"review"})
    assert not submissions
    assert review["approval_token"]
    assert any(a["value"] == "Checked" for a in review["review_summary"])
    with pytest.raises(ValueError, match="Take over"):
        ab.command(root, {"session_id": ident, "action": "text", "text": "Unreviewed change"})
    ab.command(root, {"session_id": ident, "action": "approve", "approval_token": review["approval_token"]})
    confirmed = wait_state(root, ident, {"confirmed"})
    assert confirmed["reference"] == "REF-12345"
    assert len(submissions) == 1 and b"Reviewed+NHS+evidence" in submissions[0][1]
    assert cc.get_job(conn, 1)["applied_at"]
    meta = conn.execute("SELECT * FROM cc_job_meta").fetchone()
    assert meta["reference"] == "REF-12345" and meta["submission_method"] == "assisted_agent"
    assert (root / "browser_sessions" / ident / "confirmation.png").exists()
    with pytest.raises(ValueError, match="ended"):
        ab.command(root, {"session_id": ident, "action": "approve", "approval_token": review["approval_token"]})


@pytest.mark.parametrize("path,expected", [("stale", "manual"), ("old", "manual"), ("uncertain", "uncertain")])
def test_stale_approval_old_confirmation_and_uncertain_result(workspace, portal, planner, path, expected):
    root, conn = workspace
    base, submissions = portal
    packet(root, conn, base + "/" + path)
    ident = ab.start(conn, root, {"id": 1, "documents_approved": True})["session"]["id"]
    review = wait_state(root, ident, {"review"})
    if path == "stale":
        time.sleep(3)
    ab.command(root, {"session_id": ident, "action": "approve", "approval_token": review["approval_token"]})
    wait_state(root, ident, {expected})
    assert len(submissions) == (1 if path == "uncertain" else 0)
    assert not cc.get_job(conn, 1)["applied_at"]
    if path == "uncertain":
        assert cc.get_job(conn, 1)["apply_status"] == "submission_uncertain"
        with pytest.raises(ValueError, match="reconcile"):
            ab.start(conn, root, {"id": 1, "documents_approved": True})


def test_login_handoff_and_stop(workspace, portal, planner):
    root, conn = workspace
    base, submissions = portal
    packet(root, conn, base + "/login")
    ident = ab.start(conn, root, {"id": 1, "documents_approved": True})["session"]["id"]
    wait_state(root, ident, {"manual"})
    assert not planner
    ab.command(root, {"session_id": ident, "action": "key", "key": "Tab"})
    ab.command(root, {"session_id": ident, "action": "text", "text": "FixturePassword"})
    ab.command(root, {"session_id": ident, "action": "key", "key": "Tab"})
    ab.command(root, {"session_id": ident, "action": "key", "key": "Enter"})
    deadline = time.monotonic()+10
    while "/login" in ab.status(root, ident)["session"]["page_url"] and time.monotonic()<deadline:
        time.sleep(0.1)
    assert "/login" not in ab.status(root, ident)["session"]["page_url"]
    assert "FixturePassword" not in " ".join(r[0] for r in conn.execute("SELECT detail FROM cc_events"))
    ab.command(root, {"session_id": ident, "action": "stop"})
    wait_state(root, ident, {"stopped"})
    assert cc.get_job(conn, 1)["apply_status"] == "prepared" and not submissions


def test_workspace_browser_ui_submission(http_workspace, portal, planner):
    base, root, conn = http_workspace
    employer, submissions = portal
    packet(root, conn, employer + "/uncertain")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1100})
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(base + "/workspace")
            page.get_by_role("button", name="View record", exact=True).click()
            page.get_by_role("button", name="Apply with agent", exact=True).click()
            page.locator("#agent-documents-approved").check()
            page.locator("#agent-start").click()
            try:
                page.locator("#agent-approve").wait_for(state="visible")
            except Exception:
                pytest.fail("UI state: " + page.locator('.agent-dialog').inner_text() + "\nSessions: " + str([s.public() for s in ab._sessions.values()]))
            page.wait_for_function("document.querySelector('#agent-image').naturalWidth===1280")
            assert not submissions
            page.locator("#agent-approve").click()
            page.wait_for_function("document.querySelector('#agent-state').textContent.startsWith('uncertain')", timeout=25000)
            assert len(submissions) == 1 and not errors
            assert "no automatic resubmission" in page.locator("#agent-progress").inner_text()
        finally:
            browser.close()
    with httpx.Client(base_url=base) as client:
        assert client.get("/assets/assisted-browser.js").status_code == 200
        assert client.get("/api/center/browser?session_id=foreign").status_code == 404
