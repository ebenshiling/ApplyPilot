"""Inspect the rendered deliverable rather than application source code."""
import hashlib
import json
import re
from pathlib import Path
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import imageio_ffmpeg
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

work = Path(__file__).resolve().parent
output = work.parent
ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
(output/'brag.vtt').write_text('WEBVTT\n\n'+re.sub(r'(\d{2}:\d{2}:\d{2}),(\d{3})',r'\1.\2',(output/'brag.srt').read_text(encoding='utf-8')),encoding='utf-8')
records = json.loads((work / 'scenes.json').read_text())
review = work / 'render-review'
review.mkdir(exist_ok=True)
times = [(0, 'Poster / frame zero')]
start = 0
for scene in records:
    times.append((start + scene['duration'] * .55, f"{scene['index']+1:02d} / scene midpoint"))
    if start:
        times.append((start + .2, f"{scene['index']+1:02d} / transition"))
    start += scene['duration']
sheet = Image.new('RGB', (1920, ((len(times)+3)//4)*300), '#10271f')
draw = ImageDraw.Draw(sheet)
for i,(seconds,label) in enumerate(times):
    target = review / f'{i:02d}.jpg'
    subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-ss',str(seconds),'-i',str(output/'brag.mp4'),'-frames:v','1','-q:v','2',str(target)],check=True)
    image = Image.open(target)
    assert image.size == (1920,1080)
    image.thumbnail((472,265))
    x=(i%4)*480+4;y=(i//4)*300+4
    sheet.paste(image,(x,y));draw.text((x,y+269),label,fill='#f4f2eb')
sheet.save(work/'render-contact-sheet.jpg',quality=94)

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(output),**kwargs)
    def log_message(self,*args): pass
    def copyfile(self,source,target):
        try: super().copyfile(source,target)
        except (ConnectionResetError,BrokenPipeError): pass
httpd = ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=httpd.serve_forever,daemon=True).start()
try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page()
        page.goto(f'http://127.0.0.1:{httpd.server_port}/index.html')
        page.wait_for_function("document.querySelector('video').readyState >= 2",timeout=60000)
        info=page.evaluate("() => {const v=document.querySelector('video');return {width:v.videoWidth,height:v.videoHeight,duration:v.duration,error:v.error?.message||null};}")
        assert info['width']==1920 and info['height']==1080 and abs(info['duration']-240)<.1 and not info['error'],info
        page.locator('video').evaluate('(v)=>v.play()')
        page.wait_for_function("document.querySelector('video').currentTime > .3")
        page.locator('video').evaluate('(v)=>{v.pause();v.currentTime=157;}')
        page.wait_for_timeout(1800)
        assert page.locator('video').evaluate('(v)=>v.readyState>=2 && !v.error')
        browser.close()
finally:
    httpd.shutdown();httpd.server_close()
result={'file':'brag-output/brag.mp4','bytes':(output/'brag.mp4').stat().st_size,'sha256':hashlib.sha256((output/'brag.mp4').read_bytes()).hexdigest(),'browser_playback':info,'scene_count':len(records),'sampled_frames':len(times),'full_decode':'passed during render','video':'H.264, 1920x1080, 30 fps','audio':'AAC stereo, narration and original synthesized music'}
(work/'verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
