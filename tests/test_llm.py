from applypilot import llm
import json

import httpx
import pytest


@pytest.fixture
def mocked_client(monkeypatch):
    clients = []
    original_client = httpx.Client

    def make(model, handler, provider="openai", **options):
        transport_client = original_client(transport=httpx.MockTransport(handler), trust_env=False)
        monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: transport_client)
        try:
            client = llm.LLMClient(provider, "https://api.example.test/v1", model, "test-key", **options)
        except Exception:
            transport_client.close()
            raise
        clients.append(client)
        return client

    yield make
    for client in clients:
        client._client.close()


@pytest.mark.parametrize("model", ["gpt-6-luna", "gpt-5.6-luna"])
@pytest.mark.parametrize("effort", ["none", "low", "medium", "high", "xhigh", "max"])
def test_luna_request_contract_and_usage(mocked_client, monkeypatch, model, effort):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", effort)
    messages = [{"role": "system", "content": "Return factual JSON only."}, {"role": "user", "content": "Return {\"ok\":true}."}]

    def response(request):
        payload = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert payload["model"] == model
        assert payload["max_completion_tokens"] == 600
        assert "max_tokens" not in payload
        assert payload["reasoning_effort"] == effort
        assert ("temperature" in payload) == (effort == "none")
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["messages"][0]["role"] == "developer"
        assert payload["messages"][1] == messages[1]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}], "usage": {"prompt_tokens": 90, "completion_tokens": 20, "completion_tokens_details": {"reasoning_tokens": 10}}})

    client = mocked_client(model, response)
    assert client.chat(messages, temperature=0.2, max_tokens=600, response_mime_type="application/json") == '{"ok":true}'
    assert messages[0]["role"] == "system"
    assert client.last_usage["completion_tokens_details"]["reasoning_tokens"] == 10


def test_luna_defaults_to_no_reasoning_and_cache_identity_changes(mocked_client, monkeypatch):
    monkeypatch.delenv("OPENAI_REASONING_EFFORT", raising=False)
    client = mocked_client("gpt-6-luna", lambda request: httpx.Response(200))
    assert client.openai_reasoning_effort == "none"
    identity = client.cache_identity
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "medium")
    assert client.openai_reasoning_effort == "medium"
    assert client.cache_identity != identity


def test_invalid_luna_effort_fails_before_network_call(mocked_client, monkeypatch):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "minimal")
    def response(request):
        pytest.fail("Invalid reasoning configuration must not make a paid request")
    client = mocked_client("gpt-6-luna", response)
    with pytest.raises(ValueError, match="OPENAI_REASONING_EFFORT"):
        client.chat([{"role": "user", "content": "Test"}])


@pytest.mark.parametrize("model,provider", [("gpt-4.1-mini", "openai"), ("gpt-4o-mini", "openai"), ("gpt-6-luna", "local")])
def test_existing_payloads_keep_legacy_parameters(mocked_client, monkeypatch, model, provider):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "invalid-does-not-apply")
    def response(request):
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 300
        assert payload["temperature"] == 0.1
        assert payload["messages"][0]["role"] == "system"
        assert "reasoning_effort" not in payload and "max_completion_tokens" not in payload
        return httpx.Response(200, json={"choices": [{"message": {"content": "Answer"}}]})
    client = mocked_client(model, response, provider)
    assert client.chat([{"role": "system", "content": "Be factual"}], temperature=0.1, max_tokens=300) == "Answer"


@pytest.mark.parametrize("choice,expected", [
    ({"finish_reason": "length", "message": {"content": '{"partial":'}}, "output limit"),
    ({"finish_reason": "stop", "message": {"content": None}}, "no usable text"),
    ({"finish_reason": "stop", "message": {"content": " "}}, "no usable text"),
    (None, "no completion choices"),
])
def test_luna_rejects_truncated_or_empty_outputs_without_retry(mocked_client, monkeypatch, choice, expected):
    monkeypatch.delenv("OPENAI_REASONING_EFFORT", raising=False)
    calls = []
    def response(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [choice] if choice else [], "usage": {"completion_tokens": 300}})
    client = mocked_client("gpt-6-luna", response)
    with pytest.raises(RuntimeError, match=expected):
        client.chat([{"role": "user", "content": "Return JSON"}], max_tokens=300)
    assert len(calls) == 1
    assert client.last_usage["completion_tokens"] == 300


