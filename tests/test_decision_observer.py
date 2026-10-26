import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from applypilot import ai_settings
from applypilot import decision_observer as decisions
from applypilot.apply import assisted_browser as ab
from applypilot.database import close_connection, init_db
from test_command_center import workspace, http_workspace  # noqa: F401, F811
from test_assisted_browser import portal, planner, packet, wait_state  # noqa: F401, F811


def enable(root, monkeypatch, budget=20):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-observer-key")
    monkeypatch.setenv("APPLYPILOT_AI_DAILY_REQUESTS", "50")
    return ai_settings.save(root, {"decisions_observer": {"enabled": True, "daily_budget": budget}})


def response_for(view):
    known = decisions.baseline(view)
    answers = [{"type": "choice", "name": "page_state", "choice": known["page_state"], "confidence": 0.96},
               {"type": "predicate", "name": "new_confirmation", "probability": 0.99 if known["new_confirmation"] else 0.01}]
    if len(decisions.questions(view)) == 3:
        answers.append({"type": "choice", "name": "next_control", "choice": known["next_control"], "confidence": 0.95})
    return {"answers": answers, "usage": {"input_tokens": 1000}}


@pytest.fixture
def fake_provider(monkeypatch):
    actual = httpx.Client
    captured = []
    def serve(request):
        captured.append(request)
        view = json.loads(json.loads(request.content)["input"])
        return httpx.Response(200, json=response_for(view))
    monkeypatch.setattr(decisions.httpx, "Client", lambda **options: actual(transport=httpx.MockTransport(serve), **options))
    return captured


