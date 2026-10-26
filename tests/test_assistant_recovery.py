import json
import os
from types import SimpleNamespace

import httpx
import pytest

from applypilot import ai_settings, command_center as cc, llm
from test_command_center import workspace, http_workspace  # noqa: F401


@pytest.mark.parametrize("provider", ["gemini", "openai", "anthropic"])
def test_interactive_timeout_is_single_attempt_and_restores_pipeline_defaults(provider):
    calls = []
    client = llm.LLMClient(provider, "https://example.org", "fixture-model", "fixture-key")
    def transport(request):
        calls.append(request)
        assert request.extensions["timeout"]["read"] == 45
        assert request.extensions["timeout"]["connect"] == 10
        raise httpx.ReadTimeout("raw-private-request-detail", request=request)
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(transport), timeout=120)
    try:
        with llm.request_budget():
            with pytest.raises(httpx.ReadTimeout):
                client.chat([{"role": "user", "content": "Explain"}])
        assert len(calls) == 1
        assert llm._request_options() == {}
        assert llm._attempt_limit() == llm._MAX_RETRIES
        assert client._client.timeout.read == 120
    finally:
        client._client.close()


@pytest.mark.parametrize("kind,category,status", [("timeout", "timeout", 504), ("payment", "payment", 503), ("unavailable", "provider_unavailable", 503), ("empty", "empty_response", 503)])
def test_assistant_http_errors_are_safe_terminal_and_not_cached(http_workspace, monkeypatch, kind, category, status):
    base, _, conn = http_workspace
    def chat(*args, **kwargs):
        assert llm._attempt_limit() == 1
        if kind == "timeout":
            raise httpx.ReadTimeout("private-key-123")
        if kind == "payment":
            raise llm.ProviderAccessError("gemini", "payment", 402, "Gemini blocked this request (HTTP 402; payment). Check billing/credits in Settings.")
        if kind == "unavailable":
            raise RuntimeError("private-key-123")
        return "   "
    monkeypatch.setattr(llm, "get_client", lambda: SimpleNamespace(provider="gemini", model="fixture-model", chat=chat))
    with httpx.Client(base_url=base) as client:
        response = client.post("/api/center/assistant", json={"task": "explain", "id": 1})
    assert response.status_code == status
    assert response.json()["category"] == category
    assert "private-key-123" not in response.text
    assert conn.execute("SELECT COUNT(*) FROM cc_ai_cache").fetchone()[0] == 0
    row = conn.execute("SELECT request_status,failure_category FROM cc_ai_usage").fetchone()
    assert tuple(row) == ("failed", category)


def test_empty_cached_answer_is_regenerated(workspace, monkeypatch):
    root, conn = workspace
    client = SimpleNamespace(provider="gemini", model="fixture-model", last_usage={}, chat=lambda *a, **kw: "Valid evidence")
    monkeypatch.setattr(llm, "get_client", lambda: client)
    cc.assistant(conn, root, {"id": 1})
    conn.execute("UPDATE cc_ai_cache SET answer=''")
    conn.commit()
    assert not cc.assistant(conn, root, {"id": 1})["cached"]
    assert conn.execute("SELECT request_status FROM cc_ai_usage ORDER BY id DESC LIMIT 1").fetchone()[0] == "completed"


def test_explicit_service_key_choice_preserves_dotenv_and_workspace_isolation(workspace, tmp_path, monkeypatch):
    root, _ = workspace
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-service-secret")
    original = "GEMINI_API_KEY=fixture-workspace-secret\nOTHER_CONFIG=keep\n"
    (root/".env").write_text(original)
    public = ai_settings.public(root)
    assert public["credential_origins"]["GEMINI_API_KEY"]["source"] == "workspace .env"
    assert public["credential_origins"]["GEMINI_API_KEY"]["workspace_env_differs_from_service"]
    assert "fixture-service-secret" not in json.dumps(public)
    assert "fixture-workspace-secret" not in json.dumps(public)
    assert ai_settings.environment(root)["GEMINI_API_KEY"] == "fixture-workspace-secret"
    ai_settings.save(root, {"credentials": {"GEMINI_API_KEY": None}})
    assert ai_settings.environment(root)["GEMINI_API_KEY"] == "fixture-service-secret"
    assert (root/".env").read_text() == original
    ai_settings.save(root, {"credentials": {"GEMINI_API_KEY": "fixture-encrypted-secret"}})
    assert ai_settings.environment(root)["GEMINI_API_KEY"] == "fixture-encrypted-secret"
    assert ai_settings.public(root)["credential_origins"]["GEMINI_API_KEY"]["source"] == "encrypted workspace key"
    other = tmp_path/"other"
    other.mkdir()
    (other/".env").write_text("GEMINI_API_KEY=other-workspace-secret\n")
    assert ai_settings.environment(other)["GEMINI_API_KEY"] == "other-workspace-secret"
    assert os.environ["GEMINI_API_KEY"] == "fixture-service-secret"


