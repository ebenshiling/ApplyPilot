# ruff: noqa: F811
"""Measure actual displayed feedback, not just the HTTP queue acknowledgement."""
import json
import os
import threading
import time
import hashlib
import subprocess
from pathlib import Path

import httpx
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from applypilot.apply import assisted_browser as ab
from applypilot.apply import llm_engine
from test_command_center import workspace, http_workspace  # noqa: F401
from test_assisted_browser import packet, wait_state  # noqa: F401


@pytest.fixture
def interaction_portal():
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path.startswith('/frame'):
                html='<label>Embedded field<input></label>'
            elif self.path.startswith('/next'):
                html='<h1>Next page</h1><label>Next answer<input required></label>'
            else:
                html='''<style>body{margin:0}#feedback{display:block;width:200px;height:80px;background:rgb(255,0,0);border:0}#name{position:absolute;top:100px;left:20px;width:300px;height:40px}iframe{display:block;width:500px;height:25px;border:0}#drag{position:absolute;top:260px;left:20px;margin:0;width:280px;height:40px;background:#ddd}#double{position:absolute;top:330px;left:20px;margin:0;width:280px;height:40px}#next{position:absolute;top:210px;left:20px}#final{position:absolute;top:400px;left:400px;width:200px;height:80px}</style>
                <button type="button" id="feedback" onclick="this.dataset.on=this.dataset.on==='yes'?'no':'yes';this.style.background=this.dataset.on==='yes'?'rgb(0,255,0)':'rgb(255,0,0)'">Toggle feedback</button>
                <label>Name<input id="name" required></label><label>Manual consent<input type="checkbox" required></label><a id="next" href="/next">Next page</a>
                <input id="drag" type="range" min="0" max="100" value="0" oninput="if(this.value>70)document.querySelector('#feedback').style.background='rgb(0,255,0)'"><p id="double" ondblclick="document.querySelector('#feedback').style.background='rgb(0,0,255)'">Double click me</p><button id="final" type="button" onclick="document.querySelector('#feedback').style.background='rgb(255,0,255)'">Submit application</button>'''
                html+=''.join(f'<iframe src="/frame?number={i}"></iframe>' for i in range(10))
            self.send_response(200)
            self.send_header('Content-Type','text/html')
            self.end_headers()
            self.wfile.write(html.encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    server.server_close()
    thread.join(3)


PIXEL_READER = """() => {
 const canvasView=document.querySelector('#agent-canvas');const image=canvasView&&!canvasView.hidden?canvasView:document.querySelector('#agent-image');if(!image||!(image.naturalWidth||image.width))return null;
 const canvas=window.__latencyCanvas ||= Object.assign(document.createElement('canvas'),{width:1,height:1});
 const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(image,10,10,1,1,0,0,1,1);
 return Array.from(ctx.getImageData(0,0,1,1).data).slice(0,3);
}"""


def test_mouse_click_to_visible_feedback_under_slow_focus_reads(http_workspace, interaction_portal, monkeypatch):
    base,root,conn=http_workspace
    packet(root,conn,interaction_portal)
    monkeypatch.setattr(llm_engine,'_llm_plan_fills',lambda *args,**kwargs:[])
    original=ab.Session.read_focus
    def slow_focus(self):
        time.sleep(.2)  # Busy third-party frames must not delay the next click.
        return original(self)
    monkeypatch.setattr(ab.Session,'read_focus',slow_focus)
    from playwright.sync_api import sync_playwright
    samples=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1600,'height':1100})
            page.goto(base+'/workspace')
            page.get_by_role('button',name='View record',exact=True).click()
            page.get_by_role('button',name='Apply with agent',exact=True).click()
            page.locator('#agent-documents-approved').check()
            page.locator('#agent-start').click()
            page.locator('#agent-input').wait_for(state='visible',timeout=60000)
            page.wait_for_function('('+PIXEL_READER+")()?.[0]>200",timeout=15000)
            for i in range(8):
                rect=page.locator('#agent-image').bounding_box()
                started=time.perf_counter()
                page.mouse.click(rect['x']+100*rect['width']/1280,rect['y']+40*rect['height']/900)
                channel=1 if i%2==0 else 0
                page.wait_for_function('('+PIXEL_READER+")()?.["+str(channel)+"]>200",polling=16,timeout=10000)
                samples.append(round((time.perf_counter()-started)*1000,2))
            print(json.dumps({'mouse_click_to_display_ms':samples,'mean_ms':round(sum(samples)/len(samples),2),'p95_ms':sorted(samples)[-1],'fixture':'10 visible iframes, 200ms simulated focus read, real UI mouse and displayed frame pixel'}))
            assert sum(samples)/len(samples)<450 and max(samples)<700
            page.locator('#agent-stop').click()
        finally:
            browser.close()
    for ident,session in list(ab._sessions.items()):
        if session.root==root.resolve():
            if session.state not in ab.TERMINAL:
                ab.command(root,{'session_id':ident,'action':'stop'})
            wait_state(root,ident,{'stopped'})
            ab._sessions.pop(ident,None)


