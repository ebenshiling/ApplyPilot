import json
import threading
import time
from types import SimpleNamespace

import pytest

from applypilot.apply import assisted_browser as ab
from applypilot import decision_observer
from test_command_center import workspace  # noqa: F401, F811
from test_assisted_browser import portal, planner, packet, wait_state  # noqa: F401, F811


def test_known_postback_listing_excludes_submit_inputs_and_header_login():
    frame=SimpleNamespace(url='https://barhale.kallidusrecruit.com/VacancyInformation.aspx?VId=1844')
    info={'fields':[],'postback_listing':True,'text':'IT Application Support Analyst',
          'controls':[{'index':0,'label':'Login','tag':'a','href':'javascript:postback()','disabled':False},
                      *[{'index':i,'label':'Apply','tag':'input','href':'','disabled':False,'postback_entry':True,'name':f'ApplyNowButton{i}'} for i in (1,2)]]}
    entries=ab.listing_entries([(frame,info)])
    assert len(entries)==1 and entries[0][1]['index']==1 and entries[0][2]
    view=decision_observer.page_view([info])
    assert decision_observer.baseline(view)['page_state']=='listing'
    info['fields']=[{'type':'text','value':'Reviewed answer','label':'Supporting information','valid':True}]
    assert not ab.listing_entries([(frame,info)])
    info['fields']=[]
    frame.url='https://barhale.kallidusrecruit.com/ReviewApplication.aspx'
    assert not ab.listing_entries([(frame,info)])
    frame.url='https://unverified.example/VacancyInformation.aspx'
    assert not ab.listing_entries([(frame,info)])


@pytest.mark.parametrize('operation,message,expected',[
    ('inspect','Execution context was destroyed, password=secret','navigation_changed'),
    ('capture','Timeout 5000ms exceeded: token=secret','screenshot_timeout'),
    ('navigate','Timeout 5000ms exceeded','navigation_timeout'),
    ('capture','Target page, context or browser has been closed','browser_closed'),
    ('launch','Executable missing','browser_launch'),
])
def test_error_categories_do_not_include_raw_request_data(operation,message,expected):
    assert ab.error_category(RuntimeError(message),operation)==expected


def test_entry_guard_authorizes_only_the_current_element(portal):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            context=browser.new_context()
            context.add_init_script(ab.GUARD)
            page=context.new_page()
            page.goto('data:text/html,<form><input type=hidden value=server-token><input id=entry type=submit value=Apply><input id=final type=submit value="Submit application"></form><script>window.posts=0;document.querySelector("form").addEventListener("submit",e=>{e.preventDefault();window.posts++})</script>')
            assert page.evaluate(ab.INSPECT)['fields']==[]
            page.locator('#entry').click()
            assert page.evaluate('window.posts')==0
            page.locator('#entry').evaluate('el=>window.__applypilotEntryControl=el')
            page.locator('#final').click()
            assert page.evaluate('window.posts')==0
            page.locator('#entry').click()
            assert page.evaluate('window.posts')==1
            page.evaluate('window.__applypilotEntryControl=null')
            page.locator('form').evaluate('el=>el.requestSubmit()')
            assert page.evaluate('window.posts')==1
        finally:
            browser.close()


