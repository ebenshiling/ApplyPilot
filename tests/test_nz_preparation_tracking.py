"""Owned-document manual mode, review epochs, portability and country isolation."""
from pathlib import Path
import json
import os
import sqlite3
import zipfile

import pytest

from applypilot import preparation,document_review,country_context as country,canonical,country_answers
from applypilot import database as db,setup_workspace as setup
from applypilot.discovery import nz,observations
from applypilot.apply import application_packet as packets
from applypilot.workspace_transfer import export_workspace,import_workspace
from test_nz_discovery import foundation,nz_workspace,context,permitted  # noqa: F401
from test_preparation_operations import ready_workspace
from document_fixtures import text_pdf
from test_command_center import workspace,http_workspace  # noqa: F401


def reference(root,conn,location="Auckland, New Zealand"):
    nz.capture(conn,root,{"url":"https://seek.co.nz/job?id=fixture","title":"Python Developer","company":"Fixture Employer","location":location})
    return dict(conn.execute("SELECT rowid AS id,* FROM jobs WHERE country_code='NZ'").fetchone())


def prepared(root,conn,cfg):
    ready_workspace(root,conn)
    job=reference(root,conn)
    with country.operation(context(root,cfg,conn)):
        result=preparation.manual_document(conn,root,{"id":job["id"],"kind":"resume"},actor="fixture_owner")
        assert result["status"]=="approved"
        job=dict(conn.execute("SELECT rowid AS id,* FROM jobs WHERE country_code='NZ'").fetchone())
        source=Path(job["tailored_resume_path"])
        text_pdf(source.with_suffix(".pdf"),source.read_text(encoding="utf-8"))
        canonical.bind_pdf(conn,root,source,source.with_suffix(".pdf"),job)
    return job,result


def test_reference_only_manual_preparation_uses_owned_facts_without_source_fetch_or_ai(nz_workspace):
    root,conn,cfg=nz_workspace
    job,result=prepared(root,conn,cfg)
    meta=observations.metadata(job)
    assert meta["reference_only"] and not meta["ai_use_allowed"] and job["full_description"] is None
    assert "Fixture Employer used Python" in Path(result["path"]).read_text()
    assert conn.execute("SELECT COUNT(*) FROM cc_writing_requests").fetchone()[0]==0
    assert conn.execute("SELECT COUNT(*) FROM cc_pipeline_usage").fetchone()[0]==0
    assert packets.status(conn,root,job["id"])["ready"]
    assert not packets.status(conn,root,job["id"])["application_ready"]


def test_exact_document_review_is_actor_bound_and_stale_pdf_cannot_reuse_it(nz_workspace):
    root,conn,cfg=nz_workspace
    job,_=prepared(root,conn,cfg)
    value=document_review.context(conn,root,job["id"])
    document_review.approve(conn,root,{"id":job["id"],"country_code":"NZ","human_reviewed":True,"expected_review_token":value["review_token"]},actor="fixture_owner")
    assert document_review.require(conn,root,job["id"],actor="fixture_owner")["approved"]
    with pytest.raises(ValueError,match="actor-bound"):
        document_review.require(conn,root,job["id"],actor="other_actor")
    source=Path(job["tailored_resume_path"])
    source.with_suffix(".pdf").write_bytes(source.with_suffix(".pdf").read_bytes()+b"Changed")
    assert not document_review.current(conn,root,job["id"])["approved"]
    with pytest.raises(ValueError,match="changed"):
        document_review.approve(conn,root,{"id":job["id"],"country_code":"NZ","human_reviewed":True,"expected_review_token":value["review_token"]},actor="fixture_owner")


def test_unknown_advert_country_needs_explicit_destination_without_rewriting_evidence(nz_workspace):
    root,conn,cfg=nz_workspace
    ready_workspace(root,conn)
    job=reference(root,conn,location=None)
    with pytest.raises(ValueError,match="destination"):
        preparation.require_job(job,root,text_required=False)
    preparation.choose_destination(conn,root,{"id":job["id"],"confirmed":True,"note":"Reviewed the NZ destination manually"},actor="fixture_owner")
    stored=dict(conn.execute("SELECT * FROM jobs WHERE country_code='NZ'").fetchone())
    assert observations.metadata(stored)["advertised_country"] is None
    assert preparation.require_job(stored,root,text_required=False)


