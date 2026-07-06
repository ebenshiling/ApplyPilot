import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from applypilot.database import get_connection


def _start_server(host: str) -> tuple[threading.Thread, int, Any]:
    """Start dashboard server on an ephemeral port.

    Returns (thread, port, httpd).
    """
    import applypilot.dashboard_server as ds

    # Mirror serve_dashboard() but allow port=0 and return the handle.
    ds._PIPELINE._hydrate_from_history()
    ds._ensure_dashboard(force=True)
    httpd = ds.ThreadingHTTPServer((host, 0), ds._Handler)
    port = int(httpd.server_address[1])
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    t.start()
    return t, port, httpd


def _wait_ok(url: str, *, timeout_s: float = 5.0) -> None:
    import urllib.request

    deadline = time.time() + timeout_s
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return
        except Exception as e:
            last_err = e
            time.sleep(0.05)
    raise RuntimeError(f"server not ready: {last_err}")


def _http_json(method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            raw = r.read()
            return r.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            data = {"detail": raw.decode("utf-8", errors="replace")}
        return int(e.code), data


def _http_bytes(method: str, url: str) -> tuple[int, bytes, dict[str, str]]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return int(r.status), r.read(), dict(r.headers.items())
    except urllib.error.HTTPError as e:
        return int(e.code), e.read(), dict(e.headers.items())


def _seed_job_for_jobs_list(db_path: Path) -> None:
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT OR REPLACE INTO jobs (
            url, title, company, search_query, salary, description, location, site,
            full_description, application_url, detail_error,
            sponsorship_explicit, sponsor_licensed, sponsor_match_name,
            fit_score, score_reasoning, scored_at,
            tailored_resume_path, supporting_statement_path, cover_letter_path,
            tailor_status, cover_letter_status,
            applied_at, apply_status, apply_error,
            strategy, discovered_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?
        )
        """,
        (
            "https://example.com/job/endpoint-1",
            "Platform Analyst",
            "Example Co",
            "Data Analyst",
            "$95k",
            "Short desc",
            "Remote",
            "linkedin",
            "Full description line 1\nline 2",
            "https://example.com/apply/endpoint-1",
            None,
            "yes",
            "yes",
            "Example Sponsor",
            8,
            "sql, python\nstrong match",
            "2026-01-01T00:00:00Z",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "jobspy",
            "2026-01-01T00:00:00Z",
        ),
    )
    conn.commit()


def test_setup_endpoints_write_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Force server to use our temp app dir.
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))

    # applypilot.config is imported very early in this test suite; reload so
    # APP_DIR/DB_PATH reflect the env override.
    import importlib
    import applypilot.config as cfg

    importlib.reload(cfg)

    # Reload modules that capture APP_DIR at import time.
    import applypilot.view as view
    import applypilot.dashboard_server as ds

    importlib.reload(view)
    importlib.reload(ds)

    # Ensure DB exists so dashboard generation doesn't crash.
    from applypilot.database import close_connection, init_db

    db_path = cfg.DB_PATH
    init_db(db_path)

    host = "127.0.0.1"
    t, port, httpd = _start_server(host)
    _wait_ok(f"http://{host}:{port}/health")

    st, data = _http_json("GET", f"http://{host}:{port}/api/setup/status")
    assert st == 200
    assert data.get("ok") is True
    s = data.get("status") or {}
    assert s.get("app_dir") == str(tmp_path)

    # Read endpoint works
    st, data = _http_json("GET", f"http://{host}:{port}/api/setup/read")
    assert st == 200
    assert data.get("ok") is True
    assert isinstance(data.get("profile"), dict)
    assert isinstance(data.get("searches_text"), str)
    assert isinstance(data.get("resume_text"), str)

    # Write profile
    st, data = _http_json(
        "POST",
        f"http://{host}:{port}/api/setup/profile",
        {
            "profile": {
                "personal": {
                    "full_name": "A B",
                    "email": "a@example.com",
                    "city": "X",
                    "country": "Y",
                    "password": "should-not-persist",
                }
            }
        },
    )
    assert st == 200
    assert (tmp_path / "profile.json").exists()
    profile_saved = json.loads((tmp_path / "profile.json").read_text(encoding="utf-8"))
    assert "password" not in (profile_saved.get("personal") or {})

    st, data = _http_json(
        "POST",
        f"http://{host}:{port}/api/setup/profile",
        {
            "profile": {
                "resume_sections": {
                    "application_support_projects": [
                        {
                            "header": "Poultry ERP Management System",
                            "subtitle": "Technologies: Django REST Framework, React, SQL, REST APIs",
                            "bullets": [
                                "Developed and supported an integrated ERP platform for poultry farm operations covering inventory, finance, sales, purchases, analytics, and operational reporting",
                                "Investigated and resolved business logic, API, and data consistency issues across interconnected modules",
                                "Worked with SQL-backed transactional data, automation workflows, and production-style operational processes",
                                "Implemented reporting, monitoring, and analytics features to support operational decision making",
                            ],
                        }
                    ]
                }
            }
        },
    )
    assert st == 200
    profile_saved = json.loads((tmp_path / "profile.json").read_text(encoding="utf-8"))
    app_projects = ((profile_saved.get("resume_sections") or {}).get("application_support_projects") or [])
    assert len(app_projects) == 1
    assert app_projects[0].get("header") == "Poultry ERP Management System"

    # Write resume text
    st, data = _http_json(
        "POST",
        f"http://{host}:{port}/api/setup/resume-text",
        {"text": "Hello resume\nLine 2\n"},
    )
    assert st == 200
    assert (tmp_path / "resume.txt").exists()

    st, data = _http_json(
        "POST",
        f"http://{host}:{port}/api/setup/resume-variant",
        {"key": "data_analyst", "text": "Variant body\n"},
    )
    assert st == 200

    st, data = _http_json(
        "POST",
        f"http://{host}:{port}/api/setup/resume-variant-rename",
        {"old_key": "data_analyst", "new_key": "data_reporting"},
    )
    assert st == 200
    assert data.get("ok") is True
    assert (tmp_path / "resume_variants" / "data_reporting.txt").exists()
    assert not (tmp_path / "resume_variants" / "data_analyst.txt").exists()

    st, data = _http_json(
        "POST",
        f"http://{host}:{port}/api/setup/resume-variant-duplicate",
        {"source_key": "data_reporting", "new_key": "data_reporting_copy"},
    )
    assert st == 200
    assert data.get("ok") is True
    assert (tmp_path / "resume_variants" / "data_reporting_copy.txt").exists()

    # Write searches
    st, data = _http_json(
        "POST",
        f"http://{host}:{port}/api/setup/searches",
        {"text": 'defaults:\n  location: "Remote"\nqueries:\n  - query: "X"\n    tier: 1\n'},
    )
    assert st == 200
    assert (tmp_path / "searches.yaml").exists()

    # Status reflects setup
    st, data = _http_json("GET", f"http://{host}:{port}/api/setup/status")
    assert st == 200
    s = data.get("status") or {}
    assert s.get("has_profile") is True
    assert s.get("has_resume_txt") is True
    assert s.get("has_searches") is True

    # Invalid profile patch should fail schema validation.
    st, data = _http_json(
        "POST",
        f"http://{host}:{port}/api/setup/profile",
        {"profile": {"not_a_real_field": {"x": 1}}},
    )
    assert st == 400
    assert data.get("error") == "write_failed"

    # Invalid searches.yaml should fail strict validation.
    st, data = _http_json(
        "POST",
        f"http://{host}:{port}/api/setup/searches",
        {"text": 'queries:\n  - query: "X"\n    tier: 1\nboards:\n  - monster\n'},
    )
    assert st == 400
    assert data.get("error") == "write_failed"

    # Cleanup thread-local sqlite handle
    close_connection(db_path)
    try:
        httpd.shutdown()
        httpd.server_close()
    except Exception:
        pass


def test_job_match_endpoint_scores_and_generates_tailored_cv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))

    import importlib
    import applypilot.config as cfg

    importlib.reload(cfg)

    import applypilot.view as view
    import applypilot.dashboard_server as ds

    importlib.reload(view)
    importlib.reload(ds)

    from applypilot.database import close_connection, init_db

    db_path = cfg.DB_PATH
    init_db(db_path)

    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "personal": {"full_name": "Ada Example", "email": "ada@example.com"},
                "account": {"username": "ada"},
            }
        ),
        encoding="utf-8",
    )

    def _fake_score_job(resume_text: str, job: dict) -> dict:
        assert "SQL" in resume_text
        assert job["title"] == "Data Analyst"
        return {"score": 8, "keywords": "sql, power bi", "reasoning": "Strong fit.", "confidence": 0.91}

    def _fake_tailor_resume(resume_text: str, job: dict, profile: dict) -> tuple[str, dict]:
        assert profile.get("personal", {}).get("full_name") == "Ada Example"
        return (
            "ADA EXAMPLE\nDATA ANALYST\nSUMMARY\n- Tailored summary\n",
            {"status": "approved", "attempts": 1, "validator": {"errors": []}},
        )

    def _fake_convert_to_pdf(text_path, output_path=None, html_only=False, pdf_metadata=None):
        out = output_path or Path(text_path).with_suffix(".pdf")
        Path(out).write_bytes(b"%PDF-1.4\n%stub\n")
        return Path(out)

    monkeypatch.setattr("applypilot.dashboard_server._regen_dashboard_async", lambda *args, **kwargs: None)
    monkeypatch.setattr("applypilot.config.check_tier", lambda *args, **kwargs: None)
    monkeypatch.setattr("applypilot.scoring.scorer.score_job", _fake_score_job)
    monkeypatch.setattr("applypilot.scoring.tailor.tailor_resume", _fake_tailor_resume)
    monkeypatch.setattr("applypilot.scoring.pdf.convert_to_pdf", _fake_convert_to_pdf)
    monkeypatch.setattr(
        "applypilot.scoring.pdf.build_pdf_metadata",
        lambda *args, **kwargs: {"/Title": "stub"},
    )

    host = "127.0.0.1"
    t, port, httpd = _start_server(host)
    _wait_ok(f"http://{host}:{port}/health")

    st, data = _http_json(
        "POST",
        f"http://{host}:{port}/api/job-match/generate",
        {
            "resume_text": "SQL\nPower BI\nReporting\n",
            "job_text": "Need SQL, Power BI, and stakeholder reporting.",
            "title": "Data Analyst",
            "org": "Example Org",
            "location": "Remote",
            "min_score": 7,
        },
    )

    assert st == 200
    assert data.get("ok") is True
    assert data.get("run_id")
    assert data.get("score") == 8
    assert data.get("tailored") is True
    assert data.get("tailor_status") == "approved"
    assert "Tailored summary" in str(data.get("tailored_text") or "")

    tailored_path = Path(str(data.get("tailored_path") or ""))
    report_path = Path(str(data.get("report_path") or ""))
    pdf_path = Path(str(data.get("pdf_path") or ""))
    assert tailored_path.exists()
    assert report_path.exists()
    assert pdf_path.exists()
    assert tailored_path.parent == (tmp_path / "tailored_resumes")

    st, hist = _http_json("GET", f"http://{host}:{port}/api/job-match/history")
    assert st == 200
    assert hist.get("ok") is True
    runs = hist.get("runs") or []
    assert runs
    assert runs[0].get("title") == "Data Analyst"
    assert runs[0].get("score") == 8
    assert runs[0].get("tailored") is True
    assert runs[0].get("tailored_path")
    assert "Need SQL" in str(runs[0].get("job_text") or "")
    assert "SQL" in str(runs[0].get("resume_text") or "")
    assert "Tailored summary" in str(runs[0].get("tailored_text") or "")

    run_id = str(runs[0].get("run_id") or "")
    st, body, headers = _http_bytes("GET", f"http://{host}:{port}/api/job-match/history-export")
    assert st == 200
    exported = json.loads(body.decode("utf-8"))
    assert (exported.get("runs") or [])[0].get("run_id") == run_id

    st, cleared = _http_json(
        "POST",
        f"http://{host}:{port}/api/job-match/history-clear",
        {"delete_files": False},
    )
    assert st == 200
    assert cleared.get("ok") is True

    st, imported = _http_json(
        "POST",
        f"http://{host}:{port}/api/job-match/history-import",
        exported,
    )
    assert st == 200
    assert imported.get("ok") is True
    assert imported.get("imported") == 1

    st, hist = _http_json("GET", f"http://{host}:{port}/api/job-match/history")
    assert st == 200
    runs = hist.get("runs") or []
    assert runs
    run_id = str(runs[0].get("run_id") or "")

    st, updated = _http_json(
        "POST",
        f"http://{host}:{port}/api/job-match/history-update",
        {"run_id": run_id, "favorite": True, "notes": "Strong SQL match"},
    )
    assert st == 200
    assert updated.get("ok") is True
    rec = updated.get("record") or {}
    assert rec.get("favorite") is True
    assert rec.get("notes") == "Strong SQL match"

    st, body, headers = _http_bytes("GET", f"http://{host}:{port}/api/job-match/download?run_id={run_id}&kind=txt")
    assert st == 200
    assert b"Tailored summary" in body
    assert "attachment;" in str(headers.get("Content-Disposition") or "")

    st, body, headers = _http_bytes("GET", f"http://{host}:{port}/api/job-match/download?run_id={run_id}&kind=pdf")
    assert st == 200
    assert body.startswith(b"%PDF-")

    st, promoted = _http_json(
        "POST",
        f"http://{host}:{port}/api/job-match/promote-resume",
        {"run_id": run_id},
    )
    assert st == 200
    assert promoted.get("ok") is True
    assert (tmp_path / "resume.txt").exists()
    assert "Tailored summary" in (tmp_path / "resume.txt").read_text(encoding="utf-8")

    st, deleted = _http_json(
        "POST",
        f"http://{host}:{port}/api/job-match/history-delete",
        {"run_id": run_id, "delete_files": True},
    )
    assert st == 200
    assert deleted.get("ok") is True
    assert not tailored_path.exists()
    assert not pdf_path.exists()

    st, hist = _http_json("GET", f"http://{host}:{port}/api/job-match/history")
    assert st == 200
    assert hist.get("runs") == []

    close_connection(db_path)
    try:
        httpd.shutdown()
        httpd.server_close()
    except Exception:
        pass


def test_job_match_endpoint_skips_tailoring_below_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))

    import importlib
    import applypilot.config as cfg

    importlib.reload(cfg)

    import applypilot.view as view
    import applypilot.dashboard_server as ds

    importlib.reload(view)
    importlib.reload(ds)

    from applypilot.database import close_connection, init_db

    db_path = cfg.DB_PATH
    init_db(db_path)

    (tmp_path / "profile.json").write_text(json.dumps({"personal": {"full_name": "Ada Example"}}), encoding="utf-8")

    monkeypatch.setattr("applypilot.dashboard_server._regen_dashboard_async", lambda *args, **kwargs: None)
    monkeypatch.setattr("applypilot.config.check_tier", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "applypilot.scoring.scorer.score_job",
        lambda *args, **kwargs: {"score": 6, "keywords": "sql", "reasoning": "Moderate fit.", "confidence": 0.7},
    )

    called = {"tailor": 0}

    def _unexpected_tailor(*args, **kwargs):
        called["tailor"] += 1
        return ("", {"status": "approved"})

    monkeypatch.setattr("applypilot.scoring.tailor.tailor_resume", _unexpected_tailor)

    host = "127.0.0.1"
    t, port, httpd = _start_server(host)
    _wait_ok(f"http://{host}:{port}/health")

    st, data = _http_json(
        "POST",
        f"http://{host}:{port}/api/job-match/generate",
        {
            "resume_text": "SQL\nPower BI\n",
            "job_text": "Need strong SQL and Power BI reporting.",
            "title": "Reporting Analyst",
            "org": "Example Org",
            "min_score": 7,
        },
    )

    assert st == 200
    assert data.get("ok") is True
    assert data.get("score") == 6
    assert data.get("score_below_threshold") is True
    assert data.get("tailored") is False
    assert called["tailor"] == 0

    st, hist = _http_json("GET", f"http://{host}:{port}/api/job-match/history")
    assert st == 200
    assert hist.get("ok") is True
    runs = hist.get("runs") or []
    assert runs
    assert runs[0].get("title") == "Reporting Analyst"
    assert runs[0].get("score") == 6
    assert runs[0].get("tailored") is False

    st, cleared = _http_json(
        "POST",
        f"http://{host}:{port}/api/job-match/history-clear",
        {"delete_files": True},
    )
    assert st == 200
    assert cleared.get("ok") is True
    assert cleared.get("cleared") == 1

    st, hist = _http_json("GET", f"http://{host}:{port}/api/job-match/history")
    assert st == 200
    assert hist.get("runs") == []

    close_connection(db_path)
    try:
        httpd.shutdown()
        httpd.server_close()
    except Exception:
        pass


def test_job_match_history_supports_bulk_style_update_and_delete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))

    import importlib
    import applypilot.config as cfg

    importlib.reload(cfg)

    import applypilot.view as view
    import applypilot.dashboard_server as ds

    importlib.reload(view)
    importlib.reload(ds)

    from applypilot.database import close_connection, init_db

    db_path = cfg.DB_PATH
    init_db(db_path)

    (tmp_path / "profile.json").write_text(json.dumps({"personal": {"full_name": "Ada Example"}}), encoding="utf-8")

    monkeypatch.setattr("applypilot.dashboard_server._regen_dashboard_async", lambda *args, **kwargs: None)
    monkeypatch.setattr("applypilot.config.check_tier", lambda *args, **kwargs: None)

    scores = iter(
        [
            {"score": 8, "keywords": "sql, python", "reasoning": "Strong match.", "confidence": 0.9},
            {"score": 9, "keywords": "sql, leadership", "reasoning": "Very strong match.", "confidence": 0.95},
        ]
    )

    def _fake_score_job(*args, **kwargs):
        return next(scores)

    def _fake_tailor_resume(resume_text: str, job: dict, profile: dict) -> tuple[str, dict]:
        return (
            "ADA EXAMPLE\nSUMMARY\n- Tailored for " + str(job.get("title") or "") + "\n",
            {"status": "approved", "attempts": 1, "validator": {"errors": []}},
        )

    def _fake_convert_to_pdf(text_path, output_path=None, html_only=False, pdf_metadata=None):
        out = output_path or Path(text_path).with_suffix(".pdf")
        Path(out).write_bytes(b"%PDF-1.4\n%stub\n")
        return Path(out)

    monkeypatch.setattr("applypilot.scoring.scorer.score_job", _fake_score_job)
    monkeypatch.setattr("applypilot.scoring.tailor.tailor_resume", _fake_tailor_resume)
    monkeypatch.setattr("applypilot.scoring.pdf.convert_to_pdf", _fake_convert_to_pdf)
    monkeypatch.setattr(
        "applypilot.scoring.pdf.build_pdf_metadata",
        lambda *args, **kwargs: {"/Title": "stub"},
    )

    host = "127.0.0.1"
    t, port, httpd = _start_server(host)
    _wait_ok(f"http://{host}:{port}/health")

    try:
        for title in ["Data Analyst", "Analytics Lead"]:
            st, data = _http_json(
                "POST",
                f"http://{host}:{port}/api/job-match/generate",
                {
                    "resume_text": "SQL\nPython\nLeadership\n",
                    "job_text": f"Need strong SQL experience for {title}.",
                    "title": title,
                    "org": "Example Org",
                    "location": "Remote",
                    "min_score": 7,
                },
            )
            assert st == 200
            assert data.get("ok") is True
            assert data.get("tailored") is True

        st, hist = _http_json("GET", f"http://{host}:{port}/api/job-match/history")
        assert st == 200
        runs = hist.get("runs") or []
        assert len(runs) == 2

        run_ids = [str(r.get("run_id") or "") for r in runs]
        assert all(run_ids)

        for idx, run_id in enumerate(run_ids):
            st, updated = _http_json(
                "POST",
                f"http://{host}:{port}/api/job-match/history-update",
                {
                    "run_id": run_id,
                    "favorite": True,
                    "notes": f"bulk note {idx + 1}",
                },
            )
            assert st == 200
            assert updated.get("ok") is True
            rec = updated.get("record") or {}
            assert rec.get("favorite") is True
            assert rec.get("notes") == f"bulk note {idx + 1}"

        st, hist = _http_json("GET", f"http://{host}:{port}/api/job-match/history")
        assert st == 200
        refreshed = hist.get("runs") or []
        assert len(refreshed) == 2
        assert all((r.get("favorite") is True) for r in refreshed)
        assert sorted(str(r.get("notes") or "") for r in refreshed) == ["bulk note 1", "bulk note 2"]

        deleted_paths: list[Path] = []
        for run_id in run_ids:
            run = next(r for r in refreshed if str(r.get("run_id") or "") == run_id)
            deleted_paths.append(Path(str(run.get("tailored_path") or "")))
            st, deleted = _http_json(
                "POST",
                f"http://{host}:{port}/api/job-match/history-delete",
                {"run_id": run_id, "delete_files": True},
            )
            assert st == 200
            assert deleted.get("ok") is True

        st, hist = _http_json("GET", f"http://{host}:{port}/api/job-match/history")
        assert st == 200
        assert hist.get("runs") == []
        assert all(not p.exists() for p in deleted_paths)
    finally:
        close_connection(db_path)
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass


def test_jobs_list_endpoint_accepts_parse_qs_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))

    import importlib
    import applypilot.config as cfg

    importlib.reload(cfg)

    import applypilot.view as view
    import applypilot.dashboard_server as ds

    importlib.reload(view)
    importlib.reload(ds)

    from applypilot.database import close_connection, init_db

    db_path = cfg.DB_PATH
    init_db(db_path)
    _seed_job_for_jobs_list(db_path)

    host = "127.0.0.1"
    t, port, httpd = _start_server(host)
    _wait_ok(f"http://{host}:{port}/health")

    try:
        st, data = _http_json(
            "GET",
            f"http://{host}:{port}/api/jobs/list?min_score=7&hide_moderate=1&status=active&role=&site=&search=&sponsorship=&score_limit=8:25",
        )
        assert st == 200
        assert data.get("ok") is True
        assert data.get("shown") == 1
        groups = data.get("groups") or []
        assert len(groups) == 1
        assert groups[0].get("score") == 8
        jobs = groups[0].get("jobs") or []
        assert len(jobs) == 1
        assert jobs[0].get("title") == "Platform Analyst"
        assert jobs[0].get("discovered_at") == "2026-01-01T00:00:00Z"
        assert isinstance(jobs[0].get("age_days"), int)

        st, data = _http_json(
            "GET",
            f"http://{host}:{port}/api/jobs/list?min_score=7&hide_moderate=1&status=active&role=&site=&search=&sponsorship=&age_days=99999&score_limit=8:25",
        )
        assert st == 200
        assert data.get("ok") is True
        assert data.get("shown") == 0
        assert data.get("groups") == []
    finally:
        close_connection(db_path)
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass


def test_job_select_endpoints_do_not_regen_dashboard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))

    import importlib
    import applypilot.config as cfg

    importlib.reload(cfg)

    import applypilot.view as view
    import applypilot.dashboard_server as ds

    importlib.reload(view)
    importlib.reload(ds)

    from applypilot.database import close_connection, init_db

    db_path = cfg.DB_PATH
    init_db(db_path)
    _seed_job_for_jobs_list(db_path)

    regen_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ds,
        "_regen_dashboard_async",
        lambda *args, **kwargs: regen_calls.append(("called", "called")),
    )

    host = "127.0.0.1"
    t, port, httpd = _start_server(host)
    _wait_ok(f"http://{host}:{port}/health")

    try:
        st, data = _http_json(
            "POST",
            f"http://{host}:{port}/api/jobs/select",
            {"id": 1, "selected": True, "exclusive": False},
        )
        assert st == 200
        assert data.get("ok") is True
        assert data.get("selected") is True
        assert regen_calls == []

        conn = get_connection(db_path)
        row = conn.execute("SELECT apply_status FROM jobs WHERE rowid = 1").fetchone()
        assert row is not None
        assert row[0] == "selected"

        st, data = _http_json(
            "POST",
            f"http://{host}:{port}/api/jobs/select-clear",
            {},
        )
        assert st == 200
        assert data.get("ok") is True
        assert data.get("cleared") == 1
        assert regen_calls == []

        row = conn.execute("SELECT apply_status FROM jobs WHERE rowid = 1").fetchone()
        assert row is not None
        assert row[0] is None
    finally:
        close_connection(db_path)
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass
