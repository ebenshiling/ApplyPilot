# ruff: noqa: F811
import os

import httpx
import pytest
import yaml

from applypilot import source_registry as sr
from applypilot.config import load_sites_config
from applypilot.discovery.smartextract import build_scrape_targets
from test_pipeline_workspace import workspace, server  # noqa: F401


def test_indeed_homepage_save_and_preview_share_validation_before_fetch(workspace, monkeypatch):
    root, conn = workspace
    item = {"name":"Indeed","type":"search","url":"https://uk.indeed.com/"}
    monkeypatch.setattr(httpx,'Client',lambda *a,**kw:pytest.fail('Invalid search template reached network'))
    for operation in (lambda:sr.save_source(root,item), lambda:sr.preview(root,item,'Data Analyst','London')):
        with pytest.raises(ValueError,match='Search URL templates need') as exc:
            operation()
        assert sr.INDEED_TEMPLATE in str(exc.value)
    assert not (root/'sites.yaml').exists()
    assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1


def test_corrected_indeed_template_saves_and_expands_without_network(workspace):
    root, _ = workspace
    saved=sr.save_source(root,{'name':'Indeed','type':'search','url':sr.INDEED_TEMPLATE,'base_url':''})
    assert saved['id']=='indeed' and saved['base_url']=='https://uk.indeed.com'
    target=build_scrape_targets([saved],{'queries':[{'query':'Application Support Analyst'}],'locations':[{'location':'London'}]})[0]
    assert target['url']=='https://uk.indeed.com/jobs?q=Application+Support+Analyst&l=London'
    assert any(s['id']=='indeed' for s in load_sites_config(root)['sites'])
    with pytest.raises(ValueError,match='already exists'):
        sr.save_source(root,{'name':'Indeed','type':'search','url':sr.INDEED_TEMPLATE})


def test_static_location_template_remains_backward_compatible(workspace):
    root,_=workspace
    saved=sr.save_source(root,{'name':'Regional listing','type':'static','url':'https://example.org/{location_encoded}/jobs'})
    target=build_scrape_targets([saved],{'queries':[{'query':'Analyst'}],'locations':[{'location':'Bristol'}]})[0]
    assert target['url']=='https://example.org/Bristol/jobs'


@pytest.mark.parametrize('url,kind',[
    ('https://example.org/jobs?q={query_encoded','search'),
    ('https://example.org/jobs?q={query_encoded!r}','search'),
    ('https://example.org/jobs?q={{query_encoded}}','search'),
    ('https://example.org/jobs?q={unsupported}','search'),
    ('https://example.org/jobs?q={query_encoded}','static'),
])
def test_malformed_templates_are_actionable_and_not_persisted(workspace,url,kind):
    root,_=workspace
    with pytest.raises(ValueError):
        sr.save_source(root,{'name':'Fixture Source','type':kind,'url':url})
    assert not (root/'sites.yaml').exists()


def test_edit_preserves_advanced_source_metadata_and_other_catalog_entries(workspace):
    root,_=workspace
    data={'sites':[{'id':'custom','name':'Custom','type':'search','url':'https://example.org/jobs?q={query_encoded}','location_mode':'region'},
                   {'id':'other','name':'Other','type':'static','url':'https://other.example.org/jobs'}],
          'base_urls':{'Custom':'https://example.org/departments/','Other':'https://other.example.org'},'custom_option':{'keep':True}}
    (root/'sites.yaml').write_text(yaml.safe_dump(data),encoding='utf-8')
    sr.save_source(root,{'id':'custom','name':'Custom','type':'search','url':'https://example.org/search?q={query_encoded}','base_url':'https://example.org/departments/'})
    after=yaml.safe_load((root/'sites.yaml').read_text())
    assert next(s for s in after['sites'] if s['id']=='custom')['location_mode']=='region'
    assert next(s for s in after['sites'] if s['id']=='other')==data['sites'][1]
    assert after['custom_option']==data['custom_option']
    assert next(s for s in load_sites_config(root)['sites'] if s['id']=='custom')['base_url']=='https://example.org/departments/'


@pytest.mark.parametrize('content',['sites: {}','sites: [broken]','base_urls: []','[not, a, mapping]','sites: ['])
def test_invalid_workspace_yaml_is_reported_without_overwrite(workspace,content):
    root,_=workspace
    path=root/'sites.yaml'
    path.write_text(content,encoding='utf-8')
    with pytest.raises(ValueError,match='Workspace sites.yaml'):
        sr.save_source(root,{'name':'New','type':'static','url':'https://example.org/jobs'})
    assert path.read_text()==content


def preview_client(monkeypatch,response=None,error=None):
    original=sr.validate_url
    monkeypatch.setattr(sr,'validate_url',lambda url,**kw:original(url,resolve=False))
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def get(self,url):
            if error:
                raise error
            if callable(response):
                return response(url)
            return httpx.Response(response,request=httpx.Request('GET',url))
    monkeypatch.setattr(httpx,'Client',Client)


