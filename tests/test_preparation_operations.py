"""Fixture-only request accounting, restart holds and NZ technical separation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
import yaml

from applypilot import operations as ops,writing_studio as studio,country_context as country,config,database as db
from applypilot import preparation,setup_workspace as setup
from test_native_studios import workspace,draft_data  # noqa: F401
from test_llm import mocked_client  # noqa: F401
from test_nz_discovery import foundation,nz_workspace,context  # noqa: F401
from test_canonical_finalisation import save_fact,verify,import_master


def test_identical_concurrent_studio_generation_reuses_one_lease_and_result(workspace,monkeypatch):
    root,conn=workspace
    from applypilot import ai_routing,llm
    from applypilot.scoring import cover_letter
    entered,release=threading.Event(),threading.Event()
    calls=[]
    monkeypatch.setattr(ai_routing,"route_info",lambda *a:{"configured":True,"provider":"openai","model":"fixture"})
    monkeypatch.setattr(llm,"get_client",lambda:SimpleNamespace(last_model="fixture"))
    def generate(*a,**kw):
        calls.append(1);entered.set();assert release.wait(10)
        return draft_data()["text"]
    monkeypatch.setattr(cover_letter,"generate_cover_letter",generate)
    def worker():
        handle=db.get_connection(root/"applypilot.db")
        try:return studio.generate(handle,root,draft_data())
        finally:db.close_connection(root/"applypilot.db")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(worker)
        assert entered.wait(10)
        pending=pool.submit(worker).result(timeout=10)
        assert pending["pending"] and pending["state"]=="in_progress" and calls==[1]
        release.set();result=first.result(timeout=10)
    assert pending["request_id"]==result["request_id"]
    cached=studio.generate(conn,root,draft_data())
    assert cached["cached"] and cached["document"]["id"]==result["document"]["id"]
    assert conn.execute("SELECT COUNT(*) FROM cc_writing_requests").fetchone()[0]==1


def test_failed_and_expired_requests_never_automatically_replay(workspace):
    root,conn=workspace
    record=ops.reserve(conn,root,"cover","same_input","GB",origin="studio")
    ops.fail(conn,record)
    again=ops.reserve(conn,root,"cover","same_input","GB",origin="studio")
    assert not again["new"] and again["state"]=="failed"
    with pytest.raises(ops.OperationHeld):ops.cached(again)
    fresh=ops.reserve(conn,root,"cover","same_input","GB",origin="studio",fresh=True,request_key="explicit_retry_fixture")
    conn.execute("UPDATE cc_writing_requests SET lease_until=? WHERE id=?",((datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat(),fresh["id"]))
    conn.commit()
    expired=ops.reserve(conn,root,"cover","same_input","GB",origin="studio",fresh=True,request_key="explicit_retry_fixture")
    assert expired["state"]=="needs_review" and not expired["new"]
    assert conn.execute("SELECT COUNT(*) FROM cc_writing_requests").fetchone()[0]==2


def test_country_does_not_reset_shared_workspace_budget(nz_workspace):
    root,conn,cfg=nz_workspace
    (root/"searches.yaml").write_text(yaml.safe_dump(cfg|{"preparation_daily_budget":1}),encoding="utf-8")
    first=ops.reserve(conn,root,"score","one","GB")
    ops.complete(conn,first,{"score":8})
    with pytest.raises(ops.OperationHeld,match="budget"):
        ops.reserve(conn,root,"score","two","NZ")
    with pytest.raises(ValueError,match="country"):
        ops.status(conn,first["id"],"NZ")


def test_actual_provider_attempt_caps_and_unknown_failed_usage_are_preserved(nz_workspace,mocked_client,monkeypatch):
    root,conn,cfg=nz_workspace
    from applypilot import llm
    monkeypatch.setattr(llm.time,"sleep",lambda _:None)
    monkeypatch.setattr(llm,"_MAX_RETRIES",2)
    attempts=[]
    def respond(request):
        attempts.append(1)
        return httpx.Response(503,json={"error":{"message":"temporary"}}) if len(attempts)==1 else httpx.Response(200,json={"choices":[{"message":{"content":"Fixture response"}}],"usage":{"prompt_tokens":5,"completion_tokens":2}})
    client=mocked_client("gpt-4.1-mini",respond)
    (root/"searches.yaml").write_text(yaml.safe_dump(cfg|{"ai_request_limits":{"max_requests_per_operation":2,"max_transport_requests_per_day":2}}),encoding="utf-8")
    record=ops.reserve(conn,root,"cover","request_fixture","GB")
    with ops.scope(root,record):
        assert client.chat([{"role":"user","content":"Fixture only"}],max_tokens=100)=="Fixture response"
        from applypilot.pipeline_store import record_usage
        record_usage(client)
        with pytest.raises(ops.OperationHeld,match="cap"):
            client.chat([{"role":"user","content":"Must not be sent"}],max_tokens=100)
    assert len(attempts)==2
    rows=conn.execute("SELECT input_tokens,output_tokens,country_code FROM cc_pipeline_usage ORDER BY id").fetchall()
    assert [r[:] for r in rows]==[(None,None,"GB"),(5,2,"GB")]
    assert conn.execute("SELECT transport_attempts FROM cc_writing_requests WHERE id=?",(record["id"],)).fetchone()[0]==2
    ops.complete(conn,record,"done")
    next_record=ops.reserve(conn,root,"cover","other_country_fixture","NZ")
    with ops.scope(root,next_record),pytest.raises(ops.OperationHeld,match="Workspace transport budget"):
        client.chat([{"role":"user","content":"No NZ budget reset"}],max_tokens=100)
    assert len(attempts)==2


def test_token_and_input_limits_fail_before_any_provider_attempt(nz_workspace,mocked_client):
    root,conn,cfg=nz_workspace
    attempts=[]
    client=mocked_client("gpt-4.1-mini",lambda request:attempts.append(request) or httpx.Response(200))
    (root/"searches.yaml").write_text(yaml.safe_dump(cfg|{"ai_request_limits":{"max_prompt_characters":1000,"max_output_tokens_per_call":128}}),encoding="utf-8")
    record=ops.reserve(conn,root,"cover","limit_fixture","GB")
    with ops.scope(root,record):
        with pytest.raises(ops.OperationHeld,match="input/output"):
            client.chat([{"role":"user","content":"x"*2000}],max_tokens=100)
        with pytest.raises(ops.OperationHeld,match="input/output"):
            client.chat([{"role":"user","content":"bounded"}],max_tokens=1000)
    assert not attempts and conn.execute("SELECT transport_attempts FROM cc_writing_requests WHERE id=?",(record["id"],)).fetchone()[0]==0


def ready_workspace(root,conn):
    from applypilot.nz_documents import reconcile
    fact=save_fact(root,conn,text="Fixture Employer used Python for reporting.")
    verify(root,conn,fact)
    for category in ("software","data_bi","support"):
        item=import_master(root,conn,category=category,key="nz_"+category,text=fact["text"])
        assert reconcile(conn,root,item["key"])["status"]=="reconciled"


def captured_job(root,conn,rights="No visa sponsorship is available.",ai=True):
    from applypilot.discovery import nz
    nz.capture(conn,root,{"url":"https://employer.example.nz/job?id=1","title":"Python Developer","company":"Fixture Employer","location":"Auckland, New Zealand","reference_only":False,"job_text":("Python API development and SQL reporting are required. "*8)+rights,"storage_allowed":True,"ai_use_allowed":ai,"permission_reference":"Synthetic user-owned fixture"})
    return dict(conn.execute("SELECT rowid AS id,* FROM jobs WHERE country_code='NZ'").fetchone())


def test_nz_technical_score_inputs_and_cache_do_not_change_with_recruitment_evidence(nz_workspace,monkeypatch):
    root,conn,cfg=nz_workspace
    ready_workspace(root,conn)
    from applypilot.scoring import scorer
    from applypilot.role_routing import score_fingerprint
    messages=[]
    def respond(body,**kwargs):
        messages.append(body)
        return '{"score":8,"keywords":["Python"],"reasoning":"Synthetic technical fixture","confidence":0.9}'
    monkeypatch.setattr(scorer,"chat_json",respond)
    job=captured_job(root,conn)
    with country.operation(context(root,cfg,conn)):
        first_hash=score_fingerprint(job,root)
        first=scorer.score_job("Fixture Employer used Python for reporting.",job)
    updated=captured_job(root,conn,rights="Overseas applicants are welcome. We are an accredited employer.")
    with country.operation(context(root,cfg,conn)):
        assert score_fingerprint(updated,root)==first_hash
        second=scorer.score_job("Fixture Employer used Python for reporting.",updated)
    assert first["score"]==second["score"]==8 and len(messages)==1
    user=messages[0][1]["content"].casefold()
    assert "visa" not in user and "accredited" not in user and "overseas" not in user


def test_nz_ai_use_denied_even_with_all_masters_reconciled(nz_workspace,monkeypatch):
    root,conn,cfg=nz_workspace
    ready_workspace(root,conn)
    job=captured_job(root,conn,ai=False)
    from applypilot.scoring import scorer
    model=Mock(side_effect=AssertionError("No source AI permission"))
    monkeypatch.setattr(scorer,"chat_json",model)
    with country.operation(context(root,cfg,conn)),pytest.raises(country.CountryError,match="AI-use permission"):
        scorer.score_job("Verified source",job)
    model.assert_not_called()


def test_nz_model_profile_projection_keeps_shared_history_but_not_uk_disclosures(nz_workspace):
    root,conn,cfg=nz_workspace
    ready_workspace(root,conn)
    job=captured_job(root,conn)
    @country.preparation_task
    def inspect_profile(job,profile):return profile
    profile={"personal":{"full_name":"Fixture","legally_authorized_to_work":"Yes","willing_to_relocate":"No"},"work_authorization":{"legally_authorized_to_work":"Yes"},"resume_sections":{"summary":["Genuine shared UK history"]}}
    with country.operation(context(root,cfg,conn)):
        output=inspect_profile(job,profile)
    assert output["work_authorization"]=={} and "legally_authorized_to_work" not in output["personal"]
    assert output["resume_sections"]==profile["resume_sections"]
