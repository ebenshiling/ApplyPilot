# ruff: noqa: F811
import csv
import io
import json
import os
import zipfile

import httpx
import pytest

from applypilot import command_center as cc
from applypilot import opportunity_insights as oi
from applypilot.apply import assisted_browser as ab
from test_command_center import workspace, http_workspace  # noqa: F401, F811


def test_direct_database_changes_are_structured_and_transactional(workspace):
    root, conn = workspace
    url = cc.get_job(conn, 1)["url"]
    conn.execute("UPDATE jobs SET fit_score=9,score_reasoning='Strong Python evidence',scored_at=? WHERE rowid=1", (cc.now(),))
    conn.commit()
    cc.update_job(conn, root, 1, "notes", {"notes": "Spoke to employer", "priority": 2, "due_at": "2026-01-01T09:00:00Z"})
    events = oi.activity(conn, {"job_url": url}, complete=True)["events"]
    score = next(e for e in events if e["kind"] == "scoring")
    assert score["payload"]["before"]["fit_score"] == 8
    assert score["payload"]["after"]["fit_score"] == 9
    assert any(e["kind"] == "record_edit" and e["payload"]["after"].get("notes") == "Spoke to employer" for e in events)
    assert any(e["kind"] == "reminder" for e in events)
    count = conn.execute("SELECT COUNT(*) FROM cc_events").fetchone()[0]
    conn.execute("UPDATE jobs SET fit_score=1 WHERE rowid=1")
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM cc_events").fetchone()[0] == count
    assert oi.opportunities(conn)[0]["fit_score"] == 9


def test_complete_export_keeps_deleted_identity_events_and_browser_attempt(workspace):
    root, conn = workspace
    url = cc.get_job(conn, 1)["url"]
    ab._schema(conn)
    conn.execute("INSERT INTO cc_browser_sessions(id,job_url,state,detail,created_at,updated_at) VALUES('old-attempt',?,'stopped','Candidate stopped',?,?)", (url, cc.now(), cc.now()))
    for n in range(260):
        cc.event(conn, url, "progress", f"Step {n}", payload={"step": n})
    conn.execute("UPDATE jobs SET tailor_status='failed',tailor_failure_detail='Missing required experience' WHERE rowid=1")
    conn.execute("DELETE FROM cc_browser_sessions WHERE job_url=?", (url,))
    conn.execute("DELETE FROM jobs WHERE url=?", (url,))
    conn.commit()
    tables = oi.datasets(conn)
    assert tables["opportunities"][0]["record_state"] == "deleted"
    assert tables["opportunities"][0]["tailor_failure_detail"] == "Missing required experience"
    assert len(tables["events"]) > 260
    assert tables["browser_attempts"][0]["id"] == "old-attempt"
    assert len(oi.activity(conn)["events"]) == 30
    body, mime, _ = oi.export(conn)
    assert mime == "application/zip"
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        rows = list(csv.DictReader(io.StringIO(archive.read("events.csv").decode("utf-8-sig"))))
        assert len(rows) == manifest["counts"]["events"] > 260
        assert archive.read("browser_attempts.csv")


def test_manual_event_has_distinct_event_time_and_does_not_change_outcome(workspace):
    root, conn = workspace
    url = cc.get_job(conn, 1)["url"]
    oi.record_manual(conn, {"job_url": url, "category": "interview", "detail": "Recruiter invited me to discuss the role", "occurred_at": "2020-01-02T09:30:00+01:00"})
    row = oi.activity(conn, {"kind": "candidate_activity"})["events"][0]
    assert row["occurred_at"] == "2020-01-02T08:30:00+00:00" and row["created_at"] != row["occurred_at"]
    assert not cc.get_job(conn, 1)["applied_at"]
    assert row["payload"]["affects_status"] is False
    with pytest.raises(LookupError):
        oi.record_manual(conn, {"job_url": "https://other-workspace.invalid/1", "detail": "Wrong workspace"})
    with pytest.raises(ValueError):
        oi.record_manual(conn, {"job_url": url, "detail": "Invalid time", "occurred_at": "2099-01-01T00:00:00Z"})


