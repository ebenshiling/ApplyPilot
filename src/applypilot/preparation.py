"""Explicit NZ preparation capability; technical fit is not immigration suitability."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re

from applypilot import country_context as country,setup_workspace as setup,canonical


def require_ready(app_dir,conn=None):
    from applypilot.database import get_connection
    from applypilot.nz_documents import state
    country.require_enabled("NZ",setup.read_searches_dict(app_dir))
    if not (Path(app_dir)/"applypilot.db").is_file():
        raise country.CountryError("New Zealand preparation requires an initialized workspace and reconciled masters")
    conn=conn or get_connection(Path(app_dir)/"applypilot.db")
    if not state(conn,app_dir)["candidate_import_complete"]:
        raise country.CountryError("New Zealand preparation requires current canonical reconciliation of all three NZ masters")
    return conn


def require_job(job,app_dir,*,ai=False,text_required=True):
    from applypilot.discovery.observations import metadata
    from applypilot.nz_documents import route
    conn=require_ready(app_dir)
    if job.get("country_code")!="NZ":raise country.CountryError("NZ preparation requires a persisted NZ destination")
    stored=conn.execute("SELECT * FROM jobs WHERE url=? AND country_code='NZ'",(job.get("url"),)).fetchone()
    if not stored:raise country.CountryError("New Zealand preparation requires a saved, explicitly captured NZ vacancy")
    stored=dict(stored)
    if stored["applied_at"] or stored["apply_status"] in {"in_progress","running","submitting","submission_uncertain"}:
        raise country.CountryError("NZ preparation cannot mutate an active, submitted or uncertain application")
    from applypilot.command_center import blocked
    visibility=conn.execute("SELECT visibility FROM cc_job_meta WHERE job_url=?",(stored["url"],)).fetchone()
    if stored["apply_status"] in {"skipped","blocked"} or visibility and visibility[0]!="active" or blocked(conn,stored["url"],stored.get("company")):
        raise country.CountryError("Restore/unblock the NZ opportunity before new preparation")
    meta=metadata(stored)
    if meta.get("country_status")!="confirmed" and not (meta.get("destination_choice",{}).get("country_code")=="NZ" and meta.get("destination_choice",{}).get("source_hash")==meta.get("content_hash")):
        raise country.CountryError("NZ preparation needs an explicitly confirmed/selected NZ destination; unknown adverts remain discoverable")
    if text_required and (meta.get("reference_only") or not meta.get("storage_allowed")):
        raise country.CountryError("NZ preparation needs permitted stored text and an explicitly confirmed NZ advert destination")
    if text_required and meta.get("provider_kind")=="manual":
        from applypilot.source_access import restricted_host
        from urllib.parse import urlsplit
        if restricted_host(urlsplit(meta.get("original_listing_url") or stored["url"]).hostname or "") or not str(meta.get("permission_reference") or "").strip():
            raise country.CountryError("Restricted or unattested manual sources remain reference-only for automated preparation")
    description=stored.get("full_description") or stored.get("description") or ""
    if text_required and (not description or hashlib.sha256(description.encode()).hexdigest()!=meta.get("content_hash")):
        raise country.CountryError("NZ preparation requires current source-bound vacancy text; refresh or recapture permitted text")
    supplied=job.get("full_description") or job.get("description") or ""
    if supplied and hashlib.sha256(supplied.encode()).hexdigest()!=meta.get("content_hash"):
        raise country.CountryError("NZ vacancy text changed; reload the captured operation before generation")
    if any(job.get(k) is not None and job.get(k)!=stored.get(k) for k in ("title","company")):
        raise country.CountryError("NZ vacancy target changed; save/reselect the actual target before generation")
    if ai and not meta.get("ai_use_allowed"):
        raise country.CountryError("This NZ source has no recorded AI-use permission; use permitted manual drafting")
    if text_required and meta.get("provider_kind")!="manual":
        from applypilot.discovery.nz import source
        from applypilot.source_access import require
        item=source(Path(app_dir),meta.get("source_id"))
        method="workday_cxs" if item["provider_kind"]=="workday" else "jobspy" if item["provider_kind"]=="board" else "http"
        require(item,method=method)
        if ai and not item.get("access",{}).get("ai_use_allowed"):
            raise country.CountryError("Current NZ source permission does not allow AI use")
    route(stored,app_dir)
    return stored


def choose_destination(conn,app_dir,data,*,actor):
    from applypilot.command_center import get_job,event
    from applypilot.discovery.observations import metadata
    job=get_job(conn,int(data.get("id") or 0))
    country.check_job(job,app_dir,country_code="NZ")
    meta=metadata(job)
    if data.get("confirmed") is not True or not isinstance(data.get("note"),str) or not 1<=len(data["note"].strip())<=2000:
        raise ValueError("Explicitly review the NZ destination and record a bounded note")
    if meta.get("country_status")=="out_of_scope":raise ValueError("Known non-NZ advert evidence cannot be silently overridden; correct the source/destination first")
    choice={"country_code":"NZ","source_hash":meta.get("content_hash"),"actor":actor,"recorded_at":datetime.now(timezone.utc).isoformat(),"note":data["note"].strip()}
    meta["destination_choice"]=choice
    conn.execute("UPDATE jobs SET vacancy_metadata=? WHERE url=? AND country_code='NZ'",(json.dumps(meta),job["url"]))
    event(conn,job["url"],"nz_destination_choice","Candidate selected NZ destination; advert-country evidence retained separately",source="manual",payload=choice)
    conn.commit()
    return {"destination_choice":choice}


def manual_document(conn,app_dir,data,*,actor):
    from applypilot.command_center import get_job,event
    from applypilot.nz_documents import route
    from applypilot.pipeline_store import persist_document
    import uuid
    job=get_job(conn,int(data.get("id") or 0))
    country.check_job(job,app_dir,country_code="NZ")
    require_job(job,app_dir,text_required=False)
    kind=data.get("kind","resume")
    if kind not in {"resume","cover","statement"}:raise ValueError("Choose a CV, cover letter or supporting statement")
    text=data.get("text")
    if text is None:
        if kind!="resume":raise ValueError("Provide your own letter/statement text; no source text or model is retrieved")
        master=route(job,Path(app_dir))
        text=master.text
        source=conn.execute("SELECT role FROM cc_cv_sources WHERE source_key=?",(master.key,)).fetchone()
        lines=text.splitlines()
        for index,line in enumerate(lines[:6]):
            if source[0] and line.strip()==source[0]:lines[index]="Target role: "+job["title"]
        text="\n".join(lines)+"\n"
    if not isinstance(text,str) or not 1<=len(text)<=100000:raise ValueError("Manual document text must be nonempty and up to 100,000 characters")
    folder={"resume":"tailored_resumes","cover":"cover_letters","statement":"supporting_statements"}[kind]
    suffix={"resume":".txt","cover":"_CL.txt","statement":"_SS.txt"}[kind]
    path=Path(app_dir)/folder/("NZ_manual_"+uuid.uuid4().hex+suffix)
    setup._atomic_write_bytes(path,text.encode())
    result={"url":job["url"],"path":str(path),"status":"approved","source":"manual_owned_candidate_text"}
    persist_document(conn,"tailor" if kind=="resume" else kind,result,manual_owned_text=True)
    from applypilot.command_center import register_document
    review_path=path.with_suffix(".review.json")
    setup._atomic_write_bytes(review_path,json.dumps(canonical.check(conn,app_dir,text,job,kind=kind),indent=2).encode())
    document_id=register_document(conn,Path(app_dir),job,path,None,review_path,"manual candidate text",kind=kind)
    event(conn,job["url"],"manual_document","Candidate-owned text retained; factual gate determines final versus draft status",source="manual",payload={"actor":actor,"kind":kind,"status":result["status"]})
    conn.commit()
    return result|{"document_id":document_id,"kind":kind}


def technical_text(text):
    """Exclude recruitment/rights/compensation clauses, not the technical score."""
    forbidden=re.compile(r"work rights|right to work|work authori[sz]|visa|sponsor|aewv|overseas applicants|international applicants|accredited employer|immigration|citizen|residen|relocat|salary|kiwisaver",re.I)
    kept=[]
    for sentence in re.split(r"(?<=[.!?])\s+|\n",str(text or "")):
        for clause in re.split(r"\s+(?:and|but|however)\s+",sentence,flags=re.I):
            if clause.strip() and not forbidden.search(clause):kept.append(clause.strip())
    return "\n".join(kept)


def enrichment_eligible(job,app_dir):
    from applypilot.discovery.observations import metadata
    from applypilot.discovery.nz import source
    from applypilot.source_access import readiness
    meta=metadata(job)
    if meta.get("reference_only") or meta.get("provider_kind") not in {"direct","workday"}:return False
    try:
        item=source(Path(app_dir),meta.get("source_id"))
        return readiness(item,method="workday_cxs" if item["provider_kind"]=="workday" else "http") is None
    except ValueError:return False


def enrich(conn,app_dir,*,limit=100):
    from applypilot.discovery import observations as obs,nz
    from applypilot import source_access as access
    from urllib.parse import urlsplit
    count=errors=0
    with access.request_scope():
        for row in conn.execute("SELECT * FROM jobs WHERE country_code='NZ' AND full_description IS NULL AND applied_at IS NULL ORDER BY discovered_at DESC LIMIT ?",(min(100,int(limit)),)).fetchall():
            job=dict(row)
            if not enrichment_eligible(job,app_dir):continue
            meta=obs.metadata(job);item=nz.source(Path(app_dir),meta["source_id"])
            count+=1
            try:
                if item["provider_kind"]=="workday":
                    from applypilot.discovery.workday import _fetch_one_detail
                    path=urlsplit(meta["original_listing_url"]).path
                    marker="/job/";position=path.find(marker)
                    if position<0:raise ValueError("Workday detail path unavailable")
                    result=_fetch_one_detail(item,{"url":meta["original_listing_url"],"title":job["title"],"company":job["company"],"location":job["location"],"external_path":path[position:]})
                    if result.get("detail_error"):raise ValueError("Workday detail unavailable")
                    batch=[result]
                else:
                    response=access.read_http(item,meta["original_listing_url"])
                    if response["http_status"]!=200:raise ValueError("Source detail unavailable")
                    batch=obs.structured_jobs(response["html"],response["url"])
                matched=[j for j in batch if j.get("url") and (obs.identity_url(j["url"],item)==job["url"] or meta.get("provider_job_id") and obs.text(j.get("provider_job_id") or j.get("job_req_id"))==meta["provider_job_id"])]
                matched=[j for j in matched if len(obs.text(j.get("full_description") or j.get("description")))>200]
                if not matched:raise ValueError("No matching substantive structured detail")
                obs.store(conn,matched,item,method="workday_api" if item["provider_kind"]=="workday" else "json_ld_no_ai")
                conn.execute("UPDATE jobs SET detail_error=NULL WHERE url=?",(job["url"],))
            except Exception:
                errors+=1
                conn.execute("UPDATE jobs SET detail_error='Permitted structured detail unavailable; refresh the source or use permitted manual capture' WHERE url=?",(job["url"],))
            conn.commit()
    return {"processed":count,"ok":count-errors,"errors":errors,"status":"partial" if errors else "ok","note":"Reference-only/manual records are not fetched; no unrestricted browser or AI fallback is used"}


def technical_job(job):
    output={k:job.get(k) for k in ("url","title","company","site","country_code")}
    output["full_description"]=technical_text(job.get("full_description") or job.get("description"))
    output["location"]="Excluded from technical fit; practical location is assessed separately"
    if not output["full_description"].strip():raise ValueError("No separable technical vacancy evidence; manual clarification is needed")
    output["_technical_only"]=True
    return output


def technical_resume(conn,app_dir,route):
    row=conn.execute("SELECT role FROM cc_cv_sources WHERE source_key=? AND country_code='NZ'",(route.key,)).fetchone()
    if not row:raise country.CountryError("Technical scoring requires the selected reconciled NZ master")
    proof=canonical.check(conn,app_dir,route.text,{"country_code":"NZ","title":row[0],"company":""},kind="source_resume")
    if not proof["passed"]:raise country.CountryError("NZ master facts changed before technical scoring")
    facts={f["id"]:f for f in canonical.model(setup.read_profile(app_dir))["facts"]}
    lines=[]
    for claim in proof["claims"]:
        ids=claim.get("fact_ids",[])
        if ids and all(facts[i]["kind"] not in {"personal","disclosure"} for i in ids):
            lines.append(canonical._normal(route.text[claim["start"]:claim["end"]]))
    result=technical_text("\n".join(lines))
    if not result.strip():raise country.CountryError("No separable verified technical assertions; review the selected NZ master")
    return result


def country_profile(profile,preferences):
    output=deepcopy(profile)
    output["work_authorization"]=deepcopy(preferences.get("work_authorization") or {})
    output["availability"]=deepcopy((preferences.get("application_operations") or {}).get("availability") or {})
    output["compensation"]=deepcopy((preferences.get("search_preferences") or {}).get("compensation") or {})
    sensitive={"legally_authorized_to_work","require_sponsorship","work_permit_type","permit_expiry_date","willing_to_relocate","willing_to_travel","notice_period","earliest_start_date","available_for_full_time","available_for_part_time","available_for_contract","available_for_shifts","driving_licence","own_transport","salary_currency","salary_expectation","salary_range_min","salary_range_max"}
    for key in sensitive:output.setdefault("personal",{}).pop(key,None)
    return output


def input_fingerprint(job,app_dir,route):
    from applypilot.ai_routing import route_info
    from applypilot.database import get_connection
    projected=technical_job(job)
    resume=technical_resume(get_connection(Path(app_dir)/"applypilot.db"),app_dir,route)
    return technical_fingerprint(projected,resume,route.key,route_info("score"))


def technical_fingerprint(job,resume,key,model):
    projected=technical_job(job) if not job.get("_technical_only") else job
    return canonical.digest(["nz-technical-score-v1",projected,resume,key,model])


def practical(job,preferences,config):
    from applypilot.discovery.observations import metadata
    from applypilot.discovery.salary_filter import nz_salary_ok
    from applypilot.geography import SelectedLocations
    meta=metadata(job)
    salary=deepcopy(meta.get("salary") or {})
    target=(preferences.get("search_preferences") or {}).get("compensation") or config.get("compensation")
    location=job.get("location")
    matches=SelectedLocations(config).matches(location)
    comparable=bool(salary.get("parse_status")=="parsed" and target and target.get("basis") not in {None,"unknown"})
    salary_match=nz_salary_ok(salary,target)
    return {"state":"mismatch" if not matches or not salary_match else "needs_review" if not location or not comparable or meta.get("work_arrangement")=="unknown" else "matches",
            "location_matches":matches,"salary_comparable":comparable,"salary_matches":salary_match,"salary":salary,
            "arrangement":meta.get("work_arrangement","unknown"),"note":"Practical preferences are separate from technical fit and immigration eligibility"}


def state(conn,app_dir):
    from applypilot.discovery import nz
    from applypilot.nz_documents import state as source_state
    from applypilot.nz_evidence import assess_job
    from applypilot.command_center import details
    from applypilot.apply.application_packet import status as packet_status
    from applypilot.pipeline_workspace import insights
    from applypilot.ai_routing import route_table
    root=Path(app_dir)
    country.require_enabled("NZ",setup.read_searches_dict(root))
    sources=source_state(conn,root)
    config=nz.effective(setup.read_searches_dict(root),next((c for c in setup.read_searches_dict(root).get("campaigns",[]) if c.get("id")==nz.CAMPAIGN_ID),nz.preset()))
    preferences=country.read_preferences(conn,"NZ")["config"]
    jobs=[]
    for row in conn.execute("SELECT rowid AS id,* FROM jobs WHERE country_code='NZ' ORDER BY discovered_at DESC"):
        job=dict(row)
        manual_reason=ai_reason=None
        try:require_job(job,root,text_required=False)
        except ValueError as exc:manual_reason=str(exc)
        try:require_job(job,root,ai=True)
        except ValueError as exc:ai_reason=str(exc)
        data=details(conn,root,job["id"])
        jobs.append({"job":job,"manual_ready":manual_reason is None,"manual_reason":manual_reason,"ai_ready":ai_reason is None,"ai_reason":ai_reason,
            "meta":data["meta"],"events":data["events"],"assessment":assess_job(conn,root,job),"practical":practical(job,preferences,config),"packet":packet_status(conn,root,job["id"]),
            "documents":[dict(d)|{"kind":json.loads(d["context_json"] or "{}").get("kind","resume")} for d in conn.execute("SELECT * FROM cc_documents WHERE job_url=? ORDER BY id DESC",(job["url"],))]})
    return {"jobs":jobs,"masters_ready":sources["candidate_import_complete"],"preparation_available":sources["candidate_import_complete"],"providers":route_table(),"insights":insights(conn,root),"submission":"Apply with agent is available for reviewed packets; confirm the final employer page before submission"}


def track(conn,app_dir,data):
    from applypilot.command_center import get_job,update_job
    ids=data.get("ids") or [data.get("id")]
    if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(isinstance(i,bool) or not isinstance(i,int) for i in ids):raise ValueError("Choose 1–100 NZ job identifiers")
    action=data.get("action")
    if action not in {"pick","unpick","retry","applied","failed","snooze","archive","restore","block","hiring","notes"}:raise ValueError("Choose a supported manual tracking action")
    for ident in ids:country.check_job(get_job(conn,ident),app_dir,country_code="NZ")
    return {"results":[{"id":ident,**update_job(conn,Path(app_dir),ident,action,data|{"country_code":"NZ"})} for ident in ids]}


def messages(conn):
    return [dict(row) for row in conn.execute("SELECT m.* FROM cc_messages m LEFT JOIN jobs j ON j.url=m.job_url WHERE j.country_code='NZ' OR m.job_url IS NULL ORDER BY m.id DESC LIMIT 100")]


def document(conn,app_dir,ident,fmt):
    from applypilot.command_center import get_job
    from applypilot.scoring.pdf import build_html,parse_resume,build_letter_html
    row=conn.execute("SELECT * FROM cc_documents WHERE id=?",(int(ident),)).fetchone()
    if not row:raise ValueError("Document not found")
    job_row=conn.execute("SELECT rowid AS id,* FROM jobs WHERE url=? AND country_code='NZ'",(row["job_url"],)).fetchone()
    if not job_row:raise ValueError("Document is outside the NZ job view")
    job=get_job(conn,job_row["id"])
    country.check_job(job,app_dir,country_code="NZ")
    root=Path(app_dir).resolve();path=Path(row["text_path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size>1_000_000:raise ValueError("Owned document text is unavailable")
    kind=json.loads(row["context_json"] or "{}").get("kind","resume")
    if fmt=="txt":return path.read_bytes(),"text/plain; charset=utf-8"
    if fmt=="pdf":
        pdf=path.with_suffix(".pdf")
        canonical.require_pdf(conn,root,path,pdf,job,kind=kind)
        return pdf.read_bytes(),"application/pdf"
    if fmt=="html":
        text=path.read_text(encoding="utf-8")
        html=build_html(parse_resume(text)) if kind=="resume" else build_letter_html(text)
        return html.encode(),"text/html; charset=utf-8"
    raise ValueError("Choose TXT, HTML preview or current fact-bound PDF; generated DOCX is unsupported")
