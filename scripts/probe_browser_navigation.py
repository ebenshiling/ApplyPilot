"""Inspect a public advert without applicant values or browser actions."""
import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
from applypilot.apply.assisted_browser import INSPECT, GUARD, Session, listing_entries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('--iterations', type=int, default=3)
    parser.add_argument('--open-entry', action='store_true', help='Open a verified listing entry with an empty applicant profile')
    parser.add_argument('--cookie-choice', choices=['save_settings','accept_all'], help='Explicit cookie choice in this disposable public-site probe')
    parser.add_argument('--parent', type=Path, default=Path('ops/implementations/20261008-browser-recovery'))
    args = parser.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={'width':1280,'height':900})
            context.add_init_script(GUARD)
            page = context.new_page()
            page.goto(args.url, wait_until='domcontentloaded', timeout=45000)
            if args.open_entry:
                session=Session(args.parent.resolve(),'public_probe',{'id':0,'url':args.url},{},{},{'personal':{}},[])
                session.observer.close()
                session.observer.enabled=False
                session.context,session.page,session.browser=context,page,browser
                page.wait_for_timeout(2500)
                if args.cookie_choice:
                    name={'save_settings':'Save my settings','accept_all':'Accept all cookies'}[args.cookie_choice]
                    cookie_frame=next(f for f in page.frames if '/ico-compliance/' in f.url)
                    cookie_frame.get_by_role('button',name=name,exact=True).click(timeout=5000)
                    page.wait_for_timeout(1000)
                candidates=[]
                deadline=time.monotonic()+15
                while not candidates and time.monotonic()<deadline:
                    try:
                        candidates=listing_entries(session.inspect())
                    except Exception:
                        pass
                    if not candidates:
                        page.wait_for_timeout(250)
                if len(candidates)!=1:
                    raise RuntimeError('No verified public listing entry')
                session.open_entry(candidates[0])
                session.select_page()
                session.page.wait_for_load_state('domcontentloaded',timeout=15000)
                current=session.inspect()
                fields=[f for _,info in current for f in info['fields']]
                print(json.dumps({'checkpoint_path':urlsplit(session.page.url).path,'field_types':[f['type'] for f in fields],'controls':[c['label'] for _,info in current for c in info['controls']][:30]},ensure_ascii=True),flush=True)
                needs_login=any(f['type']=='password' for f in fields)
                eligibility_form=urlsplit(session.page.url).path.lower()=='/vacancyeligibility.aspx' and len(fields)>1
                assert needs_login or eligibility_form, 'Expected sign-in or application eligibility checkpoint'
                session.capture()
                print(json.dumps({'entry_opened':True,'application_path':urlsplit(session.page.url).path,'manual_sign_in_needed':needs_login,'eligibility_form':eligibility_form,'screenshot_bytes':len(session.image),'submission_attempted':session.submission_attempted,'applicant_values_used':False}),flush=True)
                return
            for iteration in range(args.iterations):
                try:
                    snapshots = [(frame, frame.evaluate(INSPECT)) for frame in page.frames]
                    print(json.dumps({'iteration':iteration,'frames':len(snapshots),
                        'fields':[{k:f[k] for k in ('tag','type','name','id','required','valid')} for _, info in snapshots for f in info['fields']],
                        'controls':[c for _, info in snapshots for c in info['controls'] if c['label'].lower() in {'apply','apply now','login','sign in'}]},indent=2),flush=True)
                    start = time.monotonic()
                    image = page.screenshot(type='jpeg',quality=65,timeout=5000)
                    print(json.dumps({'screenshot_bytes':len(image),'elapsed_ms':int((time.monotonic()-start)*1000)}),flush=True)
                except Exception as exc:
                    # Public, unfilled page only; use a narrow error excerpt.
                    print(json.dumps({'exception':type(exc).__name__,'error':str(exc)[:900]}),flush=True)
                page.wait_for_timeout(500)
        finally:
            browser.close()


if __name__ == '__main__':
    main()
