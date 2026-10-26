import json
import os
from types import SimpleNamespace

import pytest

from applypilot import command_center as cc
from applypilot import evidence_inbox as inbox
from applypilot.database import init_db, close_connection
from test_command_center import http_workspace as _http_workspace_fixture, workspace as workspace

http_workspace = _http_workspace_fixture


@pytest.fixture
def evidence_workspace(tmp_path):
    conn = init_db(tmp_path / "applypilot.db")
    conn.execute("INSERT INTO jobs(url,title,company,applied_at,apply_status) VALUES('https://example.org/job','Data Analyst','Example Ltd','2026-10-01T10:00:00Z','applied')")
    conn.execute("INSERT INTO cc_job_meta(job_url,reference,hiring_status,notes) VALUES('https://example.org/job','REF-123','submitted','Existing notes')")
    conn.commit()
    yield tmp_path, conn
    close_connection(tmp_path / "applypilot.db")


def message_data():
    return {"subject": "Interview invitation REF-123", "sender": "Jane <jane@example.org>", "received_at": "2026-10-03T10:00:00Z", "body": "We invite you to interview for Data Analyst at Example Ltd on 4 November 2026 at 14:00 UTC. Meeting: https://meet.google.com/abc-defg-hij. Contact Jane Recruiter."}


def model_result():
    return {"category": "application", "category_evidence": "Interview invitation", "hiring_status": "interview", "status_evidence": "invite you to interview", "job_id": 1, "match_evidence": "REF-123", "confidence": 0.9, "summary": "Interview invitation with a specified time.", "events": [{"kind": "interview", "datetime": "2026-11-04T14:00:00+00:00", "date_text": "4 November 2026", "time_text": "14:00", "timezone_text": "UTC", "evidence": "4 November 2026 at 14:00 UTC"}], "meeting_url": "https://meet.google.com/abc-defg-hij", "recruiter": "Jane Recruiter"}


@pytest.fixture
def fake_email_ai(monkeypatch):
    from applypilot import llm
    from applypilot.ai_routing import current_task
    calls = []
    behavior = {"response": model_result(), "during": None}
    def chat(messages, **kwargs):
        assert current_task() == "email"
        assert kwargs["response_mime_type"] == "application/json"
        calls.append(messages)
        if behavior["during"]:
            behavior["during"]()
        return json.dumps(behavior["response"])
    client = SimpleNamespace(provider="openai", model="gpt-6-luna", last_model="gpt-6-luna", cache_identity="openai:gpt-6-luna:none", last_usage={"prompt_tokens": 100, "completion_tokens": 80}, chat=chat)
    monkeypatch.setattr(llm, "get_client", lambda: client)
    monkeypatch.setenv("APPLYPILOT_AI_DAILY_REQUESTS", "20")
    return calls, behavior


@pytest.mark.parametrize("subject,sender,expected", [
    ("Example is hiring a Data Analyst", "LinkedIn <jobs-noreply@linkedin.com>", "job_alert"),
    ("Let's finish what you started", "contact@getabound.com", "promotion"),
    ('My partner says my job changed me', "noreply@gainrepmail.com", "promotion"),
    ("Checking in", "hr@example.org", "unknown"),
])
def test_non_application_messages_have_no_hiring_proposal(evidence_workspace, subject, sender, expected):
    _, conn = evidence_workspace
    message = inbox.import_message(conn, {"subject": subject, "sender": sender, "body": "Career content and application advice."})
    assert message["category"] == expected
    assert message["proposed_state"] is None and message["job_url"] is None
    assert inbox.listing(conn)["messages"] == []


def test_conditional_rejection_is_not_a_confirmed_outcome():
    assert inbox.classify({"subject": "Process information", "body": "If your application is unsuccessful, you may apply again."})["state"] is None


def test_duplicate_and_native_id_imports_preserve_dismissal(evidence_workspace):
    _, conn = evidence_workspace
    data = {**message_data(), "source": "gmail", "source_id": "native-123", "thread_id": "thread-1"}
    first = inbox.import_message(conn, data)
    inbox.message_action(conn, first["id"], "dismiss")
    duplicate = inbox.import_message(conn, {**data, "body": "Changed provider rendering"})
    assert duplicate["id"] == first["id"] and duplicate["status"] == "dismissed"
    assert duplicate["body"] == data["body"]
    assert conn.execute("SELECT COUNT(*) FROM cc_messages").fetchone()[0] == 1


