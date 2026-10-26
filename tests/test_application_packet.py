import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest
from pypdf import PdfReader, PdfWriter

from applypilot import command_center as cc
from applypilot.apply import application_packet as packets
from applypilot.apply import assisted_browser as ab
from applypilot.scoring import pdf
from test_command_center import workspace, http_workspace  # noqa: F401, F811
from test_assisted_browser import packet  # noqa: F401
from document_fixtures import finalized_document,text_pdf


def seed(root, conn):
    packet(root, conn)
    cover = root / "cover.txt"
    cover.write_text("Reviewed cover evidence", encoding="utf-8")
    cover.with_suffix(".pdf").write_bytes(b"%PDF-1.4\nExisting reviewed cover")
    conn.execute("UPDATE jobs SET cover_letter_path=?,document_requirements=? WHERE rowid=1", (str(cover), json.dumps({"statement": True, "cover": True})))
    job = cc.get_job(conn, 1)
    for kind, field in (("cover", "cover_letter_path"), ("statement", "supporting_statement_path")):
        conn.execute("INSERT INTO cc_writing_documents(kind,job_url,text_path,status) VALUES(?,?,?,'published')", (kind, job["url"], job[field]))
    conn.commit()
    for kind,field in (("resume","tailored_resume_path"),("cover","cover_letter_path"),("statement","supporting_statement_path")):
        finalized_document(conn,root,job[field],job,kind,pdf=True)
    return job


def missing_letters(root, conn):
    job = seed(root, conn)
    for field in ("cover_letter_path", "supporting_statement_path"):
        Path(job[field]).with_suffix(".pdf").unlink()
    return job


@pytest.fixture
def fake_renderer(monkeypatch):
    rendered = []
    def convert(source, output_path=None, **kwargs):
        rendered.append((Path(source), kwargs.get("document_kind")))
        text_pdf(output_path,Path(source).read_text(encoding="utf-8"))
        return Path(output_path)
    monkeypatch.setattr(pdf, "convert_to_pdf", convert)
    return rendered


def test_readiness_names_missing_pdfs_and_empty_sources(workspace):
    root, conn = workspace
    missing_letters(root, conn)
    status = packets.status(conn, root, 1)
    assert not status["ready"] and status["can_prepare"]
    assert status["missing"] == ["Cover letter: PDF missing", "Supporting statement: PDF missing"]
    assert [d["pdf_state"] for d in status["documents"]] == ["ready", "missing", "missing"]
    assert "PDF rendering" not in status["missing"]
    (root / "cover.txt").write_text("")
    assert "Cover letter: draft is empty" in packets.status(conn, root, 1)["missing"]


def test_preparation_preserves_text_and_submission_state(workspace, fake_renderer):
    root, conn = workspace
    job = missing_letters(root, conn)
    original = {field: hashlib.sha256(Path(job[field]).read_bytes()).hexdigest() for field in ("tailored_resume_path", "cover_letter_path", "supporting_statement_path")}
    old_token = packets.status(conn, root, 1)["packet_token"]
    result = packets.prepare(conn, root, 1)
    assert result["packet"]["ready"] and result["errors"] == []
    assert result["rendered"] == ["cover", "statement"]
    assert [kind for source, kind in fake_renderer] == ["cover", "statement"]
    assert original == {field: hashlib.sha256(Path(job[field]).read_bytes()).hexdigest() for field in original}
    assert old_token != result["packet"]["packet_token"]
    assert not cc.get_job(conn, 1)["applied_at"] and cc.get_job(conn, 1)["apply_status"] == job["apply_status"]
    assert all(Path(r[0]).is_file() for r in conn.execute("SELECT pdf_path FROM cc_writing_documents"))
    assert not list(root.glob(".*.pdf"))
    assert packets.prepare(conn, root, 1)["rendered"] == []


@pytest.mark.parametrize("mode", ["failure", "source_changed", "invalid_output", "future_report"])
def test_atomic_render_does_not_replace_old_pdf_on_failure(workspace, monkeypatch, mode):
    root, conn = workspace
    job = seed(root, conn)
    source = Path(job["cover_letter_path"])
    canonical = source.with_suffix(".pdf")
    original_pdf = canonical.read_bytes()
    stamp = source.stat().st_mtime_ns
    os.utime(canonical, ns=(stamp-1_000_000, stamp-1_000_000))
    if mode == "future_report":
        report = source.with_name(source.stem+"_REPORT.json")
        report.write_text('{}')
        os.utime(report, ns=(stamp+60_000_000_000, stamp+60_000_000_000))
    def render(path, output_path=None, **kwargs):
        if mode == "failure":
            Path(output_path).write_bytes(b"partial output")
            raise PermissionError("Locked PDF")
        if mode == "invalid_output":
            Path(output_path).write_bytes(b"invalid PDF")
            return output_path
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        with Path(output_path).open("wb") as stream:
            writer.write(stream)
        if mode == "source_changed":
            source.write_text("New reviewed text", encoding="utf-8")
        return output_path
    monkeypatch.setattr(pdf, "convert_to_pdf", render)
    result = packets.prepare(conn, root, 1)
    assert result["errors"] and not result["packet"]["ready"]
    assert canonical.read_bytes() == original_pdf and not list(root.glob(".*.pdf"))
    assert cc.get_job(conn, 1)["pdf_status"] == "failed" and not cc.get_job(conn, 1)["applied_at"]


