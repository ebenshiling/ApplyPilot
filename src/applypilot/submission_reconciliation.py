"""Candidate-verified resolution of an uncertain submission; never submits."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime,timezone
from pathlib import Path

from applypilot import command_center as cc

SOURCES={'employer_portal':'Employer portal / My applications','confirmation_email':'Employer confirmation email','employer_contact':'Employer/recruiter confirmed','validation_message':'Employer form validation or draft status','other':'Other checked evidence'}


def init_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS cc_submission_reconciliations(
        id INTEGER PRIMARY KEY AUTOINCREMENT,job_url TEXT NOT NULL,attempt_id TEXT,
        outcome TEXT NOT NULL,source TEXT NOT NULL,evidence TEXT NOT NULL,reference TEXT,
        submitted_at TEXT,created_at TEXT NOT NULL)''')
    conn.commit()


def context(conn,root,job_id):
    job=cc.get_job(conn,job_id)
    attempt=None
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='cc_browser_sessions'").fetchone():
        row=conn.execute('SELECT * FROM cc_browser_sessions WHERE job_url=? ORDER BY created_at DESC LIMIT 1',(job['url'],)).fetchone()
        attempt=dict(row) if row else None
    last=conn.execute("SELECT id,created_at,detail FROM cc_events WHERE job_url=? AND state IN ('submitting','submission_uncertain','uncertain') ORDER BY id DESC LIMIT 1",(job['url'],)).fetchone()
    token=hashlib.sha256(json.dumps({'url':job['url'],'status':job['apply_status'],'applied_at':job['applied_at'],'agent_id':job['agent_id'],'attempt':attempt,'event':dict(last) if last else None},sort_keys=True).encode()).hexdigest()
    rows=[dict(r) for r in conn.execute('SELECT * FROM cc_submission_reconciliations WHERE job_url=? ORDER BY id DESC LIMIT 10',(job['url'],))]
    image=False
    if attempt and re.fullmatch(r'[A-Za-z0-9_-]{1,80}',attempt['id']):
        path=(Path(root)/'browser_sessions'/attempt['id']/'last_attempt.jpg').resolve()
        image=path.is_relative_to(Path(root).resolve()) and path.is_file()
    return {'job_id':job_id,'uncertain':job['apply_status']=='submission_uncertain','reconciliation_token':token,
            'sources':SOURCES,'attempt':attempt,'last_event':dict(last) if last else None,'previous_checks':rows,'has_attempt_image':image}


def reconcile(conn,root,data):
    conn.execute('BEGIN IMMEDIATE')
    try:
        job_id=int(data.get('id') or 0)
        job=cc.get_job(conn,job_id)
        current=context(conn,root,job_id)
        if job['apply_status']!='submission_uncertain' or job.get('applied_at'):
            raise ValueError('Only an unresolved uncertain submission can be reconciled here')
        if data.get('reconciliation_token')!=current['reconciliation_token']:
            raise ValueError('The attempt changed; reload reconciliation before saving your check')
        if current['attempt'] and current['attempt']['state'] not in {'uncertain','failed','stopped','confirmed'}:
            raise ValueError('Stop the active browser before reconciling')
        outcome=str(data.get('outcome') or '')
        if outcome not in {'submitted','not_submitted','unknown'}:
            raise ValueError('Choose confirmed submitted, confirmed not submitted, or still unsure')
        source=str(data.get('source') or '')
        evidence=str(data.get('evidence') or '').strip()
        reference=str(data.get('reference') or '').strip()
        if source not in SOURCES or not evidence or len(evidence)>3000 or len(reference)>300:
            raise ValueError('Select your checked evidence source and describe what it shows (up to 3,000 characters)')
        if outcome!='unknown' and data.get('checked') is not True:
            raise ValueError('Confirm that you checked the employer outcome before clearing uncertainty')
        if outcome=='not_submitted' and source=='confirmation_email':
            raise ValueError('A missing email does not prove no submission. Check portal draft/validation status or ask the employer')
        submitted_at=None
        if outcome=='submitted':
            raw=str(data.get('submitted_at') or '')
            if raw:
                date=datetime.fromisoformat(raw.replace('Z','+00:00'))
                if date.tzinfo is None or date>datetime.now(timezone.utc):
                    raise ValueError('Submission date must have a time zone and cannot be in the future')
                submitted_at=date.astimezone(timezone.utc).isoformat()
            else:
                submitted_at=cc.now()
        evidence=cc.redact(evidence)
        attempt_id=(current['attempt'] or {}).get('id')
        conn.execute('INSERT INTO cc_submission_reconciliations(job_url,attempt_id,outcome,source,evidence,reference,submitted_at,created_at) VALUES(?,?,?,?,?,?,?,?)',(job['url'],attempt_id,outcome,source,evidence,reference,submitted_at,cc.now()))
        conn.execute('INSERT OR IGNORE INTO cc_job_meta(job_url) VALUES(?)',(job['url'],))
        if outcome=='submitted':
            doc=cc.snapshot_document(conn,Path(root),job)
            conn.execute("UPDATE jobs SET apply_status='applied',applied_at=?,apply_error=NULL,agent_id=NULL WHERE url=?",(submitted_at,job['url']))
            conn.execute("UPDATE cc_job_meta SET hiring_status='submitted',submission_method='candidate_reconciled',reference=?,evidence=?,document_id=COALESCE(document_id,?),updated_at=? WHERE job_url=?",(reference,evidence,doc,cc.now(),job['url']))
            cc.event(conn,job['url'],'submission','Candidate verified employer submission: '+evidence,state='submitted',source='candidate')
        elif outcome=='not_submitted':
            conn.execute("UPDATE jobs SET apply_status='prepared',apply_error=NULL,agent_id=NULL WHERE url=?",(job['url'],))
            conn.execute("UPDATE cc_job_meta SET hiring_status=NULL,submission_method=NULL,reference=?,evidence=?,updated_at=? WHERE job_url=?",(reference,evidence,cc.now(),job['url']))
        # Historical uncertain browser sessions/events remain truthful. New
        # eligibility is based on the reconciled job, never replaying a click.
        cc.event(conn,job['url'],'reconciliation',SOURCES[source]+': '+evidence+((' · Reference: '+reference) if reference else ''),state=outcome,source='candidate')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {'outcome':outcome,'locked':outcome=='unknown','retry_started':False}


def attempt_image(conn,root,job_id):
    ctx=context(conn,root,job_id)
    if not ctx['has_attempt_image']:
        raise LookupError('No saved last-attempt image is available for this attempt')
    path=(Path(root)/'browser_sessions'/ctx['attempt']['id']/'last_attempt.jpg').resolve()
    return path.read_bytes()
