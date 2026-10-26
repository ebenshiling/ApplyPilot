"""Owned non-network transaction counters; these are not live ATS acceptance."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import timedelta
import json

import pytest

from applypilot.apply import authority as a
from applypilot import document_review,database as db,country_context as country,canonical
from test_command_center import workspace  # noqa: F401
from test_application_packet import seed

ACTOR="fixture_candidate"
SESSION="fixture_authenticated_session"
TARGET="https://ats.applypilot.invalid/application/fixture"


def setup_review(root,conn):
    job=seed(root,conn)
    conn.execute("UPDATE jobs SET application_url=? WHERE rowid=1",(TARGET,));conn.commit()
    from document_fixtures import attest_text
    attest_text(conn,root,"Example Candidate")
    value=document_review.context(conn,root,1)
    document_review.approve(conn,root,{"id":1,"country_code":"GB","human_reviewed":True,"expected_review_token":value["review_token"]},actor=ACTOR)
    return a.SyntheticAdapter(TARGET,{"candidate_name":"Example Candidate","consent":True})


def issued(root,conn,adapter):
    review=a.review(conn,root,1,actor=ACTOR,session_binding=SESSION,country_code="GB")
    assert adapter.transactions==0
    token=a.approve(conn,root,review["review_id"],actor=ACTOR,session_binding=SESSION,expected_review_hash=review["review_hash"],human_approved=True)
    return review,token


def test_no_transaction_before_fresh_approval_and_single_consume_afterward(workspace):
    root,conn=workspace;adapter=setup_review(root,conn)
    with a.synthetic_scope(adapter):
        review=a.review(conn,root,1,actor=ACTOR,session_binding=SESSION)
        with pytest.raises(ValueError):a.execute(conn,root,review["review_id"],"forged",actor=ACTOR,session_binding=SESSION)
        assert adapter.transactions==0
        token=a.approve(conn,root,review["review_id"],actor=ACTOR,session_binding=SESSION,expected_review_hash=review["review_hash"],human_approved=True)
        stored=conn.execute("SELECT approval_json FROM cc_browser_sessions WHERE id=?",(review["review_id"],)).fetchone()[0]
        assert token["approval_token"] not in stored
        assert token["approval_token"] not in " ".join(r[0] for r in conn.execute("SELECT payload FROM cc_events"))
        result=a.execute(conn,root,review["review_id"],token["approval_token"],actor=ACTOR,session_binding=SESSION)
        assert result["submitted"] and result["synthetic"] and adapter.transactions==1
        with pytest.raises(ValueError):a.execute(conn,root,review["review_id"],token["approval_token"],actor=ACTOR,session_binding=SESSION)
        assert adapter.transactions==1


@pytest.mark.parametrize("case",["actor","session","forged","expired","fields","pdf","restart"])
def test_wrong_or_changed_context_never_actuates(workspace,monkeypatch,case):
    root,conn=workspace;adapter=setup_review(root,conn)
    with a.synthetic_scope(adapter):
        review,token=issued(root,conn,adapter)
        actor,session,cap=ACTOR,SESSION,token["approval_token"]
        if case=="actor":actor="other_actor"
        if case=="session":session="logged_out_or_other_session"
        if case=="forged":cap="forged_capability"
        if case=="expired":
            original=a.clock();monkeypatch.setattr(a,"clock",lambda:original+timedelta(minutes=6))
        if case=="fields":adapter.change("candidate_name","Changed after review")
        if case=="pdf":
            from pathlib import Path
            path=Path(conn.execute("SELECT tailored_resume_path FROM jobs WHERE rowid=1").fetchone()[0]).with_suffix(".pdf")
            path.write_bytes(path.read_bytes()+b"Changed")
        if case=="restart":monkeypatch.setattr(a,"BOOT_ID","different_server_instance")
        with pytest.raises(ValueError):a.execute(conn,root,review["review_id"],cap,actor=actor,session_binding=session)
        assert adapter.transactions==0 and conn.execute("SELECT applied_at FROM jobs WHERE rowid=1").fetchone()[0] is None


@pytest.mark.parametrize("action",["pointer_submit","enter","iframe_submit","popup","fetch","evaluate_js","raw_cdp","mcp","network_post"])
def test_uncontrolled_actions_do_not_reach_owned_actuator(workspace,action):
    root,conn=workspace;adapter=setup_review(root,conn)
    with pytest.raises(ValueError):adapter.action(action)
    assert adapter.transactions==0


@pytest.mark.parametrize("failure",["before_dispatch","lost_receipt","crash_after_dispatch"])
def test_consumed_interrupted_attempt_is_uncertain_and_never_replayed(workspace,failure):
    root,conn=workspace;adapter=setup_review(root,conn);adapter.failure=failure
    with a.synthetic_scope(adapter):
        review,token=issued(root,conn,adapter)
        if failure=="crash_after_dispatch":
            with pytest.raises(SystemExit):a.execute(conn,root,review["review_id"],token["approval_token"],actor=ACTOR,session_binding=SESSION)
            assert conn.execute("SELECT apply_status FROM jobs WHERE rowid=1").fetchone()[0]=="submitting"
            a.recover(conn)
        else:
            result=a.execute(conn,root,review["review_id"],token["approval_token"],actor=ACTOR,session_binding=SESSION)
            assert not result["submitted"] and not result["retry_allowed"]
        assert conn.execute("SELECT apply_status FROM jobs WHERE rowid=1").fetchone()[0]=="submission_uncertain"
        before=adapter.transactions
        with pytest.raises(ValueError):a.execute(conn,root,review["review_id"],token["approval_token"],actor=ACTOR,session_binding=SESSION)
        assert adapter.transactions==before


def test_concurrent_consumers_cannot_submit_twice(workspace):
    root,conn=workspace;adapter=setup_review(root,conn)
    with a.synthetic_scope(adapter):
        review,token=issued(root,conn,adapter)
        def worker():
            handle=db.get_connection(root/"applypilot.db")
            try:
                try:return a.execute(handle,root,review["review_id"],token["approval_token"],actor=ACTOR,session_binding=SESSION)
                except ValueError:return {"rejected":True}
            finally:db.close_connection(root/"applypilot.db")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(copy_context().run,worker) for _ in range(2)]
            values=[f.result(timeout=20) for f in futures]
        assert adapter.transactions==1 and sum(v.get("submitted",False) for v in values)==1


def test_all_real_targets_remain_manual_even_with_document_approval(workspace):
    root,conn=workspace;setup_review(root,conn)
    conn.execute("UPDATE jobs SET application_url='https://employer.example.org/apply?token=keep' WHERE rowid=1");conn.commit()
    result=a.review(conn,root,1,actor=ACTOR,session_binding=SESSION)
    assert result["mode"]=="assisted_browser" and result["supported"] and "token=keep" in result["target"]
    with pytest.raises(ValueError):a.SyntheticAdapter(result["target"])
    assert "submit" not in a.MODEL_TOOLS and "approve" not in a.MODEL_TOOLS and "evaluate_js" not in a.MODEL_TOOLS


def test_new_review_supersedes_old_capability_and_wrong_country_is_rejected(workspace):
    root,conn=workspace;adapter=setup_review(root,conn)
    with a.synthetic_scope(adapter):
        old,token=issued(root,conn,adapter)
        new=a.review(conn,root,1,actor=ACTOR,session_binding=SESSION)
        assert new["review_id"]!=old["review_id"]
        with pytest.raises(ValueError):a.execute(conn,root,old["review_id"],token["approval_token"],actor=ACTOR,session_binding=SESSION)
        with pytest.raises(country.CountryError):a.review(conn,root,1,actor=ACTOR,session_binding=SESSION,country_code="NZ")
        assert adapter.transactions==0


@pytest.mark.parametrize("change",[{"consent":False},{"candidate_name":"Invented identity"},{"hidden_request":"unexpected mutation"},{"answers":{"Unreviewed visa declaration":"Yes"}}])
def test_incomplete_identity_consent_or_answers_cannot_be_approved(workspace,change):
    root,conn=workspace;adapter=setup_review(root,conn)
    adapter.fields.update(change)
    with a.synthetic_scope(adapter),pytest.raises(ValueError):
        a.review(conn,root,1,actor=ACTOR,session_binding=SESSION)
    assert adapter.transactions==0


def test_model_proposal_does_not_change_fields_or_authorize_submit(workspace):
    root,conn=workspace;adapter=setup_review(root,conn)
    before=adapter.snapshot()
    proposal=adapter.action("propose_field_value",field="consent",value=True)
    assert proposal["requires_human_review"] and not proposal["mutated"] and adapter.snapshot()==before
    inspection=adapter.action("inspect")
    assert "candidate_name" in inspection["field_keys"] and "Example Candidate" not in json.dumps(inspection)
    with pytest.raises(ValueError):adapter._commit(object(),"forged",canonical.digest(before))
    assert adapter.transactions==0