@pytest.mark.parametrize("state", ["in_progress", "running", "submitting", "submission_uncertain", "applied", "skipped"])
def test_active_and_reconciled_records_cannot_be_rendered(workspace, fake_renderer, state):
    root, conn = workspace
    missing_letters(root, conn)
    conn.execute("UPDATE jobs SET apply_status=? WHERE rowid=1", (state,))
    conn.commit()
    with pytest.raises(ValueError, match="unavailable"):
        packets.prepare(conn, root, 1)
    assert not fake_renderer


def test_outside_workspace_and_stale_preview_are_rejected(workspace):
    root, conn = workspace
    missing_letters(root, conn)
    with pytest.raises(LookupError, match="Prepare"):
        packets.document(conn, root, 1, "cover", "pdf")
    outside = root.parent / "outside-letter.txt"
    outside.write_text("Private external content")
    conn.execute("UPDATE jobs SET cover_letter_path=? WHERE rowid=1", (str(outside),))
    conn.commit()
    assert "Cover letter: document unavailable in this workspace" in packets.status(conn, root, 1)["missing"]
    with pytest.raises(ValueError, match="unavailable"):
        packets.document(conn, root, 1, "cover", "html")


def test_packet_token_rejects_unreviewed_changes(workspace, monkeypatch):
    root, conn = workspace
    job = seed(root, conn)
    token = packets.status(conn, root, 1)["packet_token"]
    # Change PDF bytes without changing source readiness; the old approval is stale.
    Path(job["cover_letter_path"]).with_suffix(".pdf").write_bytes(b"%PDF-1.4\nChanged after review")
    monkeypatch.setattr(ab.Session, "run", lambda self: pytest.fail("Stale packet started a browser"))
    # Exact packet identity invalidates approval even when execution is released.
    assert packets.status(conn, root, 1)["packet_token"] != token
    with pytest.raises(ValueError, match="Documents changed"):
        ab.start(conn, root, {"id": 1, "documents_approved": True, "packet_token": token})
    assert not cc.get_job(conn, 1)["applied_at"]


def test_pdf_symlink_cannot_escape_workspace(workspace):
    root, conn = workspace
    job = missing_letters(root, conn)
    outside = root.parent / "outside-private.pdf"
    outside.write_bytes(b"%PDF-1.4\nPrivate external PDF")
    try:
        Path(job["cover_letter_path"]).with_suffix(".pdf").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation needs platform permissions")
    assert "Cover letter: document unavailable in this workspace" in packets.status(conn, root, 1)["missing"]
    with pytest.raises(ValueError, match="unavailable"):
        packets.document(conn, root, 1, "cover", "pdf")


def test_optional_included_document_requires_current_upload(workspace):
    root, conn = workspace
    missing_letters(root, conn)
    conn.execute("UPDATE jobs SET document_requirements=? WHERE rowid=1", (json.dumps({"cover": False, "statement": True}),))
    conn.commit()
    assert "Cover letter: PDF missing" in packets.status(conn, root, 1)["missing"]


def test_scoped_http_packet_preparation_and_previews(http_workspace, fake_renderer):
    base, root, conn = http_workspace
    missing_letters(root, conn)
    with httpx.Client(base_url=base) as client:
        before = client.get("/api/center/browser?id=1").json()["packet"]
        assert not before["ready"]
        preview = client.get("/api/center/browser/document?id=1&kind=cover&format=html")
        assert "Reviewed cover evidence" in preview.text
        assert client.get("/api/center/browser/document?id=1&kind=cover&format=pdf").status_code == 404
        out = client.post("/api/center/browser/prepare", json={"id": 1}).json()
        assert out["packet"]["ready"] and out["rendered"] == ["cover", "statement"]
        assert client.get("/api/center/browser/document?id=1&kind=statement&format=pdf").content.startswith(b"%PDF-")
        assert client.post("/api/center/browser/prepare", json={"id": 999}).status_code == 400
        assert client.get("/api/center/browser/document?id=1&kind=unknown").status_code == 400


def test_readiness_ui_recovers_missing_pdfs(http_workspace, fake_renderer):
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1 for browser verification")
    base, root, conn = http_workspace
    missing_letters(root, conn)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(base+"/workspace")
            page.get_by_role("button", name="View record", exact=True).click()
            page.get_by_role("button", name="Apply with agent", exact=True).click()
            page.get_by_role("button", name="Prepare current PDFs", exact=True).wait_for()
            assert page.locator("#agent-start").is_disabled()
            assert page.locator("#agent-documents-approved").is_disabled()
            assert "Cover letter: PDF missing" in page.locator(".agent-packet-blockers").inner_text()
            assert page.locator(".agent-document-links a").count() == 4  # Three text previews and ready CV PDF.
            page.locator("#agent-prepare").click()
            page.wait_for_function("document.querySelector('#agent-start').disabled===false")
            assert page.locator(".agent-packet-blockers").count() == 0
            assert page.locator(".agent-document-links a").count() == 6
            assert not page.locator("#agent-documents-approved").is_checked()
            assert not errors and not cc.get_job(conn, 1)["applied_at"]
        finally:
            browser.close()


def test_real_letter_render_uses_kind_for_generic_filenames(workspace):
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1 for browser verification")
    root, conn = workspace
    missing_letters(root, conn)
    result = packets.prepare(conn, root, 1)
    assert result["packet"]["ready"] and not result["errors"]
    assert "Reviewed cover evidence" in " ".join(p.extract_text() for p in PdfReader(root/"cover.pdf").pages)
    assert "Reviewed NHS evidence" in " ".join(p.extract_text() for p in PdfReader(root/"statement.pdf").pages)
