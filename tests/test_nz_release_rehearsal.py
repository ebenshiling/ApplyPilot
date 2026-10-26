"""Offline release rehearsal. Real candidate/source/ATS acceptance stays blocked."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
import yaml

from applypilot import database as db,country_context as country,preparation,document_review,nz_evidence
from applypilot.discovery import nz
from applypilot.apply import authority
from test_nz_discovery import foundation,nz_workspace,context  # noqa: F401
from test_preparation_operations import ready_workspace
from test_nz_preparation_tracking import reference
from test_nz_evidence import employer,observed,linked
from document_fixtures import text_pdf


def test_old_schema_double_migration_keeps_legacy_identity_state_and_artifact_bytes(tmp_path):
    path=tmp_path/"applypilot.db"
    original=("https://employer.example/legacy?id=old&token=keep","Legacy analyst","applied","2025-01-01")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE jobs(url TEXT PRIMARY KEY,title TEXT,apply_status TEXT,applied_at TEXT)")
        conn.execute("INSERT INTO jobs VALUES(?,?,?,?)",original)
    config=b"# protected UK config\nqueries: [{query: Analyst}]\nlocations: [{location: 'London, UK'}]\n"
    (tmp_path/"searches.yaml").write_bytes(config)
    document=tmp_path/"submitted_original.txt";document.write_bytes(b"Historical submitted bytes remain unchanged")
    digest=hashlib.sha256(document.read_bytes()).hexdigest()
    conn=db.init_db(path)
    try:
        db.init_db(path)
        assert conn.execute("SELECT url,title,apply_status,applied_at FROM jobs").fetchone()[:]==original
        assert conn.execute("SELECT country_code FROM jobs").fetchone()[0] is None
        assert (tmp_path/"searches.yaml").read_bytes()==config and hashlib.sha256(document.read_bytes()).hexdigest()==digest
        assert conn.execute("SELECT COUNT(*) FROM cc_country_profiles").fetchone()[0]==0
        assert conn.execute("SELECT COUNT(*) FROM cc_employers").fetchone()[0]==0
    finally:db.close_connection(path)


def test_manual_nz_journey_evidence_three_sources_review_tracking_and_country_rollback(nz_workspace):
    root,conn,cfg=nz_workspace
    protected=b"Protected GB submitted CV"
    old=root/"gb_submitted.txt";old.write_bytes(protected)
    conn.execute("INSERT INTO jobs(url,title,country_code,apply_status,applied_at,tailored_resume_path) VALUES('https://uk.example/old','GB history','GB','applied','2025-01-01',?)",(str(old),))
    conn.commit()
    before=(root/"searches.yaml").read_bytes()
    ready_workspace(root,conn)
    campaign=nz.save_campaign(conn,root,{"expected_revision":nz.revision(yaml.safe_load(before))})
    assert (root/"searches.yaml").read_bytes().startswith(before)
    job=reference(root,conn)
    ctx=context(root,yaml.safe_load((root/"searches.yaml").read_text()),conn)
    assert ctx.public()["preparation_available"]
    with country.operation(ctx):
        entity=employer(root,conn)
        linked(root,conn,entity,job)
        nz_evidence.record(conn,root,observed(entity,result="not_listed",quote="No public result found"),actor="fixture_owner")
        assert nz_evidence.state(conn,root)["jobs"][0]["assessment"]["code"]=="unavailable_conflicting"
        result=preparation.manual_document(conn,root,{"id":job["id"],"kind":"resume"},actor="fixture_owner")
        source=Path(result["path"])
        from applypilot.canonical import bind_pdf
        current=dict(conn.execute("SELECT rowid AS id,* FROM jobs WHERE country_code='NZ'").fetchone())
        text_pdf(source.with_suffix(".pdf"),source.read_text())
        bind_pdf(conn,root,source,source.with_suffix(".pdf"),current)
        review=document_review.context(conn,root,job["id"])
        document_review.approve(conn,root,{"id":job["id"],"country_code":"NZ","human_reviewed":True,"expected_review_token":review["review_token"]},actor="fixture_owner")
        handoff=authority.review(conn,root,job["id"],actor="fixture_owner",session_binding="fixture-session",country_code="NZ")
        assert handoff["mode"]=="assisted_browser" and handoff["supported"]
        preparation.track(conn,root,{"id":job["id"],"action":"applied","evidence":"Synthetic manual outcome recorded; no website contacted"})
    assert old.read_bytes()==protected
    assert conn.execute("SELECT apply_status,applied_at FROM jobs WHERE country_code='GB'").fetchone()[:]==("applied","2025-01-01")
    root_cfg=yaml.safe_load((root/"searches.yaml").read_text())
    (root/"searches.yaml").write_text(yaml.safe_dump(root_cfg|{"nz_workflow_enabled":False}),encoding="utf-8")
    with pytest.raises(country.CountryError,match="disabled"):country.resolve_view(root,"NZ",conn=conn)
    assert conn.execute("SELECT apply_status FROM jobs WHERE country_code='NZ'").fetchone()[0]=="applied"
    assert not nz.matrix(root,campaign["campaign"])["automatic_discovery_available"]
    assert conn.execute("SELECT COUNT(*) FROM cc_writing_requests").fetchone()[0]==0
