"""No LLM/citation proof: exact reviewed facts and final bytes are authoritative."""
import base64
from datetime import date,timedelta
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from applypilot import canonical as facts,cv_library,setup_workspace as setup,nz_documents,country_context as country
from test_nz_discovery import foundation,nz_workspace  # noqa: F401
from test_command_center import workspace,http_workspace  # noqa: F401


def save_fact(root,conn,*,text="Fixture Employer reduced reporting time by 20%.",kind="employment",**patch):
    value=facts.state(conn,root)
    item={"kind":kind,"text":text,"entity_id":"employer_fixture" if kind in {"employment","metric","employment_period"} else "", "entity_label":"Fixture Employer" if kind in {"employment","metric","employment_period"} else "",
          "source_reference":"Synthetic canonical evidence","source_quote":text}|patch
    out=facts.action(conn,root,{"expected_revision":value["revision"],"action":"save_fact","fact":item},actor="fixture_owner")
    return out["facts"][-1]


def verify(root,conn,fact):
    return facts.action(conn,root,{"action":"verify_fact","fact_id":fact["id"],"expected_revision":facts.state(conn,root)["revision"],"human_reviewed":True},actor="fixture_owner")


def test_imported_profile_and_model_citation_never_auto_verify(nz_workspace):
    root,conn,_=nz_workspace
    fact=save_fact(root,conn)
    assert not facts.check(conn,root,fact["text"],{"country_code":"GB"})["passed"]
    profile=setup.read_profile(root)
    profile["canonical_facts"]["facts"][0]["verification"]={"state":"verified","audit_event_id":999}
    setup.write_profile(root,{"canonical_facts":profile["canonical_facts"]})
    assert not facts.check(conn,root,fact["text"],{"country_code":"GB"})["passed"]
    verify(root,conn,fact)
    assert facts.check(conn,root,fact["text"],{"country_code":"GB"})["passed"]
    for text in (fact["text"].replace("20%","95%"),fact["text"]+" [F1]",fact["text"].replace("reporting","Django reporting"),"Other Employer reduced reporting time by 20%."):
        assert not facts.check(conn,root,text,{"country_code":"GB"})["passed"]


def test_approved_meaning_preserving_wording_and_edit_invalidation(nz_workspace):
    root,conn,_=nz_workspace
    fact=save_fact(root,conn)
    verify(root,conn,fact)
    wording="At Fixture Employer, reporting time was reduced by 20%."
    assert not facts.check(conn,root,wording,{"country_code":"GB"})["passed"]
    out=facts.action(conn,root,{"action":"approve_wording","expected_revision":facts.state(conn,root)["revision"],"human_reviewed":True,"fact_ids":[fact["id"]],"text":wording},actor="fixture_owner")
    assert facts.check(conn,root,wording,{"country_code":"GB"})["passed"]
    changed={k:v for k,v in fact.items() if k!="verification"}|{"text":"Fixture Employer reduced reporting time by 10%."}
    facts.action(conn,root,{"action":"save_fact","expected_revision":out["revision"],"fact":changed},actor="fixture_owner")
    assert not facts.check(conn,root,wording,{"country_code":"GB"})["passed"]


def test_wording_review_cannot_inflate_numbers_or_transfer_global_tool_experience(nz_workspace):
    root,conn,_=nz_workspace
    fact=save_fact(root,conn);verify(root,conn,fact)
    with pytest.raises(ValueError,match="New numbers"):
        facts.action(conn,root,{"action":"approve_wording","expected_revision":facts.state(conn,root)["revision"],"human_reviewed":True,"fact_ids":[fact["id"]],"text":"Fixture Employer reduced reporting time by 95%."},actor="fixture_owner")
    skill=save_fact(root,conn,kind="skill",text="Django");verify(root,conn,skill)
    with pytest.raises(ValueError,match="global skill"):
        facts.action(conn,root,{"action":"approve_wording","expected_revision":facts.state(conn,root)["revision"],"human_reviewed":True,"fact_ids":[fact["id"],skill["id"]],"text":"Fixture Employer used Django to reduce reporting time by 20%."},actor="fixture_owner")