@pytest.mark.parametrize('status,category',[(403,'blocked'),(429,'rate_limited'),(404,'not_found'),(503,'site_error')])
def test_site_http_errors_are_categorized_without_provider_advice(workspace,monkeypatch,status,category):
    root,conn=workspace
    preview_client(monkeypatch,response=status)
    with pytest.raises(sr.SourcePreviewUnavailable) as exc:
        sr.preview(root,{'name':'Indeed','type':'search','url':sr.INDEED_TEMPLATE},'Analyst','London')
    assert exc.value.category==category and exc.value.http_status==status
    assert f'HTTP {status}' in str(exc.value) and 'can still be saved' in str(exc.value)
    assert 'configuration' not in str(exc.value)
    assert not (root/'sites.yaml').exists()
    assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1


@pytest.mark.parametrize('error,category',[(httpx.ReadTimeout('private request details'),'timeout'),(httpx.ConnectError('private request details'),'network')])
def test_network_errors_are_sanitized_and_actionable(workspace,monkeypatch,error,category):
    root,_=workspace
    preview_client(monkeypatch,error=error)
    with pytest.raises(sr.SourcePreviewUnavailable) as exc:
        sr.preview(root,{'name':'Fixture','type':'static','url':'https://example.org/jobs'},'Analyst','London')
    assert exc.value.category==category and 'private request details' not in str(exc.value)


def test_preview_checks_private_redirect_and_resolves_declared_relative_base(workspace,monkeypatch):
    root,_=workspace
    preview_client(monkeypatch,response=lambda url:httpx.Response(302,headers={'location':'http://127.0.0.1/private'},request=httpx.Request('GET',url)))
    with pytest.raises(ValueError,match='private address'):
        sr.preview(root,{'name':'Fixture','type':'static','url':'https://example.org/jobs'},'Analyst','London')
    html='<script type="application/ld+json">{"@type":"JobPosting","title":"Analyst","url":"jobs/1"}</script><a href="jobs/2">Second job</a>'
    preview_client(monkeypatch,response=lambda url:httpx.Response(200,text=html,request=httpx.Request('GET',url)))
    result=sr.preview(root,{'name':'Fixture','type':'static','url':'https://example.org/listing','base_url':'https://example.org/teams/'},'Analyst','London')
    assert result['jobs'][0]['url']=='https://example.org/teams/jobs/1'
    assert result['candidate_links'][0]['url']=='https://example.org/teams/jobs/2'


def test_preview_http_reports_site_error_and_invalid_template_distinctly(server,monkeypatch):
    base,root,conn=server
    def blocked(*args):raise sr.SourcePreviewUnavailable('uk.indeed.com blocked the automated preview (HTTP 403). The source can still be saved.','blocked',http_status=403)
    monkeypatch.setattr(sr,'_preview_targets',blocked)
    with httpx.Client(base_url=base,timeout=15) as client:
        item={'name':'Indeed','type':'search','url':'https://uk.indeed.com/'}
        result=client.post('/api/center/pipeline/source/preview',json={'source':item})
        assert result.status_code==400 and 'query_encoded' in result.json()['error']
        item['url']=sr.INDEED_TEMPLATE
        result=client.post('/api/center/pipeline/source/preview',json={'source':item})
        assert result.status_code==502 and result.json()['category']=='blocked'
        assert result.json()['remote_http_status']==403 and result.json()['can_save_source'] is True
        assert client.post('/api/center/pipeline/source/save',json=item).status_code==200
    assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1


def test_source_ui_corrects_homepage_preserves_edits_and_shows_block_inline(server,monkeypatch):
    if os.environ.get('APPLYPILOT_RUN_PLAYWRIGHT')!='1':
        pytest.skip('Set APPLYPILOT_RUN_PLAYWRIGHT=1')
    base,root,conn=server
    def blocked(*args):raise sr.SourcePreviewUnavailable('uk.indeed.com blocked the automated preview (HTTP 403). The source can still be saved.','blocked',http_status=403)
    monkeypatch.setattr(sr,'_preview_targets',blocked)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1400,'height':1100})
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/workspace#pipeline')
            page.locator('#native-source-name').wait_for(state='attached')
            page.locator('#native-source-name').evaluate("el=>el.closest('details').open=true")
            page.locator('#native-queries').fill('Unsaved role override')
            page.locator('#native-source-name').fill('Indeed')
            page.locator('#native-source-url').fill('https://uk.indeed.com/')
            page.locator('#native-source-save').click()
            page.wait_for_function("document.querySelector('#native-source-feedback').textContent.includes('query_encoded')")
            assert page.locator('#native-source-url').input_value()=='https://uk.indeed.com/'
            page.locator('#native-source-use-indeed').click()
            assert page.locator('#native-source-url').input_value()==sr.INDEED_TEMPLATE
            page.locator('#native-source-save').click()
            page.wait_for_function("document.querySelector('#native-source-feedback').textContent.includes('Saved Indeed')")
            assert page.locator('#native-source-id').input_value()=='indeed'
            assert page.locator('#native-queries').input_value()=='Unsaved role override'
            page.locator('#native-source-test').click()
            page.wait_for_function("document.querySelector('#native-source-feedback').textContent.includes('HTTP 403')")
            assert page.locator('#native-source-name').input_value()=='Indeed'
            assert next(s for s in load_sites_config(root)['sites'] if s['id']=='indeed')['url']==sr.INDEED_TEMPLATE
            assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1 and not errors
        finally:
            browser.close()
