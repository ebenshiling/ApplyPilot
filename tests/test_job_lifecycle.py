import json
import os
from pathlib import Path

import httpx
import pytest

from applypilot import command_center as cc
from applypilot import job_lifecycle as lifecycle
from applypilot.database import store_jobs
from test_command_center import workspace, http_workspace  # noqa: F401, F811


def seed(root, conn):
    folder = root / 'tailored_resumes'
    folder.mkdir(exist_ok=True)
    cv = folder / 'cv.txt'
    cv.write_text('Reviewed CV')
    cv.with_suffix('.pdf').write_bytes(b'%PDF reviewed')
    cv.with_name('cv_REPORT.json').write_text('{"status":"approved"}')
    (root / 'resume.txt').write_text('Original source CV')
    (root / 'profile.json').write_text('{}')
    conn.execute("UPDATE jobs SET tailored_resume_path=?,tailor_status='failed',tailor_failure_detail=?,tailor_requirement_gaps=?,full_description=? WHERE rowid=1",
                 (str(cv), 'Essential experience was not evidenced', json.dumps({'missing': ['Essential experience']}), 'Large job advert '*5000))
    conn.commit()
    return cv


def delete(root, conn, reason='preparation_failed'):
    plan = lifecycle.preview(conn, root, 1)
    return lifecycle.retire(conn, root, {'id':1,'reason':reason,'detail':'Reviewed and no longer pursuing','cleanup_token':plan['cleanup_token']})


def test_closure_is_distinct_from_hiring_and_reversible(workspace):
    root, conn = workspace
    cv = seed(root, conn)
    lifecycle.close_record(conn, root, 1, 'requirements_not_met', 'Required experience is not evidenced')
    assert cc.get_job(conn, 1)['applied_at'] is None and cv.exists()
    out = cc.details(conn, root, 1)
    assert out['closure']['reason'] == 'requirements_not_met'
    assert out['meta']['visibility'] == 'archived' and not out['meta']['hiring_status']
    metrics=cc.overview(conn,root)['metrics']
    assert metrics['active_jobs']==0 and metrics['archived']==1 and metrics['retired']==0
    assert any('Essential experience' in e['detail'] for e in out['events'])
    cc.update_job(conn, root, 1, 'restore', {})
    assert cc.details(conn, root, 1)['closure'] is None
    assert cc.details(conn, root, 1)['meta']['visibility'] == 'active'


def test_rejection_requires_submission_and_survives_deletion(workspace):
    root, conn = workspace
    seed(root, conn)
    with pytest.raises(ValueError, match='submitted'):
        lifecycle.close_record(conn, root, 1, 'rejected')
    cc.update_job(conn, root, 1, 'applied', {'reference':'REF-100','evidence':'Employer receipt'})
    lifecycle.close_record(conn, root, 1, 'rejected', 'Employer rejection received')
    before = cc.overview(conn, root)['metrics']
    result = delete(root, conn, 'rejected')
    saved = lifecycle.detail(conn, result['history_id'])['record']['snapshot']
    assert saved['reference'] == 'REF-100' and saved['hiring_status'] == 'rejected' and saved['applied_at']
    assert cc.overview(conn, root)['metrics']['submitted'] == before['submitted'] == 1
    assert cc.overview(conn, root)['metrics']['responses'] == 1


def test_delete_removes_owned_files_but_keeps_timeline_and_originals(workspace):
    root, conn = workspace
    cv = seed(root, conn)
    cc.event(conn, cc.get_job(conn, 1)['url'], 'progress', 'Reviewed screening answers', state='prepared')
    conn.commit()
    result = delete(root, conn)
    assert result['deleted'] and result['freed_bytes'] > 0 and result['pending_files'] == 0
    assert not cv.exists() and not cv.with_suffix('.pdf').exists()
    assert (root/'resume.txt').read_text() == 'Original source CV' and (root/'profile.json').exists()
    assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
    metrics=cc.overview(conn,root)['metrics']
    assert metrics['total']==metrics['active_jobs']==metrics['archived']==0
    assert metrics['retired']==metrics['ever_found']==1
    detail = lifecycle.detail(conn, result['history_id'])
    assert any(e['detail'] == 'Reviewed screening answers' for e in detail['events'])
    assert any(e['state'] == 'deleted' for e in detail['events'])
    assert 'full_description' not in detail['record']['snapshot']