def test_scoped_answers_ignore_legacy_global_yes_and_relevant_edits_do_not_revive_old_review(nz_workspace):
    root,conn,cfg=nz_workspace
    job,_=prepared(root,conn,cfg)
    question="Are you authorised to work in New Zealand?"
    conn.execute("UPDATE jobs SET document_requirements=? WHERE url=?",(json.dumps({"cover":False,"statement":False,"answers":[question]}),job["url"]))
    conn.execute("INSERT INTO cc_answers(question,answer,updated_at) VALUES(?,?,'2026-01-01')",(question,"Yes"))
    conn.commit()
    assert "Answer: "+question in packets.status(conn,root,job["id"])["missing"]
    country_answers.save(conn,root,{"country_code":"NZ","id":job["id"],"question":question,"answer":"No","human_reviewed":True},actor="fixture_owner")
    assert country_answers.reviewed(conn,"NZ",question,job=job)["answer"]=="No"
    prefs=country.read_preferences(conn,"NZ")
    country.save_preferences(conn,"NZ",{"search_preferences":{"locations":["Wellington"]}},prefs["revision"],cfg)
    assert country_answers.reviewed(conn,"NZ",question,job=job)
    prefs=country.read_preferences(conn,"NZ")
    country.save_preferences(conn,"NZ",{"work_authorization":{"legally_authorized_to_work":"Yes"}},prefs["revision"],cfg)
    assert country_answers.reviewed(conn,"NZ",question,job=job) is None
    prefs=country.read_preferences(conn,"NZ")
    country.save_preferences(conn,"NZ",{"work_authorization":{}},prefs["revision"],cfg)
    assert country_answers.reviewed(conn,"NZ",question,job=job) is None
    with pytest.raises(ValueError,match="another jurisdiction"):
        country_answers.save(conn,root,{"country_code":"NZ","id":job["id"],"question":"Are you authorised in Australia?","answer":"Yes","human_reviewed":True},actor="fixture_owner")


def test_snapshot_keeps_preferences_and_rejects_wrong_owner_or_configuration(nz_workspace):
    root,conn,cfg=nz_workspace
    country.save_preferences(conn,"NZ",{"work_authorization":{"require_sponsorship":"Yes"}},0,cfg)
    original=context(root,cfg,conn)
    snapshot=country.export_run_snapshot(original,original.config)
    folder=root/".country_runs";folder.mkdir()
    path=folder/"fixture.json";path.write_text(json.dumps(snapshot),encoding="utf-8")
    country.save_preferences(conn,"NZ",{"work_authorization":{"require_sponsorship":"Unknown"}},1,cfg)
    latest=context(root,cfg,conn)
    restored=country.restore_run_snapshot(latest,path)
    assert restored.preferences["work_authorization"]["require_sponsorship"]=="Yes" and restored.revision==1
    modified=country.CountryContext(latest.country_code,latest.workspace,latest.campaign_id,json.dumps(latest.config|{"queries":[{"query":"changed"}]}))
    with pytest.raises(ValueError,match="does not match"):
        country.restore_run_snapshot(modified,path)
    with pytest.raises(ValueError,match="no fallback"):
        country.restore_run_snapshot(latest,root/"missing.json")


def test_backup_preserves_country_metadata_but_invalidates_authority_and_excludes_evidence_by_default(nz_workspace):
    root,conn,cfg=nz_workspace
    job,_=prepared(root,conn,cfg)
    token=document_review.context(conn,root,job["id"])["review_token"]
    document_review.approve(conn,root,{"id":job["id"],"country_code":"NZ","human_reviewed":True,"expected_review_token":token},actor="fixture_owner")
    permitted(root)
    folder=root/"evidence"/"nz";folder.mkdir(parents=True)
    (folder/"private-excerpt.txt").write_text("Explicitly governed retained excerpt")
    archive=root/"normal.zip";export_workspace(root,archive,include_generated=True)
    with zipfile.ZipFile(archive) as z:
        assert "evidence/nz/private-excerpt.txt" not in z.namelist()
    explicit=root/"evidence.zip";export_workspace(root,explicit,include_generated=True,include_evidence=True)
    with zipfile.ZipFile(explicit) as z:assert "evidence/nz/private-excerpt.txt" in z.namelist()
    target=root/"restored"
    import_workspace(archive,target)
    restored=db.init_db(target/"applypilot.db")
    try:
        assert restored.execute("SELECT country_code FROM jobs").fetchone()[0]=="NZ"
        assert restored.execute("SELECT document_approval_json FROM cc_job_meta").fetchone()[0] is None
        assert all(value=="unverified_or_changed" for value in canonical.state(restored,target)["fact_states"].values())
        assert not packets.status(restored,target,job["id"])["application_ready"]
        assert "_nz_validation" not in nz.source(target,"nz-fixture")
    finally:db.close_connection(target/"applypilot.db")
    with pytest.raises(ValueError,match="cannot be overwritten"):
        import_workspace(archive,root,overwrite=True)


