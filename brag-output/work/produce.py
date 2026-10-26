"""Reproducible product film using the real native workspace and isolated demo data."""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
from pathlib import Path
import subprocess
import shutil
import threading
import time
import wave

WORK = Path(__file__).resolve().parent
OUTPUT = WORK.parent
ROOT = Path("C:/Users/swazy/AppData/Local/Temp/opencode/applypilot-film-demo")
ROOT.mkdir(exist_ok=True)
os.environ["APPLYPILOT_DIR"] = str(ROOT)
os.environ["APPLYPILOT_MULTI_ROOT"] = str(WORK / "demo-accounts")
os.environ["APPLYPILOT_RUN_PLAYWRIGHT"] = "1"
os.environ["LLM_PROVIDER"] = "local"
os.environ["LLM_MODEL"] = "demo-local"
os.environ["LLM_URL"] = "http://127.0.0.1:1"

from playwright.sync_api import sync_playwright
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

SCENES = [
 (6, "Find the role. Own the next step.", "One workspace for your search, your evidence and your applications.", "Meet ApplyPilot. Find the role, prepare your evidence, and own the next step."),
 (10, "A workspace built around your next move.", "Use the sidebar to move between opportunities, documents and progress.", "This is the new application workspace. Your opportunities stay in view, with clear navigation, a collapsible sidebar, and a choice of light or dark theme."),
 (18, "Start with the facts.", "Settings → professional profile → review and save your application details.", "Start in Settings. Keep your contact information, work authorization, availability, summary, and skills current. Save the facts you want reused on applications. Professional evidence gives document preparation and form assistance a reliable starting point."),
 (20, "Every version. Every qualification.", "CV Studio → source library → qualifications → versioned document preview.", "CV Studio brings your source resumes and role variants together. Import a CV, edit the source text, and maintain qualifications and experience. Then choose an opportunity to inspect its saved CV. Version history and previews help you review the exact document before downloading it."),
 (12, "Show the work behind the skills.", "Portfolio → verified achievements, technologies, links and reusable evidence.", "Build a portfolio from work you can explain. Keep project achievements, dates, technologies, and links together. Included projects become reusable evidence for future application documents."),
 (26, "Make the search focused.", "Pipeline → roles and locations → CV routing → sources → preview the work.", "In Pipeline, shape a focused search. Choose your roles, priorities, and resume mappings. Add country, region, city, or remote locations, and inspect discovery coverage. Select relevant sources, including direct employer sites. Preview the effective search scope and budgets before you run the workflow. Discovery, assessment, and document preparation remain separate stages."),
 (16, "Decide where to spend your attention.", "Jobs → search → open the record → review the evidence → Pick.", "Search your opportunities and open a job record. Compare the role with the saved fit assessment and professional evidence. Inspect its requirements, document state, and timeline. Pick the opportunities you want to prepare, and keep the rest organized with archive and block controls."),
 (22, "Turn criteria into reviewed documents.", "Cover Studio + Statement Studio → target → draft → validate → preview.", "Cover Studio and Statement Studio keep the target role beside your working draft. Review the actual criteria, select relevant evidence, and check word or character limits. Edit your own text, validate it, and save another version. Preview the document and download it when you are ready. Publication is an explicit action."),
 (14, "Prepare the packet. Inspect the work.", "Pipeline → Prepare selected applications → preview queues and recovery.", "Return to Pipeline to prepare selected applications. Preview the required CV, supporting statement, cover letter, and PDF work. Stage counts show what still needs attention. Recent runs and scoped recovery controls keep preparation visible."),
 (26, "Apply with visibility and control.", "Review the packet → open the shared browser → take over → final review.", "Open Apply with agent from the job record. Review your application packet first. The shared browser lets you watch the employer page and take over when needed. Click and type directly in the live view, or select an answer field. Final submission has a separate review and approval checkpoint. This example uses a controlled demonstration employer form."),
 (14, "Keep the next conversation in context.", "Applications → hiring status → notes → reminder → calendar export.", "Applications separates submission from the hiring outcome. Open a record, add a hiring update, and keep recruiter details and notes together. Set a reminder, export it to your calendar, and use the timeline to understand what happened."),
 (14, "Turn messages into next steps.", "Evidence inbox → import → inspect the source → review the hiring update.", "Bring employer evidence into the inbox. Import a message or connect a supported mailbox. Review its category, matching opportunity, and original text before recording a hiring update. The source message stays available for context."),
 (20, "See the story behind your progress.", "Insights → interactive charts → drill down → export the underlying records.", "Insights explains your saved activity. Explore opportunity cohorts, preparation milestones, sources, fit scores, and recorded hiring progress. Click chart segments to narrow the view. The opportunity ledger preserves the context of each event, including retained history. Export the records and activity for your own analysis. These figures come from demonstration data."),
 (9, "Clear the clutter. Keep the story.", "History & storage → closure reasons → retained timeline → storage controls.", "History and storage keeps the story after an opportunity closes. Inspect the reason and retained timeline, review generated-file storage, and manage cleanup without losing the historical context."),
 (9, "Make the workspace yours.", "Settings → private AI connections → task routing → portable backup.", "Configure private AI connections and task preferences in Settings. Inspect usage budgets, optional observer tools, and download a portable workspace backup. Administrators also retain account and audit controls."),
 (4, "Your next chapter, organized.", "ApplyPilot / Your application workspace", "ApplyPilot. Your next chapter, organized."),
]