def test_shared_documents_and_registered_sources_are_preserved(workspace):
    root, conn = workspace
    cv = seed(root, conn)
    conn.execute("INSERT INTO jobs(url,title,tailored_resume_path) VALUES('https://example.org/other','Other application',?)", (str(cv),))
    conn.commit()
    plan = lifecycle.preview(conn, root, 1)
    assert plan['retained_files'] and not plan['files']
    result = delete(root, conn)
    assert cv.exists() and cv.with_suffix('.pdf').exists() and result['freed_bytes'] == 0
    assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1


def test_original_upload_reference_protects_generated_file(workspace):
    root, conn = workspace
    cv = seed(root, conn)
    conn.execute("INSERT INTO cc_cv_sources(source_key,original_path) VALUES('saved-source',?)", (str(cv),))
    conn.commit()
    assert lifecycle.preview(conn, root, 1)['retained_files']
    delete(root, conn)
    assert cv.exists() and cv.with_suffix('.pdf').exists()


def test_browser_bundle_cleanup_rejects_malformed_folder_ids(workspace):
    root, conn = workspace
    seed(root, conn)
    from applypilot.apply.assisted_browser import _schema
    _schema(conn)
    url = cc.get_job(conn, 1)['url']
    folder = root/'browser_sessions'/'valid_session_id'
    folder.mkdir(parents=True)
    (folder/'confirmation.png').write_bytes(b'Generated screenshot fixture')
    unrelated = root/'writing_documents'/'unrelated-draft.txt'
    unrelated.parent.mkdir()
    unrelated.write_text('Unrelated saved draft')
    for ident in ('valid_session_id','../../writing_documents'):
        conn.execute("INSERT INTO cc_browser_sessions(id,job_url,state,detail,created_at,updated_at) VALUES(?,?,'stopped','',?,?)", (ident,url,cc.now(),cc.now()))
    conn.commit()
    delete(root, conn)
    assert not folder.exists() and unrelated.exists()


def test_deleted_url_does_not_reappear_and_job_number_is_not_reused(workspace):
    root, conn = workspace
    seed(root, conn)
    old = cc.get_job(conn, 1)['url']
    delete(root, conn)
    new, duplicate = store_jobs(conn, [{'url':old+'?tracking=again','title':'Old listing'},{'url':'https://example.org/new','title':'New listing'}], 'Direct', 'fixture')
    assert (new, duplicate) == (1, 1)
    row = conn.execute('SELECT rowid,url FROM jobs').fetchone()
    assert row[0] > 1 and row[1] == 'https://example.org/new'


def test_stale_cleanup_preview_never_deletes(workspace):
    root, conn = workspace
    cv = seed(root, conn)
    plan = lifecycle.preview(conn, root, 1)
    cv.write_text('A new CV version after preview')
    with pytest.raises(ValueError, match='preview changed'):
        lifecycle.retire(conn, root, {'id':1,'reason':'advert_closed','cleanup_token':plan['cleanup_token']})
    assert cv.exists() and cc.get_job(conn, 1)


@pytest.mark.parametrize('state', ['in_progress','running','submitting','submission_uncertain'])
def test_active_or_uncertain_application_is_guarded(workspace, state):
    root, conn = workspace
    seed(root, conn)
    conn.execute('UPDATE jobs SET apply_status=? WHERE rowid=1', (state,))
    conn.commit()
    assert lifecycle.preview(conn, root, 1)['delete_blockers']
    with pytest.raises(ValueError):
        delete(root, conn)
    with pytest.raises(ValueError):
        lifecycle.close_record(conn, root, 1, 'advert_closed')


def test_pipeline_blocks_deletion_not_closure(workspace):
    root, conn = workspace
    seed(root, conn)
    conn.execute("INSERT INTO cc_pipeline_runs(run_id,status) VALUES('fixture','running')")
    conn.commit()
    lifecycle.close_record(conn, root, 1, 'preparation_failed')
    with pytest.raises(ValueError, match='pipeline'):
        delete(root, conn)


