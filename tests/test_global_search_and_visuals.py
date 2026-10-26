# ruff: noqa: F811
import os
import pytest

from applypilot import geography as geo, opportunity_insights as oi, pipeline_workspace as pw
from applypilot.discovery import jobspy, smartextract, workday
from applypilot.setup_workspace import read_searches_dict
from test_pipeline_workspace import workspace, server  # noqa: F401


def global_config():
    return {'country':'UK','geography_enabled':True,'location_filter_mode':'selected',
            'defaults':{'country_indeed':'uk'},'boards':['indeed','linkedin'],
            'queries':[{'query':'Analyst','tier':1}],
            'locations':[{'location':'Toronto','country':'Canada','state':'Ontario','city':'Toronto','remote':False,'label':'CA-city','distance':25},
                         {'location':'Austin','country':'USA','state':'Texas','city':'Austin','remote':False},
                         {'location':'Remote','country':'Worldwide','remote':True,'kind':'remote'}]}


@pytest.mark.parametrize('value,key',[('GB','uk'),('United Kingdom','uk'),('USA','usa'),('US','usa'),('CA','canada'),('NZ','new zealand'),('Türkiye','turkey')])
def test_country_aliases_use_runtime_identifiers(value,key):
    assert geo.market(value)==key


def test_mixed_country_requests_override_global_default_and_keep_location_metadata(monkeypatch):
    import pandas as pd
    captured=[]
    monkeypatch.setattr(jobspy,'_scrape_with_retry',lambda kwargs,**kw:captured.append(kwargs) or pd.DataFrame())
    cfg=global_config()
    for place in geo.locations(cfg)[:2]:
        jobspy._run_one_search(place|{'query':'Analyst'},['indeed','linkedin'],10,48,None,cfg['defaults'],0,[],[],{})
    assert captured[0]['country_indeed']=='canada' and captured[0]['location']=='Toronto, Ontario, Canada'
    assert captured[1]['country_indeed']=='usa' and captured[1]['location']=='Austin, Texas, United States'
    assert geo.locations(cfg)[0]['distance']==25 and geo.locations(cfg)[0]['label']=='CA-city'
    unsupported=geo.normalize_location({'location':'Accra','country':'Ghana','city':'Accra'},cfg)
    jobspy._run_one_search(unsupported|{'query':'Analyst'},['indeed','linkedin'],10,48,None,cfg['defaults'],0,[],[],{})
    assert captured[-1]['site_name']==['linkedin'] and captured[-1]['country_indeed']=='worldwide'


def test_selected_location_filters_work_for_all_discovery_engines():
    cfg=global_config()
    cfg['locations']=cfg['locations'][:2]
    cfg['location']={'accept_patterns':['UK'],'reject_patterns':['Canada','United States']}
    for module,loader in [(jobspy,jobspy._load_location_config),(smartextract,smartextract._load_location_filter),(workday,workday._load_location_filter)]:
        accept,reject=loader(cfg)
        assert module._location_ok('Toronto, ON, Canada',accept,reject)
        assert module._location_ok('Austin, Texas, US',accept,reject)
        assert not module._location_ok('Berlin, Germany',accept,reject)
        assert not module._location_ok('London, UK',accept,reject)
    remote=geo.SelectedLocations({'locations':[{'location':'Remote','remote':True,'country':'Canada','kind':'remote'}]})
    assert remote.matches('Remote (Canada)') and not remote.matches('Remote (US)')
    assert geo.SelectedLocations(global_config()).matches('Remote (India)')


def test_smart_targets_expand_geography_and_skip_country_scoped_sources():
    cfg=global_config()
    cfg['locations']=cfg['locations'][:2]
    sites=[{'name':'Global fixture','type':'search','url':'https://example.org/jobs?q={query_encoded}&country={country_encoded}&state={state_encoded}&city={city_encoded}'},
           {'name':'UK fixture','type':'search','url':'https://jobs.example.co.uk/jobs?q={query_encoded}&l={location_encoded}'}]
    targets=smartextract.build_scrape_targets(sites,cfg)
    assert len(targets)==2 and all(t['name']=='Global fixture' for t in targets)
    assert 'country=Canada&state=Ontario&city=Toronto' in targets[0]['url']
    assert 'country=United+States&state=Texas&city=Austin' in targets[1]['url']


