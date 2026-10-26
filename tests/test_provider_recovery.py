import builtins
from types import SimpleNamespace

import httpx
import pytest

from applypilot import llm, pipeline, role_routing
from applypilot.discovery import jobspy
from applypilot.enrichment import detail
from applypilot.scoring import scorer
import test_pipeline_reliability as reliability


def test_jobspy_missing_transitive_module_is_not_called_missing_distribution(monkeypatch):
    original = builtins.__import__
    def fail(name, *args, **kwargs):
        if name == "jobspy":
            raise ModuleNotFoundError("No module named curl_cffi", name="curl_cffi")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", fail)
    with pytest.raises(RuntimeError, match="installed.*curl_cffi"):
        jobspy._load_scraper()


def test_jobspy_preflight_fails_once_before_any_board_search(monkeypatch):
    calls = []
    def unavailable():
        calls.append(1)
        raise RuntimeError("required module curl_cffi")
    monkeypatch.setattr(jobspy, "_load_scraper", unavailable)
    monkeypatch.setattr(jobspy, "_full_crawl", lambda **kw: pytest.fail("must not crawl"))
    with pytest.raises(RuntimeError, match="curl_cffi"):
        jobspy.run_discovery({"queries": [{"query": "Data Analyst"}], "boards": ["indeed", "linkedin"]})
    assert calls == [1]


def test_payment_error_is_safe_and_repeated_client_call_does_not_hit_provider(monkeypatch):
    client = llm.LLMClient("gemini", "https://generativelanguage.googleapis.com", "gemini-2.5-flash", "secret-key")
    calls = []
    def blocked(*args, **kwargs):
        calls.append(1)
        request = httpx.Request("POST", "https://example.com/generate?key=secret-key")
        response = httpx.Response(402, json={"error": {"message": "secret-key billing error"}}, request=request)
        response.raise_for_status()
    monkeypatch.setattr(client, "_chat", blocked)
    monkeypatch.setattr(__import__("applypilot.pipeline_store", fromlist=["record_usage"]), "record_usage", lambda *a: None)
    for _ in range(2):
        with pytest.raises(llm.ProviderAccessError) as caught:
            client.chat([{"role": "user", "content": "Fixture"}])
        assert "secret-key" not in str(caught.value)
        assert caught.value.category == "payment"
    assert calls == [1]
    client._client.close()


def test_scoring_payment_failure_defers_batch_and_does_not_count_success(tmp_path, monkeypatch):
    conn = reliability.seed(tmp_path, fit_score=None)
    conn.execute("INSERT INTO jobs(url,title,full_description) VALUES(?,?,?)", ("https://example.com/second", "Data Analyst", "SQL reporting"))
    conn.commit()
    resume = tmp_path/"resume.txt"
    resume.write_text("Verified resume")
    monkeypatch.setattr(scorer, "get_connection", lambda: conn)
    monkeypatch.setattr(scorer, "RESUME_PATH", resume)
    monkeypatch.setattr(scorer, "route_resume_for_job", lambda j: role_routing.RoutedResume("default", str(resume), "Verified resume", 0))
    calls = []
    monkeypatch.setattr(scorer, "score_job", lambda *a: calls.append(1) or {"score": 0, "keywords": "", "reasoning": "Gemini payment blocked; check billing", "confidence": 0, "provider_blocked": True})
    result = scorer.run_scoring()
    assert calls == [1]
    assert result["scored"] == 0 and result["attempted"] == 1 and result["deferred"] == 1
    assert [r[0] for r in conn.execute("SELECT fit_score FROM jobs")] == [None, None]
    assert {r[0] for r in conn.execute("SELECT score_status FROM jobs")} == {"provider_blocked"}


def test_llm_page_text_fallback_is_preserved_but_not_reported_as_ai_success(monkeypatch):
    monkeypatch.setattr(detail, "extract_main_content", lambda page: "<p>"+"Actual job description. "*15+"</p>")
    def denied(*args, **kwargs):
        raise llm.ProviderAccessError("gemini", "payment", 402, "Gemini payment blocked")
    monkeypatch.setattr(detail, "structured_json", denied)
    out = detail.extract_with_llm(SimpleNamespace(title=lambda: "Job"), "https://example.com/job")
    assert "Actual job description" in out["full_description"]
    assert out["extraction_fallback"] == "page_text" and out["llm_error"] == "Gemini payment blocked"
    assert pipeline._stage_result({"ok": 0, "partial": 1, "error": 0})["status"] == "partial"
