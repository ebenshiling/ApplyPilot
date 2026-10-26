import hashlib
import os

import pytest

from applypilot import evidence_inbox as inbox
from applypilot import inbox_identity as identity
from test_evidence_inbox import evidence_workspace as _evidence_fixture
from test_command_center import http_workspace as _http_fixture, workspace as workspace

evidence_workspace = _evidence_fixture
http_workspace = _http_fixture


def data():
    return {"subject": "Your application for Data Analyst at Example Ltd", "sender": "Recruitment <hr@example.org>", "body": "We have received your application for Data Analyst at Example Ltd. We appreciate the time spent preparing your application. Visit our candidate portal for further information."}


def native_data(source_id="mail-1"):
    return {**data(), "body": data()["body"] + " (https://portal.example.org/details)", "source": "gmail", "source_id": source_id, "thread_id": "same-thread", "received_at": "2026-10-03T10:00:00+00:00", "rfc_message_id": "<delivery-1@example.org>"}


def seed_old_native_copy(conn, payload):
    # Simulate the previously deployed parser creating a second stored row.
    row = conn.execute("INSERT INTO cc_messages(digest,subject,body,sender,status,created_at,category,classification_version,sender_address,source,source_id,thread_id,received_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (hashlib.sha256(payload["body"].encode()).hexdigest(), payload["subject"], payload["body"], payload["sender"], "review", inbox.now(), "application", 1, inbox.address(payload["sender"]), payload["source"], payload["source_id"], payload["thread_id"], payload["received_at"]))
    conn.commit()
    return row.lastrowid


@pytest.mark.parametrize("decision", ["accepted", "dismissed", "ignored"])
def test_legacy_native_render_change_keeps_decision_and_id(evidence_workspace, decision):
    root, conn = evidence_workspace
    old = inbox.import_message(conn, data())
    if decision == "accepted":
        inbox.review(conn, root, old["id"], 1, "acknowledged")
    elif decision == "dismissed":
        inbox.message_action(conn, old["id"], "dismiss")
    else:
        inbox.message_action(conn, old["id"], "ignore_sender")
    result = inbox.import_message(conn, native_data())
    assert result["id"] == old["id"] and result["status"] == decision
    assert result["body"] == data()["body"]
    assert result["received_at"] == native_data()["received_at"]
    assert inbox.import_message(conn, native_data())["id"] == old["id"]
    assert conn.execute("SELECT COUNT(*) FROM cc_messages").fetchone()[0] == 1


def test_existing_copies_reconcile_without_deletion_or_repeat_review(evidence_workspace):
    root, conn = evidence_workspace
    old = inbox.import_message(conn, data())
    inbox.review(conn, root, old["id"], 1, "acknowledged")
    conn.execute("UPDATE cc_messages SET analysis_status='done',analysis_json='{\"summary\":\"Reviewed original\"}' WHERE id=?", (old["id"],))
    conn.commit()
    copied_id = seed_old_native_copy(conn, native_data())
    inbox.init_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM cc_messages").fetchone()[0] == 2
    assert inbox.get_message(conn, copied_id)["duplicate_of"] == old["id"]
    assert inbox.get_message(conn, old["id"])["analysis"]["summary"] == "Reviewed original"
    assert inbox.listing(conn, "applications")["total"] == 0
    assert inbox.listing(conn, "recorded")["total"] == 1
    assert inbox.listing(conn, "duplicates")["total"] == 1
    assert inbox.listing(conn, "all")["total"] == 1
    assert inbox.import_message(conn, native_data())["id"] == old["id"]
    with pytest.raises(ValueError, match="original"):
        inbox.review(conn, root, copied_id, 1, "rejected")


def test_different_native_deliveries_with_identical_text_are_not_collapsed(evidence_workspace):
    _, conn = evidence_workspace
    first = inbox.import_message(conn, {**native_data("first"), "rfc_message_id": None})
    second = inbox.import_message(conn, {**native_data("second"), "rfc_message_id": None})
    assert first["id"] != second["id"]
    inbox.init_schema(conn)
    assert inbox.listing(conn, "all")["total"] == 2


def test_same_thread_receipt_and_rejection_remain_distinct(evidence_workspace):
    _, conn = evidence_workspace
    first = inbox.import_message(conn, native_data("receipt"))
    rejected = inbox.import_message(conn, {**native_data("reply"), "subject": "Re: " + data()["subject"], "body": "We regret to inform you that we cannot progress your application further.", "rfc_message_id": "<reply@example.org>"})
    assert first["id"] != rejected["id"]
    inbox.init_schema(conn)
    assert inbox.listing(conn, "all")["total"] == 2


def test_changed_meaningful_url_or_known_receipt_time_is_not_merged(evidence_workspace):
    _, conn = evidence_workspace
    original = {**data(), "body": data()["body"] + " Join at (https://meeting.example/old)", "received_at": "2026-10-02T10:00:00Z"}
    old = inbox.import_message(conn, original)
    changed = inbox.import_message(conn, {**native_data(), "body": original["body"].replace("/old", "/new")})
    assert old["id"] != changed["id"]
    assert not identity.same_legacy_content(original | {"sender_address": "hr@example.org"}, native_data() | {"sender_address": "hr@example.org"})


def test_multiple_matching_native_candidates_are_not_guessed(evidence_workspace):
    _, conn = evidence_workspace
    old = inbox.import_message(conn, data())
    # Seed two distinct native deliveries before reconciling an unknown legacy import.
    first = native_data("first")
    second = {**native_data("second"), "body": native_data()["body"].replace("/details", "/other")}
    seed_old_native_copy(conn, first)
    seed_old_native_copy(conn, second)
    inbox.init_schema(conn)
    assert inbox.get_message(conn, old["id"])["duplicate_of"] is None
    assert inbox.listing(conn, "all")["total"] == 3


def test_changed_outcome_is_not_fuzzy_merged():
    original = data() | {"sender_address": "hr@example.org"}
    changed = original | {"body": original["body"].replace("have received", "have not received")}
    assert not identity.same_legacy_content(original, changed)


def test_same_rfc_delivery_has_aliases_for_both_providers(evidence_workspace):
    _, conn = evidence_workspace
    first = inbox.import_message(conn, native_data("gmail-id"))
    second = inbox.import_message(conn, {**native_data("outlook-id"), "source": "outlook"})
    assert first["id"] == second["id"]
    assert conn.execute("SELECT COUNT(*) FROM cc_message_identities").fetchone()[0] == 2
    assert inbox.import_message(conn, {**native_data("outlook-id"), "source": "outlook"})["id"] == first["id"]


def test_conflicting_recorded_decisions_are_not_hidden(evidence_workspace):
    root, conn = evidence_workspace
    old = inbox.import_message(conn, data())
    inbox.review(conn, root, old["id"], 1, "acknowledged")
    copied = seed_old_native_copy(conn, native_data())
    conn.execute("UPDATE cc_messages SET status='accepted',proposed_state='rejected',job_url='https://example.org/other' WHERE id=?", (copied,))
    conn.commit()
    inbox.init_schema(conn)
    assert inbox.get_message(conn, copied)["duplicate_of"] is None
    assert inbox.listing(conn, "recorded")["total"] == 2


def test_stale_page_offset_returns_a_valid_page(evidence_workspace):
    _, conn = evidence_workspace
    for i in range(26):
        inbox.import_message(conn, {**data(), "subject": f"Your application {i}"})
    result = inbox.listing(conn, offset=25, limit=25)
    assert result["offset"] == 25 and len(result["messages"]) == 1
    inbox.message_action(conn, result["messages"][0]["id"], "dismiss")
    clamped = inbox.listing(conn, offset=25, limit=25)
    assert clamped["offset"] == 0 and len(clamped["messages"]) == 25


def test_browser_refresh_keeps_nodes_sources_and_page_position(http_workspace):
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1 for browser verification")
    from playwright.sync_api import sync_playwright
    base, _, conn = http_workspace
    for i in range(26):
        inbox.import_message(conn, {**data(), "subject": f"Your application {i}"})
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(base + "/workspace#inbox", wait_until="domcontentloaded")
        page.locator('[data-inbox-page="next"]').click()
        page.wait_for_function("state.inboxOffset===25 && document.querySelectorAll('#inbox-list [data-inbox-message]').length===1")
        card = page.locator("#inbox-list [data-inbox-message]")
        card.locator("summary").click()
        page.evaluate("window.savedCard=document.querySelector('#inbox-list [data-inbox-message]');window.savedNext=document.querySelector('[data-inbox-page=next]')")
        page.evaluate("loadInbox()")
        assert page.evaluate("savedCard===document.querySelector('#inbox-list [data-inbox-message]')")
        assert page.evaluate("savedNext===document.querySelector('[data-inbox-page=next]')")
        assert card.locator("details").evaluate("el=>el.open")
        assert page.evaluate("state.inboxOffset") == 25
        card.locator('[data-inbox-action="dismiss"]').click()
        page.wait_for_function("state.inboxOffset===0 && document.querySelectorAll('#inbox-list [data-inbox-message]').length===25")
        assert page.locator('[data-inbox-page="previous"]').is_disabled()
        assert page.locator('[data-inbox-page="next"]').is_disabled()
        browser.close()