def test_entity_and_credential_status_cannot_be_removed_by_wording_review(nz_workspace):
    root,conn,_=nz_workspace
    fact=save_fact(root,conn)
    verify(root,conn,fact)
    with pytest.raises(ValueError,match="attribution"):
        facts.action(conn,root,{"action":"approve_wording","expected_revision":facts.state(conn,root)["revision"],"human_reviewed":True,"fact_ids":[fact["id"]],"text":"Reduced reporting time by 20%."},actor="fixture_owner")
    credential=save_fact(root,conn,kind="credential",text="PL-300 — in progress",status="in_progress")
    verify(root,conn,credential)
    with pytest.raises(ValueError,match="incomplete"):
        facts.action(conn,root,{"action":"approve_wording","expected_revision":facts.state(conn,root)["revision"],"human_reviewed":True,"fact_ids":[credential["id"]],"text":"PL-300 certified"},actor="fixture_owner")
    expired=save_fact(root,conn,kind="credential",text="Fixture certificate earned",status="earned",expires=(date.today()-timedelta(days=1)).isoformat())
    verify(root,conn,expired)
    assert facts.state(conn,root)["fact_states"][expired["id"]]=="unverified_or_changed"


def test_global_skill_cannot_be_moved_into_employer_experience(nz_workspace):
    root,conn,_=nz_workspace
    skill=save_fact(root,conn,kind="skill",text="Python")
    verify(root,conn,skill)
    assert facts.check(conn,root,"TECHNICAL SKILLS\nPython",{"country_code":"GB"})["passed"]
    assert not facts.check(conn,root,"EXPERIENCE\nPython",{"country_code":"GB"})["passed"]


def test_actual_final_bytes_policy_country_and_scoped_dependencies(nz_workspace):
    root,conn,cfg=nz_workspace
    fact=save_fact(root,conn)
    verify(root,conn,fact)
    path=root/"final.txt"
    path.write_bytes(("SUMMARY\r\n"+fact["text"]+"\r\n").encode())
    job={"url":"https://example.nz/job?id=1","country_code":"NZ"}
    proof=facts.finalize(conn,root,path,job)
    assert proof["passed"] and facts.require_final(conn,root,path,job)["passed"]
    count=conn.execute("SELECT COUNT(*) FROM cc_events WHERE kind='canonical_document_finalized'").fetchone()[0]
    facts.finalize(conn,root,path,job)
    assert conn.execute("SELECT COUNT(*) FROM cc_events WHERE kind='canonical_document_finalized'").fetchone()[0]==count
    other=save_fact(root,conn,kind="personal",text="Unrelated fixture name")
    verify(root,conn,other)
    assert facts.require_final(conn,root,path,job)["passed"]
    with pytest.raises(facts.FactReviewRequired):facts.require_final(conn,root,path,job|{"country_code":"GB"})
    path.write_bytes(path.read_bytes()+b"New unsupported claim.\n")
    with pytest.raises(facts.FactReviewRequired):facts.require_final(conn,root,path,job)


def test_professional_field_correction_invalidates_both_countries_not_history(nz_workspace):
    root,conn,_=nz_workspace
    setup.write_profile(root,{"personal":{"full_name":"Fixture Candidate"}})
    fact=save_fact(root,conn,kind="personal",text="Fixture Candidate",profile_path="personal.full_name")
    verify(root,conn,fact)
    paths=[]
    for code in ("GB","NZ"):
        path=root/(code+".txt")
        path.write_text(fact["text"],encoding="utf-8")
        facts.finalize(conn,root,path,{"country_code":code})
        paths.append(path)
    original=[p.read_bytes() for p in paths]
    setup.write_profile(root,{"personal":{"full_name":"Corrected Fixture Candidate"}})
    for code,path in zip(("GB","NZ"),paths):
        with pytest.raises(facts.FactReviewRequired):facts.require_final(conn,root,path,{"country_code":code})
    assert [p.read_bytes() for p in paths]==original


def test_typed_period_union_does_not_infer_tool_tenure_or_double_count(nz_workspace):
    root,conn,_=nz_workspace
    for start,end in (("2020-01-01","2021-01-01"),("2020-06-01","2022-01-01")):
        fact=save_fact(root,conn,kind="employment_period",text=f"Fixture Employer employment from {start} until {end}.",period={"start":start,"end":end,"time_basis":"part_time"})
        verify(root,conn,fact)
    result=facts.calendar_employment(conn,setup.read_profile(root))
    assert result["days"]==731 and "not full-time-equivalent or tool-specific" in result["text"]
    assert facts.check(conn,root,result["text"],{"country_code":"GB"})["passed"]
    assert not facts.check(conn,root,"Five years of Django experience.",{"country_code":"GB"})["passed"]