def test_manual_tracking_does_not_need_masters_and_mixed_country_bulk_writes_fail_before_updates(nz_workspace):
    root,conn,cfg=nz_workspace
    job=reference(root,conn)
    conn.execute("INSERT INTO jobs(url,title,country_code) VALUES('https://uk.example/job','UK','GB')")
    conn.commit()
    uk=conn.execute("SELECT rowid FROM jobs WHERE country_code='GB'").fetchone()[0]
    with pytest.raises(ValueError):preparation.track(conn,root,{"ids":[job["id"],uk],"action":"archive"})
    assert conn.execute("SELECT COUNT(*) FROM cc_job_meta WHERE visibility='archived'").fetchone()[0]==0
    preparation.track(conn,root,{"id":job["id"],"action":"applied","evidence":"External manual receipt checked"})
    row=conn.execute("SELECT country_code,apply_status,applied_at FROM jobs WHERE country_code='NZ'").fetchone()
    assert row[0:2]==("NZ","applied") and row[2]


def test_nz_additions_do_not_change_proven_legacy_inputs_or_mark_unknown_documents_verified(nz_workspace):
    root,conn,cfg=nz_workspace
    from applypilot import command_center as cc
    source=root/"legacy.txt";source.write_text("Historical unverified CV")
    conn.execute("INSERT INTO jobs(url,title,country_code,tailored_resume_path) VALUES('https://uk.example/job','Analyst','GB',?)",(str(source),))
    legacy=cc.legacy_fingerprint(root)
    conn.execute("INSERT INTO cc_documents(job_url,text_path,fingerprint,created_at,context_json) VALUES('https://uk.example/job',?,?,?,?)",(str(source),legacy,cc.now(),'{}'))
    conn.commit()
    before=cc.fingerprint(root)
    ready_workspace(root,conn)
    nz.save_campaign(conn,root,{"expected_revision":nz.revision(setup.read_searches_dict(root))})
    assert cc.fingerprint(root)==before
    row=conn.execute("SELECT * FROM cc_documents WHERE job_url='https://uk.example/job'").fetchone()
    assert not cc.document_stale(conn,root,row)
    assert json.loads(row["context_json"]).get("factual_state")!="current"
    conn.execute("UPDATE cc_documents SET fingerprint='legacy-untracked',context_json='{}'")
    conn.commit()
    assert cc.document_stale(conn,root,conn.execute("SELECT * FROM cc_documents").fetchone())


def test_retired_and_analytical_views_use_persisted_country(nz_workspace):
    root,conn,cfg=nz_workspace
    job=reference(root,conn)
    conn.execute("INSERT INTO jobs(url,title,country_code) VALUES('https://uk.example/job','UK','GB')");conn.commit()
    from applypilot import opportunity_insights as insights,job_lifecycle as lifecycle
    with country.operation(context(root,cfg,conn)):
        assert {r["country_code"] for r in insights.opportunities(conn)}=={"NZ"}
        preview=lifecycle.preview(conn,root,job["id"])
        lifecycle.retire(conn,root,{"id":job["id"],"reason":"not_pursuing","detail":"Synthetic NZ closure","cleanup_token":preview["cleanup_token"]})
        assert lifecycle.history(conn,root)["records"][0]["snapshot"]["country_code"]=="NZ"
    assert not lifecycle.history(conn,root)["records"]
    assert {r["country_code"] for r in insights.opportunities(conn)}=={"GB"}