RESUME = """Alex Morgan
Application Support Analyst
Manchester, United Kingdom | alex.morgan@example.com | +44 7700 900123

SUMMARY
Application support analyst with experience in incident resolution, SQL diagnostics, service documentation and clear stakeholder communication. Uses verified project evidence to explain practical work and professional development.

CORE SKILLS
Incident triage | SQL diagnostics | Service documentation | Stakeholder communication

PROFESSIONAL EXPERIENCE
Application Support Analyst at Northstar Digital
2023–present | Manchester
- Investigated application incidents using SQL queries, logs and reproducible steps.
- Maintained service documentation and shared troubleshooting notes with colleagues.
- Coordinated incident updates with users and technical teams.
- Built a support knowledge base with reviewed troubleshooting procedures.

PROJECTS
Service reliability knowledge base
2025 | SQL, Python, Markdown
- Built a searchable collection of incident diagnostics and reviewed runbooks.
- Documented recurring issues and clear resolution steps.

TECHNICAL SKILLS
Tools: SQL, Python, Power BI, Git, ServiceNow

EDUCATION
BSc Computing | Example University | 2022

CERTIFICATIONS
ITIL Foundation | Example Training | Earned
"""

COVER = """Dear Hiring Manager,

I am applying for the Application Support Analyst role at Meridian Health. My experience at Northstar Digital combines application incident investigation, SQL diagnostics and clear communication with users and technical colleagues. I would bring a practical, evidence-led approach to keeping services reliable.

In my current role, I investigate incidents through queries, application logs and reproducible steps. I document findings so that colleagues can understand what was checked and why a resolution was chosen. I also maintain service documentation and share troubleshooting notes with the team.

My service reliability knowledge base project brought reviewed runbooks and recurring-issue diagnostics into one searchable collection. This work reflects my interest in making support knowledge accessible and reusable. I would apply the same care to your service documentation and incident handovers.

I would welcome the opportunity to discuss the role and explain the examples behind my application. Thank you for considering my application.

Kind regards,
Alex Morgan"""

STATEMENT = """Incident investigation and practical diagnostics

In my application support role at Northstar Digital, I investigate incidents by reviewing SQL queries, application logs and the steps needed to reproduce an issue. I record the checks performed and distinguish confirmed findings from assumptions. This helps colleagues continue an investigation and understand the basis of a proposed resolution.

Clear communication and service documentation

I coordinate updates with users and technical colleagues. My approach is to explain the current position clearly, document the next action, and make the information useful to people with different levels of technical knowledge. I maintain service documentation and share troubleshooting notes as part of this work.

Reusable evidence and continuous learning

I built a service reliability knowledge base containing reviewed troubleshooting procedures and recurring-issue diagnostics. The project uses SQL, Python and Markdown and organizes practical support evidence for reuse. I have also completed ITIL Foundation study and continue to develop my understanding of service management.

I would bring these examples to the Application Support Analyst role, while reviewing the full person specification and discussing areas where further learning is required."""

DESCRIPTION = """Application Support Analyst — Meridian Health
Support reliable internal applications and help colleagues resolve technical incidents.

Essential criteria:
- Experience investigating application incidents and documenting resolution steps.
- Practical SQL diagnostics and application log analysis.
- Clear communication with users and technical stakeholders.
- Evidence of service documentation and continuous learning.

Desirable criteria:
- ITIL Foundation or equivalent service-management learning.
- A portfolio project demonstrating reusable troubleshooting knowledge.

Manchester / hybrid. Permanent. Salary £38,000–£44,000.
This employer and role are fictional demonstration material."""


def command(args, **kwargs):
    subprocess.run([str(x) for x in args], check=True, **kwargs)


