"""Manual-only evidence, exact quotes and adversarial provenance/identity cases."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from applypilot import country_context as country, nz_evidence as evidence, source_access as access
from applypilot.discovery import nz, observations as obs
from test_nz_discovery import foundation, nz_workspace, vacancy, context, permitted  # noqa: F401
from test_command_center import http_workspace, workspace  # noqa: F401


def employer(root, conn, **patch):
    return evidence.save_employer(conn,root,{"legal_name":"Fixture Hiring Limited","nzbn":"1234567890123","identity_state":"confirmed","identity_confirmed":True,
        "identity_evidence":{"source_url":"https://employer.example.nz/legal","quote":"Fixture Hiring Limited is the hiring legal employer","observed_at":access.now()}}|patch,actor="fixture_owner")


def observed(item, **patch):
    return {"employer_id":item["id"],"method":"manual_official_check","kind":"accreditation","verification_state":"success","result":"accredited", "source_url":evidence.OFFICIAL_URL,
            "query_type":"nzbn","query":item["nzbn"],"observed_at":access.now(),"quote":"Fixture Hiring Limited — accredited", "observed_legal_name":item["legal_name"], "observed_nzbn":item["nzbn"]}|patch


def manual_job(root,conn,text="",**patch):
    data={"url":"https://employer.example.nz/vacancy?id=123","title":"Developer","location":"Auckland, New Zealand"}|patch
    if text: data|={"reference_only":False,"job_text":text,"storage_allowed":True,"permission_reference":"Synthetic user-owned fixture"}
    nz.capture(conn,root,data)
    return dict(conn.execute("SELECT rowid AS id,* FROM jobs WHERE country_code='NZ' ORDER BY rowid DESC").fetchone())


def linked(root,conn,item,job):
    return evidence.link_job(conn,root,{"id":job["id"],"employer_id":item["id"],"relationship":"hiring_employer","confirmed":True,"source_url":job["url"],"quote":"Hiring employer: "+item["legal_name"]},actor="fixture_owner")


def test_format_and_fuzzy_identity_never_confirm_or_merge(nz_workspace):
    root,conn,_=nz_workspace
    with pytest.raises(ValueError,match="13-digit"):
        employer(root,conn,nzbn="123")
    with pytest.raises(ValueError,match="Confirm"):
        employer(root,conn,identity_confirmed=False)
    item=employer(root,conn,identity_state="ambiguous",identity_confirmed=False)
    assert evidence.reduce(conn,root,item["id"])["evidence_state"]=="identity_ambiguous"
    with pytest.raises(ValueError,match="already belongs"):
        employer(root,conn,legal_name="Another Subsidiary")
    assert conn.execute("SELECT COUNT(*) FROM cc_employers").fetchone()[0]==1
    matches=evidence.suggestions(conn,root,"Fixture Hiring Limite")
    assert matches[0]["match"]=="fuzzy_suggestion_only" and matches[0]["requires_hiring_identity_confirmation"]


@pytest.mark.parametrize("relationship",["recruiter","brand","parent","subsidiary","ambiguous"])
def test_related_entities_cannot_transfer_accreditation(nz_workspace,relationship):
    root,conn,_=nz_workspace
    item=employer(root,conn)
    job=manual_job(root,conn)
    evidence.record(conn,root,observed(item),actor="fixture_owner")
    with pytest.raises(ValueError,match="hiring legal employer"):
        evidence.link_job(conn,root,{"id":job["id"],"employer_id":item["id"],"relationship":relationship,"confirmed":True,"source_url":job["url"],"quote":"Group company"},actor="fixture_owner")
    result=evidence.link_job(conn,root,{"id":job["id"],"employer_id":item["id"],"relationship":relationship,"confirmed":False},actor="fixture_owner")
    assert result["code"]=="unavailable_conflicting" and not result["identity_confirmed"]


def test_no_result_is_unknown_and_does_not_lower_technical_fit(nz_workspace):
    root,conn,_=nz_workspace
    item=employer(root,conn)
    job=manual_job(root,conn)
    conn.execute("UPDATE jobs SET fit_score=9,apply_status='selected'")
    conn.commit()
    linked(root,conn,item,job)
    out=evidence.record(conn,root,observed(item,result="not_listed",quote="No results found for the exact query"),actor="fixture_owner")
    assert out["employer"]["accreditation_result"]=="unknown"
    assert "choose not to appear" in out["employer"]["not_listed_limitation"]
    assert conn.execute("SELECT fit_score,apply_status FROM jobs").fetchone()[:]==(9,"selected")
    assert evidence.state(conn,root)["jobs"][0]["assessment"]["code"]=="unavailable_conflicting"


def test_failed_recheck_does_not_erase_or_extend_last_success(nz_workspace):
    root,conn,_=nz_workspace
    item=employer(root,conn)
    checked=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
    evidence.record(conn,root,observed(item,observed_at=checked),actor="fixture_owner")
    evidence.record(conn,root,observed(item,verification_state="unavailable",result="unknown",quote="",observed_at=access.now()),actor="fixture_owner")
    reduced=evidence.reduce(conn,root,item["id"])
    assert reduced["accreditation_result"]=="accredited" and reduced["evidence_state"]=="fresh"
    assert reduced["last_successful_check"]==checked and reduced["latest_failure"]=="unavailable"
    later=evidence.reduce(conn,root,item["id"],at=datetime.now(timezone.utc)+timedelta(days=2))
    assert later["evidence_state"]=="stale" and later["accreditation_result"]=="unknown" and later["last_successful_check"]==checked
    reminder=evidence.state(conn,root)
    assert reminder["official_access"]=="manual_only"


def test_expired_artifact_source_is_not_expired_accreditation(nz_workspace):
    root,conn,_=nz_workspace
    item=employer(root,conn)
    yesterday=(datetime.now(timezone.utc)-timedelta(days=1)).date().isoformat()
    evidence.record(conn,root,observed(item,source_expires_at=yesterday),actor="fixture_owner")
    state=evidence.reduce(conn,root,item["id"])
    assert state["evidence_state"]=="source_expired" and state["accreditation_result"]=="unknown"
    other=employer(root,conn,legal_name="Other Hiring Limited",nzbn="2234567890123")
    evidence.record(conn,root,observed(other,result="expired",quote="Accreditation status: expired"),actor="fixture_owner")
    state=evidence.reduce(conn,root,other["id"])
    assert state["evidence_state"]=="fresh" and state["accreditation_result"]=="expired"


def test_conflicting_official_records_require_explicit_resolution(nz_workspace):
    root,conn,_=nz_workspace
    item=employer(root,conn)
    first=evidence.record(conn,root,observed(item),actor="fixture_owner")["evidence_id"]
    second=evidence.record(conn,root,observed(item,result="revoked",quote="Accreditation revoked"),actor="fixture_owner")["evidence_id"]
    assert evidence.reduce(conn,root,item["id"])["evidence_state"]=="conflicting"
    with pytest.raises(ValueError,match="override official"):
        evidence.record(conn,root,observed(item,method="user_note",supersedes_ids=[second],resolution_note="Prefer positive"),actor="fixture_owner")
    evidence.record(conn,root,observed(item,result="revoked",quote="Current official result: revoked",supersedes_ids=[first,second],resolution_note="Fresh manual official check resolves earlier contradictory observations"),actor="fixture_owner")
    assert evidence.reduce(conn,root,item["id"])["accreditation_result"]=="revoked"
    assert len(evidence.history(conn,root,item["id"]))==3


def test_employer_assertion_is_not_official_but_supports_review_only(nz_workspace):
    root,conn,_=nz_workspace
    item=employer(root,conn)
    job=manual_job(root,conn)
    linked(root,conn,item,job)
    evidence.record(conn,root,observed(item,method="employer_supplied",source_url="https://employer.example.nz/letter",quote="We state that we are accredited"),actor="fixture_owner")
    reduced=evidence.reduce(conn,root,item["id"])
    assert reduced["accreditation_result"]=="unknown" and reduced["last_successful_check"] is None
    assert evidence.state(conn,root)["jobs"][0]["assessment"]["code"]=="potential_review"


@pytest.mark.parametrize("patch",[
    {"observed_at":"2999-01-01T00:00:00Z"}, {"observed_at":"2026-10-01"},
    {"query":"9994567890123"}, {"query_type":"legal_name","query":"Unrelated Parent"},
    {"observed_nzbn":"9994567890123"}, {"observed_legal_name":"Other Subsidiary"},
    {"source_url":"https://employer.example.nz/pretend-official"},
    {"method":"authorised_feed"}, {"verification_state":"unavailable","result":"accredited"},
    {"quote":""}, {"source_effective_at":"2999-01-01"}])
def test_bad_time_identity_query_or_provenance_cannot_become_evidence(nz_workspace,patch):
    root,conn,_=nz_workspace
    item=employer(root,conn)
    with pytest.raises(ValueError):
        evidence.record(conn,root,observed(item,**patch),actor="fixture_owner")
    assert conn.execute("SELECT COUNT(*) FROM cc_employer_evidence").fetchone()[0]==0


def test_observations_immutable_actor_owned_artifacts_and_tamper_detection(nz_workspace):
    root,conn,_=nz_workspace
    item=employer(root,conn)
    out=evidence.record(conn,root,observed(item,artifact={"name":"../../redacted.txt","b64":base64.b64encode(b"Minimal permitted excerpt").decode(),"storage_allowed":True}),actor="fixture_owner")
    ident=out["evidence_id"]
    row=evidence.history(conn,root,item["id"])[0]
    assert row["observation"]["actor"]=="fixture_owner" and row["artifact_integrity"]
    assert row["observation"]["artifact"]["path"].startswith("evidence/nz/")
    assert evidence.artifact(conn,root,ident)[0]==b"Minimal permitted excerpt"
    for sql in ("UPDATE cc_employer_evidence SET result='revoked'", "DELETE FROM cc_employer_evidence"):
        with pytest.raises(sqlite3.IntegrityError): conn.execute(sql)
        conn.rollback()
    (root/row["observation"]["artifact"]["path"]).write_bytes(b"Changed")
    assert evidence.reduce(conn,root,item["id"])["accreditation_result"]=="unknown"
    with pytest.raises(ValueError,match="changed"):
        evidence.artifact(conn,root,ident)


@pytest.mark.parametrize("text,code",[
    ("Overseas applicants are welcome for this New Zealand role.","explicit_support"),
    ("We offer visa sponsorship for this New Zealand role.","explicit_support"),
    ("Visa sponsorship may be available for this New Zealand role, subject to eligibility.","potential_review"),
    ("Applicants must have existing unrestricted NZ work rights.","explicit_restriction"),
    ("Applicants must already have full work rights in New Zealand.","explicit_restriction"),
    ("No visa sponsorship is available for this NZ role.","explicit_restriction"),
    ("Overseas applicants are welcome for this NZ role. No visa sponsorship is available for this NZ role.","unavailable_conflicting"),
    ("We do not offer visa sponsorship for this NZ role.","explicit_restriction"),
    ("You do not need existing NZ work rights to express interest.","unavailable_conflicting"),
    ("No need for existing NZ work rights to express interest.","unavailable_conflicting"),
    ("We offer visa sponsorship for our Australian roles.","unavailable_conflicting"),
    ("As a group we offer visa sponsorship globally.","potential_review"),
    ("Python, SQL and customer support experience.","unavailable_conflicting"),
    ("We are an accredited employer in New Zealand.","unavailable_conflicting")])
def test_literal_recruitment_rules_negation_conditions_jurisdiction_and_conflict(nz_workspace,text,code):
    root,conn,_=nz_workspace
    job=manual_job(root,conn,text)
    assessment=evidence.assess_job(conn,root,job)
    assert assessment["code"]==code,assessment
    assert not assessment["technical_fit_affected"] and assessment["candidate_work_rights"]=="not_assessed"
    for claim in assessment["claims"]:
        assert claim["quote"] in text
        assert claim["source_hash"]==hashlib.sha256(text.encode()).hexdigest()
        assert claim["rule_version"]=="nz-recruitment-v1"


def test_stale_advert_and_identity_change_never_become_current_support(nz_workspace):
    root,conn,_=nz_workspace
    job=manual_job(root,conn,"Overseas applicants are welcome for this NZ role.")
    assert evidence.assess_job(conn,root,job,at=datetime.now(timezone.utc)+timedelta(days=8))["code"]=="unavailable_conflicting"
    item=employer(root,conn)
    assert linked(root,conn,item,job)["code"]=="explicit_support"
    evidence.save_employer(conn,root,{"id":item["id"],"expected_revision":item["revision"],"legal_name":item["legal_name"],"nzbn":item["nzbn"],"identity_state":"confirmed","identity_confirmed":True,"identity_evidence":item["identity_evidence"]},actor="fixture_owner")
    assert evidence.state(conn,root)["jobs"][0]["assessment"]["code"]=="unavailable_conflicting"


def test_official_accreditation_is_only_potential_and_adverse_blocks_assertions(nz_workspace):
    root,conn,_=nz_workspace
    item=employer(root,conn)
    job=manual_job(root,conn)
    linked(root,conn,item,job)
    first=evidence.record(conn,root,observed(item),actor="fixture_owner")["evidence_id"]
    assert evidence.state(conn,root)["jobs"][0]["assessment"]["code"]=="potential_review"
    evidence.record(conn,root,observed(item,result="revoked",quote="Revoked",supersedes_ids=[first],resolution_note="Current official adverse check"),actor="fixture_owner")
    job=manual_job(root,conn,"Overseas applicants are welcome for this NZ role.")
    assert evidence.assess_job(conn,root,job)["code"]=="unavailable_conflicting"


def test_manual_review_does_not_relabel_facts_or_change_fit(nz_workspace):
    root,conn,_=nz_workspace
    job=manual_job(root,conn,"No visa sponsorship is available for this NZ role.")
    before=evidence.assess_job(conn,root,job)["code"]
    out=evidence.review(conn,root,{"id":job["id"],"decision":"reviewed_continue","note":"I have separately reviewed my own rights"},actor="fixture_owner")
    assert out["assessment"]["code"]==before=="explicit_restriction"
    assert conn.execute("SELECT fit_score,apply_status FROM jobs").fetchone()[:]==(None,None)


def test_failed_observation_cannot_retract_success_or_import_uk_rights(nz_workspace):
    root,conn,cfg=nz_workspace
    item=employer(root,conn)
    ident=evidence.record(conn,root,observed(item),actor="fixture_owner")["evidence_id"]
    with pytest.raises(ValueError,match="failed check"):
        evidence.record(conn,root,observed(item,result="unknown",verification_state="unavailable",supersedes_ids=[ident],resolution_note="Network failure"),actor="fixture_owner")
    assert evidence.state(conn,root)["candidate_disclosures"]=={}
    country.save_preferences(conn,"NZ",{"work_authorization":{"require_sponsorship":"Unknown"}},0,cfg)
    assert evidence.state(conn,root)["candidate_disclosures"]=={"require_sponsorship":"Unknown"}


def test_second_candidate_cannot_access_first_candidates_artifact_or_identity(nz_workspace,tmp_path):
    root,conn,cfg=nz_workspace
    item=employer(root,conn)
    ident=evidence.record(conn,root,observed(item),actor="fixture_owner")["evidence_id"]
    from applypilot.database import init_db,close_connection
    other=root/"other_candidate"
    other.mkdir()
    (other/"searches.yaml").write_text(yaml.safe_dump(cfg),encoding="utf-8")
    second=init_db(other/"applypilot.db")
    try:
        assert evidence.state(second,other)["employers"]==[]
        with pytest.raises(LookupError):evidence.history(second,other,item["id"])
        with pytest.raises(ValueError):evidence.artifact(second,other,ident)
    finally:close_connection(other/"applypilot.db")


def test_country_workspace_and_authenticated_actor_are_server_enforced(nz_workspace):
    root,conn,cfg=nz_workspace
    from applypilot import command_api
    item=employer(root,conn)
    handler=SimpleNamespace(_send_json=Mock(),_current_user=lambda:{"username":"actual_owner"})
    command_api.post(handler,"/api/center/nz/evidence/record",observed(item)|{"country_code":"NZ","actor":"forged_admin"},root,None)
    assert handler._send_json.call_args.args[0]==200
    assert evidence.history(conn,root,item["id"])[0]["observation"]["actor"]=="actual_owner"
    command_api.get(handler,"/api/center/nz/evidence","country_code=GB",root)
    assert handler._send_json.call_args.args[0]==400
    conn.execute("INSERT INTO jobs(url,title,country_code) VALUES('https://uk.example/job','UK','GB')")
    conn.commit()
    ident=conn.execute("SELECT rowid FROM jobs WHERE country_code='GB'").fetchone()[0]
    with pytest.raises(ValueError,match="NZ vacancy"):
        evidence.link_job(conn,root,{"id":ident,"employer_id":item["id"]},actor="fixture_owner")
    with country.operation(country.resolve(root,country_code="GB",config=cfg)), pytest.raises(ValueError,match="outside"):
        evidence.state(conn,root)


def test_local_evidence_ui_records_identity_and_manual_not_listed(http_workspace):
    if os.environ.get("APPLYPILOT_RUN_COUNTRY_UI")!="1": pytest.skip("Local-only country UI suite disabled")
    from playwright.sync_api import sync_playwright
    base,root,conn=http_workspace
    (root/"searches.yaml").write_text("nz_workflow_enabled: true\ncountry: UK\nqueries: [{query: Analyst}]\nlocations: [{location: 'London, UK'}]\n",encoding="utf-8")
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page()
            external=[]
            def route(request):
                if request.request.url.startswith(base+"/"): request.continue_()
                else: external.append(request.request.url); request.abort()
            page.route("**/*",route)
            page.goto(base+"/workspace")
            page.wait_for_function("document.querySelector('#job-search')!==null")
            page.locator("#workspace-country").select_option("NZ")
            page.locator("#nz-employer-form").wait_for(state="attached")
            page.locator("#nz-employer-checks > summary").click()
            page.get_by_text("Record / correct a legal employer identity",exact=True).click()
            page.locator("#nz-employer-name").fill("UI Hiring Limited")
            page.locator("#nz-employer-nzbn").fill("1234567890123")
            page.locator("#nz-identity-state").select_option("confirmed")
            page.locator("#nz-identity-source").fill("https://employer.example.nz/legal")
            page.locator("#nz-identity-quote").fill("UI Hiring Limited is the legal hiring employer")
            page.locator("#nz-identity-time").fill(access.now())
            page.locator("#nz-identity-confirmed").check()
            page.locator("#nz-employer-form button[type=submit]").click()
            page.wait_for_function("[...document.querySelectorAll('#nz-check-employer option')].some(o=>o.textContent.includes('UI Hiring Limited'))")
            page.get_by_text("Record a manual official check or employer-supplied evidence",exact=True).click()
            ident=conn.execute("SELECT id FROM cc_employers").fetchone()[0]
            page.locator("#nz-check-employer").select_option(ident)
            page.locator("#nz-check-time").fill(access.now())
            page.locator("#nz-check-result").select_option("not_listed")
            page.locator("#nz-check-quote").fill("No public results found for this NZBN")
            page.locator("#nz-evidence-form button[type=submit]").click()
            page.wait_for_function("document.querySelector('#toast').textContent.includes('Evidence appended')")
            assert evidence.reduce(conn,root,ident)["accreditation_result"]=="unknown"
            assert not any("immigration.govt.nz" in url for url in external)
        finally: browser.close()
