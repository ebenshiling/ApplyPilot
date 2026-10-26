import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from applypilot import ai_routing as routing
from applypilot import llm


@pytest.fixture
def routed_env(monkeypatch):
    for task in routing.TASKS:
        for suffix in ("PROVIDER", "MODEL", "REASONING_EFFORT", "THINKING_BUDGET", "REASONING_TOKEN_ALLOWANCE"):
            monkeypatch.delenv(f"LLM_{task.upper()}_{suffix}", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-6-luna")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "none")
    monkeypatch.setenv("OPENAI_API_KEY", "private-openai-test-value")
    monkeypatch.setenv("GEMINI_API_KEY", "private-gemini-test-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "private-anthropic-test-value")
    monkeypatch.delenv("LLM_URL", raising=False)
    monkeypatch.setenv("LLM_SCORE_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_SCORE_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("LLM_SCORE_THINKING_BUDGET", "0")
    monkeypatch.setenv("LLM_TAILOR_PROVIDER", "openai")
    monkeypatch.setenv("LLM_TAILOR_MODEL", "gpt-6-luna")
    monkeypatch.setenv("LLM_REVIEW_PROVIDER", "openai")
    monkeypatch.setenv("LLM_REVIEW_MODEL", "gpt-4.1-mini")
    return monkeypatch


@pytest.fixture
def fake_clients(routed_env):
    calls = []
    replies = {}
    class Client:
        def __init__(self, provider, base_url, model, api_key, **options):
            self.provider, self.model = provider, model
            self.options = options
            self.thinking_budget = options.get("thinking_budget")
            self.cache_identity = f"{provider}:{model}:{options}"
            self.last_usage = {"input_tokens": 10, "output_tokens": 5}
            self._client = SimpleNamespace(close=lambda: None)
        def chat(self, messages, **kwargs):
            task = routing.current_task()
            calls.append({"task": task, "provider": self.provider, "model": self.model, "options": self.options, "request": kwargs})
            return replies.get(task, "Answer")
        def ask(self, text, **kwargs):
            return self.chat([{"role": "user", "content": text}], **kwargs)
    routed_env.setattr(llm, "LLMClient", Client)
    routed_env.setattr(llm, "_clients", {})
    routed_env.setattr(llm, "_instance", None)
    return calls, replies


def test_provider_only_override_uses_its_own_model_default(routed_env):
    routed_env.delenv("LLM_SCORE_MODEL")
    route = routing.resolve_route("score")
    assert route.provider == "gemini" and route.model == "gemini-2.5-flash"
    assert route.thinking_budget == 0
    assert route.model != "gpt-6-luna"


def test_unconfigured_task_inherits_global_route(routed_env):
    route = routing.resolve_route("assistant")
    assert route.provider == "openai" and route.model == "gpt-6-luna"
    assert not route.explicit


def test_explicit_task_does_not_require_default_provider_key(routed_env):
    routed_env.delenv("OPENAI_API_KEY")
    assert routing.resolve_route("score").provider == "gemini"
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        routing.resolve_route("tailor")


def test_missing_credentials_do_not_fall_back(routed_env):
    routed_env.delenv("GEMINI_API_KEY")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        routing.resolve_route("score")
    description = routing.route_info("score")
    assert not description["configured"]
    assert "private-openai-test-value" not in json.dumps(description)


def test_diagnostics_and_repr_exclude_secrets(routed_env):
    descriptions = json.dumps(routing.route_table())
    route = routing.resolve_route("score")
    for secret in ("private-openai-test-value", "private-gemini-test-value", "private-anthropic-test-value"):
        assert secret not in descriptions and secret not in repr(route)


@pytest.mark.parametrize("name,value", [("LLM_SCORE_PROVIDER", "invalid"), ("LLM_SCORE_THINKING_BUDGET", "many"), ("LLM_SCORE_THINKING_BUDGET", "-2"), ("LLM_TAILOR_REASONING_EFFORT", "minimal")])
def test_invalid_configuration_is_detected(routed_env, name, value):
    routed_env.setenv(name, value)
    with pytest.raises(ValueError):
        routing.resolve_route("score" if "SCORE" in name else "tailor")


def test_nested_review_and_exception_restore_task(fake_clients):
    with routing.task_context("tailor"):
        writer = llm.get_client()
        with routing.task_context("review"):
            assert llm.get_client().model == "gpt-4.1-mini"
        assert llm.get_client() is writer
        with pytest.raises(RuntimeError):
            with routing.task_context("score"):
                assert llm.get_client().provider == "gemini"
                raise RuntimeError("Stop")
        assert routing.current_task() == "tailor"
    assert routing.current_task() is None


def test_thread_scopes_and_temporary_models_do_not_leak(fake_clients):
    barrier = threading.Barrier(2)
    def writer():
        with routing.task_context("tailor"):
            base = llm.get_client()
            with llm.temporary_model("gpt-4.1-mini"):
                barrier.wait(timeout=5)
                assert llm.get_client().model == "gpt-4.1-mini"
                with routing.task_context("score"):
                    assert llm.get_client().provider == "gemini"
            assert llm.get_client() is base
            return base.model
    def reviewer():
        with routing.task_context("review"):
            barrier.wait(timeout=5)
            return llm.get_client().model
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(writer), pool.submit(reviewer)
        assert first.result() == "gpt-6-luna"
        assert second.result() == "gpt-4.1-mini"


def test_real_scoring_and_judge_boundaries_select_separate_clients(fake_clients):
    calls, replies = fake_clients
    from applypilot.scoring.scorer import score_job
    from applypilot.scoring.tailor import judge_tailored_resume
    replies["score"] = '{"score":8,"keywords":"SQL","reasoning":"Direct skill match","confidence":0.9}'
    replies["review"] = "VERDICT: PASS\nISSUES: none"
    job = {"title": "Data Analyst", "site": "Example", "full_description": "SQL reporting"}
    assert score_job("SQL reporting experience", job)["score"] == 8
    result = judge_tailored_resume("Evidence", "Evidence", "Data Analyst", {})
    assert result["passed"] and result["model"] == "gpt-4.1-mini"
    assert [(c["task"], c["provider"]) for c in calls] == [("score", "gemini"), ("review", "openai")]


def test_writing_and_extraction_boundaries_are_explicit():
    from applypilot.scoring.tailor import tailor_resume
    from applypilot.scoring.cover_letter import generate_cover_letter
    from applypilot.scoring.supporting_statement import generate_supporting_statement
    from applypilot.discovery.smartextract import ask_llm, judge_api_responses, execute_css_selectors, _run_one_site
    assert tailor_resume.__llm_task__ == "tailor"
    assert generate_cover_letter.__llm_task__ == "cover"
    assert generate_supporting_statement.__llm_task__ == "statement"
    assert all(f.__llm_task__ == "extract" for f in (ask_llm, judge_api_responses, execute_css_selectors, _run_one_site))


def test_statement_cache_changes_with_route(routed_env):
    from applypilot.scoring.supporting_statement import _statement_cache_path
    first = _statement_cache_path("Resume", {"title": "Analyst"})
    routed_env.setenv("LLM_STATEMENT_MODEL", "gpt-4.1-mini")
    second = _statement_cache_path("Resume", {"title": "Analyst"})
    assert first != second


def test_writing_headroom_changes_pool_and_cache_identity(fake_clients, routed_env):
    routed_env.setenv("LLM_TAILOR_REASONING_EFFORT", "low")
    with routing.task_context("tailor"):
        first = llm.get_client()
        assert first.options["reasoning_token_allowance"] == 2048
        routed_env.setenv("LLM_TAILOR_REASONING_TOKEN_ALLOWANCE", "4096")
        second = llm.get_client()
        assert second is not first
        assert second.options["reasoning_token_allowance"] == 4096
        assert second.cache_identity != first.cache_identity


@pytest.mark.parametrize("value", ["-1", "16385", "many"])
def test_route_rejects_invalid_writing_allowance(routed_env, value):
    routed_env.setenv("LLM_TAILOR_REASONING_TOKEN_ALLOWANCE", value)
    with pytest.raises(ValueError, match="REASONING_TOKEN_ALLOWANCE"):
        routing.resolve_route("tailor")


def test_json_uses_task_thinking_budget(fake_clients, routed_env):
    calls, replies = fake_clients
    routed_env.setenv("LLM_SCORE_THINKING_BUDGET", "512")
    replies["score"] = '{"ok":true}'
    with routing.task_context("score"):
        assert llm.chat_json([{"role": "user", "content": "Return JSON"}]) == '{"ok":true}'
    assert calls[-1]["request"]["thinking_budget"] == 512


def test_structured_repair_keeps_explicit_route_and_restores_parent(routed_env):
    calls = []
    def reply(*args, **kwargs):
        calls.append(routing.current_task())
        return "invalid" if len(calls) == 1 else '{"ok":true}'
    routed_env.setattr(llm, "chat_json", reply)
    with routing.task_context("review"):
        assert llm.structured_json([], required_keys=("ok",), routing_task="extract") == {"ok": True}
        assert routing.current_task() == "review"
    assert calls == ["extract", "extract"]


@pytest.mark.parametrize("task,route", list(routing.ASSISTANT_ROUTES.items()))
def test_dashboard_assistant_uses_task_route_and_usage(fake_clients, tmp_path, task, route):
    from applypilot import command_center as center
    from applypilot.database import init_db, close_connection
    calls, _ = fake_clients
    conn = init_db(tmp_path / "applypilot.db")
    conn.execute("INSERT INTO jobs(url,title,company) VALUES('https://example.org/job','Analyst','Example')")
    conn.commit()
    try:
        result = center.assistant(conn, tmp_path, {"id": 1, "task": task})
        assert result["routing_task"] == route
        assert calls[-1]["task"] == route
        assert result["provider"] == calls[-1]["provider"]
        assert conn.execute("SELECT provider FROM cc_ai_usage").fetchone()[0] == result["provider"]
        assert center.assistant(conn, tmp_path, {"id": 1, "task": task})["cached"]
        assert len(calls) == 1
    finally:
        close_connection(tmp_path / "applypilot.db")


def test_form_mapping_uses_its_own_route(fake_clients, routed_env):
    from applypilot.apply import llm_engine
    calls, replies = fake_clients
    routed_env.setenv("LLM_FORMS_PROVIDER", "gemini")
    replies["forms"] = '{"fills":[{"by":"id","key":"email","value_key":"email"}]}'
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE cc_answers(id INTEGER,question TEXT,answer TEXT)")
    routed_env.setattr(llm_engine, "get_connection", lambda: conn)
    try:
        fills = llm_engine._llm_plan_fills({"title": "Analyst"}, {"full_name": "Candidate", "email": "candidate@example.org"}, [{"id": "email", "type": "email"}])
        assert fills[0]["value"] == "candidate@example.org"
        assert calls[-1]["task"] == "forms" and calls[-1]["provider"] == "gemini"
    finally:
        conn.close()