def import_master(root,conn,category="software",key="nz_master",text="Fixture Employer reduced reporting time by 20%."):
    return cv_library.import_files(conn,root,[{"name":"master.txt","key":key,"role":"Software Developer","country_code":"NZ","category":category,"b64":base64.b64encode(text.encode()).decode()}])["results"][0]


def test_nz_import_collision_original_retention_reconciliation_and_uk_config(nz_workspace):
    root,conn,_=nz_workspace
    original=(root/"searches.yaml").read_bytes()
    fact=save_fact(root,conn)
    first=import_master(root,conn)
    second=import_master(root,conn)
    assert first["key"]!=second["key"] and first["ok"] and second["ok"]
    metadata=dict(conn.execute("SELECT * FROM cc_cv_sources WHERE source_key=?",(first["key"],)).fetchone())
    assert Path(metadata["original_path"]).read_bytes()==fact["text"].encode()
    assert (root/"searches.yaml").read_bytes()==original
    assert nz_documents.reconcile(conn,root,first["key"])["status"]=="unresolved"
    verify(root,conn,fact)
    assert nz_documents.reconcile(conn,root,first["key"])["status"]=="reconciled"
    with pytest.raises(ValueError,match="one reconciled"):
        nz_documents.route({"country_code":"NZ","title":"Python Developer"},root)
    route=nz_documents.route({"country_code":"NZ","title":"Python Developer","resume_route_override":first["key"]},root)
    assert route.key==first["key"]
    from applypilot.role_routing import suggested_routing
    assert not suggested_routing(root)["variants"]


@pytest.mark.parametrize("category,title",[(category,title) for category,titles in nz_documents.CATEGORIES.items() for title in titles])
def test_all_requested_nz_titles_choose_exact_category_master(nz_workspace,category,title):
    root,conn,_=nz_workspace
    fact=save_fact(root,conn)
    verify(root,conn,fact)
    master=import_master(root,conn,category=category)
    nz_documents.reconcile(conn,root,master["key"])
    assert nz_documents.route({"country_code":"NZ","title":title.title()},root).key==master["key"]
    with pytest.raises(ValueError,match="Unknown/ambiguous"):
        nz_documents.route({"country_code":"NZ","title":"Unmapped Administrative Role"},root)
    setup.write_resume_variant(root,master["key"],fact["text"]+"\nInvented Django experience.")
    with pytest.raises(ValueError,match="reconciliation"):
        nz_documents.route({"country_code":"NZ","title":title.title()},root)


def test_pipeline_finaliser_preserves_existing_pointer_for_unverified_draft(nz_workspace):
    root,conn,_=nz_workspace
    old=root/"old.txt";old.write_bytes(b"Historical submitted bytes")
    draft=root/"draft.txt";draft.write_bytes(b"New unsupported model wording")
    url="https://uk.example/job"
    conn.execute("INSERT INTO jobs(url,title,country_code,tailored_resume_path) VALUES(?,?,?,?)",(url,"Developer","GB",str(old)))
    conn.commit()
    from applypilot.pipeline_store import persist_document
    result={"url":url,"path":str(draft),"status":"approved"}
    persist_document(conn,"tailor",result)
    row=conn.execute("SELECT tailored_resume_path,tailor_status FROM jobs WHERE url=?",(url,)).fetchone()
    assert row[:]==(str(old),"needs_fact_review") and old.read_bytes()==b"Historical submitted bytes" and draft.exists()


def test_client_cannot_inject_verification_through_fact_action(nz_workspace):
    root,conn,_=nz_workspace
    from applypilot import command_api
    handler=SimpleNamespace(_send_json=Mock(),_current_user=lambda:{"username":"actual_owner"})
    command_api.post(handler,"/api/center/canonical/action",{"country_code":"NZ","expected_revision":0,"action":"save_fact","fact":{"id":"forged","kind":"personal","text":"Invented","verification":{"state":"verified"}}},root,None)
    assert handler._send_json.call_args.args[0]==400
    assert facts.state(conn,root)["facts"]==[]


