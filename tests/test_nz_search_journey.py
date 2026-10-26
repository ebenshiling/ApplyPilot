import json
import os
import re
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from applypilot import command_api, nz_search
from applypilot.discovery import nz
from test_nz_discovery import foundation, nz_workspace, permitted  # noqa: F401
from test_command_center import http_workspace, workspace  # noqa: F401


def test_search_needs_no_employer_cv_campaign_or_source_and_changes_no_data(nz_workspace):
    root, conn, cfg = nz_workspace
    before = (root/'searches.yaml').read_bytes()
    result = nz_search.search(root, {'query':'Application Support Engineer','location':'Wellington'})
    assert result['mode'] == 'external_board_results' and not result['automatic_run_started']
    assert len(result['links']) == 3
    for link in result['links']:
        assert urlsplit(link['url']).scheme == 'https'
        assert 'Wellington' in link['url']
    assert (root/'searches.yaml').read_bytes() == before
    assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM cc_employers').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM cc_cv_sources').fetchone()[0] == 0


@pytest.mark.parametrize('query,location',[('', 'Auckland'), ('x'*161,'Auckland'),('Analyst\nDeveloper','Auckland'),('Analyst','London')])
def test_search_validates_a_short_role_and_nz_location(nz_workspace,query,location):
    root, _, _ = nz_workspace
    with pytest.raises(ValueError):
        nz_search.search(root,{'query':query,'location':location})


def test_search_encodes_query_instead_of_turning_input_into_url_markup(nz_workspace):
    root, _, _ = nz_workspace
    query='C++ developer & "data" <script>'
    out = nz_search.search(root,{'query':query,'location':'New Zealand'})
    seek = parse_qs(urlsplit(out['links'][0]['url']).query)
    assert seek['keywords'] == [query] and '<script>' not in out['links'][0]['url']


def seed_run(conn, ident='nz-run', code='NZ', state='running'):
    config={'country_context':{'country_code':code},'stages':['discover']}
    result={'stages':[{'stage':'discover','result':{'sources':{'jobspy':'skipped','smartextract':{'new':2,'existing':1,'found':3,'errors':0}}}}]}
    conn.execute('INSERT INTO cc_pipeline_runs(run_id,started_at,status,config,result) VALUES(?,?,?,?,?)',(ident,'2026-10-19T10:00:00Z',state,json.dumps(config),json.dumps(result)))
    conn.commit()


@pytest.mark.parametrize('record_state,runtime,state,active',[
    ('running',{'running':True},'running',True),('running',{'starting':True},'starting',True),
    ('completed',{'running':False,'exit_code':0},'completed',False),('partial',{},'partial',False),
    ('failed',{},'failed',False),('running',{'exit_code':-1},'stopped',False),
    ('running',{},'interrupted',False),('running',{'exit_code':1},'failed',False)])
def test_discovery_status_has_a_definite_terminal_state(nz_workspace,record_state,runtime,state,active):
    root, conn, _ = nz_workspace
    seed_run(conn,state=record_state)
    runner=SimpleNamespace(status=lambda:{'run_id':'nz-run',**runtime})
    out=nz_search.discovery_status(conn,root,runner,'nz-run')
    assert out['state']==state and out['active'] is active
    assert out['counts']=={'new':2,'existing':1,'found':3,'errors':0}


def test_status_never_treats_a_uk_run_as_nz_or_starts_a_worker(nz_workspace):
    root, conn, _ = nz_workspace
    seed_run(conn,'uk-run',code='GB')
    runner=SimpleNamespace(status=lambda:{'run_id':'uk-run','running':True},start=Mock())
    out=nz_search.discovery_status(conn,root,runner)
    assert out['state']=='idle' and out['other_workflow_active']
    with pytest.raises(ValueError,match='not found'):
        nz_search.discovery_status(conn,root,runner,'uk-run')
    runner.start.assert_not_called()


def test_start_acknowledgement_uses_owned_history_before_child_creates_run_row(nz_workspace):
    root, conn, _ = nz_workspace
    runner=SimpleNamespace(status=lambda:{'run_id':'new-run','starting':True},history=lambda limit:[{'run_id':'new-run','stages':['discover'],'country_context':{'country_code':'NZ'}}])
    out=nz_search.discovery_status(conn,root,runner,'new-run')
    assert out['active'] and out['state']=='starting' and out['counts'] is None


def test_stop_rechecks_expected_run_id_under_runner_lock(nz_workspace):
    from applypilot.dashboard_server import _PipelineRunner
    root, _, _ = nz_workspace
    runner=_PipelineRunner(root);runner._run_id='uk-new-run';runner._proc=Mock()
    ok, result=runner.stop(expected_run_id='old-nz-run')
    assert not ok and result['error']=='run_changed'
    runner._proc.terminate.assert_not_called()


def test_duplicate_start_is_rejected_without_starting_another_run(nz_workspace):
    root, conn, cfg=nz_workspace
    item=permitted(root)
    nz.save_campaign(conn,root,{'expected_revision':nz.revision(cfg),'campaign':nz.preset()|{'smart_sites':[item['name']]}})
    active={'running':False,'starting':False}
    def start(payload):active['starting']=True;return True,{'run_id':'only-run'}
    runner=SimpleNamespace(status=lambda:active,start=Mock(side_effect=start))
    handler=SimpleNamespace(_send_json=Mock())
    command_api.post(handler,'/api/center/nz/start',{'country_code':'NZ'},root,runner)
    assert handler._send_json.call_args.args[0]==200
    command_api.post(handler,'/api/center/nz/start',{'country_code':'NZ'},root,runner)
    assert handler._send_json.call_args.args[0]==400 and runner.start.call_count==1


