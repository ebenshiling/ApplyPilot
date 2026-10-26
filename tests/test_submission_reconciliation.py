import os
from datetime import datetime,timezone,timedelta

import httpx
import pytest

from applypilot import command_center as cc
from applypilot import submission_reconciliation as reconciliation
from applypilot.apply import assisted_browser as ab
from test_command_center import workspace,http_workspace  # noqa: F401, F811
from test_assisted_browser import packet  # noqa: F401


def uncertain(root,conn):
    packet(root,conn)
    conn.execute("UPDATE jobs SET apply_status='submission_uncertain',apply_error=? WHERE rowid=1",('Submit was attempted but confirmation was not detected',))
    ab._schema(conn)
    url=cc.get_job(conn,1)['url']
    conn.execute("INSERT INTO cc_browser_sessions(id,job_url,state,detail,created_at,updated_at) VALUES('attempt_fixture',?,'uncertain','No verified receipt',?,?)",(url,cc.now(),cc.now()))
    cc.event(conn,url,'approval','Approved once',state='approved',source='candidate')
    conn.commit()


def data(root,conn,outcome='submitted',**changes):
    return {'id':1,'outcome':outcome,'source':'employer_portal','evidence':'My applications shows the checked status','reference':'REF-100','checked':True,'reconciliation_token':reconciliation.context(conn,root,1)['reconciliation_token'],**changes}


def test_confirmed_submitted_sets_outcome_reference_and_preserves_history(workspace):
    root,conn=workspace
    uncertain(root,conn)
    date=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    out=reconciliation.reconcile(conn,root,data(root,conn,submitted_at=date,evidence='Portal shows Submitted, REF-100'))
    assert out=={'outcome':'submitted','locked':False,'retry_started':False}
    job=cc.get_job(conn,1)
    assert job['apply_status']=='applied' and job['applied_at']==date and not job['apply_error']
    meta=conn.execute('SELECT * FROM cc_job_meta').fetchone()
    assert meta['hiring_status']=='submitted' and meta['reference']=='REF-100' and meta['document_id']
    assert meta['submission_method']=='candidate_reconciled'
    assert conn.execute("SELECT COUNT(*) FROM cc_events WHERE kind='approval' AND state='approved'").fetchone()[0]==1
    assert conn.execute("SELECT COUNT(*) FROM cc_submission_reconciliations WHERE outcome='submitted'").fetchone()[0]==1


def test_confirmed_not_submitted_unblocks_only_after_explicit_check(workspace):
    root,conn=workspace
    uncertain(root,conn)
    out=reconciliation.reconcile(conn,root,data(root,conn,'not_submitted',evidence='Employer portal shows Draft, not submitted'))
    assert not out['retry_started'] and not out['locked']
    job=cc.get_job(conn,1)
    assert job['apply_status']=='prepared' and not job['applied_at']
    from applypilot.apply.application_packet import status
    assert status(conn,root,1)['ready']  # Explicitly finalized fixture documents remain current.
    assert conn.execute("SELECT state FROM cc_browser_sessions").fetchone()[0]=='uncertain'


def test_unknown_keeps_retry_lock_and_records_check(workspace):
    root,conn=workspace
    uncertain(root,conn)
    out=reconciliation.reconcile(conn,root,data(root,conn,'unknown',checked=False,evidence='Portal unavailable; contacted recruiter'))
    assert out['locked'] and cc.get_job(conn,1)['apply_status']=='submission_uncertain'
    # The retry lock remains authoritative after application execution is released.
    with pytest.raises(ValueError,match='not ready|reconcile'):
        ab.start(conn,root,{'id':1,'documents_approved':True})
    assert cc.get_job(conn,1)['apply_status']=='submission_uncertain'


@pytest.mark.parametrize('changes',[
    {'evidence':''},{'source':'guess'},{'checked':False},{'outcome':'not_submitted','source':'confirmation_email','evidence':'No email yet'},
    {'submitted_at':'2026-01-01T10:00:00'},{'submitted_at':'2099-01-01T00:00:00+00:00'},
])
def test_inadequate_evidence_and_bad_dates_do_not_clear_uncertainty(workspace,changes):
    root,conn=workspace
    uncertain(root,conn)
    with pytest.raises(ValueError):
        reconciliation.reconcile(conn,root,data(root,conn,**changes))
    assert cc.get_job(conn,1)['apply_status']=='submission_uncertain' and not cc.get_job(conn,1)['applied_at']


def test_stale_attempt_and_general_failure_cannot_bypass_reconciliation(workspace):
    root,conn=workspace
    uncertain(root,conn)
    old=data(root,conn)
    conn.execute("UPDATE cc_browser_sessions SET updated_at='changed' WHERE id='attempt_fixture'")
    conn.commit()
    with pytest.raises(ValueError,match='attempt changed'):
        reconciliation.reconcile(conn,root,old)
    with pytest.raises(ValueError,match='Reconcile submission'):
        cc.update_job(conn,root,1,'failed',{'reason':'I want to retry'})
    with pytest.raises(ValueError,match='receipt'):
        cc.update_job(conn,root,1,'applied',{})


def test_uncertain_message_has_correct_recovery_category():
    result=cc.classify_failure('Submit was attempted but confirmation was not detected. Check the employer portal/email before retrying; no automatic resubmission.')
    assert result['category']=='uncertain' and not result['retryable']


def test_reconciliation_http_and_saved_image_are_scoped(http_workspace):
    base,root,conn=http_workspace
    uncertain(root,conn)
    folder=root/'browser_sessions'/'attempt_fixture'
    folder.mkdir(parents=True)
    (folder/'last_attempt.jpg').write_bytes(b'\xff\xd8fixture image')
    with httpx.Client(base_url=base) as client:
        ctx=client.get('/api/center/submission/reconciliation?id=1').json()
        assert ctx['uncertain'] and ctx['has_attempt_image']
        assert client.get('/api/center/submission/attempt-image?id=1').content.startswith(b'\xff\xd8')
        assert client.get('/api/center/submission/attempt-image?id=999').status_code==404
        response=client.post('/api/center/submission/reconcile',json=data(root,conn,'not_submitted',evidence='Employer portal confirms draft only'))
        response.raise_for_status()
        assert cc.get_job(conn,1)['apply_status']=='prepared'


def test_reconciliation_ui_resolves_not_submitted_without_auto_retry(http_workspace):
    if os.environ.get('APPLYPILOT_RUN_PLAYWRIGHT')!='1':
        pytest.skip('Set APPLYPILOT_RUN_PLAYWRIGHT=1')
    base,root,conn=http_workspace
    uncertain(root,conn)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page()
            page.goto(base+'/workspace')
            page.get_by_role('button',name='View record',exact=True).click()
            assert 'uncertain' in page.locator('#detail-hiring').input_value() or 'uncertain' in page.locator('#detail-hiring').inner_text().lower()
            page.get_by_role('button',name='Reconcile submission',exact=True).click()
            page.locator('#reconciliation-outcome').select_option('not_submitted')
            page.locator('#reconciliation-evidence').fill('My applications explicitly shows Draft / not submitted')
            page.locator('#reconciliation-checked').check()
            page.locator('#reconciliation-save').click()
            page.locator('#reconciliation-save').wait_for(state='detached')
            assert cc.get_job(conn,1)['apply_status']=='prepared' and not cc.get_job(conn,1)['applied_at']
            assert conn.execute("SELECT COUNT(*) FROM cc_events WHERE kind='reconciliation'").fetchone()[0]==1
        finally:
            browser.close()