def test_json_and_csv_redact_credentials_and_csv_formulas_without_breaking_payload(workspace):
    _, conn = workspace
    url = cc.get_job(conn, 1)["url"]
    cc.event(conn, url, "candidate_activity", " =HYPERLINK(1)", payload={"note": "password=secret", "nested": {"reason": "Safe context"}})
    conn.commit()
    body, _, _ = oi.export(conn, {"format": "json"})
    parsed = json.loads(body)
    assert "secret" not in body.decode()
    event = next(e for e in parsed["events"] if e["kind"] == "candidate_activity")
    assert event["payload"]["nested"]["reason"] == "Safe context"
    body, _, _ = oi.export(conn, {"format": "csv", "dataset": "events"})
    row = next(r for r in csv.DictReader(io.StringIO(body.decode("utf-8-sig"))) if r["kind"] == "candidate_activity")
    assert row["detail"].startswith("'")
    assert json.loads(row["payload"])["note"] == "password=[redacted]"


def test_tracking_initialization_is_idempotent_and_does_not_backdate_observations(workspace):
    _, conn = workspace
    before = conn.execute("SELECT COUNT(*) FROM cc_events").fetchone()[0]
    oi.init_schema(conn)
    oi.init_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM cc_events").fetchone()[0] == before
    assert oi.summary(conn)["record_count"] == 1


def test_existing_workspace_migration_preserves_known_dates_without_inventing_history(tmp_path, monkeypatch):
    from applypilot.database import init_db, close_connection
    initialize = oi.init_schema
    monkeypatch.setattr(oi, "init_schema", lambda conn: None)
    path = tmp_path / "legacy.db"
    conn = init_db(path)
    try:
        conn.execute("INSERT INTO jobs(url,title,discovered_at) VALUES('https://example.org/old','Old role','2020-01-01T00:00:00Z')")
        conn.commit()
        monkeypatch.setattr(oi, "init_schema", initialize)
        initialize(conn)
        record = oi.opportunities(conn)[0]
        event = oi.activity(conn)["events"][0]
        assert record["discovered_at"] == "2020-01-01T00:00:00Z"
        assert event["kind"] == "baseline" and event["source"] == "migration"
        assert event["occurred_at"] != record["discovered_at"]
        assert event["payload"]["history_complete"] is False
        initialize(conn)
        assert oi.activity(conn)["total"] == 1
    finally:
        close_connection(path)


def test_insights_http_exports_and_filters(http_workspace):
    base, _, conn = http_workspace
    url = cc.get_job(conn, 1)["url"]
    with httpx.Client(base_url=base, timeout=15) as client:
        response = client.post("/api/center/insights/activity", json={"job_url": url, "category": "follow_up", "detail": "Asked about the expected decision date"})
        assert response.status_code == 200 and response.json()["status_changed"] is False
        out = client.get("/api/center/insights", params={"kind": "candidate_activity"}).json()
        assert out["activity"]["total"] == 1
        response = client.get("/api/center/insights/export", params={"format": "json"})
        assert "attachment" in response.headers["Content-Disposition"]
        assert response.json()["manifest"]["counts"]["opportunities"] == 1
        assert client.get("/assets/opportunity-insights.js").status_code == 200


def test_insights_record_and_calendar_ui_are_inline(http_workspace):
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1")
    base, _, conn = http_workspace
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1100})
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(base + "/workspace#analytics")
            page.locator("#activity-total").wait_for()
            page.wait_for_function("document.querySelector('#activity-total').textContent.includes('matching events')")
            page.locator("#activity-add").click()
            page.locator("#activity-category").select_option("employer_contact")
            page.locator("#activity-detail").fill("Employer said assessment invitations go out next week.")
            page.locator("#activity-save").click()
            page.wait_for_function("document.querySelector('#activity-list').textContent.includes('assessment invitations')")
            page.locator('#activity-list [data-detail="1"]').first.click()
            page.locator("#export-calendar").click()
            assert "Choose a reminder" in page.locator("#calendar-error").inner_text()
            assert page.url.startswith(base + "/workspace")
            page.locator("#detail-due").fill("2026-01-05T10:30")
            with page.expect_download() as download:
                page.locator("#export-calendar").click()
            assert download.value.suggested_filename == "opportunity-1-reminder.ics"
            assert conn.execute("SELECT due_at FROM cc_job_meta").fetchone()[0]
            assert not errors
        finally:
            browser.close()