def test_ignore_sender_is_exact_and_reversible(evidence_workspace):
    _, conn = evidence_workspace
    first = inbox.import_message(conn, {"subject": "Job alerts", "sender": "News <news@example.org>", "body": "New opportunities"})
    inbox.message_action(conn, first["id"], "ignore_sender")
    hidden = inbox.import_message(conn, {"subject": "More jobs", "sender": "NEWS@example.org", "body": "More opportunities"})
    assert hidden["status"] == "ignored"
    other = inbox.import_message(conn, {"subject": "Received your application", "sender": "hr@example.org", "body": "We received your application"})
    assert other["status"] == "review"
    inbox.unignore(conn, 1)
    inbox.message_action(conn, hidden["id"], "restore")
    assert inbox.get_message(conn, hidden["id"])["status"] == "review"


def test_migration_preserves_recorded_history_and_clears_bad_unreviewed_defaults(evidence_workspace):
    _, conn = evidence_workspace
    conn.execute("DROP TABLE cc_messages")
    conn.execute("CREATE TABLE cc_messages(id INTEGER PRIMARY KEY,digest TEXT UNIQUE,subject TEXT,body TEXT,sender TEXT,job_url TEXT,proposed_state TEXT,status TEXT,created_at TEXT)")
    for i, status in ((1, "review"), (2, "accepted")):
        conn.execute("INSERT INTO cc_messages VALUES(?,?,?,?,?,?,?,?,?)", (i, str(i), "Job alerts", "Newsletter", "jobs-noreply@linkedin.com", "https://example.org/job", "acknowledged", status, cc.now()))
    conn.commit()
    inbox.init_schema(conn)
    assert inbox.get_message(conn, 1)["proposed_state"] is None
    assert inbox.get_message(conn, 2)["status"] == "accepted"
    assert inbox.get_message(conn, 2)["proposed_state"] == "acknowledged"
    assert conn.execute("SELECT hiring_status FROM cc_job_meta").fetchone()[0] == "submitted"
    assert inbox.listing(conn, "alerts")["total"] == 1


def test_ambiguous_and_partial_references_do_not_pick_wrong_job(evidence_workspace):
    _, conn = evidence_workspace
    jobs = inbox.candidates(conn)
    assert inbox.match_candidates({"subject": "REF-1234", "body": "Message"}, jobs)[0] == []
    conn.execute("INSERT INTO jobs(url,title,company,applied_at) VALUES('https://example.org/second','Data Analyst','Example Ltd','2026-10-02')")
    conn.commit()
    found, _ = inbox.match_candidates({"body": "Data Analyst at Example Ltd"}, inbox.candidates(conn))
    assert len(found) == 2


@pytest.mark.parametrize("date_text,time_text,zone,model_date,expected", [
    ("4 November 2026", "14:00", "UTC", "2026-11-04T14:00:00Z", "2026-11-04T14:00:00+00:00"),
    ("November 4, 2026", "2 pm", "GMT", None, "2026-11-04T14:00:00+00:00"),
    ("2026-11-04", "14:00", "UTC+01:00", None, "2026-11-04T13:00:00+00:00"),
    ("tomorrow", "14:00", "UTC", None, "2026-10-04T14:00:00+00:00"),
    ("02/03/2026", "14:00", "UTC", None, None),
    ("4 November", "14:00", "UTC", None, None),
    ("4 November 2026", "14:00", "UTC", "2026-11-05T14:00:00Z", None),
    ("4 July 2026", "14:00", "Europe/London", None, "2026-07-04T13:00:00+00:00"),
    ("25 October 2026", "01:30", "Europe/London", None, None),
])
def test_dates_require_exact_clock_zone_and_source(date_text, time_text, zone, model_date, expected):
    quote = f"Interview {date_text} at {time_text} {zone}"
    message = {"body": quote, "received_at": "2026-10-03T10:00:00Z"}
    event = {"evidence": quote, "date_text": date_text, "time_text": time_text, "timezone_text": zone, "datetime": model_date}
    assert inbox.verified_datetime(event, message)[0] == expected


def test_missing_receipt_or_zone_does_not_invent_relative_date():
    event = {"evidence": "tomorrow at 14:00 UTC", "date_text": "tomorrow", "time_text": "14:00", "timezone_text": "UTC"}
    assert inbox.verified_datetime(event, {"body": event["evidence"]})[0] is None
    event["timezone_text"] = "Europe/London"
    assert inbox.verified_datetime(event, {"body": event["evidence"], "received_at": "2026-10-03T10:00Z"})[0] is None


