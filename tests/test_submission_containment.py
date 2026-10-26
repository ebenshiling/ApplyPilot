from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from applypilot.apply.safety import SubmissionContained
from test_command_center import workspace  # noqa: F401


def test_live_executors_and_direct_final_helpers_are_contained_before_side_effects(monkeypatch):
    monkeypatch.setenv("APPLYPILOT_LIVE_APPLICATIONS_DISABLED", "1")
    from applypilot.apply import launcher, llm_engine, assisted_browser, chrome
    calls = [lambda: launcher.main(), lambda: launcher.worker_loop(), lambda: launcher.run_job({}, 9222), lambda: launcher.gen_prompt("https://example.org"),
             lambda: launcher._make_mcp_config(9222), lambda: llm_engine.main(),
             lambda: llm_engine._assist_one_job({}, 0, True, False, False),
             lambda: llm_engine._worker_loop(0, 1, 7, True, False, True, False, None, False),
             lambda: llm_engine._maybe_submit_application(object(), {}, {}, ""),
             lambda: chrome.launch_chrome(0), lambda: assisted_browser.start(None, None, {}),
             lambda: assisted_browser.Session.run(object()), lambda: assisted_browser.Session.submit(object(), "anything"),
             lambda: assisted_browser.Session.handle(object(), "approve", {}),
             lambda: assisted_browser.Session.apply_input(object(), {"type": "key", "key": "Enter"})]
    # Old boolean/environment intents must never grant a capability.
    monkeypatch.setenv("APPLYPILOT_ALLOW_SUBMIT", "1")
    monkeypatch.setenv("APPLYPILOT_NZ_DISABLED", "0")
    for call in calls:
        with pytest.raises(SubmissionContained, match="service operator"):
            call()


@pytest.mark.parametrize("name", ["_js_click_best_apply_entry", "_apply_fills", "_click_by_text",
    "_click_exact_text", "_js_click_exact_text", "_js_click_forward_action", "_js_accept_privacy_consent",
    "_js_talemetry_privacy_unblock", "_js_click_forward_prefer_continue", "_js_click_primary_action",
    "_dismiss_common_banners", "_click_locator_expect_popup", "_enter_application_flow"])
def test_direct_legacy_actuators_have_no_tool_call_escape_hatch(name, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_LIVE_APPLICATIONS_DISABLED", "1")
    from applypilot.apply import llm_engine
    action = getattr(llm_engine, name)
    assert not hasattr(action, "__wrapped__")
    with pytest.raises(SubmissionContained):
        action(object(), arbitrary="No page or tool method may be used")


@pytest.mark.parametrize("arguments", [[], ["--submit"], ["--engine", "llm", "--submit"], ["--dry-run"], ["--gen", "--url", "https://example.org"], ["--continuous", "--workers", "3"]])
def test_cli_submit_modes_fail_closed_but_syntax_remains(arguments, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_LIVE_APPLICATIONS_DISABLED", "1")
    from applypilot import cli
    monkeypatch.setattr(cli, "_bootstrap", lambda: None)
    result = CliRunner().invoke(cli.app, ["apply", *arguments])
    assert result.exit_code == 3, result.output
    assert "no browser worker" in result.output


@pytest.mark.parametrize("mode", ["prepare", "submit"])
def test_scheduled_live_execution_holds_for_manual_review_without_launch_or_http(workspace, monkeypatch, mode):
    monkeypatch.setenv("APPLYPILOT_LIVE_APPLICATIONS_DISABLED", "1")
    root, conn = workspace
    from applypilot.scheduler import schedule, tick
    from applypilot import command_center as cc
    schedule(conn, 1, (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), mode)
    conn.execute("UPDATE cc_schedules SET run_at=?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
    conn.commit()
    runner = SimpleNamespace(status=lambda: {"running": False}, start=Mock(side_effect=AssertionError("No live worker")))
    check = Mock(side_effect=AssertionError("No employer request"))
    monkeypatch.setattr(cc, "check_listing", check)
    tick(conn, runner)
    row = conn.execute("SELECT * FROM cc_schedules").fetchone()
    assert row["state"] == "needs_review" and row["mode"] == mode
    assert cc.get_job(conn, 1)["applied_at"] is None
    runner.start.assert_not_called()
    check.assert_not_called()


def test_uk_document_packet_preparation_still_works(workspace, monkeypatch):
    root, conn = workspace
    from applypilot.apply import application_packet
    from applypilot.scoring import pdf
    from pypdf import PdfWriter
    cv = root / "approved.txt"
    cv.write_text("Verified UK CV", encoding="utf-8")
    conn.execute("UPDATE jobs SET tailored_resume_path=?,country_code='GB',document_requirements=?", (str(cv), '{"cover":false,"statement":false}'))
    conn.commit()
    def render(source, output_path=None, **kwargs):
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.write(output_path)
        return output_path
    monkeypatch.setattr(pdf, "convert_to_pdf", render)
    from document_fixtures import finalized_document,text_pdf
    from applypilot.command_center import get_job
    finalized_document(conn,root,cv,get_job(conn,1))
    monkeypatch.setattr(pdf,"convert_to_pdf",lambda source,output_path=None,**kwargs:text_pdf(output_path,source.read_text(encoding="utf-8")))
    before = application_packet.status(conn, root, 1)
    assert before["can_prepare"]
    prepared = application_packet.prepare(conn, root, 1)
    assert prepared["packet"]["ready"]
    assert cv.read_text() == "Verified UK CV" and cv.with_suffix(".pdf").is_file()
    assert conn.execute("SELECT applied_at FROM jobs").fetchone()[0] is None
