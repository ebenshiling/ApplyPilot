from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from applypilot import country_context as country,database as db,discovery_scope as scope,pipeline_workspace as pw
from applypilot.discovery import jobspy,workday,smartextract
from test_country_foundation import foundation  # noqa: F401
from test_command_center import http_workspace,workspace  # noqa: F401


def settings(**changes):
    return {'max_new_jobs':3,'results_per_search':3,'title_match':True,'processing_scope':'new',**changes}


def context(root,cfg,policy=None):
    return country.resolve(root,country_code='GB',config=cfg|{'queries':[{'query':'Application Support Analyst','tier':1},{'query':'Data Analyst','tier':1},{'query':'IT Support Analyst','tier':1}],
        'exclude_titles':['senior'],'_run_scope':policy or settings()})


def payload(**changes):
    return {'scope_policy':settings(),'stages':['discover','enrich','score'],'workers':1,'min_score':7,'selected_only':False,'stream':False,
        'board_search_budget':2,'smart_target_budget':2,'workday_target_budget':2,'discover_skip_jobspy':False,'discover_skip_workday':True,'discover_skip_smarte':True,**changes}


def job(url,title='Application Support Analyst'):
    return {'url':url,'title':title,'company':'Fixture','location':'London, UK','description':'Synthetic permitted job description. '*20}


@pytest.mark.parametrize('title,expected',[
    ('Application Support Analyst',True),('Application Support Analyst - permanent',True),('Data Analyst',True),
    ('Claims Advisor, P&C insurance',False),('Expert Advisor, Financial Due Diligence',False),('Delivery coordinator',False),
    ('IT Support Analyst',True),('Support services adviser',False)])
def test_matching_titles_do_not_treat_provider_keywords_as_title_proof(title,expected):
    assert scope.title_matches(title,['Application Support Analyst','Data Analyst','IT Support Analyst']) is expected


def test_plus_and_hash_language_titles_keep_their_meaning():
    assert scope.title_matches('C++ Developer',['C++ Developer'])
    assert not scope.title_matches('C Developer',['C++ Developer'])
    assert not scope.title_matches('C# Developer',['C++ Developer'])


def test_one_budget_across_jobspy_workday_direct_and_generic_storage(foundation,monkeypatch):
    import pandas as pd
    root,conn,cfg=foundation;monkeypatch.setenv('APPLYPILOT_RUN_ID','scope-run')
    conn.execute("INSERT INTO jobs(url,title,full_description,fit_score,score_reasoning) VALUES('https://example.org/old','Application Support Analyst','Old description',0,'{\"score\":7}')");conn.commit()
    @scope.bounded_discovery
    def discover():
        frame=pd.DataFrame([{'job_url':'https://example.org/board1','title':'Application Support Analyst','location':'London, UK','description':'Description '*40},
                            {'job_url':'https://example.org/board2','title':'IT Support Analyst','location':'London, UK','description':'Description '*40},
                            {'job_url':'https://example.org/bad','title':'Claims Advisor','location':'London, UK'}])
        jobspy.store_jobspy_results(conn,frame,'indeed')
        workday.store_results(conn,[{'apply_url':'https://example.org/senior','title':'Senior Application Support Analyst','location':'London, UK','full_description':'Description '*40},
                                   {'apply_url':'https://example.org/wd','title':'Data Analyst','location':'London, UK','full_description':'Description '*40}],{})
        smartextract._store_jobs_filtered(conn,[job('https://example.org/extra')],'Fixture','fixture',[],[])
        db.store_jobs(conn,[job('https://example.org/generic')],'Fixture','fixture')
        return {'status':'ok'}
    with country.operation(context(root,cfg)):
        result=discover()
        assert result['collection_scope']['new_jobs']==3 and result['collection_scope']['limit_reached']
        assert result['collection_scope']['title_rejections']>=2
        from applypilot.eligibility import candidates
        assert {j['url'] for j in candidates(conn,'score')}=={'https://example.org/board1','https://example.org/board2','https://example.org/wd'}
        from applypilot.scoring.scorer import _repair_zero_scores
        assert _repair_zero_scores(conn)==0
        assert conn.execute("SELECT fit_score FROM jobs WHERE url='https://example.org/old'").fetchone()[0]==0
    assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==4
    assert not conn.execute("SELECT 1 FROM jobs WHERE url IN ('https://example.org/bad','https://example.org/senior')").fetchone()


