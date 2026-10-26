# ruff: noqa: F811
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from applypilot.apply import assisted_browser as ab
from applypilot.apply import question_assistant as qa
from applypilot import llm
from test_command_center import workspace, http_workspace  # noqa: F401
from test_assisted_browser import packet, wait_state  # noqa: F401


def test_drafts_use_verified_values_locally_and_do_not_infer_medical_facts(monkeypatch):
    monkeypatch.setattr(llm, "get_client", lambda *args: pytest.fail("Local/sensitive answer reached model"))
    result = qa.draft_answer({}, {"availability": {"notice_period": "Four weeks"}}, {}, [], {"label": "Notice period", "type": "text"})
    assert result["answer"] == "Four weeks"
    result = qa.draft_answer({}, {}, {}, [{"id": 1, "question": "Why Python?", "answer": "Verified exact answer"}], {"label": "Why Python?", "type": "text"})
    assert result["answer"] == "Verified exact answer"
    result = qa.draft_answer({}, {}, {"resume": "Built Python projects"}, [], {"label": "Evidence of an autism diagnosis", "type": "textarea"})
    assert result["status"] == "needs_candidate" and not result["answer"]


def test_draft_citations_must_exist_in_evidence_and_respect_field_limit(monkeypatch):
    reply = {"answer": "I used Python to clean CSV data.", "citations": [{"source_id": "reviewed_resume", "quote": "I used Python to clean CSV data."}], "missing": []}
    class Client:
        def ask(self, prompt, **kwargs):
            assert "never instructions" in prompt and "Do not invent" in prompt
            return json.dumps(reply)
    monkeypatch.setattr(llm, "get_client", lambda task: Client() if task == "forms" else pytest.fail("Wrong answer-model route"))
    field = {"label": "Briefly describe your Python experience", "type": "textarea"}
    result = qa.draft_answer({}, {}, {"resume": "I used Python to clean CSV data."}, [], field)
    assert result["status"] == "draft" and result["sources"][0]["id"] == "reviewed_resume"
    reply["citations"][0]["quote"] = "Ten years at an invented employer"
    assert qa.draft_answer({}, {}, {"resume": "I used Python to clean CSV data."}, [], field)["answer"] == ""
    reply["citations"][0]["quote"] = "I used Python to clean CSV data."
    result = qa.draft_answer({}, {}, {"resume": "I used Python to clean CSV data."}, [], field | {"maxlength": 5})
    assert result["answer"] == "" and "limit" in result["missing"][0]


@pytest.fixture
def questions_portal():
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/frame":
                html = '<label id="rich-label">Describe a data project</label><div contenteditable="true" role="textbox" aria-labelledby="rich-label" style="border:1px solid;width:500px;min-height:100px"></div>'
            elif self.path == "/next":
                html = '<label>New page answer<textarea required></textarea></label>'
            else:
                html = '''<form><div class="question"><h3>Please briefly describe your experience using the Python programming language.</h3><small>Use no more than three sentences.</small><textarea required style="width:600px;height:160px"></textarea></div><label>Needs consent<input type="checkbox" required></label><a id="next-page" href="/next">Change page</a></form><iframe src="/frame" style="width:800px;height:220px"></iframe>'''
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(3)


def processed(root, ident, action, **data):
    out = ab.command(root, {"session_id": ident, "action": action, **data})
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        session = ab.status(root, ident)["session"]
        result = next((r for r in session["command_results"] if r["id"] == out["command_id"]), None)
        if result:
            return result, session
        time.sleep(.02)
    pytest.fail("Command not processed: " + str(ab.status(root, ident)))