def test_gemini_fallback_does_not_mutate_shared_model_or_expose_key(mocked_client, monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")
    seen = []
    def response(request):
        assert "key=" not in str(request.url)
        assert request.headers["x-goog-api-key"] == "test-key"
        seen.append(request.url.path)
        if "gemini-old" in request.url.path:
            return httpx.Response(404, json={"error": {"message": "model not available"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Answer"}]}}]})
    client = mocked_client("gemini-old", response, "gemini")
    assert client.ask("Question") == "Answer"
    assert client.model == "gemini-old"
    assert client.last_model == "gemini-2.5-flash"
    assert len(seen) == 2


@pytest.mark.parametrize("task,requested", [("tailor", 2300), ("statement", 2100), ("cover", 1200)])
@pytest.mark.parametrize("effort,extra", [("none", 0), ("low", 2048), ("medium", 4096)])
def test_writing_reasoning_has_bounded_total_headroom(mocked_client, monkeypatch, task, requested, effort, extra):
    from applypilot.ai_routing import task_context
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", effort)
    def response(request):
        payload = json.loads(request.content)
        assert payload["max_completion_tokens"] == requested + extra
        assert payload["reasoning_effort"] == effort
        assert ("temperature" in payload) == (effort == "none")
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "Complete document"}}]})
    client = mocked_client("gpt-6-luna", response)
    with task_context(task):
        assert client.chat([{"role": "user", "content": "Write a factual document"}], max_tokens=requested) == "Complete document"


@pytest.mark.parametrize("task", ["score", "summary", "review", "assistant", "forms", "email", "extract"])
def test_nonwriting_reasoning_keeps_original_limit(mocked_client, monkeypatch, task):
    from applypilot.ai_routing import task_context
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "low")
    client = mocked_client("gpt-6-luna", lambda request: httpx.Response(200))
    with task_context(task):
        assert client.completion_token_limit(600) == 600


def test_explicit_headroom_is_bounded_and_ignored_with_none(mocked_client, monkeypatch):
    from applypilot.ai_routing import task_context
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "low")
    client = mocked_client("gpt-6-luna", lambda request: httpx.Response(200), reasoning_token_allowance=4096)
    with task_context("tailor"):
        assert client.completion_token_limit(2300) == 6396
        assert client.completion_token_limit(127000) == 128000
        monkeypatch.setenv("OPENAI_REASONING_EFFORT", "none")
        assert client.completion_token_limit(2300) == 2300


@pytest.mark.parametrize("allowance", [-1, 16385, True, 1.5])
def test_invalid_headroom_is_rejected(mocked_client, allowance):
    with pytest.raises(ValueError, match="allowance"):
        mocked_client("gpt-6-luna", lambda request: pytest.fail("No request expected"), reasoning_token_allowance=allowance)


def test_parse_json_response_accepts_fence_and_preamble() -> None:
    raw = 'Here is the result:\n```json\n{"full_description":"A role", "application_url":null}\n```'

    assert llm.parse_json_response(raw) == {"full_description": "A role", "application_url": None}


def test_parse_json_response_uses_content_after_thinking_block() -> None:
    raw = '<think>internal reasoning</think>\n{"statement":"Evidence-led statement"}'

    assert llm.parse_json_response(raw) == {"statement": "Evidence-led statement"}


def test_structured_json_repairs_invalid_model_response(monkeypatch) -> None:
    responses = ["not json", '{"statement":"Repaired statement"}']
    calls: list[list[dict]] = []

    def fake_chat_json(messages, **kwargs):
        calls.append(messages)
        return responses.pop(0)

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)

    result = llm.structured_json(
        [{"role": "user", "content": "Return a statement."}],
        required_keys=("statement",),
        task_name="test statement",
    )

    assert result == {"statement": "Repaired statement"}
    assert len(calls) == 2
    assert "previous response did not satisfy" in calls[1][-1]["content"]