def test_usage_correlation_does_not_double_count_or_treat_unknown_usage_as_zero(nz_workspace):
    root,conn,_=nz_workspace
    from applypilot.pipeline_store import usage_report
    conn.execute("INSERT INTO cc_pipeline_usage(run_id,provider,model,input_tokens,output_tokens,created_at,request_key,country_code) VALUES('fixture','openai','fixture',5,2,'2026-01-01','shared','NZ')")
    conn.execute("INSERT INTO cc_ai_usage(provider,model,input_tokens,output_tokens,created_at,request_key,country_code) VALUES('openai','fixture',5,2,'2026-01-01','shared','NZ')")
    conn.execute("INSERT INTO cc_pipeline_usage(run_id,provider,model,created_at,request_key,country_code) VALUES('fixture','openai','fixture','2026-01-01','unknown-attempt','NZ')")
    conn.commit()
    out=usage_report(conn,{"openai/fixture":{"input_per_million":1,"output_per_million":2}},country_code="NZ")["usage"][0]
    assert out["requests"]==2 and out["recorded_input_tokens"]==5 and out["input_tokens"] is None and out["estimated_usd"] is None


def test_local_nz_manual_prepare_review_and_track_without_provider_or_employer_requests(http_workspace,monkeypatch):
    if os.environ.get("APPLYPILOT_RUN_COUNTRY_UI")!="1":pytest.skip("Local-only country UX disabled")
    from playwright.sync_api import sync_playwright
    from applypilot.scoring import pdf
    base,root,conn=http_workspace
    cfg={"nz_workflow_enabled":True,"country":"UK","queries":[{"query":"Analyst"}],"locations":[{"location":"London, UK"}]}
    import yaml
    (root/"searches.yaml").write_text(yaml.safe_dump(cfg),encoding="utf-8")
    ready_workspace(root,conn)
    job=reference(root,conn)
    monkeypatch.setattr(pdf,"convert_to_pdf",lambda source,output_path=None,**kwargs:text_pdf(output_path or source.with_suffix('.pdf'),source.read_text(encoding='utf-8')))
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();errors=[];external=[]
            page.on("pageerror",lambda exc:errors.append(str(exc)))
            page.on("dialog",lambda dialog:dialog.accept("External receipt checked") if dialog.type=="prompt" else dialog.accept())
            def route(request):
                if request.request.url.startswith(base+"/"):request.continue_()
                else:external.append(request.request.url);request.abort()
            page.route("**/*",route)
            page.goto(base+"/workspace")
            page.wait_for_function("document.querySelector('#job-search')!==null")
            page.locator("#workspace-country").select_option("NZ")
            page.locator("#nz-manual-document-form").wait_for(state="attached")
            page.locator('[data-nz-step="nz-documents-stage"]').click()
            page.get_by_text("Prepare candidate-owned text without AI",exact=True).click()
            page.locator("#nz-manual-job").select_option(str(job["id"]))
            page.locator("#nz-manual-document-form button[type=submit]").click()
            page.wait_for_function("document.querySelector('#nz-manual-document-result')?.textContent.includes('approved')")
            page.locator("#refresh").click()
            page.locator(f'[data-nz-render="{job["id"]}"]').wait_for()
            page.wait_for_function(f"!document.querySelector('[data-nz-render=\"{job['id']}\"]').disabled")
            page.locator(f'[data-nz-render="{job["id"]}"]').click()
            page.wait_for_function("document.querySelector('#toast').textContent.includes('PDF bytes checked')")
            page.locator(f'[data-document-review="{job["id"]}"]').click()
            page.locator("#final-documents-human-reviewed").check()
            page.locator("#final-documents-record").click()
            page.wait_for_function("document.querySelector('#final-document-review-feedback').textContent.includes('does not authorize')")
            page.locator('[data-review-close]').click()
            page.locator(f'[data-nz-track="applied"][data-nz-track-job="{job["id"]}"]').click()
            page.wait_for_function("document.querySelector('#toast').textContent.includes('tracking updated')")
            assert conn.execute("SELECT apply_status FROM jobs WHERE country_code='NZ'").fetchone()[0]=="applied"
            assert conn.execute("SELECT COUNT(*) FROM cc_writing_requests").fetchone()[0]==0
            assert not errors and not any("seek.co.nz" in uri for uri in external)
        finally:browser.close()
