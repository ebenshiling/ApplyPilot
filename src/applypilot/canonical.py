"""Shared, human-reconciled factual authority and closed-wording final-byte gate.

Existing/imported profile/CV text is not automatically verified. A model's fact ID,
review verdict or client-supplied manifest is not proof. Verification/wording review
is recorded by an authenticated human service action in the existing audit ledger.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid

from applypilot import setup_workspace as setup, country_context as country

KINDS={"personal","employment","project","skill","credential","education","metric","employment_period","disclosure"}
POLICIES={"GB":"gb-evidence-only-v1","NZ":"nz-evidence-only-v1"}
HEADINGS={"SUMMARY","PROFESSIONAL SUMMARY","PROFILE","PROFESSIONAL PROFILE","PROFESSIONAL EXPERIENCE","WORK EXPERIENCE","EMPLOYMENT HISTORY","EXPERIENCE","PROJECTS","TECHNICAL PROJECTS","EDUCATION","CERTIFICATIONS","CERTIFICATES","QUALIFICATIONS","TECHNICAL SKILLS","CORE TECHNICAL SKILLS","CORE SKILLS","TECHNICAL ENVIRONMENT","PROFESSIONAL DEVELOPMENT","PROFESSIONAL MEMBERSHIPS","SUPPORTING STATEMENT","COVER LETTER"}
NONFACTUAL={"Dear Hiring Manager,","Dear Recruitment Team,","Kind regards,","Yours sincerely,","Yours faithfully,","Thank you for considering my application.","I would welcome the opportunity to discuss my application."}


def _current_rights(text):
    return bool(re.search(r"\b(?:legally authori[sz]ed to work|(?:have|hold) (?:current |full |unrestricted ).{0,25}work rights|(?:need|require|do not need|do not require).{0,20}(?:visa )?sponsorship|willing to relocate)\b",text,re.I))


class FactReviewRequired(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def validate(value):
    if not isinstance(value,dict) or set(value)-{"version","revision","facts","wordings"}:
        raise ValueError("Unsupported canonical fact fields")
    if value.get("version")!=1 or isinstance(value.get("revision"),bool) or not isinstance(value.get("revision"),int) or value["revision"]<0:
        raise ValueError("Canonical facts require version 1 and a revision")
    if not isinstance(value.get("facts"),list) or len(value["facts"])>1500 or not isinstance(value.get("wordings"),list) or len(value["wordings"])>1500:
        raise ValueError("Canonical facts/wordings must be bounded lists")
    ids=[]
    for fact in value["facts"]:
        if not isinstance(fact,dict) or fact.get("kind") not in KINDS or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}",str(fact.get("id") or "")):
            raise ValueError("Facts need stable IDs and supported kinds")
        for key in ("text","entity_id","entity_label","source_reference","source_quote"):
            if not isinstance(fact.get(key),str) or len(fact[key])>6000:
                raise ValueError("Fact text/entity/source fields must be bounded strings")
        if not fact["text"].strip() or "\n" in fact["text"]:
            raise ValueError("Each fact is one complete atomic line/assertion")
        if _current_rights(fact["text"]) and fact["kind"]!="disclosure":
            raise ValueError("Current rights/relocation assertions require an explicit country-scoped disclosure fact")
        if fact.get("profile_path") is not None and (not isinstance(fact["profile_path"],str) or not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+){1,8}",fact["profile_path"]) or fact["profile_path"].startswith("canonical_facts.")):
            raise ValueError("Use a bounded professional-profile field reference")
        if fact["kind"] in {"employment","project","metric","employment_period"} and (not fact["entity_id"] or not fact["entity_label"] or fact["entity_label"].casefold() not in fact["text"].casefold()):
            raise ValueError("Entity-specific assertions must retain the exact employer/project label")
        period=fact.get("period")
        if period is not None:
            if fact["kind"]!="employment_period" or not isinstance(period,dict) or set(period)-{"start","end","time_basis"} or period.get("time_basis") not in {"full_time","part_time","unknown"}:
                raise ValueError("Use a typed employment period and stated time basis")
            start,end=date.fromisoformat(period["start"]),date.fromisoformat(period["end"])
            if start>=end or end>date.today():raise ValueError("Employment periods need verified nonfuture start/end dates")
        if fact.get("status") is not None and fact["status"] not in {"earned","in_progress","expired"}:
            raise ValueError("Invalid credential status")
        if fact.get("status") in {"in_progress","expired"} and fact["status"].replace("_"," ") not in fact["text"].casefold():
            raise ValueError("Credential wording must retain its incomplete/expired status")
        if fact.get("expires") is not None:
            if fact["kind"] not in {"credential","disclosure"}:raise ValueError("Expiry belongs only to credential or country-disclosure facts")
            date.fromisoformat(fact["expires"])
        if fact["kind"]=="disclosure":
            country.normalize_country(fact.get("country_code"),optional=False)
            if fact.get("profile_path") and re.match(r"(?:work_authorization|availability|compensation|personal\.(?:legally_authorized|require_sponsorship|work_permit))",fact["profile_path"]):
                raise ValueError("Country disclosures cannot inherit legacy/global rights fields; use explicit country evidence")
        ids.append(fact["id"])
    if len(ids)!=len(set(ids)):raise ValueError("Canonical fact IDs must be unique")
    wording_ids=[]
    for wording in value["wordings"]:
        if not isinstance(wording,dict) or not isinstance(wording.get("text"),str) or not 1<=len(wording["text"])<=6000 or "\n" in wording["text"] or not isinstance(wording.get("fact_ids"),list) or not wording["fact_ids"] or any(i not in ids for i in wording["fact_ids"]):
            raise ValueError("Wordings need one atomic line and existing canonical fact references")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}",str(wording.get("id") or "")):raise ValueError("Invalid wording ID")
        wording_ids.append(wording["id"])
    if len(wording_ids)!=len(set(wording_ids)):raise ValueError("Wording IDs must be unique")
    return value


def model(profile):
    return validate(profile.get("canonical_facts") or {"version":1,"revision":0,"facts":[],"wordings":[]})


def _projection(profile,path):
    current=profile
    for part in path.split("."):
        current=current[int(part)] if isinstance(current,list) else current[part]
    return current


def fact_hash(fact,profile):
    output={k:v for k,v in fact.items() if k!="verification"}
    if fact.get("profile_path"):
        try:output["current_profile_value"]=_projection(profile,fact["profile_path"])
        except (KeyError,IndexError,ValueError,TypeError):output["current_profile_value"]={"missing":True}
    return digest(output)


def _audit_valid(conn,ident,kind,expected):
    row=conn.execute("SELECT payload FROM cc_events WHERE id=? AND kind=?",(ident,kind)).fetchone() if isinstance(ident,int) and not isinstance(ident,bool) else None
    if not row:return False
    try:return json.loads(row[0]).get("proof_hash")==expected
    except (TypeError,ValueError):return False


def verified(conn,fact,profile):
    proof=fact.get("verification") or {}
    if fact.get("expires") and date.fromisoformat(fact["expires"])<date.today() and fact.get("status")!="expired":return False
    if fact["kind"]=="disclosure" and fact.get("expires") and date.fromisoformat(fact["expires"])<date.today():return False
    if fact["kind"]=="disclosure" and fact.get("country_dependency_hash")!=country.preference_dependency(conn,fact["country_code"],"work_authorization"):return False
    return proof.get("state")=="verified" and _audit_valid(conn,proof.get("audit_event_id"),"canonical_fact_verified",fact_hash(fact,profile))


def _audit(conn,kind,payload,actor):
    from applypilot.command_center import event
    event(conn,None,kind,"Human canonical-evidence action" if actor!="deterministic_finaliser" else "Deterministic document finalisation",source="candidate" if actor!="deterministic_finaliser" else "system",payload=payload|{"actor":actor})
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def action(conn,app_dir,data,*,actor):
    """Called only from authenticated human routes; generated callers cannot verify."""
    profile=setup.read_profile(app_dir)
    value=model(profile)
    if data.get("expected_revision")!=value["revision"] or isinstance(data.get("expected_revision"),bool):
        raise ValueError("Canonical facts changed; reload before reviewing")
    operation=data.get("action")
    if operation=="save_fact":
        raw=data.get("fact")
        if not isinstance(raw,dict) or set(raw)-{"id","kind","text","entity_id","entity_label","source_reference","source_quote","profile_path","period","status","expires","country_code","country_dependency_hash"}:
            raise ValueError("Fact input cannot contain verification/approval metadata")
        clean={"id":raw.get("id") or uuid.uuid4().hex,"kind":raw.get("kind"),"text":raw.get("text"),"entity_id":raw.get("entity_id",""),"entity_label":raw.get("entity_label",""),"source_reference":raw.get("source_reference",""),"source_quote":raw.get("source_quote","")}|raw
        clean["id"]=raw.get("id") or uuid.uuid4().hex
        if clean["kind"]=="disclosure":clean["country_dependency_hash"]=country.preference_dependency(conn,country.normalize_country(clean.get("country_code"),optional=False),"work_authorization")
        clean["verification"]={"state":"unverified"}
        previous=next((f for f in value["facts"] if f["id"]==clean["id"]),None)
        if previous and fact_hash(previous,profile)==fact_hash(clean,profile):clean["verification"]=previous.get("verification",{"state":"unverified"})
        value["facts"]=[f for f in value["facts"] if f["id"]!=clean["id"]]+[clean]
    elif operation=="verify_fact":
        fact=next((f for f in value["facts"] if f["id"]==data.get("fact_id")),None)
        if not fact or data.get("human_reviewed") is not True or not fact["source_reference"].strip() or not fact["source_quote"].strip():
            raise ValueError("Explicit human review and canonical source reference/quote are required")
        if fact["kind"]=="disclosure" and fact.get("country_dependency_hash")!=country.preference_dependency(conn,fact["country_code"],"work_authorization"):
            raise ValueError("Country disclosure context changed; correct/reconfirm the assertion before verifying it again")
        if fact.get("profile_path"):
            try:
                current_value=_projection(profile,fact["profile_path"])
                if isinstance(current_value,(str,int,float)) and str(current_value).strip() and not re.search(r"(?<!\w)"+re.escape(str(current_value).strip())+r"(?!\w)",fact["text"],re.I):
                    raise ValueError("The fact wording conflicts with its current professional-field value; correct the assertion first")
            except (KeyError,IndexError,ValueError,TypeError) as exc:raise ValueError("The referenced professional field is unavailable") from exc
        proof_hash=fact_hash(fact,profile)
        ident=_audit(conn,"canonical_fact_verified",{"fact_id":fact["id"],"proof_hash":proof_hash},actor)
        fact["verification"]={"state":"verified","actor":actor,"verified_at":_now(),"audit_event_id":ident}
    elif operation=="approve_wording":
        ids=data.get("fact_ids")
        facts=[f for f in value["facts"] if isinstance(ids,list) and f["id"] in ids]
        text=data.get("text")
        if data.get("human_reviewed") is not True or not isinstance(ids,list) or any(not isinstance(i,str) for i in ids) or not facts or len(facts)!=len(set(ids)) or any(not verified(conn,f,profile) for f in facts) or not isinstance(text,str):
            raise ValueError("Review meaning-preserving wording against current verified facts; a model/citation cannot approve it")
        if _current_rights(text) and not all(f["kind"]=="disclosure" for f in facts):
            raise ValueError("Current rights wording requires verified country-scoped disclosure evidence")
        from decimal import Decimal
        def numbers(value):
            return {Decimal(m.replace(",","")) for m in re.findall(r"\d[\d,]*(?:\.\d+)?",value)}
        supported_numbers=set().union(*(numbers(f["text"]) for f in facts))
        if numbers(text)-supported_numbers:
            raise ValueError("New numbers/durations require a separately verified fact or explicit verified computation, not wording approval")
        attributed=[f for f in facts if f["kind"] in {"employment","project","metric","employment_period"}]
        for skill in (f for f in facts if f["kind"]=="skill"):
            if attributed and skill["text"].casefold() in text.casefold() and not any(skill["text"].casefold() in f["text"].casefold() for f in attributed):
                raise ValueError("A global skill cannot establish its use at a particular employer/project")
        if any(f["entity_label"] and f["kind"] in {"employment","project","metric","employment_period"} and f["entity_label"].casefold() not in text.casefold() for f in facts):
            raise ValueError("Reviewed wording must retain the actual employer/project attribution")
        if any(f.get("status") in {"in_progress","expired"} and f["status"].replace("_"," ") not in text.casefold() for f in facts):
            raise ValueError("Reviewed wording cannot turn an incomplete/expired credential into an earned qualification")
        item={"id":uuid.uuid4().hex,"text":text,"fact_ids":ids,"fact_hashes":{f["id"]:fact_hash(f,profile) for f in facts}}
        proof_hash=digest(item)
        item["review"]={"actor":actor,"reviewed_at":_now(),"audit_event_id":_audit(conn,"canonical_wording_reviewed",{"proof_hash":proof_hash},actor)}
        value["wordings"].append(item)
    else:raise ValueError("Unknown canonical fact action")
    value["revision"]+=1
    validate(value)
    from applypilot.command_center import record_legacy_compatibility
    record_legacy_compatibility(conn,app_dir)
    setup.write_profile(app_dir,{"canonical_facts":value})
    conn.commit()
    return state(conn,app_dir)


def state(conn,app_dir):
    profile=setup.read_profile(app_dir)
    value=model(profile)
    return value|{"fact_states":{f["id"]:"verified" if verified(conn,f,profile) else "unverified_or_changed" for f in value["facts"]},"notice":"User-supplied/imported/model text is not automatically fact-verified."}


def _normal(line):
    return re.sub(r"\s+"," ",re.sub(r"^\s*[-*•]\s+","",line)).strip()


def calendar_employment(conn,profile):
    """Union verified typed periods; no inferred years, tool tenure or FTE claim."""
    facts=[f for f in model(profile)["facts"] if f["kind"]=="employment_period" and f.get("period") and verified(conn,f,profile)]
    if not facts:return None
    periods=sorted((date.fromisoformat(f["period"]["start"]),date.fromisoformat(f["period"]["end"])) for f in facts)
    merged=[]
    for start,end in periods:
        if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
        else:merged.append((start,end))
    days=sum((end-start).days for start,end in merged)
    return {"text":f"Verified calendar employment duration: {days} days through {periods[-1][1].isoformat()} (not full-time-equivalent or tool-specific tenure).",
            "fact_ids":[f["id"] for f in facts],"days":days,"as_of":periods[-1][1].isoformat(),"rule":"union_half_open_calendar_dates_v1"}


def check(conn,app_dir,text,job,*,kind="resume"):
    if kind not in {"resume","cover","statement","answer","source_resume"}:raise ValueError("Unsupported factual document kind")
    profile=setup.read_profile(app_dir)
    value=model(profile)
    code=country.normalize_country(job.get("country_code")) or "GB"
    country.require_enabled(code,setup.read_searches_dict(app_dir))
    facts={f["id"]:f for f in value["facts"] if verified(conn,f,profile) and (f["kind"]!="disclosure" or country.normalize_country(f["country_code"])==code)}
    approved={_normal(f["text"]):{"fact_ids":[f["id"]],"wording_id":None,"kinds":{f["kind"]}} for f in facts.values()}
    computed=calendar_employment(conn,profile)
    if computed:approved[computed["text"]]=computed|{"wording_id":"computed_calendar_employment_v1","kinds":{"employment_period"}}
    for wording in value["wordings"]:
        proof={k:v for k,v in wording.items() if k!="review"}
        if all(i in facts and wording.get("fact_hashes",{}).get(i)==fact_hash(facts[i],profile) for i in wording["fact_ids"]) and _audit_valid(conn,wording.get("review",{}).get("audit_event_id"),"canonical_wording_reviewed",digest(proof)):
            approved[_normal(wording["text"])]=wording|{"kinds":{facts[i]["kind"] for i in wording["fact_ids"]}}
    spans,errors,used=[],[],set()
    section="header" if kind in {"resume","source_resume"} else "letter"
    offset=0
    target="I am applying for the "+str(job.get("title") or "")+" role"+(" at "+str(job["company"]) if job.get("company") else "")+"."
    for number,line in enumerate(text.splitlines(keepends=True),1):
        normal=_normal(line)
        if not normal:offset+=len(line);continue
        if normal in HEADINGS:
            section=normal
            spans.append({"line":number,"start":offset,"end":offset+len(line),"structural":True})
        elif normal in NONFACTUAL or (section in {"header","letter"} and (normal==target or normal=="Target role: "+str(job.get("title") or "") or (section=="header" and normal==str(job.get("title") or "")))):
            spans.append({"line":number,"start":offset,"end":offset+len(line),"nonfactual_template":True})
        elif normal in approved:
            item=approved[normal]
            if item["kinds"]=={"skill"} and section not in {"TECHNICAL SKILLS","CORE TECHNICAL SKILLS","CORE SKILLS","TECHNICAL ENVIRONMENT"}:
                errors.append({"line":number,"reason":"Global skill proof cannot establish use at a different employer or project"})
            else:
                used.update(item["fact_ids"])
                spans.append({"line":number,"start":offset,"end":offset+len(line),"fact_ids":item["fact_ids"],"wording_id":item.get("wording_id",item.get("id"))})
        else:errors.append({"line":number,"reason":"Unverified/new wording requires an evidence-linked human review","text":normal[:300]})
        offset+=len(line)
    if not used:errors.append({"reason":"No verified canonical factual assertions were found"})
    dependencies={i:fact_hash(facts[i],profile) for i in sorted(used)}
    preferences=country.read_preferences(conn,code)["config"]
    has_disclosure=any(facts[i]["kind"]=="disclosure" for i in used)
    disclosure=country.preference_dependency(conn,code,"work_authorization") if code=="NZ" and (kind!="source_resume" or has_disclosure) else None
    configured_policy=preferences.get("document_policy",{})
    if not isinstance(configured_policy,dict):raise ValueError("Document policy must be an object")
    policy={"common":configured_policy.get("common",{}),"kind":configured_policy.get(kind,{}),"configured_policy_id":configured_policy.get("policy_id")} if kind!="source_resume" else {}
    if kind!="source_resume":
        rule=policy["kind"]
        if not isinstance(rule,dict):raise ValueError("Document-kind policy must be an object")
        for name,default,ceiling,count in (("max_words",{"resume":5000,"cover":800,"statement":2500,"answer":800}[kind],10000,len(text.split())),("max_characters",100000,200000,len(text))):
            maximum=rule.get(name,default)
            if isinstance(maximum,bool) or not isinstance(maximum,int) or not 1<=maximum<=ceiling:raise ValueError("Invalid document policy limit: "+name)
            if count>maximum:errors.append({"reason":f"Document exceeds {name}: {count} > {maximum}; shorten without inventing/truncating claims"})
    return {"version":1,"passed":not errors,"errors":errors,"country_code":code,"policy_id":POLICIES[code],"kind":kind,
        "text_sha256":hashlib.sha256(text.encode()).hexdigest(),"target_hash":digest({k:job.get(k) for k in ("url","title","company")}),"fact_dependencies":dependencies,"dependency_hash":digest([dependencies,POLICIES[code],disclosure,policy]),"claims":spans,"validated_at":_now(),"job_url":job.get("url")}


def finalize(conn,app_dir,path,job,*,kind="resume"):
    path=Path(path).resolve()
    if not path.is_relative_to(Path(app_dir).resolve()) or not path.is_file() or path.stat().st_size>1_000_000:
        raise FactReviewRequired("Final documents must be bounded owned text files")
    try:
        return require_final(conn,app_dir,path,job,kind=kind)
    except FactReviewRequired:
        pass
    text=path.read_bytes().decode("utf-8")
    result=check(conn,app_dir,text,job,kind=kind)
    if not result["passed"]:
        raise FactReviewRequired("Document remains a draft: "+"; ".join(e["reason"] for e in result["errors"][:3]))
    proof_hash=digest({k:result[k] for k in ("text_sha256","dependency_hash","country_code","policy_id","kind","job_url","target_hash")})
    result["audit_event_id"]=_audit(conn,"canonical_document_finalized",{"proof_hash":proof_hash},"deterministic_finaliser")
    setup._atomic_write_bytes(path.with_suffix(path.suffix+".claims.json"),json.dumps(result,indent=2).encode())
    conn.commit()
    return result


def require_final(conn,app_dir,path,job,*,kind="resume"):
    path=Path(path).resolve()
    try:
        if not path.is_relative_to(Path(app_dir).resolve()) or path.stat().st_size>1_000_000:raise ValueError("Unowned document")
        current=check(conn,app_dir,path.read_bytes().decode("utf-8"),job,kind=kind)
        saved=json.loads(path.with_suffix(path.suffix+".claims.json").read_text(encoding="utf-8"))
        fields=("text_sha256","dependency_hash","country_code","policy_id","kind","job_url","target_hash")
        proof_hash=digest({k:current[k] for k in fields})
        if not current["passed"] or any(saved.get(k)!=current[k] for k in fields) or not _audit_valid(conn,saved.get("audit_event_id"),"canonical_document_finalized",proof_hash):
            raise ValueError("Document or factual/country dependencies changed")
        return current
    except (OSError,ValueError,TypeError,KeyError) as exc:
        raise FactReviewRequired("Current factual finalisation is required; retained historical documents are not automatically verified") from exc


def bind_pdf(conn,app_dir,text_path,pdf_path,job,*,kind="resume"):
    current=require_final(conn,app_dir,text_path,job,kind=kind)
    path=Path(pdf_path).resolve()
    if not path.is_relative_to(Path(app_dir).resolve()) or not path.is_file() or path.stat().st_size>12_000_000:
        raise FactReviewRequired("PDF must be a bounded owned artifact")
    from pypdf import PdfReader
    reader=PdfReader(str(path))
    if not 1<=len(reader.pages)<=50:raise FactReviewRequired("PDF has no readable pages or exceeds the document limit")
    import unicodedata
    from collections import Counter
    aliases={"PROFESSIONAL SUMMARY":"SUMMARY","PROFESSIONAL PROFILE":"PROFILE","CORE TECHNICAL SKILLS":"TECHNICAL SKILLS","CORE SKILLS":"TECHNICAL SKILLS","WORK EXPERIENCE":"PROFESSIONAL EXPERIENCE","EMPLOYMENT HISTORY":"PROFESSIONAL EXPERIENCE","EXPERIENCE":"PROFESSIONAL EXPERIENCE"}
    def comparable(value):
        lines=[]
        for line in unicodedata.normalize("NFKC",value).splitlines():
            normal=_normal(line).replace("**","").replace("’","'").replace("–","-").replace("—","-")
            lines.append(aliases.get(normal.upper(),normal).casefold())
        return re.sub(r"\s+"," "," ".join(lines)).strip()
    rendered=comparable("\n".join(page.extract_text() or "" for page in reader.pages))
    text=Path(text_path).read_bytes().decode("utf-8")
    if hashlib.sha256(text.encode()).hexdigest()!=current["text_sha256"]:
        raise FactReviewRequired("Text changed during PDF factual round-trip")
    for claim in current["claims"]:
        if claim.get("fact_ids") and comparable(text[claim["start"]:claim["end"]]) not in rendered:
            raise FactReviewRequired("PDF round-trip lost or changed a verified factual assertion")
    if Counter(re.findall(r"\w+|[^\w\s]",comparable(text)))!=Counter(re.findall(r"\w+|[^\w\s]",rendered)):
        raise FactReviewRequired("PDF round-trip added, omitted or changed source content")
    result={k:current[k] for k in ("text_sha256","dependency_hash","country_code","policy_id","kind","job_url","target_hash")}
    result["pdf_sha256"]=hashlib.sha256(path.read_bytes()).hexdigest()
    result["audit_event_id"]=_audit(conn,"canonical_pdf_bound",{"proof_hash":digest(result)},"deterministic_finaliser")
    setup._atomic_write_bytes(path.with_suffix(path.suffix+".claims.json"),json.dumps(result,indent=2).encode())
    conn.commit()
    return result


def require_pdf(conn,app_dir,text_path,pdf_path,job,*,kind="resume"):
    current=require_final(conn,app_dir,text_path,job,kind=kind)
    path=Path(pdf_path).resolve()
    try:
        if not path.is_relative_to(Path(app_dir).resolve()) or path.stat().st_size>12_000_000:raise ValueError("Unowned PDF")
        saved=json.loads(path.with_suffix(path.suffix+".claims.json").read_text(encoding="utf-8"))
        proof={k:current[k] for k in ("text_sha256","dependency_hash","country_code","policy_id","kind","job_url","target_hash")}|{"pdf_sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
        if any(saved.get(k)!=v for k,v in proof.items()) or not _audit_valid(conn,saved.get("audit_event_id"),"canonical_pdf_bound",digest(proof)):
            raise ValueError("PDF or factual dependencies changed")
        return proof
    except (ValueError,OSError,KeyError,TypeError) as exc:
        raise FactReviewRequired("The actual PDF bytes need a current factual round-trip binding") from exc