def test_parallel_storage_cannot_exceed_global_limit(foundation,monkeypatch):
    root,conn,cfg=foundation;monkeypatch.setenv('APPLYPILOT_RUN_ID','parallel-run')
    def worker(index):
        with sqlite3.connect(root/'applypilot.db',timeout=30) as own:
            own.row_factory=sqlite3.Row
            return db.store_jobs(own,[job(f'https://example.org/{index}/{i}') for i in range(10)],'Fixture','fixture')[0]
    @scope.bounded_discovery
    def discover():
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures=[pool.submit(copy_context().run,worker,i) for i in range(4)]
            counts=[f.result() for f in futures]
        return {'new':sum(counts)}
    with country.operation(context(root,cfg,settings(max_new_jobs=5))):
        result=discover()
    assert result['new']==5 and conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==5


def test_saved_processing_selection_is_separate_from_new_collection_budget(foundation,monkeypatch):
    root,conn,cfg=foundation;monkeypatch.setenv('APPLYPILOT_RUN_ID','saved-run')
    urls=['https://example.org/saved1','https://example.org/saved2']
    for url in urls:conn.execute('INSERT INTO jobs(url,title,location,full_description) VALUES(?,?,?,?)',(url,'Data Analyst','London, UK','Existing description'))
    conn.commit();policy=settings(max_new_jobs=2,processing_scope='matching',saved_job_urls=urls)
    scope.initialize_selection(conn,'saved-run',policy)
    @scope.bounded_discovery
    def discover():
        workday.store_results(conn,[{'apply_url':f'https://example.org/new{i}','title':'Data Analyst','location':'London, UK','full_description':'Description '*40} for i in range(4)],{})
        return {'status':'ok'}
    with country.operation(context(root,cfg,policy)):
        result=discover()
        from applypilot.eligibility import candidates
        assert {j['url'] for j in candidates(conn,'score')}==set(urls)
    assert result['collection_scope']['new_jobs']==2


def test_workday_scans_only_the_requested_window_and_filters_before_details(foundation,monkeypatch):
    root,conn,cfg=foundation;monkeypatch.setenv('APPLYPILOT_RUN_ID','workday-window')
    fetch=Mock(return_value={'total':1000,'jobPostings':[{'title':'Claims Advisor','locationsText':'London, UK','externalPath':'/job/a'} for _ in range(20)]})
    monkeypatch.setattr(workday,'workday_search',fetch)
    @scope.bounded_discovery
    def discover():
        rows=workday.search_employer('fixture',{'name':'Fixture'},'Application Support Analyst',location_filter=False)
        assert rows==[]
        return {'status':'ok'}
    with country.operation(context(root,cfg,settings(results_per_search=3))):discover()
    assert fetch.call_count==1 and fetch.call_args.kwargs['limit']==3


def test_preview_uses_unsaved_roles_locations_and_explicit_empty_sources(foundation):
    root,conn,cfg=foundation
    before=(root/'searches.yaml').read_bytes()
    data=payload(config_patch={'queries':[{'query':'IT Support Analyst','tier':1}],'locations':[{'location':'Manchester, UK'}],
        'boards':['indeed'],'smart_sites':[],'smart_sources_disabled':True,'location_filter_mode':'selected'})
    with country.operation(country.resolve(root,country_code='GB',config=cfg)):
        out=pw.preview(conn,root,data)
    assert out['scope_summary']['roles']==['IT Support Analyst']
    assert any('Manchester' in p for p in out['scope_summary']['locations'])
    assert out['scope_summary']['sources']['direct']==out['scope_summary']['sources']['workday']==[]
    assert out['scope_summary']['requests']['boards'][0]['query']=='IT Support Analyst'
    assert out['config']['defaults']['results_per_site']==3
    assert out['can_start'] and out['preview_token']
    assert (root/'searches.yaml').read_bytes()==before
    none=pw.preview(conn,root,data|{'discover_skip_jobspy':True})
    assert not none['can_start']


def test_new_only_preview_excludes_the_existing_backlog_without_model_calls(foundation,monkeypatch):
    root,conn,cfg=foundation
    conn.executemany('INSERT INTO jobs(url,title,full_description) VALUES(?,?,?)',[(f'https://example.org/old/{i}','Claims Advisor','Old description') for i in range(100)])
    conn.commit()
    from applypilot.scoring import scorer
    model=Mock(side_effect=AssertionError('Preview must not call a model'));monkeypatch.setattr(scorer,'score_job',model)
    with country.operation(country.resolve(root,country_code='GB',config=cfg)):
        out=pw.preview(conn,root,payload())
    assert out['queue']['score']==out['queue']['enrich']==0
    assert out['scope_summary']['existing_saved_excluded']==100
    model.assert_not_called()


@pytest.mark.parametrize('key,value',[('max_new_jobs',0),('max_new_jobs',501),('results_per_search',0),('results_per_search',51),('title_match','true'),('processing_scope','all')])
def test_scoped_limits_reject_unbounded_or_ambiguous_values(key,value):
    with pytest.raises(ValueError):scope.validate_policy(settings(**{key:value}))