def test_setting_is_off_by_default_and_private(workspace, monkeypatch):
    root, _ = workspace
    monkeypatch.delenv("APPLYPILOT_DECISIONS_OBSERVER", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-private-service-key")
    assert not ai_settings.public(root)["decisions_observer"]["enabled"]
    public = enable(root, monkeypatch, 12)
    assert public["decisions_observer"]["daily_budget"] == 12
    assert public["decisions_observer"]["mode"] == "observation_only"
    assert "fixture-observer-key" not in json.dumps(public)
    other = root / "other"
    other.mkdir()
    assert not ai_settings.public(other)["decisions_observer"]["enabled"]
    for value in (True, 0, 201, 3.5, "20"):
        with pytest.raises(ValueError):
            ai_settings.save(root, {"decisions_observer": {"daily_budget": value}})
    with pytest.raises(ValueError):
        ai_settings.save(root, {"decisions_observer": {"enabled": "yes"}})


def test_page_payload_excludes_values_urls_credentials_and_known_contacts():
    raw = [{"fields": [{"type": "text", "id": "private-dom-id", "value": "Do not transmit this answer", "label": "Your application name"}],
            "controls": [{"label": "Review", "href": "https://example.org/?access_token=SecretToken"}],
            "text": "Application for Test Person at user@example.org\nApplication access_token=SecretToken\nApplication authorization=Bearer BearerSecret\nApplication password='quoted secret phrase'\nApplication answer: Do not transmit this answer\nApplication profile https://example.org/private"}]
    view = decisions.page_view(raw, personal={"full_name": "Test Person"})
    encoded = json.dumps(view)
    for secret in ("Do not transmit", "private-dom-id", "Test Person", "user@example.org", "SecretToken", "BearerSecret", "quoted secret phrase", "https://"):
        assert secret not in encoded
    assert view["frames"][0]["fields"][0]["filled"]
    raw[0]["fields"] = [{"type": "password", "label": "Password", "value": "SecretPassword"}]
    assert decisions.page_view(raw)["frames"][0]["excerpt"] == ""


def test_request_and_usage_match_official_contract(workspace, monkeypatch, fake_provider):
    root, conn = workspace
    enable(root, monkeypatch)
    case = decisions.fixtures()[2]
    result = decisions.evaluate(conn, root, case["view"], session_id="fixture-session")
    assert result["status"] == "completed" and result["prediction"]["page_state"] == "application"
    request = fake_provider[0]
    payload = json.loads(request.content)
    assert str(request.url) == "https://api.openai.com/v1/decisions"
    assert request.headers["authorization"] == "Bearer fixture-observer-key"
    assert payload["model"] == "gpt-6-luna"
    assert all(q["type"] in {"choice", "predicate"} for q in payload["questions"])
    usage = conn.execute("SELECT * FROM cc_ai_usage").fetchone()
    assert usage["task"] == "decisions_observer" and usage["input_tokens"] == 1000
    assert usage["request_status"] == "completed" and usage["output_tokens"] is None
    assert result["estimated_standard_usd"] == pytest.approx(0.0001)
    report = decisions.report(conn, root)
    assert report["summary"]["browser_agreed"] == 1
    assert "fixture-observer-key" not in json.dumps(report)


@pytest.mark.parametrize("mutation", ["unknown_choice", "wrong_type", "duplicate", "infinite", "boolean", "missing"])
def test_malformed_answers_are_rejected(mutation):
    view = decisions.fixtures()[2]["view"]
    data = response_for(view)
    if mutation == "unknown_choice":
        data["answers"][0]["choice"] = "execute_submit"
    elif mutation == "wrong_type":
        data["answers"][0]["type"] = "score"
    elif mutation == "duplicate":
        data["answers"][1] = data["answers"][0]
    elif mutation == "infinite":
        data["answers"][0]["confidence"] = math.inf
    elif mutation == "boolean":
        data["answers"][1]["probability"] = True
    else:
        data["answers"].pop()
    with pytest.raises(ValueError):
        decisions._parse(data, decisions.questions(view))


@pytest.mark.parametrize("status,category", [(401, "authentication"), (402, "billing"), (429, "quota_or_rate_limit"), (500, "provider_unavailable")])
def test_provider_errors_are_bounded_and_redacted(workspace, monkeypatch, status, category):
    root, conn = workspace
    enable(root, monkeypatch)
    actual = httpx.Client
    calls = []
    def error(request):
        calls.append(request)
        return httpx.Response(status, text="password=EchoedSecret fixture-observer-key")
    monkeypatch.setattr(decisions.httpx, "Client", lambda **options: actual(transport=httpx.MockTransport(error), **options))
    result = decisions.evaluate(conn, root, decisions.fixtures()[0]["view"])
    assert result["status"] == "failed" and result["category"] == category and len(calls) == 1
    public = json.dumps(decisions.report(conn, root))
    assert "EchoedSecret" not in public and "fixture-observer-key" not in public
    assert conn.execute("SELECT request_status FROM cc_ai_usage").fetchone()[0] == "failed"


def test_refusal_unknown_usage_and_false_confirmation_are_explicit(workspace, monkeypatch):
    root, conn = workspace
    enable(root, monkeypatch)
    view = decisions.fixtures()[-1]["view"]
    monkeypatch.setattr(decisions, "_request", lambda key, payload: {"status": "refused", "category": "refusal", "prediction": None, "input_tokens": None, "latency_ms": 3})
    decisions.evaluate(conn, root, view)
    monkeypatch.setattr(decisions, "_request", lambda key, payload: {"status": "completed", "category": None, "prediction": {"page_state": "confirmation", "new_confirmation": True, "next_control": "none"}, "input_tokens": None, "latency_ms": 7})
    decisions.evaluate(conn, root, view, run_id="fixture-run", expected=decisions.fixtures()[-1]["expected"])
    summary = decisions.report(conn, root)["summary"]
    assert summary["false_confirmations"] == 1 and summary["labelled_correct"] == 0
    assert summary["page_state_correct"] == 0 and summary["confirmation_correct"] == 0 and summary["navigation_correct"] == 1
    assert summary["failed"] == 1 and summary["usage_unknown"] == 2
    assert summary["reported_input_tokens"] is None and summary["estimated_standard_usd"] is None


def test_disabled_observer_and_missing_key_never_call_provider(workspace, monkeypatch):
    root, conn = workspace
    monkeypatch.setattr(decisions, "_request", lambda *args: pytest.fail("Disabled observer made a provider request"))
    ai_settings.save(root, {"decisions_observer": {"enabled": False}})
    view = decisions.fixtures()[0]["view"]
    assert decisions.evaluate(conn, root, view)["category"] == "disabled"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ai_settings.save(root, {"decisions_observer": {"enabled": True}})
    assert decisions.evaluate(conn, root, view)["category"] == "missing_connection"
    assert conn.execute("SELECT COUNT(*) FROM cc_ai_usage").fetchone()[0] == 0


def test_daily_reservations_are_atomic_across_workers(workspace, monkeypatch):
    root, conn = workspace
    enable(root, monkeypatch, 2)
    decisions.schema(conn)
    monkeypatch.setattr(decisions, "_request", lambda *args: {"status": "failed", "category": "timeout", "prediction": None, "input_tokens": None, "latency_ms": 10})
    def worker(_):
        db = init_db(root / "applypilot.db")
        try:
            return decisions.evaluate(db, root, decisions.fixtures()[0]["view"])
        finally:
            close_connection(root / "applypilot.db")
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(worker, range(5)))
    assert sum(r["status"] == "failed" for r in results) == 2
    assert sum(r.get("category") == "daily_budget" for r in results) == 3
    assert conn.execute("SELECT COUNT(*) FROM cc_ai_usage").fetchone()[0] == 2
    ai_settings.save(root, {"decisions_observer": {"daily_budget": 20}})
    monkeypatch.setenv("APPLYPILOT_AI_DAILY_REQUESTS", "2")
    assert decisions.evaluate(conn, root, decisions.fixtures()[0]["view"])["category"] == "daily_budget"