def test_campaign_and_preview_preserve_geography_and_resume_priorities(workspace):
    root,conn=workspace
    original=read_searches_dict(root)
    cfg=global_config()
    out=pw.save_config(conn,root,{'patch':{k:cfg[k] for k in ('country','locations','geography_enabled','location_filter_mode')}})
    assert out['config']['queries']==original['queries']
    assert out['config']['resume_routing']==original['resume_routing']
    preview=pw.preview(conn,root,{})
    assert preview['geography']['locations'][0]['provider_country']=='canada'
    assert preview['geography']['locations'][1]['provider_country']=='usa'
    pw.save_campaign(conn,root,{'name':'Global campaign',**cfg})
    saved=pw.campaign_config(root,'global-campaign')
    assert saved['location_filter_mode']=='selected' and saved['locations'][0]['distance']==25


def test_visuals_count_unique_records_and_ignore_stale_submitted_failure(workspace):
    root,conn=workspace
    url=conn.execute('SELECT url FROM jobs WHERE rowid=1').fetchone()[0]
    conn.execute("UPDATE jobs SET applied_at='2026-10-03T00:00:00Z',apply_status='failed' WHERE rowid=1")
    conn.execute("INSERT INTO cc_job_meta(job_url,hiring_status) VALUES(?,'withdrawn')",(url,))
    for i in range(50):
        conn.execute("INSERT INTO cc_events(job_url,kind,source,state,detail,created_at) VALUES(?,'progress','test','updated',?,'2026-10-03T00:00:00Z')",(url,str(i)))
    conn.commit()
    out=oi.summary(conn)
    assert out['record_count']==1
    assert out['visualizations']['hiring_funnel'][0]['count']==1
    assert out['visualizations']['hiring_funnel'][1]['count']==0
    assert out['stages'][-1]['failed']==0
    assert out['visualizations']['response_rate']==0
    assert sum(s['count'] for s in out['visualizations']['score_bands'])==1
    assert sum(d['events'] for d in out['visualizations']['activity_days'])>=50
    assert oi.summary(conn,{'score_band':'unknown'})['record_count']==0
    exported=oi.datasets(conn,{'site':'Direct'})
    assert len(exported['opportunities'])==1 and exported['events']


def test_visual_charts_filter_and_geography_editor_save_in_real_ui(server):
    if os.environ.get('APPLYPILOT_RUN_PLAYWRIGHT')!='1':
        pytest.skip('Set APPLYPILOT_RUN_PLAYWRIGHT=1')
    base,root,conn=server
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1450,'height':1100})
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/workspace#analytics')
            page.locator('.viz-distribution svg').wait_for()
            assert page.locator('.viz-card').count()==12
            if os.environ.get('APPLYPILOT_VISUAL_SCREENSHOTS'):
                from pathlib import Path
                output=Path(os.environ['APPLYPILOT_VISUAL_SCREENSHOTS'])
                assert output.is_dir()
                page.screenshot(path=str(output/'applypilot-visual-insights.png'),full_page=True)
            page.locator('[data-viz-score="unknown"]').click()
            page.wait_for_function("document.querySelector('#activity-total').textContent.includes('0 retained opportunity identities')")
            page.locator('#viz-reset').click()
            page.wait_for_function("document.querySelector('#activity-total').textContent.includes('1 retained opportunity identities')")
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            if os.environ.get('APPLYPILOT_VISUAL_SCREENSHOTS'):
                page.screenshot(path=str(output/'applypilot-visual-insights-mobile.png'),full_page=True)
            page.locator('[data-view="pipeline"]').click()
            page.locator('#geo-default-country').wait_for(state='attached')
            page.locator('#native-config').evaluate('el=>el.open=true')
            page.locator('.geo-editor').evaluate('el=>el.open=true')
            page.locator('#geo-default-country').fill('United Kingdom')
            row=page.locator('[data-geo-row="0"]')
            row.locator('[data-geo="country"]').fill('Canada')
            row.locator('[data-geo="state"]').fill('Ontario')
            row.locator('[data-geo="city"]').fill('Toronto')
            page.locator('#geo-filter-mode').select_option('selected')
            page.locator('#native-config-save').click()
            page.wait_for_function("document.querySelector('#toast')?.textContent.includes('Searches and resume mappings saved.')")
            saved=read_searches_dict(root)
            assert saved['locations'][0]['country']=='Canada' and saved['locations'][0]['state']=='Ontario'
            assert saved['queries'][0]['tier']==3 and saved['resume_routing']['families']
            assert not errors
        finally:
            browser.close()