def test_preview_token_changes_with_scope_and_settings(foundation):
    root,conn,_=foundation
    first=pw.preview(conn,root,payload())
    changed=pw.preview(conn,root,payload(scope_policy=settings(max_new_jobs=2)))
    assert first['preview_token']!=changed['preview_token']
    (root/'searches.yaml').write_text((root/'searches.yaml').read_text()+'\nboard_search_budget: 7\n')
    assert pw.preview(conn,root,payload())['preview_token']!=first['preview_token']


def test_invalid_saved_limits_cannot_brick_the_next_pipeline_load(foundation):
    root,conn,_=foundation;before=(root/'searches.yaml').read_bytes()
    with pytest.raises(ValueError):pw.save_config(conn,root,{'patch':{'discovery_defaults':{'max_new_jobs':0}}})
    assert (root/'searches.yaml').read_bytes()==before


def test_native_start_requires_reviewed_scope_and_rejects_cached_unbounded_request(foundation):
    from applypilot import command_api
    root,conn,_=foundation;handler=SimpleNamespace(_send_json=Mock());runner=SimpleNamespace(start=Mock())
    (root/'resume.txt').write_text('Synthetic source evidence')
    command_api.post(handler,'/api/center/pipeline/start',{'country_code':'GB','stages':['discover','enrich','score']},root,runner)
    assert handler._send_json.call_args.args[0]==400
    command_api.post(handler,'/api/center/pipeline/start',payload(country_code='GB'),root,runner)
    assert handler._send_json.call_args.args[0]==400
    assert 'reviewed' in handler._send_json.call_args.args[1]['error']
    runner.start.assert_not_called()


def test_reviewed_plan_is_accepted_but_a_changed_saved_configuration_is_not(foundation):
    from applypilot import command_api
    root,conn,cfg=foundation;(root/'resume.txt').write_text('Synthetic source evidence')
    data=payload(country_code='GB');handler=SimpleNamespace(_send_json=Mock());runner=SimpleNamespace(start=Mock(return_value=(True,{'run_id':'synthetic-approved'})))
    with country.operation(country.resolve(root,country_code='GB',config=cfg)):
        plan=pw.preview(conn,root,data)
    command_api.post(handler,'/api/center/pipeline/start',data|{'expected_preview_token':plan['preview_token']},root,runner)
    assert handler._send_json.call_args.args[0]==200 and runner.start.call_count==1
    (root/'searches.yaml').write_text((root/'searches.yaml').read_text()+'\nboard_search_budget: 9\n')
    command_api.post(handler,'/api/center/pipeline/start',data|{'expected_preview_token':plan['preview_token']},root,runner)
    assert handler._send_json.call_args.args[0]==400 and runner.start.call_count==1


def test_compiled_scope_cannot_be_widened_by_legacy_top_level_overrides(foundation):
    from applypilot.dashboard_server import _PipelineRunner
    root,conn,cfg=foundation
    compiled=scope.effective_config(conn,root,payload())
    logs=root/'logs';logs.mkdir()
    runner=_PipelineRunner(root)
    file,_=runner._write_search_override_if_needed({'_country_snapshot':compiled,'search_queries':['Claims Advisor'],'boards':['google'],
        'results_per_site':999,'board_search_budget':99},run_id='fixture',log_dir=logs)
    captured=yaml.safe_load(Path(file).read_text())
    assert captured['queries']==compiled['queries']
    assert captured['defaults']['results_per_site']==3 and captured['board_search_budget']==2
    assert captured['sites']==['indeed']


def test_scoped_board_execution_uses_the_reviewed_request_plan(foundation,monkeypatch):
    root,conn,cfg=foundation;monkeypatch.setenv('APPLYPILOT_RUN_ID','board-execution')
    compiled=scope.effective_config(conn,root,payload(scope_policy=settings(max_new_jobs=2),search_queries=['Application Support Analyst']))
    calls=[]
    def one(target,sites,results,*args,**kwargs):
        calls.append((target['query'],target['location'],sites,results))
        new,old=db.store_jobs(conn,[job('https://example.org/actual-board')],'indeed','fixture')
        return {'new':new,'existing':old,'errors':0,'total':1}
    monkeypatch.setattr(jobspy,'_run_one_search',one)
    @scope.bounded_discovery
    def discover():return jobspy.run_discovery(compiled)
    with country.operation(country.resolve(root,country_code='GB',config=compiled)):
        result=discover()
    assert calls==[(t['query'],t['location'],[t['board']],3) for t in compiled['_planned_board_searches']]
    assert result['new']==1 and result['collection_scope']['new_jobs']==1