def test_observer_deduplicates_and_benchmark_has_labelled_results(workspace, monkeypatch):
    root, conn = workspace
    enable(root, monkeypatch)
    calls = []
    def classify(key, view):
        calls.append(view)
        return {"status": "completed", "category": None, "prediction": {**decisions.baseline(view), "page_state_confidence": 0.96}, "input_tokens": 100, "latency_ms": 5}
    monkeypatch.setattr(decisions, "_request", classify)
    observer = decisions.Observer(root, "fixture-session")
    try:
        view = decisions.fixtures()[0]["view"]
        observer.observe(view)
        observer.future.result(timeout=10)
        observer.observe(view)
        assert len(calls) == 1
    finally:
        observer.close()
    run = decisions.start_benchmark(conn, root)
    deadline = time.monotonic()+20
    while time.monotonic()<deadline:
        out = decisions.report(conn, root)
        if out["latest_run"]["state"] != "running":
            break
        time.sleep(0.05)
    assert out["latest_run"]["id"] == run["run_id"] and out["latest_run"]["state"] == "completed"
    assert out["summary"]["labelled_correct"] == out["summary"]["labelled_cases"] == 9
    assert out["summary"]["browser_compared"] == 1
    assert run["case_count"] == 9 and len(calls) == 10


def test_disagreement_cannot_submit_or_delay_takeover(workspace, portal, planner, monkeypatch):
    root, conn = workspace
    base, submissions = portal
    packet(root, conn, base+"/uncertain")
    enable(root, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    def blocked(key, view):
        entered.set()
        release.wait(120)
        return {"status": "completed", "category": None, "prediction": {"page_state": "confirmation", "new_confirmation": True, "next_control": "f0:c0"}, "input_tokens": 100, "latency_ms": 10}
    monkeypatch.setattr(decisions, "_request", blocked)
    ident = ab.start(conn, root, {"id": 1, "documents_approved": True})["session"]["id"]
    try:
        review = wait_state(root, ident, {"review"}, timeout=60)
        assert entered.wait(30), ab.status(root, ident)
        assert review["approval_token"] and not submissions
        started = time.monotonic()
        ab.command(root, {"session_id": ident, "action": "takeover"})
        wait_state(root, ident, {"manual"}, timeout=3)
        assert time.monotonic()-started < 3 and not release.is_set()
        release.set()
        ab._sessions[ident].observer.future.result(timeout=10)
        assert not submissions and not cc_job_applied(conn)
        assert ab.status(root, ident)["session"]["state"] == "manual"
    finally:
        release.set()
        ab.command(root, {"session_id": ident, "action": "stop"})
        wait_state(root, ident, {"stopped"})
        ab._sessions.pop(ident, None)


def cc_job_applied(conn):
    return conn.execute("SELECT applied_at FROM jobs WHERE rowid=1").fetchone()[0]


def test_trial_settings_and_benchmark_ui(http_workspace, monkeypatch):
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1 for browser verification")
    base, root, conn = http_workspace
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-observer-key")
    monkeypatch.setattr(decisions, "_request", lambda key, view: {"status": "completed", "category": None, "prediction": decisions.baseline(view), "input_tokens": 100, "latency_ms": 3})
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(base+"/workspace#settings")
            page.locator("#ai-decisions-settings > summary").click()
            page.locator("#ai-decisions-enabled").check()
            page.locator("#ai-decisions-budget").fill("12")
            page.locator("#native-ai-save").click()
            page.wait_for_function("document.querySelector('#ai-decisions-budget').value==='12' && document.querySelector('#ai-decisions-benchmark').disabled===false")
            page.locator("#ai-decisions-settings > summary").click()
            page.locator("#ai-decisions-benchmark").click()
            page.wait_for_function("document.querySelector('#ai-decisions-report').textContent.includes('9 / 9')", timeout=20000)
            assert "browser checks agreeing with baseline" in page.locator("#ai-decisions-report").inner_text()
            assert "fixture-observer-key" not in page.content() and not errors
        finally:
            browser.close()
    assert decisions.report(conn, root)["summary"]["labelled_cases"] == 9
