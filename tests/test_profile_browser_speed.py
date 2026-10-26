import json
import os
import time

import httpx
import pytest

from applypilot.application_profile import application_values,contact_values,local_fills
from applypilot.apply import assisted_browser as ab
from applypilot.apply import llm_engine
from applypilot.setup_workspace import write_profile,read_profile
from test_command_center import workspace,http_workspace  # noqa: F401, F811
from test_assisted_browser import portal,planner,packet,wait_state  # noqa: F401, F811


def test_profile_merge_and_legacy_address_state_are_preserved(workspace):
    root,_=workspace
    write_profile(root,{'personal':{'full_name':'Example Middle Candidate','address':'Old street','province_state':'Existing region','custom_fact':'Keep'},'eeo_voluntary':{'veteran_status':'Prefer not to say'}})
    write_profile(root,{'personal':{'title':'Mx','first_name':'Example','middle_names':'Middle','last_name':'Candidate','address_line1':'New street','address_line2':'Flat 4','postal_code':'AB1 2CD','date_of_birth':'1990-02-03'},'eeo_voluntary':{'gender':'Non-binary'}})
    profile=read_profile(root)
    values=application_values(profile)
    assert values['address_line1']=='New street' and values['address_line2']=='Flat 4'
    assert values['province_state']=='Existing region' and values['middle_names']=='Middle'
    assert profile['personal']['custom_fact']=='Keep' and values['veteran_status']=='Prefer not to say'
    assert 'gender' not in contact_values(profile['personal']) and 'date_of_birth' not in contact_values(profile['personal'])


def test_local_values_fill_title_address_gender_and_saved_authorization_only():
    profile={'personal':{'title':'Mr','first_name':'Actual','last_name':'Name','address_line1':'45 Main Road','country':'United Kingdom','date_of_birth':'1990-01-02'},'eeo_voluntary':{'gender':'Male'},'work_authorization':{'require_sponsorship':'Yes'}}
    fields=[{'id':'title','tag':'select','type':'select-one','label':'Title','value':'0','options':[{'label':'Please select','value':'0'},{'label':'Mr','value':'1'}]},
            {'id':'street','tag':'input','type':'text','autocomplete':'address-line1','value':''},
            {'id':'dob','tag':'input','type':'date','label':'Date of birth','value':''},
            {'id':'gender','tag':'select','type':'select-one','label':'Gender','value':'','options':[{'label':'Male','value':'M'},{'label':'Female','value':'F'}]},
            {'id':'sponsor','tag':'select','type':'select-one','label':'Do you require sponsorship?','value':'','options':[{'label':'Yes','value':'Y'},{'label':'No','value':'N'}]},
            {'id':'religion','tag':'input','type':'text','label':'Religion','value':''},
            {'id':'consent','tag':'input','type':'checkbox','label':'I agree to the privacy notice','value':''}]
    result={a['key']:a for a in local_fills(profile,fields)}
    assert result['title']['value']=='1' and result['title']['method']=='select'
    assert result['street']['value']=='45 Main Road' and result['dob']['value']=='1990-01-02'
    assert result['gender']['value']=='M' and result['sponsor']['value']=='Y'
    assert 'religion' not in result and 'consent' not in result


def test_existing_fields_radio_choices_and_unsupported_options_are_not_overwritten():
    profile={'personal':{'first_name':'Saved'},'eeo_voluntary':{'gender':'Male'}}
    fields=[{'id':'first','tag':'input','type':'text','label':'First name','value':'User correction'},
            {'id':'male','name':'gender','tag':'input','type':'radio','label':'Male','group_label':'Gender','value':'M','checked':False},
            {'id':'female','name':'gender','tag':'input','type':'radio','label':'Female','group_label':'Gender','value':'F','checked':True}]
    assert local_fills(profile,fields)==[]
    fields[-1]['checked']=False
    result=local_fills(profile,fields)
    assert len(result)==1 and result[0]['key']=='male' and result[0]['method']=='check'


def test_sensitive_fields_are_not_sent_to_the_model(monkeypatch):
    monkeypatch.setattr(llm_engine,'get_client',lambda:pytest.fail('Sensitive local-only fields reached the model'))
    fields=[{'id':'gender','type':'text','label':'Gender'},{'id':'dob','type':'date','label':'Date of birth'}]
    assert llm_engine._llm_plan_fills({}, {'full_name':'Candidate'},fields,verified_answers=[])==[]


@pytest.mark.parametrize('events',[[{'type':'approve'}],[{'type':'text','text':'x'*15001}],[{'type':'key','key':'Control+P'}],[],[{'type':'scroll'}]*65])
def test_bad_input_batch_is_rejected_atomically(events):
    with pytest.raises(ValueError):
        ab.input_events(events)