def test_known_stopped_run_is_settled_but_unknown_cli_remains_guarded(workspace):
    root, conn = workspace
    seed(root, conn)
    conn.execute("INSERT INTO cc_pipeline_runs(run_id,status) VALUES('ended','running'),('unknown-cli','running')")
    conn.commit()
    from types import SimpleNamespace
    runner = SimpleNamespace(status=lambda:{'running':False,'starting':False},history=lambda **kw:[{'run_id':'ended','ended_at':1}])
    lifecycle.reconcile_pipeline(conn, runner)
    assert conn.execute("SELECT status FROM cc_pipeline_runs WHERE run_id='ended'").fetchone()[0] == 'interrupted'
    assert conn.execute("SELECT status FROM cc_pipeline_runs WHERE run_id='unknown-cli'").fetchone()[0] == 'running'


def test_locked_file_cleanup_is_retryable_after_history_commit(workspace, monkeypatch):
    root, conn = workspace
    cv = seed(root, conn)
    actual = Path.unlink
    def locked(path, *args, **kwargs):
        if path == cv:
            raise PermissionError('Viewer locked')
        return actual(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', locked)
    result = delete(root, conn)
    assert result['pending_files'] == 1 and conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
    assert lifecycle.detail(conn, result['history_id'])['events']
    monkeypatch.setattr(Path, 'unlink', actual)
    retried = lifecycle.retry_cleanup(conn, root, result['history_id'])
    assert retried['pending_files'] == 0 and not cv.exists()


def test_outside_path_and_compaction_preserve_records(workspace):
    root, conn = workspace
    seed(root, conn)
    outside = root.parent/'outside-original-cv.txt'
    outside.write_text('Private original')
    conn.execute('UPDATE jobs SET tailored_resume_path=? WHERE rowid=1', (str(outside),))
    conn.commit()
    result = delete(root, conn)
    assert outside.read_text() == 'Private original'
    events = lifecycle.detail(conn, result['history_id'])['events']
    lifecycle.compact(conn, root)
    assert lifecycle.detail(conn, result['history_id'])['events'] == events


def test_lifecycle_http_is_workspace_scoped(http_workspace):
    base, root, conn = http_workspace
    seed(root, conn)
    with httpx.Client(base_url=base) as client:
        assert client.get('/assets/job-lifecycle.js').status_code == 200
        plan = client.get('/api/center/lifecycle/preview?id=1').json()
        response = client.post('/api/center/lifecycle/delete', json={'id':1,'reason':'advert_closed','cleanup_token':plan['cleanup_token']})
        response.raise_for_status()
        ident = response.json()['history_id']
        assert client.get('/api/center/lifecycle/history').json()['total'] == 1
        assert client.get('/api/center/lifecycle/detail?id='+ident).json()['record']['reason'] == 'advert_closed'
        assert client.get('/api/center/lifecycle/detail?id=foreign').status_code == 404


def test_close_and_delete_ui_retains_timeline(http_workspace):
    if os.environ.get('APPLYPILOT_RUN_PLAYWRIGHT') != '1' and os.environ.get('APPLYPILOT_RUN_COUNTRY_UI') != '1':
        pytest.skip('Enable local-only lifecycle browser verification')
    base, root, conn = http_workspace
    seed(root, conn)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            errors = []
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base+'/workspace')
            page.get_by_role('button', name='View record', exact=True).click()
            page.get_by_role('button', name='Close / delete record', exact=True).click()
            page.locator('#lifecycle-reason').select_option('requirements_not_met')
            page.locator('#lifecycle-note').fill('Essential experience missing')
            page.get_by_role('radio', name='Delete job & generated files').check()
            page.locator('#lifecycle-save').click()
            page.locator('#job-search').wait_for()
            assert page.evaluate('state.view') == 'jobs' and page.evaluate('location.hash') == '#jobs'
            page.locator('.rail [data-view="history"]').click()
            page.get_by_role('button', name='View timeline', exact=True).wait_for()
            page.get_by_role('button', name='View timeline', exact=True).click()
            assert 'Essential experience missing' in page.locator('.lifecycle-dialog').inner_text()
            assert 'requirements not met' in page.locator('.lifecycle-dialog').inner_text()
            assert not errors
            assert (root/'resume.txt').exists()
        finally:
            browser.close()