@pytest.mark.parametrize('operation',['advance','capture','inspect'])
def test_transient_error_preserves_manual_session_and_allows_resume(workspace,portal,planner,monkeypatch,operation):
    root,conn=workspace
    base,posts=portal
    packet(root,conn,base+'/uncertain')
    original=getattr(ab.Session,operation)
    injected=threading.Event()
    def flaky(self,*args,**kwargs):
        # Startup capture succeeds; inject when work starts, including the
        # queue.Empty branch or the post-action inspection/capture boundary.
        if not injected.is_set() and self.state in {'running','review'}:
            injected.set()
            self.operation='capture' if operation=='capture' else 'inspect'
            raise RuntimeError('Execution context was destroyed password=FixturePrivateSecret')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(ab.Session,operation,flaky)
    ident=ab.start(conn,root,{'id':1,'documents_approved':True})['session']['id']
    try:
        manual=wait_state(root,ident,{'manual'},timeout=60)
        assert injected.is_set() and manual['last_error']['category']=='navigation_changed'
        assert not posts and not cc_applied(conn)
        diagnostic=json.loads((root/'browser_sessions'/ident/'diagnostic.json').read_text())
        assert 'FixturePrivateSecret' not in json.dumps(diagnostic)
        deadline=time.monotonic()+10
        while not ab._sessions[ident].image and time.monotonic()<deadline:
            time.sleep(.1)
        assert ab.screenshot(root,ident).startswith(b'\xff\xd8')
        ab.command(root,{'session_id':ident,'action':'resume'})
        review=wait_state(root,ident,{'review'},timeout=20)
        assert review['approval_token'] and not posts
    finally:
        ab.command(root,{'session_id':ident,'action':'stop'})
        wait_state(root,ident,{'stopped'})
        ab._sessions.pop(ident,None)


def cc_applied(conn):
    return conn.execute('SELECT applied_at FROM jobs WHERE rowid=1').fetchone()[0]


@pytest.mark.parametrize('with_cookies',[False,True])
def test_kallidus_postback_entry_reaches_login_without_final_approval(workspace,portal,planner,monkeypatch,with_cookies):
    from playwright.sync_api import Browser
    root,conn=workspace
    base,posts=portal
    target='https://barhale.kallidusrecruit.com/VacancyInformation.aspx?VId=1844'
    packet(root,conn,target)
    create=Browser.new_context
    def context_with_fixture(browser,*args,**kwargs):
        context=create(browser,*args,**kwargs)
        html=f'''<form action="{base}/login" method="get"><input type="hidden" value="server-state"><a href="#">Login</a><div id="vacancy-information"><input class="AdvortoApplyNowButton" type="submit" name="ApplyNowButton1" value="Apply"><input class="AdvortoApplyNowButton" type="submit" name="ApplyNowButton2" value="Apply"></div></form>'''
        if with_cookies:
            html+='<iframe src="/ico-compliance/index.html" style="position:fixed;inset:0;width:100vw;height:100vh;z-index:9999;background:white"></iframe>'
        cookie_html='''<button onclick="parent.document.querySelector('iframe').style.display='none'">Save my settings</button><button onclick="parent.document.querySelector('iframe').style.display='none'">Accept all cookies</button>'''
        context.route('https://barhale.kallidusrecruit.com/**',lambda route:route.fulfill(status=200,content_type='text/html',body=cookie_html if '/ico-compliance/' in route.request.url else html))
        return context
    monkeypatch.setattr(Browser,'new_context',context_with_fixture)
    ident=ab.start(conn,root,{'id':1,'documents_approved':True})['session']['id']
    try:
        manual=wait_state(root,ident,{'manual'},timeout=60)
        if with_cookies:
            assert manual['cookie_controls'] and 'Cookie preferences' in manual['detail']
            assert not posts and not cc_applied(conn)
            ab.command(root,{'session_id':ident,'action':'cookie','choice':'save_settings'})
            deadline=time.monotonic()+10
            while ab.status(root,ident)['session']['cookie_controls'] and time.monotonic()<deadline:
                time.sleep(.1)
            assert not ab.status(root,ident)['session']['cookie_controls']
            ab.command(root,{'session_id':ident,'action':'resume'})
            deadline=time.monotonic()+20
            while not ab.status(root,ident)['session']['page_url'].startswith(base+'/login') and time.monotonic()<deadline:
                time.sleep(.1)
            manual=wait_state(root,ident,{'manual'},timeout=20)
        assert manual['page_url'].startswith(base+'/login')
        assert 'Login or a security check' in manual['detail']
        assert not manual['approval_token'] and not posts and not cc_applied(conn)
    finally:
        ab.command(root,{'session_id':ident,'action':'stop'})
        wait_state(root,ident,{'stopped'})
        ab._sessions.pop(ident,None)