def test_hallucinated_match_link_contact_and_status_are_not_proposed(evidence_workspace):
    _, conn = evidence_workspace
    data = model_result()
    data.update(job_id=999, status_evidence="We offer you a job", meeting_url="https://invented.example/meeting", recruiter="Invented recruiter")
    result = inbox.validate_analysis(data, message_data(), inbox.candidates(conn))
    assert result["job_url"] is None and result["hiring_status"] is None
    assert result["meeting_url"] is None and result["recruiter"] is None
    assert result["issues"]


def test_analysis_is_cached_and_does_not_apply_dates_or_status(evidence_workspace, fake_email_ai):
    root, conn = evidence_workspace
    calls, _ = fake_email_ai
    message = inbox.import_message(conn, message_data())
    result = inbox.analyze(conn, root, message["id"])
    assert result["message"]["analysis_status"] == "done"
    assert result["message"]["proposed_due_at"] == "2026-11-04T14:00:00+00:00"
    assert inbox.analyze(conn, root, message["id"])["cached"]
    assert len(calls) == 1
    meta = conn.execute("SELECT hiring_status,due_at FROM cc_job_meta").fetchone()
    assert meta[0] == "submitted" and meta[1] is None
    inbox.review(conn, root, message["id"], 1, "interview", due_at=result["message"]["proposed_due_at"], include_details=True, use_recruiter=True)
    meta = conn.execute("SELECT hiring_status,due_at,notes,recruiter FROM cc_job_meta").fetchone()
    assert meta[0] == "interview" and meta[1] == "2026-11-04T14:00:00+00:00"
    assert "Existing notes" in meta[2] and "https://meet.google.com" in meta[2]
    assert meta[3] == "Jane Recruiter"
    with pytest.raises(ValueError, match="already"):
        inbox.review(conn, root, message["id"], 1, "rejected")


def test_dismissal_during_inference_is_not_overwritten(evidence_workspace, fake_email_ai):
    root, conn = evidence_workspace
    _, behavior = fake_email_ai
    message = inbox.import_message(conn, message_data())
    def dismiss():
        assert not conn.in_transaction
        inbox.message_action(conn, message["id"], "dismiss")
    behavior["during"] = dismiss
    result = inbox.analyze(conn, root, message["id"])
    assert result["message"]["status"] == "dismissed"
    assert result["message"]["analysis_status"] == "discarded"


def test_budget_and_nonapplication_review_guards(evidence_workspace, fake_email_ai, monkeypatch):
    root, conn = evidence_workspace
    calls, _ = fake_email_ai
    message = inbox.import_message(conn, message_data())
    monkeypatch.setenv("APPLYPILOT_AI_DAILY_REQUESTS", "0")
    with pytest.raises(ValueError, match="budget"):
        inbox.analyze(conn, root, message["id"])
    assert not calls
    alert = inbox.import_message(conn, {"subject": "Job alerts", "body": "New jobs", "sender": "news@example.org"})
    with pytest.raises(ValueError, match="review"):
        inbox.review(conn, root, alert["id"], 1, "acknowledged")
    with pytest.raises(ValueError, match="Choose"):
        inbox.review(conn, root, message["id"], 1, "")


def test_bad_ai_json_leaves_hiring_records_untouched(evidence_workspace, fake_email_ai):
    root, conn = evidence_workspace
    _, behavior = fake_email_ai
    behavior["response"] = {"category": "application", "confidence": 1, "category_evidence": "Invented quote"}
    message = inbox.import_message(conn, message_data())
    with pytest.raises(ValueError, match="evidence"):
        inbox.analyze(conn, root, message["id"])
    assert inbox.get_message(conn, message["id"])["analysis_status"] == "failed"
    assert conn.execute("SELECT hiring_status FROM cc_job_meta").fetchone()[0] == "submitted"


def test_bucket_filtering_happens_before_pagination(evidence_workspace):
    _, conn = evidence_workspace
    inbox.import_message(conn, message_data())
    for i in range(30):
        inbox.import_message(conn, {"subject": f"Job alerts {i}", "sender": "news@example.org", "body": "New jobs"})
    result = inbox.listing(conn, "applications", limit=5)
    assert result["total"] == 1 and len(result["messages"]) == 1
    assert result["counts"]["alerts"] == 30