def test_profile_ui_roundtrip_has_complete_application_details(http_workspace):
    if os.environ.get('APPLYPILOT_RUN_PLAYWRIGHT')!='1':
        pytest.skip('Set APPLYPILOT_RUN_PLAYWRIGHT=1')
    base,root,_=http_workspace
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page()
            page.goto(base+'/workspace#settings')
            def field(group,key):return page.locator(f'[data-profile-group="{group}"][data-profile-key="{key}"]')
            field('personal','title').fill('Mx')
            field('personal','first_name').fill('Example')
            field('personal','middle_names').fill('Middle')
            field('personal','last_name').fill('Candidate')
            field('personal','address_line1').fill('45 Main Road')
            field('personal','address_line2').fill('Flat 4')
            field('personal','postal_code').fill('AB1 2CD')
            field('personal','date_of_birth').fill('1990-02-03')
            field('eeo_voluntary','gender').fill('Non-binary')
            field('eeo_voluntary','disability_status').fill('Prefer not to say')
            page.locator('#native-profile-save').click()
            page.wait_for_function("document.querySelector('#save-state').textContent==='Saved'")
            saved=read_profile(root)
            assert saved['personal']['full_name']=='Example Middle Candidate'
            assert saved['personal']['address']==saved['personal']['address_line1']=='45 Main Road'
            assert saved['eeo_voluntary']['gender']=='Non-binary'
            page.reload()
            assert field('personal','address_line2').input_value()=='Flat 4'
            assert field('eeo_voluntary','gender').input_value()=='Non-binary'
        finally:
            browser.close()


def test_hot_paths_skip_database_and_input_batch_preserves_order(http_workspace,portal,planner,monkeypatch):
    base,root,conn=http_workspace
    employer,_=portal
    packet(root,conn,employer+'/login')
    ident=ab.start(conn,root,{'id':1,'documents_approved':True})['session']['id']
    try:
        wait_state(root,ident,{'manual'},timeout=60)
        import applypilot.command_api as api
        monkeypatch.setattr(api,'init_db',lambda *args:pytest.fail('Browser hot path initialized database'))
        with httpx.Client(base_url=base,timeout=10) as client:
            start=time.perf_counter()
            response=client.post('/api/center/browser/command',json={'session_id':ident,'action':'input','events':[{'type':'key','key':'Tab'},{'type':'text','text':'BufferedPassword'},{'type':'key','key':'Tab'},{'type':'key','key':'Enter'}]})
            response.raise_for_status()
            ack_ms=(time.perf_counter()-start)*1000
            assert ack_ms<1000
            assert client.get('/api/center/browser?session_id='+ident).status_code==200
            assert client.get('/api/center/browser/screenshot?session_id='+ident).content.startswith(b'\xff\xd8')
            with client.stream('GET','/api/center/browser/stream?session_id='+ident) as stream:
                assert stream.headers['content-type'].startswith('multipart/x-mixed-replace')
                first=next(stream.iter_bytes())
                assert b'applypilot-frame' in first
        deadline=time.monotonic()+15
        while '/login' in ab.status(root,ident)['session']['page_url'] and time.monotonic()<deadline:
            time.sleep(.05)
        assert '/login' not in ab.status(root,ident)['session']['page_url']
        assert ab.status(root,ident)['session']['performance']['last_input_ms'] is not None
        print(json.dumps({'fixture_input_batch_events':4,'request_ack_ms':round(ack_ms,2),'last_input_processed_ms':ab.status(root,ident)['session']['performance']['last_input_ms'],'schema_initializations_in_hot_path':0}))
    finally:
        ab.command(root,{'session_id':ident,'action':'stop'})
        wait_state(root,ident,{'stopped'})
        ab._sessions.pop(ident,None)


def test_fast_typing_uses_batched_requests_and_live_stream(http_workspace,portal,planner):
    base,root,conn=http_workspace
    employer,_=portal
    packet(root,conn,employer+'/login')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1500,'height':1100})
            requests=[]
            page.on('request',lambda r:requests.append(r.post_data_json) if r.method=='POST' and r.url.endswith('/api/center/browser/command') else None)
            page.goto(base+'/workspace')
            page.get_by_role('button',name='View record',exact=True).click()
            page.get_by_role('button',name='Apply with agent',exact=True).click()
            page.locator('#agent-documents-approved').check()
            page.locator('#agent-start').click()
            page.locator('#agent-input').wait_for(state='visible',timeout=60000)
            page.wait_for_function("document.querySelector('#agent-image').naturalWidth===1280")
            assert page.locator('#agent-image').get_attribute('data-transport')=='decoded_frames'
            page.locator('#agent-image').focus()
            page.keyboard.press('Tab')  # Moves local focus; use explicit remote Tab button below.
            page.get_by_role('button',name='Tab',exact=True).click()
            page.locator('#agent-image').focus()
            page.keyboard.type('A smooth buffered sentence',delay=0)
            page.wait_for_timeout(250)
            batches=[r for r in requests if r.get('action')=='input']
            assert len(batches)<8
            texts=''.join(' ' if e.get('key')=='Space' else e.get('text','') for r in batches for e in r['events'])
            assert texts=='A smooth buffered sentence'
            assert not any(r.get('action')=='text' for r in requests)
            page.locator('#agent-stop').click()
        finally:
            browser.close()
    for ident,session in list(ab._sessions.items()):
        if session.root==root.resolve():
            if session.state not in ab.TERMINAL:
                ab.command(root,{'session_id':ident,'action':'stop'})
                wait_state(root,ident,{'stopped'})
            ab._sessions.pop(ident,None)