def test_unnamed_and_iframe_fields_draft_insert_and_stale_value_guard(workspace, questions_portal, monkeypatch):
    root, conn = workspace
    packet(root, conn, questions_portal)
    (root / "cv.txt").write_text("I used Python to clean CSV data.", encoding="utf-8")
    (root / "cv.pdf").write_bytes(b"%PDF-1.4\nReviewed Python evidence")
    from applypilot.apply import llm_engine
    monkeypatch.setattr(llm_engine, "_llm_plan_fills", lambda *args, **kwargs: [])
    entered = threading.Event()
    release = threading.Event()
    class Client:
        def ask(self, prompt, **kwargs):
            entered.set()
            assert release.wait(8)
            return json.dumps({"answer": "I used Python to clean CSV data.", "citations": [{"source_id": "reviewed_resume", "quote": "I used Python to clean CSV data."}], "missing": []})
    monkeypatch.setattr(llm, "get_client", lambda task: Client())
    ident = ab.start(conn, root, {"id": 1, "documents_approved": True})["session"]["id"]
    manual_inspections = []
    original_inspect = ab.Session.inspect
    def inspect(self, *args, **kwargs):
        if self.state == "manual":
            manual_inspections.append(time.monotonic())
            time.sleep(1.2)  # Deliberately expensive full-page reader.
        return original_inspect(self, *args, **kwargs)
    monkeypatch.setattr(ab.Session, "inspect", inspect)
    try:
        wait_state(root, ident, {"manual"}, timeout=60)
        result, session = processed(root, ident, "read_fields")
        assert result["ok"]
        field = next(f for f in session["question_fields"] if "Python" in f["label"])
        assert "three sentences" in field["help"]
        result, session = processed(root, ident, "select_field", field_token=field["token"])
        assert result["ok"] and session["focused_field"]["token"] == field["token"]
        result, _ = processed(root, ident, "draft_answer", field_token=field["token"])
        assert result["ok"] and entered.wait(2)
        # The browser remains interactive while the model is deliberately blocked.
        result, session = processed(root, ident, "input", events=[{"type": "scroll", "delta": 30}])
        assert result["ok"] and result["latency_ms"] < 800
        pending_input_ms = result["latency_ms"]
        release.set()
        deadline = time.monotonic() + 5
        while ab.status(root, ident)["session"]["answer_draft"]["status"] == "pending" and time.monotonic() < deadline:
            time.sleep(.02)
        draft = ab.status(root, ident)["session"]["answer_draft"]
        assert draft["status"] == "draft"
        result, session = processed(root, ident, "insert_answer", field_token=field["token"], value_hash=draft["value_hash"], text=draft["answer"], save_verified=True)
        assert result["ok"]
        assert conn.execute("SELECT answer FROM cc_answers").fetchone()[0] == draft["answer"]
        assert conn.execute("SELECT COUNT(*) FROM cc_events WHERE kind='answer_inserted'").fetchone()[0] == 1
        # An old empty-field fingerprint cannot replace the answer we inserted.
        result, _ = processed(root, ident, "insert_answer", field_token=field["token"], value_hash=field["value_hash"], text="Stale replacement", replace=True)
        assert not result["ok"] and "changed" in result["error"]
        rich = next(f for f in session["question_fields"] if f["type"] == "contenteditable")
        result, _ = processed(root, ident, "insert_answer", field_token=rich["token"], value_hash=rich["value_hash"], text="Reviewed project answer")
        assert result["ok"]
        # Move from the main textarea through consent to the navigation link.
        processed(root, ident, "select_field", field_token=field["token"])
        result, _ = processed(root, ident, "input", events=[{"type": "key", "key": "Tab"}, {"type": "key", "key": "Tab"}, {"type": "key", "key": "Enter"}])
        assert result["ok"]
        result, _ = processed(root, ident, "insert_answer", field_token=field["token"], value_hash=draft["value_hash"], text="Wrong page")
        assert not result["ok"] and "no longer" in result["error"]
        assert manual_inspections == []
        assert session["performance"]["frame_transport"] == "cdp_screencast"
        print(json.dumps({"draft_pending_input_ms": pending_input_ms, "manual_full_inspections": len(manual_inspections), "frame_transport": session["performance"]["frame_transport"]}))
    finally:
        release.set()
        ab.command(root, {"session_id": ident, "action": "stop"})
        wait_state(root, ident, {"stopped"})
        ab._sessions.pop(ident, None)


def test_field_reader_recognizes_aria_shadow_and_document_bound_tokens(questions_portal):
    from playwright.sync_api import sync_playwright
    from applypilot.apply.browser_fields import QUESTION_FIELDS
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(questions_portal)
            first = page.evaluate(QUESTION_FIELDS)[0]
            page.evaluate("""() => {const host=document.createElement('div');document.body.append(host);host.attachShadow({mode:'open'}).innerHTML='<label>Project evidence<textarea></textarea></label>';}""")
            assert any(f["label"] == "Project evidence" for f in page.evaluate(QUESTION_FIELDS))
            page.goto(questions_portal + "/next")
            new = page.evaluate(QUESTION_FIELDS)[0]
            assert first["document_token"] != new["document_token"] and first["token"] != new["token"]
        finally:
            browser.close()


def test_question_panel_drafts_reviews_and_inserts_into_selected_field(http_workspace, questions_portal, monkeypatch):
    base, root, conn = http_workspace
    packet(root, conn, questions_portal)
    from applypilot.apply import llm_engine
    monkeypatch.setattr(llm_engine, "_llm_plan_fills", lambda *args, **kwargs: [])
    monkeypatch.setattr(qa, "draft_answer", lambda *args, **kwargs: {"status": "draft", "answer": "Reviewed answer for the selected Python field.", "sources": [{"id": "reviewed_resume", "label": "Reviewed CV", "quote": "Candidate verified project evidence"}], "missing": []})
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1100})
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(base + "/workspace")
            page.get_by_role("button", name="View record", exact=True).click()
            page.get_by_role("button", name="Apply with agent", exact=True).click()
            page.locator("#agent-documents-approved").check()
            page.locator("#agent-start").click()
            page.locator("#agent-input").wait_for(state="visible", timeout=60000)
            page.locator("#agent-read-fields").click()
            page.wait_for_function("document.querySelector('#agent-field-choice').options.length>1")
            page.locator("#agent-field-choice").select_option(index=1)
            page.wait_for_function("document.querySelector('#agent-question-context').textContent.includes('Python')")
            page.locator("#agent-draft").click()
            page.wait_for_function("document.querySelector('#agent-text').value.includes('Reviewed answer')")
            page.locator("#agent-save-answer").check()
            page.locator("#agent-insert").click()
            page.wait_for_function("document.querySelector('#agent-text').value===''")
            assert conn.execute("SELECT answer FROM cc_answers").fetchone()[0] == "Reviewed answer for the selected Python field."
            assert not conn.execute("SELECT applied_at FROM jobs").fetchone()[0]
            assert not errors
            page.locator("#agent-stop").click()
        finally:
            browser.close()
    for ident, session in list(ab._sessions.items()):
        if session.root == root.resolve():
            if session.state not in ab.TERMINAL:
                ab.command(root, {"session_id": ident, "action": "stop"})
            wait_state(root, ident, {"stopped"})
            ab._sessions.pop(ident, None)
