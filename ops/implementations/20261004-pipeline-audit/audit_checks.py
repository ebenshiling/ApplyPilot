"""Offline reproductions of audit findings, not permanent regression tests.

Run in a fresh Python process. All pipeline execution uses mocks, an in-memory
database, and a temporary workspace. Optional --searches is read-only and emits
configuration counts, not resume/profile content. Assertions describe the audited
behavior and should be replaced by correctness tests when fixes are implemented.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from unittest.mock import Mock, patch

import yaml


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--searches", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    findings: dict = {}

    with tempfile.TemporaryDirectory(
        prefix="applypilot-audit-", dir=os.environ.get("APPLYPILOT_AUDIT_TMP")
    ) as temp, patch.dict(os.environ, {"APPLYPILOT_DIR": temp}):
        root = Path(temp)
        for key in ("APPLYPILOT_SEARCHES_PATH", "APPLYPILOT_SELECTED_ONLY", "APPLYPILOT_APPLY_SELECTED_ONLY"):
            os.environ.pop(key, None)

        # Import only after workspace isolation; never initialize the default DB.
        from applypilot import database as db, pipeline as pipe, role_routing as routing
        from applypilot.dashboard_server import _PipelineRunner
        from applypilot.discovery import smartextract as smart
        from applypilot.scoring import pdf, scorer, supporting_statement as statement

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        columns = ", ".join(f"{name} {kind}" for name, kind in db._ALL_COLUMNS.items())
        conn.execute(f"CREATE TABLE jobs ({columns})")
        conn.execute("CREATE TABLE cc_job_meta (job_url TEXT, visibility TEXT)")
        conn.execute("CREATE TABLE blocked_urls (prefix TEXT PRIMARY KEY)")
        resume = root / "resume.txt"
        resume.write_text("Fixture resume evidence " * 12, encoding="utf-8")

        # Query override is one string, including commas. Advanced keys survive
        # this per-run path (unlike the simple Settings YAML generator).
        searches = root / "searches.yaml"
        searches.write_text(yaml.safe_dump({
            "queries": [{"query": "Data Analyst", "tier": 1}],
            "resume_routing": {"variants": {}, "families": []},
        }), encoding="utf-8")
        runner = object.__new__(_PipelineRunner)
        runner._app_dir = root
        override, _ = runner._write_search_override_if_needed(
            {"search_query": "Application Support Analyst, Data Analyst"},
            run_id="fixture", log_dir=root,
        )
        generated = yaml.safe_load(Path(override).read_text(encoding="utf-8"))
        assert len(generated["queries"]) == 1
        assert "," in generated["queries"][0]["query"]
        assert "resume_routing" in generated
        findings["comma_override"] = generated["queries"]

        # Zero scores from transient inference failures are no longer pending.
        with patch.object(scorer, "chat_json", side_effect=RuntimeError("fixture outage")):
            result = scorer.score_job("Fixture resume", {"title": "Data Analyst"})
        assert result["score"] == 0
        conn.execute("INSERT INTO jobs (url, title, full_description, fit_score) VALUES (?, ?, ?, ?)",
                     ("https://fixture.invalid/zero", "Data Analyst", "Fixture description", result["score"]))
        assert not db.get_jobs_by_stage(conn=conn, stage="pending_score")
        findings["zero_score_excluded_from_retry"] = True
        conn.execute("DELETE FROM jobs")

        # Archived work is counted by streaming but removed by the tailor query.
        conn.execute("INSERT INTO jobs (url, full_description, fit_score) VALUES (?, ?, ?)",
                     ("https://fixture.invalid/archived", "Fixture description", 8))
        conn.execute("INSERT INTO cc_job_meta VALUES (?, ?)", ("https://fixture.invalid/archived", "archived"))
        with patch.object(pipe, "get_connection", return_value=conn):
            pending = pipe._count_pending("tailor")
        eligible = db.get_jobs_by_stage(conn=conn, stage="pending_tailor")
        assert pending == 1 and len(eligible) == 0
        findings["archived_tailor_pending_vs_eligible"] = [pending, len(eligible)]
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM cc_job_meta")

        # Existing PDF does not change the .txt DB path used by streaming counts.
        pdf_dir = root / "tailored"
        pdf_dir.mkdir()
        text = pdf_dir / "fixture.txt"
        text.write_text("Fixture resume " * 15, encoding="utf-8")
        text.with_suffix(".pdf").write_bytes(b"fixture PDF presence only")
        conn.execute("INSERT INTO jobs (url, tailored_resume_path) VALUES (?, ?)",
                     ("https://fixture.invalid/pdf", str(text)))
        with patch.object(pipe, "get_connection", return_value=conn), patch.object(pdf, "TAILORED_DIR", pdf_dir):
            pending = pipe._count_pending("pdf")
            conversions = pdf.batch_convert()
        assert pending == 1 and conversions == 0
        findings["existing_pdf_pending_vs_conversions"] = [pending, conversions]

        # Bound the busy-loop reproduction to exactly three passes.
        tracker = pipe._StageTracker()
        tracker.mark_done("cover", {"status": "skipped"})
        stop = threading.Event()
        calls: list[int] = []

        def no_progress(**kwargs):
            calls.append(1)
            if len(calls) == 3:
                stop.set()
            return {"status": "ok"}

        with patch.object(pipe, "_count_pending", return_value=1), \
             patch.dict(pipe._STAGE_RUNNERS, {"pdf": no_progress}), \
             patch.object(stop, "wait", wraps=stop.wait) as waited:
            pipe._run_stage_streaming("pdf", tracker, stop)
            assert len(calls) == 3 and waited.call_count == 0
        findings["no_progress_passes_without_backoff"] = 3

        # A skipped direct predecessor permits completion while an earlier
        # selected producer is still active (e.g. tailor + pdf).
        tracker = pipe._StageTracker()
        tracker.mark_done("cover", {"status": "skipped"})
        with patch.object(pipe, "_count_pending", return_value=0):
            pipe._run_stage_streaming("pdf", tracker, threading.Event())
        assert tracker.is_done("pdf") and not tracker.is_done("tailor")
        findings["pdf_finished_while_tailor_not_done"] = True

        # A returned stage error is discarded by streaming.
        tracker = pipe._StageTracker()
        tracker.mark_done("enrich")
        with patch.object(pipe, "_count_pending", side_effect=[1, 0]), \
             patch.dict(pipe._STAGE_RUNNERS, {"score": Mock(return_value={"status": "error: fixture"})}):
            pipe._run_stage_streaming("score", tracker, threading.Event())
        assert tracker.get_results()["score"]["status"] == "ok"
        findings["returned_error_reported_ok"] = True

        # Sequential statements receive default 7 instead of requested threshold.
        mocked = Mock(return_value={"status": "ok"})
        with patch.object(pipe, "get_stats", return_value={}), \
             patch.object(pipe, "_append_metric"), \
             patch.dict(pipe._STAGE_RUNNERS, {"statement": mocked}):
            pipe._run_sequential(["statement"], min_score=9)
        assert "min_score" not in mocked.call_args.kwargs
        findings["sequential_statement_threshold_not_forwarded"] = True

        # Statement batch bypasses selection/visibility; AI/validators are mocked.
        conn.execute("DELETE FROM jobs")
        for name, score, status in (("selected_low", 4, "selected"), ("unselected", 8, None), ("archived", 8, None)):
            conn.execute("INSERT INTO jobs (url, title, full_description, fit_score, apply_status, tailored_resume_path) VALUES (?, ?, ?, ?, ?, ?)",
                         (f"https://fixture.invalid/{name}", name, "Fixture description", score, status, str(resume)))
        conn.execute("INSERT INTO cc_job_meta VALUES (?, ?)", ("https://fixture.invalid/archived", "archived"))
        with patch.dict(os.environ, {"APPLYPILOT_SELECTED_ONLY": "1"}), \
             patch.object(statement, "get_connection", return_value=conn), \
             patch.object(statement, "load_profile", return_value={}), \
             patch.object(statement, "RESUME_PATH", resume), \
             patch.object(statement, "STATEMENT_DIR", root / "statements"), \
             patch.object(statement, "generate_supporting_statement", return_value="Fixture statement"), \
             patch.object(statement, "_validate_statement", return_value=[]), \
             patch.object(statement, "evaluate_statement_quality", return_value={}), \
             patch.object(statement.naming, "supporting_statement_filename", side_effect=lambda *a, **kw: kw["job"]["title"] + ".txt"):
            statement.run_supporting_statements()
        processed = [r[0] for r in conn.execute("SELECT title FROM jobs WHERE supporting_statement_path IS NOT NULL ORDER BY title")]
        assert processed == ["archived", "unselected"]
        findings["selected_only_statements_processed"] = processed

        # Missing routing configuration ignores the existence of saved variants.
        with patch.object(routing, "RESUME_PATH", resume), patch.object(routing, "load_search_config", return_value={}):
            routed = routing.route_resume_for_job({"title": "Data Analyst"})
        assert routed.key == "default"
        findings["unconfigured_route"] = routed.key

        # Text cache lacks modification-time/content invalidation.
        variant = root / "variant.txt"
        variant.write_text("old fixture", encoding="utf-8")
        routing.clear_resume_text_cache()
        assert routing._read_text_cached(variant) == "old fixture"
        variant.write_text("new fixture", encoding="utf-8")
        assert routing._read_text_cached(variant) == "old fixture"
        findings["resume_text_cache_stale_after_edit"] = True

        # Blending is evaluated before disabled context/threshold rules.
        route_cfg = {"resume_routing": {
            "variants": {"role": str(variant), "context": str(variant), "blend": str(variant)},
            "families": [{"variant": "role", "keywords": ["support"]}],
            "context_families": [{"variant": "context", "keywords": ["nhs"]}],
            "context_routing": {"enabled": False, "min_hits": 2},
            "blended_routes": {"enabled": True, "combos": [{"role_variant": "role", "context_variant": "context", "variant": "blend"}]},
        }}
        with patch.object(routing, "load_search_config", return_value=route_cfg):
            routed = routing.route_resume_for_job({"title": "Support Analyst", "full_description": "NHS"})
        assert routed.key == "blend"
        findings["blend_with_context_disabled_and_one_context_hit"] = routed.key

        # A custom supplied source can expand, but is not registered by allow-list.
        custom = {"name": "Fixture Careers", "type": "search", "url": "https://fixture.invalid/jobs?q={query_encoded}"}
        targets = smart.build_scrape_targets(sites=[custom], search_cfg={"queries": [{"query": "Data Analyst"}]})
        assert len(targets) == 1 and "Data+Analyst" in targets[0]["url"]
        assert not any(s.get("name") == custom["name"] for s in smart.load_sites())
        findings["custom_source_requires_registry_entry"] = True

        # Smart storage drops even a supplied employer field.
        conn.execute("DELETE FROM jobs")
        with patch.object(db, "is_url_blocked", return_value=False), patch.object(db, "find_existing_job_url", return_value=None):
            smart._store_jobs_filtered(conn, [{"url": "https://fixture.invalid/employer", "title": "Data Analyst", "company": "Fixture Employer", "location": "London"}],
                                       "Fixture Careers", "fixture", [], [])
        assert conn.execute("SELECT company FROM jobs").fetchone()[0] is None
        findings["smart_storage_discards_supplied_company"] = True
        conn.close()
        routing.clear_resume_text_cache()

        if args.searches:
            cfg = yaml.safe_load(args.searches.read_text(encoding="utf-8")) or {}
            queries = cfg.get("queries") or []
            locations = cfg.get("locations") or []
            boards = cfg.get("boards") or cfg.get("sites") or []
            allowed = {str(s).lower() for s in cfg.get("smart_sites") or []}
            targets = smart.build_scrape_targets(search_cfg=cfg)
            if allowed:
                targets = [t for t in targets if t["name"].lower() in allowed]
            targets = smart._dedupe_site_query_location_twins(targets)
            findings["workspace_configuration_snapshot"] = {
                "query_count": len(queries), "location_count": len(locations), "board_count": len(boards),
                "planned_board_searches_before_overrides": len(queries) * len(locations) * len(boards),
                "smart_targets_before_environment_filters_and_cap": len(targets),
                "resume_routing_present": isinstance(cfg.get("resume_routing"), dict),
                "data_analyst_searched_by_default_workday_tiers": any(q.get("query") == "Data Analyst" and q.get("tier", 99) <= cfg.get("workday_max_tier", 2) for q in queries),
                "graduate_titles_excluded": "graduate" in cfg.get("exclude_titles", []),
            }

    report = {"result": "reproduced", "finding_count": len(findings), "findings": findings}
    if args.out:
        if not args.out.parent.is_dir():
            raise ValueError("Output parent must already exist")
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