def test_actual_pdf_round_trip_rejects_extra_claims_and_mutated_bytes(nz_workspace):
    root,conn,_=nz_workspace
    from document_fixtures import text_pdf
    fact=save_fact(root,conn)
    verify(root,conn,fact)
    source=root/"approved.txt";source.write_text(fact["text"],encoding="utf-8")
    job={"country_code":"GB","url":"https://employer.example/job","title":"Developer"}
    facts.finalize(conn,root,source,job,kind="cover")
    pdf=root/"approved.pdf"
    text_pdf(pdf,fact["text"]+"\nInvented Django leadership experience.")
    with pytest.raises(facts.FactReviewRequired,match="added, omitted"):
        facts.bind_pdf(conn,root,source,pdf,job,kind="cover")
    text_pdf(pdf,fact["text"])
    facts.bind_pdf(conn,root,source,pdf,job,kind="cover")
    assert facts.require_pdf(conn,root,source,pdf,job,kind="cover")
    pdf.write_bytes(pdf.read_bytes()+b"Changed after review")
    with pytest.raises(facts.FactReviewRequired):facts.require_pdf(conn,root,source,pdf,job,kind="cover")


@pytest.mark.parametrize("stage,field",[("tailor","tailored_resume_path"),("cover","cover_letter_path"),("statement","supporting_statement_path")])
def test_all_batch_publication_paths_require_current_verified_bytes(nz_workspace,stage,field):
    root,conn,_=nz_workspace
    fact=save_fact(root,conn);verify(root,conn,fact)
    url="https://uk.example/job"
    conn.execute("INSERT INTO jobs(url,title,country_code) VALUES(?,?,?)",(url,"Developer","GB"));conn.commit()
    path=root/(stage+".txt");path.write_text(fact["text"],encoding="utf-8")
    from applypilot.pipeline_store import persist_document
    result={"url":url,"path":str(path),"status":"approved"}
    persist_document(conn,stage,result)
    assert result["status"]=="approved"
    assert conn.execute(f"SELECT {field} FROM jobs").fetchone()[0]==str(path)
    draft=root/(stage+"_new.txt");draft.write_text("Unsupported tool claim with a valid [F1] citation",encoding="utf-8")
    result={"url":url,"path":str(draft),"status":"approved"}
    persist_document(conn,stage,result)
    assert result["status"]=="needs_fact_review" and conn.execute(f"SELECT {field} FROM jobs").fetchone()[0]==str(path)


def test_nz_disclosures_never_inherit_uk_values_or_answer_foreign_conditional_questions(nz_workspace):
    from applypilot.application_profile import application_values,local_fills
    profile={"personal":{"full_name":"Fixture","willing_to_relocate":"No","legally_authorized_to_work":"Yes"},"work_authorization":{"legally_authorized_to_work":"Yes","require_sponsorship":"No"},"availability":{"willing_to_relocate":"No"},"compensation":{"salary_currency":"GBP"}}
    values=application_values(profile,country_code="NZ")
    assert not {"legally_authorized_to_work","require_sponsorship","willing_to_relocate","salary_currency"}&values.keys()
    preferences={"work_authorization":{"legally_authorized_to_work":"Yes","require_sponsorship":"No","permit_expiry_date":"2020-01-01"}}
    assert "require_sponsorship" not in application_values(profile,country_code="NZ",country_preferences=preferences)
    preferences={"work_authorization":{"legally_authorized_to_work":"Yes"}}
    fields=[{"id":"nz","type":"text","label":"Are you authorized to work in New Zealand?"},{"id":"uk","type":"text","label":"Are you authorized to work in the United Kingdom?"},{"id":"condition","type":"text","label":"Do you have unrestricted New Zealand work rights?"}]
    fills=local_fills(profile,fields,country_code="New Zealand",country_preferences=preferences)
    assert len(fills)==1 and fills[0]["key"]=="nz"
    fact=save_fact(nz_workspace[0],nz_workspace[1],kind="disclosure",text="I have a reviewed NZ work-rights disclosure.",country_code="NZ")
    verify(nz_workspace[0],nz_workspace[1],fact)
    assert facts.check(nz_workspace[1],nz_workspace[0],fact["text"],{"country_code":"NZ"})["passed"]
    assert not facts.check(nz_workspace[1],nz_workspace[0],fact["text"],{"country_code":"GB"})["passed"]


def test_country_policies_keep_nz_supporting_evidence_distinct_and_real_nhs_history(nz_workspace):
    from applypilot.scoring.supporting_statement import _build_prompt
    job={"country_code":"NZ","title":"Support Analyst","company":"Fixture NZ","full_description":"Employer requests evidence of incident handling."}
    messages=_build_prompt(variant={"name":"values","guidance":"Open with NHS values in action"},resume_text="Verified prior UK NHS employment remains genuine history.",job=job,criteria=[],profile={})
    assert "New Zealand employer-requested" in messages[0]["content"]
    assert "suitable for NHS / UK" not in messages[1]["content"]
    assert "Verified prior UK NHS employment" in messages[1]["content"]


