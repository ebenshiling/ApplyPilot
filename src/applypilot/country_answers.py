"""Bounded country answers in cc_country_profiles; never reuse unscoped NZ rights."""
from datetime import datetime,timedelta,timezone
import json
import re
import uuid

from applypilot import canonical,country_context as country
from applypilot.setup_workspace import read_profile


def normalized(question):return re.sub(r"\s+"," ",str(question).strip()).casefold()


def _proof(item):return {k:v for k,v in item.items() if k not in {"audit_event_id","actor","reviewed_at"}}


def reviewed(conn,code,question,*,job=None):
    code=country.normalize_country(code,optional=False)
    prefs=country.read_preferences(conn,code)
    values=prefs["config"].get("application_operations",{}).get("answers",[])
    if not isinstance(values,list):return None
    for item in reversed(values):
        if not isinstance(item,dict) or normalized(item.get("question"))!=normalized(question) or item.get("country_code")!=code:continue
        if item.get("job_url") and (not job or item["job_url"]!=job.get("url")):continue
        if item.get("rights_dependency")!=country.preference_dependency(conn,code,"work_authorization"):continue
        from applypilot.source_access import moment
        expires=moment(item.get("expires_at"))
        if not expires or expires<=datetime.now(timezone.utc):continue
        if not canonical._audit_valid(conn,item.get("audit_event_id"),"country_answer_reviewed",canonical.digest(_proof(item))):continue
        from applypilot.country_context import workspace_for_connection
        profile=read_profile(workspace_for_connection(conn))
        facts={f["id"]:f for f in canonical.model(profile)["facts"]}
        if any(i not in facts or not canonical.verified(conn,facts[i],profile) or canonical.fact_hash(facts[i],profile)!=h for i,h in item.get("fact_hashes",{}).items()):continue
        return item
    return None


def save(conn,app_dir,data,*,actor):
    from applypilot.command_center import get_job,event,now
    code=country.normalize_country(data.get("country_code"),optional=False)
    active=country.resolve_view(app_dir,code,conn=conn)
    question,answer=data.get("question"),data.get("answer")
    if not isinstance(question,str) or not 1<=len(question.strip())<=1000 or not isinstance(answer,str) or not 1<=len(answer.strip())<=2000 or data.get("human_reviewed") is not True:
        raise ValueError("Review the exact question and your explicit answer; models cannot approve answers")
    job=get_job(conn,int(data["id"])) if data.get("id") else None
    if job:country.check_job(job,app_dir,country_code=code)
    foreign=next((c for c,p in (("AU",r"\baustrali(?:a|an)\b"),("GB",r"\buk\b|united kingdom|\bbrit(?:ain|ish)\b|\bengland\b"),("US",r"united states|\busa\b|\bamerican\b"),("CA",r"\bcanad(?:a|ian)\b"),("NZ",r"new zealand|\bnz\b")) if re.search(p,question,re.I)),None)
    if foreign and foreign!=code and data.get("question_jurisdiction")!=foreign:
        raise ValueError("This question names another jurisdiction; explicitly review that jurisdiction rather than inheriting country rights")
    refs=data.get("fact_ids",[])
    profile=read_profile(app_dir)
    facts={f["id"]:f for f in canonical.model(profile)["facts"]}
    if not isinstance(refs,list) or any(i not in facts or not canonical.verified(conn,facts[i],profile) for i in refs):raise ValueError("Answer evidence must reference current verified canonical facts")
    declaration=not re.search(r"\b(?:experience|previous|past|achievement|example)\b",question,re.I) and bool(re.search(r"right to work|work rights|authori[sz].*work|(?:need|require|visa).*sponsor|visa|citizenship|consent|agree|salary|notice period|start date|available|relocat",question,re.I))
    if answer.strip().casefold() in {"prefer not to say","prefer not to disclose","i decline to answer"}:declaration=True
    if not declaration:
        check=canonical.check(conn,app_dir,answer,job or {"country_code":code},kind="answer")
        if not check["passed"] and not (answer.strip().casefold() in {"yes","no"} and refs and data.get("meaning_reviewed") is True):
            raise ValueError("Professional answer wording requires current factual coverage or an explicit evidence-linked binary mapping")
    hours=data.get("valid_hours",24)
    if isinstance(hours,bool) or not isinstance(hours,int) or not 1<=hours<=24:raise ValueError("Answer review validity must be one to 24 hours")
    item={"id":uuid.uuid4().hex,"country_code":code,"question":question.strip(),"answer":answer.strip(),"job_url":job.get("url") if job and data.get("job_specific",True) else None,
        "question_jurisdiction":foreign or code,"rights_dependency":country.preference_dependency(conn,code,"work_authorization"),"fact_hashes":{i:canonical.fact_hash(facts[i],profile) for i in refs},
        "expires_at":(datetime.now(timezone.utc)+timedelta(hours=hours)).isoformat(),"manual_review_only":bool(foreign and foreign!=code or re.search(r"\bnot\b|\bwithout\b|\bunless\b|\bif\b",question,re.I))}
    event(conn,job.get("url") if job else None,"country_answer_reviewed","Candidate reviewed the exact country/question answer; no inferred rights or declaration",source="candidate",payload={"proof_hash":canonical.digest(item),"actor":actor,"country_code":code})
    item.update(audit_event_id=conn.execute("SELECT last_insert_rowid()").fetchone()[0],actor=actor,reviewed_at=now())
    conn.commit()
    prefs=country.read_preferences(conn,code)
    operations=dict(prefs["config"].get("application_operations") or {})
    prior=operations.get("answers",[])
    prior=prior if isinstance(prior,list) else []
    operations["answers"]=([a for a in prior if isinstance(a,dict) and not (normalized(a.get("question"))==normalized(question) and a.get("job_url")==item["job_url"])]+[item])[-30:]
    country.save_preferences(conn,code,{"application_operations":operations},prefs["revision"],active.config)
    return {"answer":item}