def test_html_meeting_links_are_preserved_without_active_content():
    from applypilot.mail_connectors import html_text
    text = html_text('<p>Interview <a href="https://meet.google.com/abc">Join</a></p><script>secret script</script>')
    assert "https://meet.google.com/abc" in text
    assert "secret script" not in text


def test_inbox_http_filters_and_actions(http_workspace):
    import httpx
    base, _, _ = http_workspace
    with httpx.Client(base_url=base, trust_env=False) as client:
        message = client.post("/api/center/messages/import", json={"subject": "Job alerts", "sender": "news@example.org", "body": "New opportunities"}).json()["message"]
        assert message["category"] == "job_alert" and message["proposed_state"] is None
        assert client.get("/api/center/messages").json()["total"] == 0
        assert client.get("/api/center/messages?bucket=alerts").json()["total"] == 1
        assert client.post("/api/center/messages/action", json={"message_id": message["id"], "action": "dismiss"}).json()["message"]["status"] == "dismissed"
        assert client.get("/api/center/messages?bucket=dismissed").json()["total"] == 1
        assert client.post("/api/center/messages/action", json={"message_id": message["id"], "action": "restore"}).json()["message"]["status"] == "review"
        assert client.post("/api/center/messages/review", json={"message_id": message["id"], "id": 1, "hiring_status": "acknowledged"}).status_code == 400


def test_inbox_browser_filtering_drafts_analysis_and_review(http_workspace, fake_email_ai):
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1 for browser verification")
    from playwright.sync_api import sync_playwright
    base, _, conn = http_workspace
    conn.execute("UPDATE jobs SET applied_at='2026-10-01T10:00:00Z',apply_status='applied' WHERE rowid=1")
    conn.execute("INSERT INTO cc_job_meta(job_url,reference,hiring_status) SELECT url,'REF-123','submitted' FROM jobs WHERE rowid=1")
    conn.commit()
    invitation = inbox.import_message(conn, message_data())
    pending = inbox.import_message(conn, {"subject": "Your application progress", "sender": "hr@example.org", "body": "Your application is being considered."})
    alert = inbox.import_message(conn, {"subject": "Example is hiring a Data Analyst", "sender": "jobs-noreply@linkedin.com", "body": "Explore jobs"})
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/workspace#inbox", wait_until="domcontentloaded")
        page.locator(f"#message-state-{pending['id']}").wait_for()
        assert page.locator(f"#message-state-{pending['id']}").input_value() == ""
        assert page.locator(f'[data-inbox-message="{alert["id"]}"]').count() == 0
        page.locator("#message-subject").fill("Unsaved pasted message")
        page.locator("#inbox-filter").select_option("alerts")
        page.locator(f'[data-inbox-message="{alert["id"]}"]').wait_for()
        assert page.locator(f"#message-state-{alert['id']}").count() == 0
        assert page.locator("#message-subject").input_value() == "Unsaved pasted message"
        page.locator(f'[data-inbox-action="dismiss"][data-message-id="{alert["id"]}"]').click()
        page.locator(f'[data-inbox-message="{alert["id"]}"]').wait_for(state="detached")
        page.locator("#inbox-filter").select_option("dismissed")
        page.locator(f'[data-inbox-action="restore"][data-message-id="{alert["id"]}"]').click()
        page.locator("#inbox-filter").select_option("applications")
        page.locator(f'[data-inbox-analyze="{invitation["id"]}"]').click()
        page.get_by_text("openai · gpt-6-luna · AI confidence estimate 90%", exact=True).wait_for()
        card = page.locator(f'[data-inbox-message="{invitation["id"]}"]')
        assert card.locator('[data-review-field="useDate"]').is_checked() is False
        assert conn.execute("SELECT due_at FROM cc_job_meta").fetchone()[0] is None
        card.locator('[data-review-field="useDate"]').check()
        page.locator("#inbox-refresh").click()
        card.locator('[data-review-field="useDate"]').wait_for()
        assert card.locator('[data-review-field="useDate"]').is_checked()
        card.locator('[data-inbox-review]').click()
        card.wait_for(state="detached")
        meta = conn.execute("SELECT hiring_status,due_at FROM cc_job_meta").fetchone()
        assert meta[0] == "interview" and meta[1] == "2026-11-04T14:00:00+00:00"
        assert not errors
        assert len(fake_email_ai[0]) == 1
        browser.close()
