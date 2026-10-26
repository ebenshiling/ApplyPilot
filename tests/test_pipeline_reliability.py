import json
import threading
import time
import pytest

from applypilot import database as db, pipeline, role_routing
from applypilot.eligibility import candidates, packet


@pytest.fixture(autouse=True)
def isolated_metrics(monkeypatch):
    monkeypatch.setattr(pipeline, "_append_metric", lambda *a: None)


def seed(tmp_path, **changes):
    conn = db.init_db(tmp_path / "applypilot.db")
    values = {"url": "https://example.com/job", "title": "Data Analyst", "full_description": "SQL reporting",
              "fit_score": 8, "discovered_at": "2026-10-04"} | changes
    conn.execute(f"INSERT INTO jobs ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
    conn.commit()
    return conn


def test_archived_jobs_do_not_keep_queue_alive(tmp_path, monkeypatch):
    conn = seed(tmp_path)
    conn.execute("INSERT INTO cc_job_meta(job_url,visibility) VALUES(?,?)", ("https://example.com/job", "archived"))
    monkeypatch.setattr(pipeline, "get_connection", lambda: conn)
    assert pipeline._count_pending("tailor") == 0
    assert not candidates(conn, "tailor")


def test_pdf_existing_artifact_drains_queue(tmp_path, monkeypatch):
    text = tmp_path / "cv.txt"
    text.write_text("Resume evidence")
    text.with_suffix(".pdf").write_bytes(b"pdf")
    conn = seed(tmp_path, tailored_resume_path=str(text))
    monkeypatch.setattr(pipeline, "get_connection", lambda: conn)
    assert pipeline._count_pending("pdf") == 0
    text.with_suffix(".pdf").unlink()
    assert pipeline._count_pending("pdf") == 1


def test_statements_obey_selected_only_and_visibility(tmp_path):
    conn = seed(tmp_path, apply_status="selected", fit_score=4, tailored_resume_path="cv.txt", full_description="NHS supporting information")
    assert len(candidates(conn, "statement", picked=True)) == 1
    conn.execute("UPDATE jobs SET apply_status=NULL,fit_score=9")
    assert not candidates(conn, "statement", picked=True)
    conn.execute("INSERT INTO cc_job_meta(job_url,visibility) VALUES(?,?)", ("https://example.com/job", "archived"))
    assert not candidates(conn, "statement", picked=False)


def test_commercial_job_does_not_need_statement(tmp_path):
    conn = seed(tmp_path, tailored_resume_path="cv.txt")
    assert not candidates(conn, "statement", picked=False)
    assert packet(conn, dict(conn.execute("SELECT * FROM jobs").fetchone()))["required"]["statement"] is False


def test_partial_stream_waits_for_actual_producer(monkeypatch):
    work = {"pdf": 0}
    rendered = []
    def tailor(**kw):
        time.sleep(.03)
        work["pdf"] = 1
        return {"approved": 1}
    def pdf(**kw):
        rendered.append(1)
        work["pdf"] = 0
        return {"generated": 1}
    monkeypatch.setattr(pipeline, "_STREAM_POLL_INTERVAL", .005)
    monkeypatch.setattr(pipeline, "_count_pending", lambda stage, *a: work["pdf"] if stage == "pdf" else int(not work.get("tailored")))
    def tailor_once(**kw):
        result = tailor(**kw)
        work["tailored"] = True
        return result
    monkeypatch.setitem(pipeline._STAGE_RUNNERS, "tailor", tailor_once)
    monkeypatch.setitem(pipeline._STAGE_RUNNERS, "pdf", pdf)
    monkeypatch.setattr(pipeline, "_append_metric", lambda *a: None)
    result = pipeline._run_streaming(["tailor", "pdf"], 7)
    assert rendered == [1]
    assert not result["errors"]


def test_stream_no_progress_is_bounded_and_not_success(monkeypatch):
    tracker = pipeline._StageTracker()
    tracker.mark_done("cover")
    calls = []
    monkeypatch.setattr(pipeline, "_count_pending", lambda *a: 1)
    monkeypatch.setattr(pipeline, "_STREAM_POLL_INTERVAL", 0)
    monkeypatch.setitem(pipeline._STAGE_RUNNERS, "pdf", lambda **kw: calls.append(1) or {"status": "error: renderer"})
    pipeline._run_stage_streaming("pdf", tracker, threading.Event())
    assert len(calls) == 5
    assert tracker.get_results()["pdf"]["status"] == "stalled"


def test_sequential_statement_receives_threshold(monkeypatch):
    got = []
    monkeypatch.setattr(pipeline, "get_stats", lambda: {})
    monkeypatch.setattr(pipeline, "_append_metric", lambda *a: None)
    monkeypatch.setitem(pipeline._STAGE_RUNNERS, "statement", lambda **kw: got.append(kw) or {"status": "ok"})
    pipeline._run_sequential(["statement"], 9)
    assert got == [{"min_score": 9}]


def test_routing_cache_is_fresh_and_context_disable_is_effective(tmp_path):
    (tmp_path / "resume.txt").write_text("Default")
    variant = tmp_path / "role.txt"
    variant.write_text("Old")
    cfg = {"resume_routing": {"variants": {"role": "role.txt", "context": "role.txt", "blend": "role.txt"},
           "families": [{"variant": "role", "keywords": ["support"]}],
           "context_families": [{"variant": "context", "keywords": ["nhs"]}],
           "context_routing": {"enabled": False},
           "blended_routes": {"enabled": True, "combos": [{"role_variant": "role", "context_variant": "context", "variant": "blend"}]}}}
    job = {"title": "Support Analyst", "full_description": "NHS"}
    first = role_routing.route_resume_for_job(job, cfg=cfg, app_dir=tmp_path)
    assert first.key == "role"
    variant.write_text("Updated evidence")
    second = role_routing.route_resume_for_job(job, cfg=cfg, app_dir=tmp_path)
    assert second.text == "Updated evidence"
    assert first.fingerprint != second.fingerprint


def test_ambiguous_routing_requires_override(tmp_path):
    (tmp_path / "resume.txt").write_text("Default")
    (tmp_path / "one.txt").write_text("One")
    (tmp_path / "two.txt").write_text("Two")
    cfg = {"resume_routing": {"variants": {"one": "one.txt", "two": "two.txt"}, "families": [
        {"variant": "one", "keywords": ["analyst"]}, {"variant": "two", "keywords": ["analyst"]}]}}
    job = {"title": "Analyst"}
    assert role_routing.route_resume_for_job(job, cfg=cfg, app_dir=tmp_path).ambiguous
    assert role_routing.route_resume_for_job(job | {"resume_route_override": "two"}, cfg=cfg, app_dir=tmp_path).key == "two"


def test_score_failure_remains_retryable_and_good_results_commit(tmp_path, monkeypatch):
    from applypilot.scoring import scorer
    conn = seed(tmp_path, fit_score=None)
    resume = tmp_path / "resume.txt"
    resume.write_text("Fixture evidence")
    monkeypatch.setattr(scorer, "get_connection", lambda: conn)
    monkeypatch.setattr(scorer, "RESUME_PATH", resume)
    monkeypatch.setattr(scorer, "route_resume_for_job", lambda j: role_routing.RoutedResume("default", str(resume), "Fixture evidence", 0))
    monkeypatch.setattr(role_routing, "score_fingerprint", lambda j,*args,**kwargs: "fixture")
    monkeypatch.setattr(scorer, "score_job", lambda *a: {"score": 0, "keywords": "", "reasoning": "temporary error", "confidence": 0})
    scorer.run_scoring()
    row = dict(conn.execute("SELECT * FROM jobs").fetchone())
    assert row["fit_score"] is None and row["score_status"] == "retryable"
    assert not candidates(conn, "score")  # backoff, not a busy retry
    conn.execute("UPDATE jobs SET score_retry_at=NULL")
    monkeypatch.setattr(scorer, "score_job", lambda *a: {"score": 8, "keywords": "SQL", "reasoning": "Evidence", "confidence": .8})
    scorer.run_scoring()
    row = dict(conn.execute("SELECT * FROM jobs").fetchone())
    assert row["fit_score"] == 8 and row["score_status"] == "completed"
    assert json.loads(row["resume_route"])["key"] == "default"