def seed():
    from applypilot import database as db, command_center as cc, setup_workspace as setup
    from applypilot import cv_library, evidence_inbox, writing_studio, job_lifecycle
    if (ROOT / "seed-ready.json").exists():
        return
    profile = {
      "personal": {"full_name":"Alex Morgan","first_name":"Alex","last_name":"Morgan","email":"alex.morgan@example.com","phone":"+44 7700 900123","city":"Manchester","country":"United Kingdom","country_code":"GB","address_line1":"10 Example Lane","postal_code":"M1 1AA","linkedin_url":"https://example.com/alex"},
      "work_authorization":{"legally_authorized_to_work":"Yes","require_sponsorship":"No"},
      "availability":{"notice_period":"One month","available_for_full_time":"Yes"},
      "compensation":{"salary_expectation":"42000","salary_currency":"GBP"},
      "experience":{"years_of_experience_total":"3","education_level":"Bachelor's degree"},
      "skills_boundary":{"programming_languages":["SQL","Python"],"frameworks":[],"tools":["Power BI","Git","ServiceNow"]},
      "resume_facts":{"preserved_companies":["Northstar Digital"],"preserved_projects":["Service reliability knowledge base"],"preserved_school":"Example University","real_metrics":[]},
      "resume_sections":{"summary":["Application support analyst focused on incident resolution, SQL diagnostics and reliable documentation."],"education":["BSc Computing | Example University | 2022"],"credentials":[{"name":"ITIL Foundation","issuer":"Example Training","status":"earned","family":"all","awarded":"2025","include":True}],"professional_experience":[{"header":"Application Support Analyst at Northstar Digital","subtitle":"2023–present | Manchester","bullets":["Investigated application incidents using SQL queries, logs and reproducible steps.","Maintained service documentation and shared troubleshooting notes."],"family":"all","include":True}],"projects":[{"id":"project-reliability","header":"Service reliability knowledge base","subtitle":"2025 | Reusable incident evidence","technologies":"SQL, Python, Markdown","bullets":["Built a searchable collection of reviewed incident runbooks.","Documented recurring issues and clear resolution steps."],"family":"all","include":True,"url":"https://example.com/projects/reliability","github":"https://example.com/alex/reliability"}],"professional_development":["Incident communication workshop | 2026"]},
    }
    setup.write_profile(ROOT, profile)
    setup.write_resume_text(ROOT, RESUME)
    setup.write_searches_yaml(ROOT, """queries:
  - query: Application Support Analyst
    tier: 1
  - query: Data Analyst
    tier: 2
locations:
  - location: Manchester, United Kingdom
    country: United Kingdom
    city: Manchester
    country_code: GB
    remote: false
  - location: Toronto, Canada
    country: Canada
    city: Toronto
    province_state: Ontario
    country_code: CA
    remote: false
  - location: Remote
    country: United Kingdom
    remote: true
boards: [indeed, linkedin, google]
country: UK
geographic_filter_mode: selected
smart_sites: [NHS Jobs, Reed]
defaults:
  results_per_site: 15
  hours_old: 72
board_search_budget: 6
smart_target_budget: 4
workday_target_budget: 4
""")
    conn = db.init_db(ROOT / "applypilot.db")
    cv_library.import_files(conn, ROOT, [{"name":"Support CV.txt","key":"application_support","role":"Application Support Analyst","b64":base64.b64encode(RESUME.encode()).decode()}, {"name":"Data CV.txt","key":"data_analyst","role":"Data Analyst","b64":base64.b64encode(RESUME.replace("Application Support Analyst","Data Analyst").encode()).decode()}])
    roles=["Application Support Analyst","Data Analyst","Service Desk Analyst","Systems Support Analyst","Technical Support Engineer","Business Analyst"]
    companies=["Meridian Health","Northstar Digital","Juniper Services","Harbour Analytics","Greenfield Systems","Atlas Research"]
    for i in range(24):
        title=roles[i%6];company=companies[(i//6+i)%6]
        if i==0: company="Meridian Health"
        status="prepared" if i==0 else "applied" if 4<=i<17 else "selected" if i in {1,2} else "failed" if i==3 else "pending"
        day=10-i%9
        when=f"2026-10-{day:02d}T10:00:00+00:00"
        url=f"https://careers.example.com/demo/{i+1}"
        conn.execute("INSERT INTO jobs(url,title,company,search_query,salary,location,site,strategy,discovered_at,full_description,detail_scraped_at,fit_score,score_confidence,score_reasoning,scored_at,apply_status,applied_at,tailor_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (url,title,company,title,"£38,000–£44,000",["Manchester, United Kingdom","Remote","Toronto, Canada"][i%3],["Direct","Indeed","LinkedIn","Workday"][i%4],"demo fixture",when,DESCRIPTION,when,[9,8,8,7,6,9][i%6],.86,"Strong alignment with saved incident investigation, SQL diagnostics and documentation evidence. Review the full requirements before applying.",when,status,when if status=="applied" else None,"approved"))
        cc.event(conn,url,"discovery","Fictional opportunity saved for the product demonstration",state="discovered",occurred_at=when)
        cc.event(conn,url,"score","Saved demo assessment reviewed alongside professional evidence",state="completed",occurred_at=when)
        hiring=["submitted","acknowledged","assessment","interview","offer","rejected"][(i-4)%6] if status=="applied" else None
        conn.execute("INSERT INTO cc_job_meta(job_url,hiring_status,priority,notes,due_at,reference,submission_method,evidence,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(url,hiring,2 if i==0 else 0,"Demonstration record — review saved evidence before the next step.","2026-10-20T10:00:00+00:00" if i in {0,4,7} else None,f"DEMO-{1000+i}" if hiring else "","manual" if hiring else None,"Fictional receipt created for the product walkthrough" if hiring else "",when))
        if hiring: cc.event(conn,url,"hiring","Fictional employer update recorded in the demo workspace",state=hiring,occurred_at=when)
    conn.commit()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page()
        for name,text in [("cv",RESUME),("cover",COVER),("statement",STATEMENT)]:
            path=ROOT/f"demo-{name}.txt";path.write_text(text,encoding="utf-8")
            from html import escape
            if name=="cv":
                from applypilot.scoring.pdf import parse_resume,build_html
                html=build_html(parse_resume(text))
            else: html="<html><body style='font:14px/1.7 Georgia;color:#22372f;padding:40px'>"+"".join("<p>"+escape(p).replace("\n","<br>")+"</p>" for p in text.split("\n\n"))+"</body></html>"
            page.set_content(html);page.pdf(path=str(path.with_suffix(".pdf")),format="A4",print_background=True)
        browser.close()
    for ident in [1,2,5,8]:
        conn.execute("UPDATE jobs SET tailored_resume_path=?,tailored_at=?,cover_letter_path=?,supporting_statement_path=?,document_requirements=? WHERE rowid=?",(str(ROOT/"demo-cv.txt"),cc.now(),str(ROOT/"demo-cover.txt"),str(ROOT/"demo-statement.txt"),json.dumps({"cover":True,"statement":True}),ident))
        conn.commit()
        job=cc.get_job(conn,ident)
        conn.execute("INSERT INTO cc_documents(job_url,text_path,pdf_path,fingerprint,variant,created_at) VALUES(?,?,?,?,?,?)",(job["url"],str(ROOT/"demo-cv.txt"),str(ROOT/"demo-cv.pdf"),cc.fingerprint(ROOT),"application_support",cc.now()))
    conn.execute("INSERT INTO cc_answers(question,answer,updated_at) VALUES(?,?,?)",("What is your notice period?","One month",cc.now()))
    conn.execute("INSERT INTO cc_pipeline_runs VALUES(?,?,?,?,?,?)",("demo-preparation", "2026-10-09T09:00:00Z","2026-10-09T09:06:00Z","completed",json.dumps({"stages":["tailor","statement","cover","pdf"],"selected_only":True}),json.dumps({"prepared":4})))
    for stage in ["tailor","statement","cover","pdf"]:
        conn.execute("INSERT INTO cc_pipeline_stages VALUES(?,?,?,?,?)",("demo-preparation",stage,"completed",json.dumps({"completed":4}),cc.now()))
    conn.commit()
    for kind,text in [("cover",COVER),("statement",STATEMENT)]:
        writing_studio.save(conn,ROOT,{"kind":kind,"job_id":1,"variant":"application_support","min_words":100,"max_words":500,"text":text})
    for subject,body,sender in [
        ("Application received — Data Analyst","Thank you for applying for Data Analyst at Northstar Digital. Your application has been received. Reference DEMO-1004.","recruitment@example.com"),
        ("Interview invitation — Systems Support Analyst","We would like to invite you to an interview for Systems Support Analyst. Please contact the recruitment team to confirm your availability.","team@example.com"),
        ("Your weekly job alert","New jobs matching your search preferences are available.","alerts@example.com")]:
        evidence_inbox.import_message(conn,{"sender":sender,"subject":subject,"body":body,"received_at":"2026-10-10T08:00:00+00:00"})
    for ident in [23,24]:
        job_lifecycle.retire(conn,ROOT,{"id":ident,"cleanup_token":job_lifecycle.preview(conn,ROOT,ident)["cleanup_token"],"reason":"advert_closed","detail":"Fictional advert closed; timeline retained for this demonstration."})
    conn.commit()
    (ROOT/"seed-ready.json").write_text(json.dumps({"fictional":True,"created":cc.now()}))
    print("Demo workspace prepared",flush=True)


PORTAL = """<!doctype html><html><head><style>*{box-sizing:border-box}body{margin:0;background:#f3f5f2;font:17px/1.5 Arial;color:#21392f}header{background:#21392f;color:white;padding:24px 50px}main{max-width:1020px;margin:30px auto;background:white;padding:30px 45px;border:1px solid #dce3db;border-radius:12px}h1{font-size:29px;margin:0 0 6px}.tag{color:#60786a;font-size:13px}label{display:block;margin:18px 0 6px;font-size:15px;font-weight:bold}input,textarea{font:17px Arial;padding:13px;width:100%;border:1px solid #b7c9bd;border-radius:6px}textarea{height:125px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}button{padding:14px 23px;color:white;background:#1b6552;border:0;border-radius:5px;font-size:16px;margin-top:18px}small{color:#68756e}</style></head><body><header>MERIDIAN HEALTH / DEMONSTRATION PORTAL</header><main><p class="tag">APPLICATION / STEP 1 OF 1</p><h1>Application Support Analyst</h1><p>Review your information before sending this demonstration application.</p><form action="/portal-done" method="post"><div class="grid"><div><label for="name">Full name</label><input id="name" name="name" value="Alex Morgan" required></div><div><label for="email">Email</label><input id="email" name="email" value="alex.morgan@example.com" required></div></div><label for="password">Account password — candidate takeover</label><input id="password" type="password" name="password" placeholder="Demonstration only" required><label for="notice">Notice period</label><input id="notice" name="notice" placeholder="Enter your saved availability"><label for="support">Supporting information</label><textarea id="support" name="support">I investigate application incidents using SQL queries, logs and reproducible steps, and maintain reviewed service documentation.</textarea><small>This is a local demonstration form. No employer application is sent.</small><br><button type="submit">Submit application</button></form></main></body></html>"""


def server():
    from applypilot import dashboard_server as ds, database as db
    class Handler(ds._Handler):
        def log_message(self,*args): pass
        def do_GET(self):
            if self.path=="/film": self._send(200,(WORK/"film.html").read_bytes(),"text/html; charset=utf-8")
            elif self.path=="/portal": self._send(200,PORTAL.encode(),"text/html; charset=utf-8")
            else: super().do_GET()
        def do_POST(self):
            if self.path=="/portal-done":
                self.rfile.read(int(self.headers.get("Content-Length",0)))
                self._send(200,b"<h1>Your application has been submitted</h1><p>Demonstration reference: DEMO-2026</p>","text/html")
            else: super().do_POST()
    ds.APP_DIR=ROOT;ds._MULTI_USER_MODE=False;ds._PIPELINE=ds._PipelineRunner(ROOT)
    httpd=ds.ThreadingHTTPServer(("127.0.0.1",0),Handler)
    threading.Thread(target=httpd.serve_forever,daemon=True).start()
    base=f"http://127.0.0.1:{httpd.server_port}"
    conn=db.init_db(ROOT/"applypilot.db")
    conn.execute("UPDATE jobs SET application_url=? WHERE rowid=1",(base+"/portal",));conn.commit()
    return httpd,base


class Director:
    def __init__(self,page,frame): self.p=page;self.f=frame;self.x=740;self.y=500
    def wait(self,seconds=1): self.p.wait_for_timeout(seconds*1000)
    def move(self,x,y):
        ox,oy=self.x,self.y
        for i in range(1,25):
            t=i/24;e=t*t*(3-2*t);xx=ox+(x-ox)*e;yy=oy+(y-oy)*e
            self.p.evaluate("p=>movePointer(p.x,p.y)",{"x":xx,"y":yy})
            self.p.mouse.move(xx,yy);self.p.wait_for_timeout(14)
        self.x,self.y=x,y
    def find(self,selector): return self.f.locator(selector).first
    def focus(self,selector):
        loc=self.find(selector);loc.wait_for(state="visible",timeout=30000)
        loc.evaluate("e=>e.scrollIntoView({behavior:'smooth',block:'center'})");self.wait(.55)
        box=loc.bounding_box();assert box,selector
        self.move(box["x"]+min(box["width"]*.5,180),box["y"]+box["height"]*.5)
        return loc
    def click(self,selector):
        loc=self.focus(selector);box=loc.bounding_box();assert box
        dx=min(box['width']*.5,180);dy=box['height']*.5
        self.x,self.y=box['x']+dx,box['y']+dy
        self.p.evaluate("p=>{movePointer(p.x,p.y);clickHalo(p.x,p.y)}",{"x":self.x,"y":self.y})
        loc.click(position={'x':dx,'y':dy});self.wait(.75);return loc
    def fill(self,selector,text):
        self.click(selector);self.p.keyboard.press("Control+A");self.p.keyboard.insert_text(text);self.wait(.6)
    def select(self,selector,value):
        loc=self.focus(selector);self.p.evaluate("p=>clickHalo(p.x,p.y)",{"x":self.x,"y":self.y});loc.select_option(str(value));self.wait(.9)
    def nav(self,view,ready):
        self.click('.rail [data-view="'+view+'"]');self.find(ready).wait_for(state="visible",timeout=30000);self.wait(.6)
        self.f.evaluate("window.scrollTo({top:0,behavior:'smooth'})");self.wait(.5)
    def scroll(self,selector): self.focus(selector);self.wait(.9)


def capture(rehearsal=False,start_index=0,end_index=16):
    seed();httpd,base=server();records=[];errors=[]
    (WORK/"raw").mkdir(exist_ok=True);(WORK/"stills").mkdir(exist_ok=True)
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            probe=browser.new_page(viewport={"width":1280,"height":900})
            probe.goto(base+'/portal');portal_fields={key:probe.locator('#'+key).bounding_box() for key in ['name','password','notice']};probe.close()
            context=browser.new_context(viewport={"width":1920,"height":1080},record_video_dir=str(WORK/"raw"),record_video_size={"width":1920,"height":1080},device_scale_factor=1)
            started=time.perf_counter();p=context.new_page();video=p.video
            p.on("pageerror",lambda err:errors.append(str(err)))
            p.on('dialog',lambda dialog:dialog.accept())
            p.goto(base+"/film",wait_until="domcontentloaded")
            f=p.frame_locator('#app');f.locator('.cards .card').first.wait_for()
            # Use the actual frame object so source controls keep their normal behavior.
            frame=p.frames[1];frame.evaluate("document.fonts.ready");p.evaluate("document.fonts.ready")
            d=Director(p,frame);d.portal_fields=portal_fields
            actions=[
              lambda: (d.wait(3),p.evaluate("hero(false)"),d.wait(1)),
              lambda: (d.click('#sidebar-toggle'),d.click('#sidebar-toggle'),d.click('#theme'),d.wait(1),d.click('#theme')),
              lambda: (d.nav('settings','#native-profile-save'),d.scroll('[data-profile-key="city"]'),d.fill('[data-profile-key="notice_period"]','One month'),d.click('#native-profile-save'),d.scroll('#profile-summary')),
              lambda: (d.nav('cv','#cv-library-panel'),d.click('[data-cv-tab="resume"]'),d.wait(1),d.click('[data-cv-tab="qualifications"]'),d.scroll('#credentials'),d.select('#preview-job',1),d.find('#cv-preview').wait_for(state='visible'),d.wait(1)),
              lambda: (d.nav('portfolio','#portfolio-projects'),d.scroll('[data-project-field="bullets"]'),d.click('#portfolio-save'),d.scroll('#portfolio-preview')),
              lambda: pipeline_scene(d),
              lambda: jobs_scene(d),
              lambda: writing_scene(d),
              lambda: (d.nav('pipeline','#native-stages'),d.click('[data-workflow="prepare"]'),d.click('#native-preview'),d.scroll('#native-preview-content'),d.scroll('#native-runs')),
              lambda: agent_scene(d),
              lambda: applications_scene(d),
              lambda: inbox_scene(d),
              lambda: insights_scene(d),
              lambda: history_scene(d),
              lambda: settings_scene(d),
              lambda: (d.nav('jobs','#job-search'),p.evaluate("hero(true,true)"),d.move(1740,870),d.wait(1)),
            ]
            for index,((duration,title,subtitle,narration),action) in enumerate(zip(SCENES,actions)):
                if index<start_index: continue
                if index>=end_index: break
                print(f"Capturing {index+1:02d}/{len(SCENES)}: {title}",flush=True)
                p.evaluate("a=>chapter(...a)",[index+1,title,subtitle,100*index/len(SCENES)])
                if index not in {0,15}: p.evaluate("hero(false)")
                begin=time.perf_counter()-started
                try:
                    action()
                except Exception:
                    p.screenshot(path=str(WORK/'capture-error.jpg'))
                    print('Capture state:',frame.evaluate("({view:state.view,dirty:state.dirty,toast:document.querySelector('#toast').textContent,content:document.querySelector('#content').innerText.slice(0,1500)})"),flush=True)
                    print('Page errors:',errors,flush=True)
                    raise
                elapsed=time.perf_counter()-started-begin
                if not rehearsal and elapsed<duration: d.wait(duration-elapsed)
                p.screenshot(path=str(WORK/"stills"/f"scene-{index:02d}.jpg"),type="jpeg",quality=92)
                end=time.perf_counter()-started
                records.append({"index":index,"duration":duration,"title":title,"subtitle":subtitle,"narration":narration,"raw_start":begin,"raw_duration":end-begin})
                (WORK/"scenes.json").write_text(json.dumps(records,indent=2),encoding="utf-8")
            context.close();raw=video.path();browser.close()
            (WORK/"recording.json").write_text(json.dumps({"raw":str(raw),"page_errors":errors,"duration":sum(r['duration'] for r in records)},indent=2))
            print("Captured all scenes. Page errors:",errors,flush=True)
            assert not errors,errors
    finally:
        from applypilot.apply import assisted_browser as ab
        for s in list(ab._sessions.values()):
            if s.state not in ab.TERMINAL: ab.command(ROOT,{"session_id":s.id,"action":"stop"})
        httpd.shutdown();httpd.server_close()
    contact_sheet()
    shutil.copytree(ROOT, WORK/'demo-snapshot', dirs_exist_ok=True)


def pipeline_scene(d):
    d.nav('pipeline','#native-stages');d.click('#native-config > summary');d.scroll('#native-query-editor')
    d.scroll('#native-locations');d.scroll('.geo-coverage' if d.find('.geo-coverage').count() else '#native-boards')
    d.click('summary:has-text("Sources & extraction previews")');d.scroll('#native-sources')
    d.scroll('#native-preview');d.click('#native-preview');d.scroll('#native-preview-content')


def jobs_scene(d):
    d.nav('jobs','#job-search');d.fill('#job-search','Application Support')
    d.click('button[data-detail="1"]');d.find('#detail-title').wait_for();d.wait(2)
    d.click('#job-detail details:has(.description) > summary');d.scroll('#job-detail .description');d.click('#close-dialog')
    if d.find('button[data-action="pick"][data-id="1"]').count(): d.click('button[data-action="pick"][data-id="1"]')
    d.wait(1)


def writing_scene(d):
    d.nav('cover','#writing-output');d.click('[data-writing-version]');d.scroll('#writing-output');d.click('#writing-check');d.scroll('#writing-preview');d.wait(1)
    d.nav('statement','#writing-output');d.click('[data-writing-version]');d.scroll('#nhs-planner');d.scroll('#writing-output');d.click('#writing-check');d.scroll('#writing-preview')


def agent_scene(d):
    d.nav('jobs','#job-search');d.fill('#job-search','Application Support');d.click('button[data-detail="1"]');d.click('#apply-with-agent')
    d.find('#agent-documents-approved').wait_for();d.click('#agent-documents-approved');d.click('#agent-start')
    d.find('#agent-image').wait_for(state='visible',timeout=45000)
    # Password fields cause the genuine application engine to request candidate takeover.
    d.find('#agent-resume').wait_for(state='visible',timeout=45000);d.wait(2)
    image=d.find('#agent-image');box=image.bounding_box();assert box
    # Coordinates are measured from the exact controlled employer form at its native viewport.
    for key,text in [('password','demo-only'),('notice','One month')]:
        field=d.portal_fields[key];box=image.bounding_box()
        x=box['x']+(field['x']+100)/1280*box['width'];y=box['y']+(field['y']+field['height']/2)/900*box['height']
        d.move(x,y);d.p.evaluate("p=>clickHalo(p.x,p.y)",{"x":x,"y":y});d.p.mouse.click(x,y);d.wait(.3)
        d.p.keyboard.type(text,delay=45);d.wait(.7)
    d.click('#agent-read-fields');d.scroll('#agent-field-choice');d.wait(1)
    d.click('#agent-review-button');d.find('#agent-approve').wait_for(state='visible');d.wait(2)
    # Show the real approval checkpoint; the recording does not send an employer application.
    d.click('#agent-stop');d.wait(1);d.click('#agent-close');d.click('#close-dialog')


def applications_scene(d):
    d.f.evaluate("state.search='';state.filter='active'");d.nav('applications','#job-search');d.click('button[data-detail="5"]')
    d.select('#detail-hiring','interview');d.fill('#detail-notes','Review incident-resolution examples before the conversation.');d.click('#save-detail')
    d.scroll('#detail-due');d.click('#export-calendar');d.scroll('.timeline');d.click('#close-dialog')


def inbox_scene(d):
    d.nav('inbox','#message-import');d.fill('#message-sender','recruitment@example.com');d.fill('#message-subject','Application received — demonstration')
    d.fill('#message-body','Thank you for your application. We have received your documents and will contact you with the next step. Reference DEMO-2026.')
    d.click('#message-import');d.scroll('#inbox-list');d.click('#inbox-list details > summary');d.wait(1)


def insights_scene(d):
    d.nav('analytics','#insight-visuals');d.find('.viz-grid').wait_for();d.scroll('.viz-card');d.wait(2)
    d.click('[data-viz-state="active"]');d.wait(2);d.click('#viz-reset');d.wait(1)
    d.scroll('.viz-card:nth-child(4)');d.wait(2);d.scroll('#opportunity-ledger .ledger-heading');d.wait(2)


def history_scene(d):
    d.nav('history','#history-find');d.scroll('.lifecycle-history');d.click('[data-history-detail]');d.wait(2)
    # Timeline modal controls use the lifecycle dialog pattern.
    d.p.keyboard.press('Escape');d.wait(.6)


def settings_scene(d):
    d.nav('settings','#ai-connections');d.scroll('#ai-connections');d.wait(1);d.click('#ai-decisions-settings > summary');d.scroll('a[href="/api/center/workspace/export"]')


def contact_sheet():
    sheet=Image.new('RGB',(1600,1040),'#10271f');draw=ImageDraw.Draw(sheet)
    for i,path in enumerate(sorted((WORK/'stills').glob('scene-*.jpg'))):
        image=Image.open(path);image.thumbnail((390,219));x=(i%4)*400+5;y=(i//4)*260+5
        sheet.paste(image,(x,y));draw.text((x,y+225),f"{i+1:02d} / {SCENES[i][1][:45]}",fill='#f4f2eb')
    sheet.save(WORK/'contact-sheet.jpg',quality=94)


def music(duration=240):
    rate=48000;t=np.arange(int(duration*rate),dtype=np.float64)/rate
    audio=np.zeros(len(t),dtype=np.float64)
    chords=[[146.832,220,293.665,349.228],[130.813,196,261.626,329.628],[174.614,261.626,349.228,440],[164.814,246.942,329.628,392]]
    segment=8.0
    for start in np.arange(0,duration,segment):
        lo=int(start*rate);hi=min(len(t),int((start+segment+1.2)*rate));local=t[lo:hi]-start
        envelope=np.minimum(local/1.2,1)*np.minimum((segment+1.2-local)/1.5,1)
        for f in chords[int(start/segment)%4]:
            tone=np.sin(2*np.pi*f*local)+.2*np.sin(2*np.pi*2*f*local)
            audio[lo:hi]+=.018*tone*np.maximum(0,envelope)
    rng=np.random.default_rng(42)
    for i,start in enumerate(np.arange(0,duration,.6)):
        lo=int(start*rate);n=min(int(.35*rate),len(t)-lo);local=np.arange(n)/rate
        if i%4==0:
            audio[lo:lo+n]+=.026*np.sin(2*np.pi*(52*local+18*(1-np.exp(-local*20))/20))*np.exp(-local*18)
        notes=chords[int(start/segment)%4];f=notes[i%4]*2
        audio[lo:lo+n]+=.013*(np.sin(2*np.pi*f*local)+.12*np.sin(2*np.pi*f*3*local))*np.exp(-local*13)
        if i%2: audio[lo:lo+n]+=.0018*rng.normal(size=n)*np.exp(-local*65)
    audio*=np.minimum(t/3,1)*np.minimum((duration-t)/4,1)
    stereo=np.column_stack((audio,np.roll(audio,400)*.96));data=(np.clip(stereo,-.95,.95)*32767).astype('<i2')
    with wave.open(str(WORK/'music.wav'),'wb') as wav:
        wav.setnchannels(2);wav.setsampwidth(2);wav.setframerate(rate);wav.writeframes(data.tobytes())


def audio_mix(records):
    music(sum(r['duration'] for r in records))
    rate=48000;full=np.zeros((240*rate,2),dtype=np.float32);offset=0
    for r in records:
        path=WORK/f"voice-{r['index']:02d}.wav"
        converted=WORK/f"voice-{r['index']:02d}-48k.wav"
        with wave.open(str(path),'rb') as w: length=w.getnframes()/w.getframerate()
        available=r['duration']-.7;speed=max(1,length/available)
        command([FFMPEG,'-hide_banner','-loglevel','error','-y','-i',path,'-af',f'atempo={speed:.6f},highpass=f=90,lowpass=f=10500','-ar',rate,'-ac',2,converted])
        with wave.open(str(converted),'rb') as w: voice=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').reshape(-1,2).astype(np.float32)/32768
        start=int((offset+.25)*rate);n=min(len(voice),len(full)-start);full[start:start+n]+=voice[:n]*.88
        offset+=r['duration']
    with wave.open(str(WORK/'music.wav'),'rb') as w: bg=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').reshape(-1,2).astype(np.float32)/32768
    out=np.tanh(full+bg*.7)*.89
    with wave.open(str(WORK/'soundtrack.wav'),'wb') as w:
        w.setnchannels(2);w.setsampwidth(2);w.setframerate(rate);w.writeframes((out*32767).astype('<i2').tobytes())


def render(reuse=False):
    records=json.loads((WORK/'scenes.json').read_text());recording=json.loads((WORK/'recording.json').read_text())
    audio_mix(records);(WORK/'encoded').mkdir(exist_ok=True)
    for r in records:
        target=WORK/'encoded'/f"scene-{r['index']:02d}.mp4"
        if reuse and target.exists() and r['index'] not in {12,15}: continue
        raw_start=r['raw_start'];raw_duration=r['raw_duration']
        if r['index']==15:
            raw_start+=raw_duration-2.7;raw_duration=2.7
        ratio=r['duration']/raw_duration
        print('Encoding scene',r['index']+1,flush=True)
        command([FFMPEG,'-hide_banner','-loglevel','error','-y','-ss',raw_start,'-i',r.get('raw',recording['raw']),'-t',raw_duration,'-an','-vf',f"setpts={ratio:.9f}*PTS,fps=30,scale=1920:1080:flags=lanczos,setsar=1,tpad=stop_mode=clone:stop_duration=1",'-t',r['duration'],'-c:v','libx264','-preset','fast','-crf',18,'-pix_fmt','yuv420p',target])
    parts=sorted((WORK/'encoded').glob('scene-*.mp4'))
    (WORK/'concat.txt').write_text(''.join("file '"+str(p).replace('\\','/')+"'\n" for p in parts),encoding='utf-8')
    command([FFMPEG,'-hide_banner','-loglevel','error','-y','-f','concat','-safe',0,'-i',WORK/'concat.txt','-c','copy',WORK/'picture.mp4'])
    # Settled title card becomes the first frame, replacing rather than adding one.
    poster=WORK/'stills'/'scene-15.jpg'
    Image.open(poster).save(OUTPUT/'brag.jpg',quality=95)
    command([FFMPEG,'-hide_banner','-loglevel','error','-y','-i',WORK/'picture.mp4','-loop',1,'-framerate',30,'-i',OUTPUT/'brag.jpg','-i',WORK/'soundtrack.wav','-filter_complex',"[0:v][1:v]overlay=enable='eq(n,0)':shortest=1[v]",'-map','[v]','-map','2:a','-t',240,'-c:v','libx264','-preset','fast','-crf',18,'-pix_fmt','yuv420p','-r',30,'-c:a','aac','-b:a','192k','-movflags','+faststart','-metadata','title=ApplyPilot — Your next chapter, organized','-metadata','comment=Native workspace product tour with fictional demonstration data',OUTPUT/'brag.mp4'])
    (OUTPUT/'share-copy.txt').write_text("From finding the role to preparing the evidence and tracking the response: a complete tour of ApplyPilot’s new application workspace. See CV and writing Studios, focused discovery, a visible assisted browser, and an evidence-backed application timeline in action.\n",encoding='utf-8')
    offset=0;subs=[]
    def stamp(t):
        ms=round(t*1000);return f'{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}'
    for r in records:
        subs.append(f"{r['index']+1}\n{stamp(offset)} --> {stamp(offset+r['duration'])}\n{r['title']}\n{r['subtitle']}\n")
        offset+=r['duration']
    (OUTPUT/'brag.srt').write_text('\n'.join(subs),encoding='utf-8')
    command([FFMPEG,'-hide_banner','-loglevel','error','-i',OUTPUT/'brag.mp4','-f','null','-'])
    result=subprocess.run([FFMPEG,'-hide_banner','-i',str(OUTPUT/'brag.mp4')],capture_output=True,text=True)
    (WORK/'media-info.txt').write_text(result.stderr,encoding='utf-8')
    assert '1920x1080' in result.stderr and '30 fps' in result.stderr and '00:04:00' in result.stderr,result.stderr
    print(f"Finished: {OUTPUT/'brag.mp4'} ({(OUTPUT/'brag.mp4').stat().st_size/1048576:.1f} MB)",flush=True)


def pickup():
    original=json.loads((WORK/'scenes.json').read_text());old_raw=json.loads((WORK/'recording.json').read_text())['raw']
    capture(True,12,13)
    new=json.loads((WORK/'scenes.json').read_text())[0];new['raw']=json.loads((WORK/'recording.json').read_text())['raw']
    for row in original: row.setdefault('raw',old_raw)
    original[12]=new
    (WORK/'scenes.json').write_text(json.dumps(original,indent=2),encoding='utf-8')


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('step',choices=['prepare','capture','rehearse','pickup','render']);ap.add_argument('--from-scene',type=int,default=1);ap.add_argument('--reuse',action='store_true');args=ap.parse_args()
    if args.step=='prepare':
        seed()
        (WORK/'narration.json').write_text(json.dumps([{"index":i,"duration":s[0],"narration":s[3]} for i,s in enumerate(SCENES)],indent=2),encoding='utf-8')
    elif args.step=='capture': capture()
    elif args.step=='rehearse': capture(True,args.from_scene-1)
    elif args.step=='pickup': pickup()
    else: render(args.reuse)