def test_real_pointer_drag_double_click_typing_and_final_guard(http_workspace, interaction_portal, monkeypatch):
    base,root,conn=http_workspace
    packet(root,conn,interaction_portal)
    monkeypatch.setattr(llm_engine,'_llm_plan_fills',lambda *args,**kwargs:[])
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1600,'height':1100})
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/workspace')
            page.get_by_role('button',name='View record',exact=True).click()
            page.get_by_role('button',name='Apply with agent',exact=True).click()
            page.locator('#agent-documents-approved').check()
            page.locator('#agent-start').click()
            page.locator('#agent-input').wait_for(state='visible',timeout=60000)
            page.wait_for_function('('+PIXEL_READER+")()?.[0]>200")
            rect=page.locator('#agent-image').bounding_box()
            def point(x,y):return rect['x']+x*rect['width']/1280,rect['y']+y*rect['height']/900
            page.mouse.move(*point(100,40))
            page.mouse.down()
            page.evaluate("window.dispatchEvent(new Event('blur'))")
            page.mouse.up()
            page.wait_for_timeout(150)
            assert page.evaluate(PIXEL_READER)[0]>200  # Cancel did not click the pressed button.
            page.mouse.move(*point(25,280))
            page.mouse.down()
            page.mouse.move(*point(285,280),steps=12)
            page.mouse.up()
            page.wait_for_function('('+PIXEL_READER+")()?.[1]>200",timeout=3000)
            page.mouse.dblclick(*point(100,350))
            page.wait_for_function('('+PIXEL_READER+")()?.[2]>200",timeout=3000)
            page.mouse.click(*point(100,120))
            page.keyboard.type('Fast mouse-selected input',delay=0)
            page.wait_for_function("document.querySelector('#agent-question-context').textContent.includes('Name')")
            ident=next(s.id for s in ab._sessions.values() if s.root==root.resolve())
            expected=hashlib.sha256(b'Fast mouse-selected input').hexdigest()
            deadline=time.monotonic()+3
            while (ab.status(root,ident)['session'].get('focused_field') or {}).get('value_hash')!=expected and time.monotonic()<deadline:
                time.sleep(.03)
            assert ab.status(root,ident)['session']['focused_field']['value_hash']==expected
            page.mouse.click(*point(450,440))
            page.wait_for_timeout(200)
            color=page.evaluate(PIXEL_READER)
            assert color[2]>200 and color[0]<40  # Guard blocked onclick.
            assert not conn.execute('SELECT applied_at FROM jobs WHERE rowid=1').fetchone()[0]
            assert not errors
            page.mouse.click(*point(50,225))
            deadline=time.monotonic()+4
            while '/next' not in ab.status(root,ident)['session']['page_url'] and time.monotonic()<deadline:
                time.sleep(.03)
            assert '/next' in ab.status(root,ident)['session']['page_url']
            page.locator('#agent-stop').click()
            wait_state(root,ident,{'stopped'})
        finally:
            browser.close()
    ab._sessions.pop(ident,None)


def test_authenticated_browser_hot_routes_skip_workspace_setup_and_keep_auth_csrf_ownership(http_workspace, monkeypatch):
    base,root,conn=http_workspace
    import applypilot.dashboard_server as ds
    import applypilot.multiuser as multi
    monkeypatch.setattr(ds,'_MULTI_USER_MODE',True)
    users={'alice-token':{'username':'alice'},'bob-token':{'username':'bob'}}
    monkeypatch.setattr(ds,'get_user_by_session',lambda token:users.get(token))
    monkeypatch.setattr(multi,'user_workspace',lambda name:root if name=='alice' else root/'other-workspace')
    monkeypatch.setattr(ds,'ensure_workspace_for_user',lambda *a:pytest.fail('Live browser initialized workspace schema'))
    monkeypatch.setattr(ds,'_runner_for_user',lambda *a:pytest.fail('Live browser initialized pipeline runner'))
    import applypilot.command_api as api
    monkeypatch.setattr(api,'workspace_connection',lambda *a:pytest.fail('Live browser initialized command-center schema'))
    session=ab.Session(root.resolve(),'owned-live',{'id':1,'url':'https://example.org/fixture'},{},{},{},[])
    session.state='manual'
    session.image=b'\xff\xd8Fixture JPEG'
    ab._sessions[session.id]=session
    try:
        with httpx.Client(base_url=base,timeout=5) as client:
            path='/api/center/browser?session_id='+session.id
            assert client.get(path).status_code==401
            client.cookies.set('ap_session','alice-token')
            client.cookies.set('ap_csrf','csrf-fixture')
            assert client.get(path).status_code==200
            assert client.get('/api/center/browser/screenshot?session_id='+session.id).status_code==200
            data={'session_id':session.id,'action':'input','compact':True,'events':[{'type':'text','text':'Test'}]}
            assert client.post('/api/center/browser/command',json=data).status_code==403
            out=client.post('/api/center/browser/command',json=data,headers={'X-CSRF-Token':'csrf-fixture'}).json()
            assert out['accepted'] and 'session' not in out
            client.cookies.set('ap_session','bob-token')
            assert client.get(path).status_code==404
            assert client.post('/api/center/browser/command',json=data,headers={'X-CSRF-Token':'csrf-fixture'}).status_code==400
            client.cookies.set('ap_session','alice-token')
            users.pop('alice-token')
            assert client.get(path).status_code==401
    finally:
        session.state='stopped'
        ab._sessions.pop(session.id,None)