def test_browser_search_first_without_setup_has_no_busy_loop_or_employer_requirement(http_workspace):
    if os.environ.get('APPLYPILOT_RUN_COUNTRY_UI')!='1':pytest.skip('Enable local-only country UI tests')
    from playwright.sync_api import sync_playwright
    base, root, conn=http_workspace
    (root/'searches.yaml').write_text('nz_workflow_enabled: true\ncountry: UK\nqueries: [{query: Analyst}]\nlocations: [{location: "London, UK"}]\n')
    original=(root/'searches.yaml').read_bytes()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1400,'height':1000});requests=[];errors=[]
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
            page.on('request',lambda r:requests.append(r.url));page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/workspace');page.wait_for_function("!document.querySelector('#refresh').disabled")
            page.select_option('#workspace-country','NZ');page.locator('#nz-search-form').wait_for()
            assert not page.locator('#nz-advanced').evaluate('e=>e.open')
            assert not page.locator('#nz-save-vacancy').evaluate('e=>e.open')
            assert not page.locator('#nz-find-jobs').is_disabled()
            assert page.locator('#nz-start').evaluate('e=>getComputedStyle(e).cursor')!='wait'
            page.locator('#nz-search-role').fill('Application Support Engineer')
            page.select_option('#nz-search-location','Wellington')
            page.get_by_role('button',name='Find jobs',exact=True).click()
            page.get_by_role('link',name='Open SEEK results',exact=False).wait_for()
            assert page.locator('#nz-search-results').get_by_role('link').count()==3
            assert not page.locator('#nz-find-jobs').is_disabled()
            assert conn.execute("SELECT COUNT(*) FROM jobs WHERE country_code='NZ'").fetchone()[0]==0
            page.wait_for_timeout(5000)
            assert not any('/api/center/nz/start' in u for u in requests)
            page.locator('#nz-save-after-search').click()
            page.locator('#nz-url').fill('https://employer.example.nz/job?id=123')
            page.locator('#nz-title').fill('Application Support Engineer')
            assert not page.locator('#nz-company').evaluate('e=>e.required')
            page.locator('#nz-capture-form button[type=submit]').click()
            page.wait_for_function("[...document.querySelectorAll('#nz-saved-jobs h3')].some(e=>e.textContent==='Application Support Engineer')")
            assert conn.execute("SELECT COUNT(*) FROM jobs WHERE country_code='NZ'").fetchone()[0]==1
            assert (root/'searches.yaml').read_bytes()==original and not errors
        finally:browser.close()


@pytest.mark.parametrize('outcome',['completed','bounded','transport_error'])
def test_import_polling_stops_without_restarting_or_duplicate_starts(http_workspace,outcome):
    if os.environ.get('APPLYPILOT_RUN_COUNTRY_UI')!='1':pytest.skip('Enable local-only country UI tests')
    from playwright.sync_api import sync_playwright
    base, root, conn=http_workspace
    (root/'searches.yaml').write_text('nz_workflow_enabled: true\ncountry: UK\nqueries: [{query: Analyst}]\nlocations: [{location: "London, UK"}]\n')
    calls={'start':0,'status':0}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page()
            page.add_init_script('const originalTimeout=window.setTimeout;window.setTimeout=(fn,ms,...args)=>originalTimeout(fn,ms===4000?1:ms,...args);')
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
            def discovery(route):
                response=route.fetch();data=response.json();data['campaign_installed']=True;data['readiness']['automatic_discovery_available']=True
                route.fulfill(response=response,json=data)
            page.route(re.compile(r'.*/api/center/nz/discovery(?:\?.*)?$'),discovery)
            def start(route):
                calls['start']+=1;route.fulfill(json={'ok':True,'run_id':'synthetic-ui-run'})
            page.route(re.compile(r'.*/api/center/nz/start$'),start)
            def status(route):
                calls['status']+=1
                if outcome=='transport_error':
                    route.fulfill(status=503,json={'ok':False,'error':'Synthetic status unavailable'});return
                done=outcome=='completed' and calls['status']>=2
                route.fulfill(json={'ok':True,'run_id':'synthetic-ui-run','state':'completed' if done else 'running','active':not done,
                    'detail':'Import completed.' if done else 'Import running.','counts':{'new':0,'existing':0,'found':0,'errors':0} if done else None})
            page.route(re.compile(r'.*/api/center/nz/discovery/status(?:\?.*)?$'),status)
            page.goto(base+'/workspace');page.wait_for_function("!document.querySelector('#refresh').disabled")
            page.select_option('#workspace-country','NZ');page.locator('#nz-search-form').wait_for()
            page.locator('#nz-advanced > summary').click()
            page.locator('#nz-start').evaluate('el=>{el.click();el.click();}')
            if outcome=='completed':
                page.wait_for_function("document.querySelector('#nz-import-status').dataset.state==='completed'")
                assert not page.locator('#nz-start').is_disabled()
                expected=2
            elif outcome=='bounded':
                page.wait_for_function("document.querySelector('#nz-import-status').textContent.includes('checking has paused')")
                assert page.locator('#nz-import-stop').is_visible()
                expected=30
            else:
                page.wait_for_function("document.querySelector('#nz-import-status').textContent.includes('Synthetic status unavailable')")
                page.wait_for_timeout(500)
                expected=3
            page.wait_for_timeout(500)
            assert calls=={'start':1,'status':expected}
            assert page.locator('#nz-start').get_attribute('aria-busy') is None
            assert conn.execute("SELECT COUNT(*) FROM jobs WHERE country_code='NZ'").fetchone()[0]==0
        finally:browser.close()