def test_nz_rename_archive_updates_nested_campaign_only_and_keeps_original(nz_workspace):
    root,conn,cfg=nz_workspace
    from applypilot.discovery import nz
    from test_nz_discovery import context
    master=import_master(root,conn)
    key=master["key"]
    metadata=dict(conn.execute("SELECT * FROM cc_cv_sources WHERE source_key=?",(key,)).fetchone())
    original=Path(metadata["original_path"]).read_bytes()
    campaign=nz.preset()|{"resume_routing":{"variants":{key:"resume_variants/"+key+".txt"},"families":[{"name":"software","variant":key}],"title_routes":{"Software Developer":key},"context_families":[{"variant":key}]}}
    root_before=(root/"searches.yaml").read_bytes()
    nz.save_campaign(conn,root,{"expected_revision":nz.revision(setup.read_searches_dict(root)),"campaign":campaign})
    with country.operation(context(root,setup.read_searches_dict(root),conn)):
        cv_library.action(conn,root,{"action":"rename","key":key,"new_key":"nz_renamed"})
    current=setup.read_searches_dict(root)["campaigns"][0]["resume_routing"]
    assert current["title_routes"]["Software Developer"]=="nz_renamed" and current["families"][0]["variant"]=="nz_renamed"
    assert (root/"searches.yaml").read_bytes().startswith(root_before)
    with country.operation(context(root,setup.read_searches_dict(root),conn)):
        cv_library.action(conn,root,{"action":"archive","key":"nz_renamed"})
    current=setup.read_searches_dict(root)["campaigns"][0]["resume_routing"]
    assert not current["variants"] and not current["title_routes"]
    assert Path(metadata["original_path"]).read_bytes()==original


def test_local_canonical_ui_reviews_fixture_fact_and_reconciles_import_without_uk_rewrite(http_workspace):
    if os.environ.get("APPLYPILOT_RUN_COUNTRY_UI")!="1":pytest.skip("Local country UI disabled")
    from playwright.sync_api import sync_playwright
    base,root,conn=http_workspace
    original=b"# UK root remains intact\nnz_workflow_enabled: true\ncountry: UK\nqueries: [{query: Analyst}]\nlocations: [{location: 'London, UK'}]\n"
    (root/"searches.yaml").write_bytes(original)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();errors=[]
            page.route("**/*",lambda route:route.continue_() if route.request.url.startswith(base+"/") else route.abort())
            page.on("pageerror",lambda exc:errors.append(str(exc)))
            page.on("dialog",lambda dialog:dialog.accept())
            page.goto(base+"/workspace")
            page.wait_for_function("document.querySelector('#job-search')!==null")
            page.locator("#workspace-country").select_option("NZ")
            page.locator("#canonical-fact-form").wait_for(state="attached")
            page.locator('[data-nz-step="nz-documents-stage"]').click()
            page.get_by_text("Add / correct one supported factual assertion",exact=True).click()
            fact={"kind":"personal","text":"UI Canonical Candidate","source_reference":"Synthetic fixture identity","source_quote":"UI Canonical Candidate"}
            page.locator("#canonical-fact-json").fill(json.dumps(fact))
            page.locator("#canonical-fact-form button[type=submit]").click()
            page.locator("[data-canonical-verify]").wait_for()
            page.locator("[data-canonical-verify]").click()
            page.wait_for_function("[...document.querySelectorAll('#canonical-panel td')].some(n=>n.textContent.startsWith('verified'))")
            page.get_by_text("Import an NZ master CV",exact=True).click()
            page.locator("#canonical-import-key").fill("nz_fixture")
            page.locator("#canonical-import-file").set_input_files({"name":"software.txt","mimeType":"text/plain","buffer":b"UI Canonical Candidate"})
            page.locator("#canonical-import-form button[type=submit]").click()
            page.locator('[data-canonical-reconcile="nz_fixture"]').wait_for()
            page.locator('[data-canonical-reconcile="nz_fixture"]').click()
            page.wait_for_function("document.querySelector('#canonical-reconciliation-result')?.textContent.includes('reconciled')")
            assert (root/"searches.yaml").read_bytes()==original
            assert conn.execute("SELECT country_code,category FROM cc_cv_sources WHERE source_key='nz_fixture'").fetchone()[:]==('NZ','software')
            assert not errors
        finally:browser.close()