def test_frame_parser_emits_body_without_waiting_for_next_frame_and_bounds_buffers():
    script=Path(__file__).resolve().parents[1]/'src/applypilot/web/browser-frame-view.js'
    source=f"require({json.dumps(str(script))});"+r"""
      const assert=require('node:assert/strict'),{MultipartFrames}=BrowserFrameView;
      const jpeg=Buffer.from([255,216,255,217]);
      const header=Buffer.from('--applypilot-frame\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\nX-Frame-Sequence: 2\r\nX-Input-Command: 7\r\n\r\n');
      for(const chunkSize of [1,7,17,4096]){
        const frames=[],parser=new MultipartFrames(f=>frames.push(f));
        const body=Buffer.concat([header,jpeg]);
        for(let i=0;i<body.length;i+=chunkSize)parser.feed(body.subarray(i,i+chunkSize));
        assert.equal(frames.length,1);assert.equal(frames[0].inputId,7);assert.equal(frames[0].sequence,'2');
        assert.deepEqual(Array.from(frames[0].bytes),Array.from(jpeg));
        parser.feed(Buffer.concat([Buffer.from('\r\n'),header,jpeg]));assert.equal(frames.length,2);
      }
      for(const length of ['0','99999999','1.5'])assert.throws(()=>new MultipartFrames(()=>{}).feed(Buffer.from('--frame\r\nContent-Length: '+length+'\r\n\r\n')));
      assert.throws(()=>new MultipartFrames(()=>{}).feed(new Uint8Array(9*1024*1024)));
      console.log('Incremental complete-body decoding and memory limits passed');
    """
    result=subprocess.run(['node','-e',source],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('event',[{'type':'mouse','phase':'invalid'},{'type':'mouse','phase':'down','button':'none'},
                                  {'type':'mouse','phase':'up','button':'left','click_count':9},
                                  {'type':'mouse','phase':'move','buttons':999}])
def test_invalid_pointer_batches_are_rejected_before_dispatch(event):
    with pytest.raises(ValueError):
        ab.input_events([event])


def test_slow_audit_writer_does_not_block_input_and_terminal_flush_keeps_all_batches(workspace, interaction_portal, monkeypatch):
    root,conn=workspace
    packet(root,conn,interaction_portal)
    monkeypatch.setattr(llm_engine,'_llm_plan_fills',lambda *a,**kw:[])
    from applypilot import command_center as cc
    original=cc.event
    def slow_event(connection,url,kind,*args,**kwargs):
        if kind=='browser_input':
            time.sleep(.4)
        return original(connection,url,kind,*args,**kwargs)
    monkeypatch.setattr(cc,'event',slow_event)
    ident=ab.start(conn,root,{'id':1,'documents_approved':True})['session']['id']
    ids=[]
    try:
        wait_state(root,ident,{'manual'},timeout=60)
        for i in range(6):
            events=([{'type':'click','x':100,'y':120}] if not i else [])+[{'type':'text','text':f'Fixture text {i}'}]
            ids.append(ab.command(root,{'session_id':ident,'action':'input','events':events})['command_id'])
        deadline=time.monotonic()+5
        while not all(any(r['id']==n for r in ab.status(root,ident)['session']['command_results']) for n in ids) and time.monotonic()<deadline:
            time.sleep(.02)
        results=[r for r in ab.status(root,ident)['session']['command_results'] if r['id'] in ids]
        assert len(results)==6 and all(r['ok'] for r in results)
        assert max(r['latency_ms'] for r in results)<1000
        ab.command(root,{'session_id':ident,'action':'stop'})
        wait_state(root,ident,{'stopped'})
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            rows=[json.loads(r[0]) for r in conn.execute("SELECT payload FROM cc_events WHERE kind='browser_input'")]
            recorded=[s['command_id'] for row in rows for s in row.get('batches',[])]
            if set(recorded)==set(ids):
                break
            time.sleep(.03)
        assert sorted(recorded)==sorted(ids)
        assert 'Fixture text' not in ''.join(r[0] for r in conn.execute("SELECT payload FROM cc_events WHERE kind='browser_input'"))
    finally:
        if ab.status(root,ident)['session']['state'] not in ab.TERMINAL:
            ab.command(root,{'session_id':ident,'action':'stop'})
            wait_state(root,ident,{'stopped'})
        ab._sessions.pop(ident,None)