def test_invalidation_does_not_touch_existing_scores_outside_the_run(foundation,monkeypatch):
    root,conn,cfg=foundation;monkeypatch.setenv('APPLYPILOT_RUN_ID','no-backlog-invalidation')
    conn.execute("INSERT INTO jobs(url,title,full_description,fit_score,score_fingerprint) VALUES('https://example.org/previous','Data Analyst','Existing evidence',8,'score-v2:old')");conn.commit()
    fingerprint=Mock(side_effect=AssertionError('Existing backlog should not be re-fingerprinted'))
    monkeypatch.setattr(pw,'score_fingerprint',fingerprint)
    with country.operation(context(root,cfg)):
        assert pw.invalidate_scores(conn,root)==0
    assert conn.execute("SELECT fit_score FROM jobs WHERE url='https://example.org/previous'").fetchone()[0]==8
    fingerprint.assert_not_called()


def test_board_result_window_is_enforced_even_if_provider_returns_too_many(foundation,monkeypatch):
    import pandas as pd
    root,conn,cfg=foundation;monkeypatch.setenv('APPLYPILOT_RUN_ID','provider-window')
    compiled=scope.effective_config(conn,root,payload(scope_policy=settings(max_new_jobs=50,results_per_search=3)))
    frame=pd.DataFrame([{'job_url':f'https://example.org/sdk/{i}','title':'Data Analyst','location':'London, UK','description':'Description '*40,'site':'indeed'} for i in range(20)])
    scrape=Mock(return_value=frame);monkeypatch.setattr(jobspy,'_scrape_with_retry',scrape)
    @scope.bounded_discovery
    def discover():return jobspy.run_discovery(compiled)
    with country.operation(country.resolve(root,country_code='GB',config=compiled)):
        result=discover()
    assert result['new']==3 and conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==3
    assert scrape.call_args.args[0]['results_wanted']==3


def test_prepare_explicit_picks_does_not_replace_selection_with_current_search_titles(foundation):
    root,conn,cfg=foundation
    (root/'resume.txt').write_text('Synthetic source evidence')
    conn.execute("INSERT INTO jobs(url,title,location,full_description,fit_score,apply_status) VALUES('https://example.org/picked','Software Engineer','London, UK','Synthetic description',8,'selected')");conn.commit()
    out=pw.preview(conn,root,payload(stages=['tailor'],selected_only=True,scope_policy=settings(processing_scope='matching')))
    assert out['scope_summary']['explicit_picks']
    assert out['config']['_run_scope']['saved_job_urls']==['https://example.org/picked']
    assert out['queue']['tailor']==1


def test_browser_edits_refresh_the_plan_and_review_can_be_cancelled_without_starting(http_workspace):
    if os.environ.get('APPLYPILOT_RUN_COUNTRY_UI')!='1':pytest.skip('Enable local-only UI tests')
    from playwright.sync_api import sync_playwright
    base,root,conn=http_workspace
    (root/'profile.json').write_text('{"personal":{"full_name":"Scope Fixture"}}')
    (root/'resume.txt').write_text('Synthetic support evidence')
    (root/'searches.yaml').write_text('country: UK\nqueries: [{query: Analyst}]\nlocations: [{location: "London, UK"}]\nboards: [indeed]\n')
    with sync_playwright() as pw_browser:
        browser=pw_browser.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1500,'height':1100});starts=[];errors=[]
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
            page.on('request',lambda r:starts.append(r.url) if r.url.split('?')[0].endswith('/pipeline/start') else None)
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/workspace#pipeline')
            try:page.locator('#scope-max-jobs').wait_for(timeout=10000)
            except Exception:pytest.fail(str(errors)+' '+page.locator('#content').inner_text())
            assert page.locator('#native-queries').input_value()=='Analyst'
            assert page.locator('#scope-max-jobs').input_value()=='50'
            assert page.locator('#scope-results').input_value()=='10'
            assert not page.locator('#scope-workday').is_checked() and not page.locator('#scope-direct').is_checked()
            page.locator('#native-queries').fill('')
            page.wait_for_function("document.querySelector('#native-preview-content').textContent.includes('Enter at least one job title')")
            assert page.locator('#native-start').is_disabled()
            page.locator('#native-queries').fill('IT Support Analyst')
            page.locator('#native-locations').fill('Manchester, UK')
            page.locator('#scope-max-jobs').fill('5')
            page.wait_for_function("pipelineState.preview?.scope_summary?.roles[0]==='IT Support Analyst' && pipelineState.preview.scope_summary.max_new_jobs===5 && pipelineState.preview.scope_summary.locations.some(v=>v.includes('Manchester'))")
            page.locator('#native-start').click()
            try:page.locator('#scope-run-confirm').wait_for(timeout=10000)
            except Exception:pytest.fail(str(errors)+' toast='+page.locator('#toast').text_content()+' state='+page.locator('#native-run-state').text_content())
            assert 'At most 5' in page.locator('.scope-review-dialog').inner_text()
            page.locator('.scope-review-dialog [data-scope-cancel]').first.click()
            assert not starts and not errors
        finally:browser.close()
