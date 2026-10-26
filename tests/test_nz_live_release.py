"""Released paths use actual browser code against local-only employer fixtures."""
import json
from pathlib import Path

import pytest

from applypilot import command_center as cc
from applypilot.apply import assisted_browser as ab
from applypilot.apply import safety
from test_command_center import workspace  # noqa: F401
from test_assisted_browser import packet, portal, planner, wait_state, end_sessions  # noqa: F401


def test_released_actuator_calls_existing_implementation_and_respects_service_stop(monkeypatch):
    monkeypatch.delenv("APPLYPILOT_LIVE_APPLICATIONS_DISABLED", raising=False)
    called = []
    action = safety.contained_browser_action(lambda value: called.append(value) or value * 2)
    assert safety.status()["live_execution_enabled"]
    assert action(4) == 8 and called == [4]
    monkeypatch.setenv("APPLYPILOT_LIVE_APPLICATIONS_DISABLED", "1")
    with pytest.raises(safety.SubmissionContained):
        action(7)
    assert called == [4] and not safety.status()["live_execution_enabled"]


def test_nz_browser_does_not_inherit_global_uk_answers(workspace):
    root, conn = workspace
    (root / "profile.json").write_text(json.dumps({"personal": {"full_name": "Test Candidate", "legally_authorized_to_work": "Yes"},
        "work_authorization": {"legally_authorized_to_work": "Yes", "require_sponsorship": "No"}}))
    conn.execute("INSERT INTO cc_answers(question,answer,updated_at) VALUES('Do you have UK work rights?','Yes',?)", (cc.now(),))
    conn.commit()
    profile, answers = ab.application_data(conn, root, {"country_code": "NZ", "url": "https://example.org/nz"})
    assert answers == [] and profile["work_authorization"] == {}
    assert "legally_authorized_to_work" not in profile["personal"]


def test_nz_existing_packet_execution_does_not_require_importing_all_master_categories(workspace):
    from applypilot.country_context import resolve_run
    from applypilot.discovery.nz import effective, preset
    root, conn = workspace
    (root / "searches.yaml").write_text("nz_workflow_enabled: true\n")
    context = resolve_run(root, effective({"nz_workflow_enabled": True}, preset()), "NZ", "nz-job-search", stages=["apply"])
    assert context.country_code == "NZ" and context.campaign_id == "nz-job-search"


@pytest.mark.parametrize("code", ["GB", "NZ"])
def test_real_browser_executor_local_portal_needs_approval_then_submits_once(workspace, portal, planner, code):
    root, conn = workspace
    base, submissions = portal
    if code == "NZ":
        path = root / "searches.yaml"
        path.write_text((path.read_text() if path.exists() else "") + "\nnz_workflow_enabled: true\n")
    conn.execute("UPDATE jobs SET country_code=? WHERE rowid=1", (code,))
    conn.commit()
    packet(root, conn, base + "/uncertain")
    ident = ab.start(conn, root, {"id": 1, "documents_approved": True})["session"]["id"]
    review = wait_state(root, ident, {"review"})
    assert not submissions and review["approval_token"]
    ab.command(root, {"session_id": ident, "action": "approve", "approval_token": review["approval_token"]})
    wait_state(root, ident, {"uncertain"})
    assert len(submissions) == 1 and not cc.get_job(conn, 1)["applied_at"]
    with pytest.raises(ValueError, match="ended"):
        ab.command(root, {"session_id": ident, "action": "approve", "approval_token": review["approval_token"]})


def test_document_change_after_browser_review_prevents_dispatch(workspace, portal, planner):
    root, conn = workspace
    base, submissions = portal
    packet(root, conn, base + "/uncertain")
    ident = ab.start(conn, root, {"id": 1, "documents_approved": True})["session"]["id"]
    review = wait_state(root, ident, {"review"})
    Path(cc.get_job(conn, 1)["tailored_resume_path"]).write_text("Changed after review")
    ab.command(root, {"session_id": ident, "action": "approve", "approval_token": review["approval_token"]})
    result = wait_state(root, ident, {"manual"})
    assert "context changed" in result["detail"] and not submissions