def browser_context():
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1 for browser verification")
    from playwright.sync_api import sync_playwright
    return sync_playwright()


@pytest.mark.parametrize("empty", [False, True])
def test_dialog_error_and_retry_are_visible(http_workspace, empty):
    base, _, _ = http_workspace
    with browser_context() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        failures = []
        page.on("pageerror", lambda error: failures.append(str(error)))
        responses = []
        def respond(route):
            responses.append(route.request.post_data_json)
            if len(responses) == 1:
                route.fulfill(status=200 if empty else 503, json={"ok": empty, "answer": ""} if empty else {"ok": False, "error": "Provider unavailable. Check Settings."})
            else:
                route.fulfill(json={"ok": True, "answer": "Recovered explanation", "provider": "gemini", "model": "fixture-model", "routing_task": "score"})
        page.route("**/api/center/assistant", respond)
        page.goto(base+"/workspace")
        page.locator('[data-detail="1"]').first.click()
        page.locator('[data-ai="explain"]').click()
        page.locator('#ai-detail[data-state="error"]').wait_for()
        assert "Reading saved evidence" not in page.locator("#ai-detail").inner_text()
        page.get_by_role("button", name="Retry answer", exact=True).click()
        page.locator('#ai-detail[data-state="completed"]').wait_for()
        assert page.locator("#ai-detail").inner_text() == "Recovered explanation"
        assert "fixture-model" in page.locator(".ai-response-meta").inner_text()
        assert len(responses) == 2 and responses[0] == responses[1]
        assert not failures
        browser.close()


def test_stop_waiting_and_late_answer_cannot_overwrite_another_job(http_workspace):
    base, _, conn = http_workspace
    conn.execute("INSERT INTO jobs(url,title,company) VALUES('https://example.org/2','Second job','Other Ltd')")
    conn.commit()
    with browser_context() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        # Deliberately ignore abort to exercise the late-response identity guard.
        page.add_init_script("""const originalFetch=window.fetch;window.fetch=(path,opts)=>path==='/api/center/assistant'?new Promise(resolve=>window.resolveLateAnswer=()=>resolve(new Response(JSON.stringify({ok:true,answer:'OLD JOB ANSWER',provider:'gemini',model:'old-model'}),{headers:{'Content-Type':'application/json'}}))):originalFetch(path,opts);""")
        page.goto(base+"/workspace")
        page.locator('[data-detail="1"]').first.click()
        page.locator('[data-ai="explain"]').click()
        page.get_by_role("button", name="Stop waiting", exact=True).click()
        assert "Stopped waiting" in page.locator("#ai-detail").inner_text()
        page.evaluate("openDetail(2)")
        page.evaluate("window.resolveLateAnswer()")
        page.wait_for_function("!document.querySelector('#ai-detail').hasAttribute('aria-busy')")
        assert "Second job" in page.locator("#detail-title").inner_text()
        assert "OLD JOB ANSWER" not in page.locator("#job-detail").inner_text()
        assert page.locator(".ai-response-meta").count() == 0
        browser.close()


def test_copilot_browser_timeout_has_terminal_retry_state(http_workspace):
    base, _, _ = http_workspace
    with browser_context() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.add_init_script("""const originalFetch=window.fetch;window.fetch=(path,opts)=>path==='/api/center/assistant'?new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')))):originalFetch(path,opts);const originalTimeout=window.setTimeout;window.setTimeout=(fn,delay,...args)=>originalTimeout(fn,delay===60000?30:delay,...args);""")
        page.goto(base+"/workspace#analytics")
        page.locator("#copilot").click()
        page.locator('#ai-overview[data-state="error"]').wait_for()
        assert "within 60 seconds" in page.locator("#ai-overview").inner_text()
        assert page.get_by_role("button", name="Retry answer", exact=True).is_visible()
        assert page.locator("#copilot").is_enabled()
        browser.close()
