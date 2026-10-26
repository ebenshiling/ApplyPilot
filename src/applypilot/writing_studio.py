"""Versioned cover/statement drafts, reusable evidence and explicit publication."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
import uuid

from applypilot import setup_workspace as setup
from applypilot.pipeline_store import now


def init_schema(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS cc_writing_documents(id INTEGER PRIMARY KEY,family TEXT,parent_id INTEGER,kind TEXT,job_url TEXT,title TEXT,text_path TEXT,pdf_path TEXT,source_json TEXT,validation TEXT,status TEXT,provider TEXT,model TEXT,created_at TEXT);
    CREATE TABLE IF NOT EXISTS cc_writing_requests(id INTEGER PRIMARY KEY,kind TEXT,input_hash TEXT,state TEXT,created_at TEXT);
    """)
    from applypilot.operations import init_schema as init_requests
    init_requests(conn)


def context(conn, app_dir: Path, data: dict) -> dict:
    from applypilot.role_routing import route_resume_for_job
    from applypilot.cv_library import source_text
    from applypilot.professional import enrich_evidence
    kind = data.get("kind", "cover")
    if kind not in {"cover", "statement"}:
        raise ValueError("Choose cover or statement")
    if data.get("job_id"):
        row = conn.execute("SELECT rowid AS job_id,* FROM jobs WHERE rowid=?", (int(data["job_id"]),)).fetchone()
        if not row:
            raise ValueError("Job not found")
        job = dict(row)
        if str(data.get("company") or "").strip():
            job["company"] = str(data["company"]).strip()[:300]
        if str(data.get("title") or "").strip():
            job["title"] = str(data["title"]).strip()[:200]
    else:
        job = {"title": str(data.get("title") or "").strip(), "company": str(data.get("company") or "").strip(),
               "site": str(data.get("company") or "Employer not specified"), "full_description": str(data.get("job_text") or "").strip()}
    if not job.get("title") or not job.get("full_description") or len(job["full_description"]) > 50000:
        raise ValueError("Provide a role title and job description (up to 50,000 characters)")
    from applypilot.country_context import check_job
    country_context=check_job(job, app_dir, country_code=data.get("country_code"), preparation=True)
    from applypilot.nhs_writing import validated_plan, confirmed_facts
    plan = validated_plan(data, kind)
    job["_nhs_plan"] = plan
    profile = setup.read_profile(app_dir)
    if country_context.country_code=="NZ":
        from applypilot.preparation import country_profile
        profile=country_profile(profile,country_context.preferences)
    profile_hash = hashlib.sha256(json.dumps({k:v for k,v in profile.items() if k!="canonical_facts"}, sort_keys=True).encode()).hexdigest()
    if not (profile.get("personal") or {}).get("full_name"):
        raise ValueError("Save your name in Profile before generating application documents")
    variant = str(data.get("variant") or "auto")
    if variant == "tailored":
        path = Path(job.get("tailored_resume_path") or "")
        if not path.resolve().is_relative_to(app_dir.resolve()) or not path.is_file():
            raise ValueError("This job has no available tailored CV")
        resume = path.read_text(encoding="utf-8")
    elif variant == "custom":
        resume = str(data.get("resume_text") or "").strip()
    elif variant == "auto":
        routed = route_resume_for_job(job, app_dir=app_dir)
        if routed.ambiguous:
            raise ValueError("Choose a specific resume for this ambiguous role")
        resume = routed.text
        variant = routed.key
    else:
        resume = source_text(app_dir, variant)
    if not resume.strip() or len(resume) > 600000:
        raise ValueError("Choose or upload a readable CV source")
    source_hash = hashlib.sha256(resume.encode()).hexdigest()
    resume, profile = enrich_evidence(resume, profile, job)
    facts = str(data.get("facts") or "").strip()
    facts_input = facts
    if len(facts) > 15000:
        raise ValueError("Additional evidence exceeds 15,000 characters")
    facts = "\n\n".join(part for part in (facts, confirmed_facts(plan)) if part)
    tone = data.get("tone", "professional")
    if tone not in {"professional", "warm", "direct"}:
        raise ValueError("Choose a supported tone")
    maximum = int(data.get("max_words", 250 if kind == "cover" else 1400))
    minimum = int(data.get("min_words", 120 if kind == "cover" else min(900, maximum//2)))
    if not 50 <= minimum <= maximum <= (800 if kind == "cover" else 2500):
        raise ValueError("Choose sensible word limits: minimum 50, maximum 800 for covers or 2500 for statements")
    instructions = str(data.get("instructions") or "")[:1500]
    instructions = f"Tone: {tone}. " + instructions
    job["_workspace_dir"] = str(app_dir)
    job["_studio_instructions"] = instructions
    structure = str(data.get("structure") or "criteria-led")
    if structure not in {"criteria-led", "story-then-criteria", "values-first"}:
        raise ValueError("Choose a supported statement structure")
    job["_studio_structure"] = structure
    from applypilot.country_context import current
    return {"kind": kind, "country_code":country_context.country_code or "GB", "country_preferences_revision":country_context.revision,"operation_country_code":current().country_code if current() else country_context.country_code, "profile_projection":"without_canonical_v1", "job": job, "profile": profile, "profile_hash": profile_hash, "source_hash": source_hash, "resume": resume, "facts": facts, "facts_input": facts_input, "variant": variant,
            "min_words": minimum, "max_words": maximum, "instructions": instructions, "structure": structure, "nhs_plan": plan}


def validate(text: str, source: dict) -> dict:
    if source["kind"] == "cover":
        from applypilot.scoring.validator import validate_cover_letter
        evidence = source["resume"] + "\n" + source["facts"]
        out = validate_cover_letter(text, source["profile"], evidence, min_words=source["min_words"], max_words=source["max_words"])
    else:
        from applypilot.scoring.supporting_statement import _validate_statement, evaluate_statement_quality
        errors = _validate_statement(text, min_words=source["min_words"], max_words=source["max_words"])
        quality = evaluate_statement_quality(text, source["resume"], source["job"], source["facts"], min_words=source["min_words"], max_words=source["max_words"])
        errors += list(quality.get("hard_failures") or [])
        name = str((source["profile"].get("personal") or {}).get("full_name") or "").strip()
        if len(name.split()) >= 2 and re.search(r"\b"+re.escape(name)+r"\b", text, re.I):
            errors.append("Contains candidate name in supporting evidence; remove identifying personal details")
        out = {"passed": not errors, "errors": errors, "quality": quality}
    from applypilot.nhs_writing import review
    plan_review = review(source.get("nhs_plan", {}), text)
    if not plan_review["within_character_limit"]:
        out["errors"].append(f"Too long for this field: {len(text)} characters, maximum {plan_review['character_limit']}")
        out["passed"] = False
    if source["kind"] == "cover":
        from applypilot.scoring.supporting_statement import _find_unsupported_claims
        claims = _find_unsupported_claims(text, source["resume"]+"\n"+source["facts"])
        out["errors"] += ["Unsupported claim: "+claim for claim in claims]
        out["passed"] = not out["errors"]
    return out | {"words": len(text.split()), "minimum": source["min_words"], "maximum": source["max_words"], "nhs_review": plan_review}


def save(conn, app_dir: Path, data: dict, *, generated: str | None = None, route: dict | None = None, _source_snapshot=None) -> dict:
    source = _source_snapshot if _source_snapshot is not None else context(conn, app_dir, data)
    text = generated if generated is not None else str(data.get("text") or "")
    if not text.strip() or len(text) > 100000:
        raise ValueError("Draft text must be nonempty and under 100,000 characters")
    parent = None
    if data.get("parent_id"):
        parent = conn.execute("SELECT * FROM cc_writing_documents WHERE id=?", (int(data["parent_id"]),)).fetchone()
        if not parent or parent["kind"] != source["kind"]:
            raise ValueError("Draft parent is unavailable")
    family = parent["family"] if parent else uuid.uuid4().hex
    filename = uuid.uuid4().hex + ("_CL.txt" if source["kind"] == "cover" else "_SS.txt")
    path = app_dir / "writing_documents" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    setup._atomic_write_text(path, text)
    check = validate(text, source)
    source["input_hash"] = hashlib.sha256(json.dumps(source, sort_keys=True, default=str).encode()).hexdigest()
    meta = route or {"provider": "manual", "model": "manual edit"}
    ident = conn.execute("INSERT INTO cc_writing_documents(family,parent_id,kind,job_url,title,text_path,source_json,validation,status,provider,model,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         (family, parent["id"] if parent else None, source["kind"], source["job"].get("url"), source["job"]["title"], str(path), json.dumps(source), json.dumps(check), "validated draft" if check["passed"] else "draft", meta.get("provider"), meta.get("model"), now())).lastrowid
    conn.commit()
    return document(conn, app_dir, ident)


def generate(conn, app_dir: Path, data: dict) -> dict:
    from applypilot.ai_routing import task_context, route_info
    from applypilot.pipeline_store import usage_context
    from applypilot.llm import get_client
    source = context(conn, app_dir, data)
    if source["nhs_plan"]["employer_ai_policy"] == "not_allowed":
        raise ValueError("You marked this employer/field as not allowing AI-generated content. Use manual drafting and the evidence planner instead.")
    info = route_info(source["kind"])
    if not info.get("configured"):
        raise ValueError("Configure an AI provider in Settings before generating a document")
    projected=dict(source)
    projected.pop("country_preferences_revision",None)
    projected["profile"]={k:v for k,v in source["profile"].items() if k!="canonical_facts"}
    projected["job"]={k:v for k,v in source["job"].items() if k in {"url","job_id","title","company","site","location","full_description","country_code","_nhs_plan","_workspace_dir","_studio_instructions","_studio_structure"}}
    digest = hashlib.sha256(json.dumps([projected, info], sort_keys=True, default=str).encode()).hexdigest()
    if not data.get("fresh"):
        row = conn.execute("SELECT id FROM cc_writing_documents WHERE source_json LIKE ? AND provider!='manual' ORDER BY id DESC LIMIT 1", ('%"generation_hash": "'+digest+'"%',)).fetchone()
        if row:
            return document(conn, app_dir, row[0]) | {"cached": True}
    from applypilot import operations
    from applypilot import canonical
    from applypilot.country_context import preference_dependency
    from applypilot.discovery.observations import metadata
    nz=source["operation_country_code"]=="NZ"
    reservation=operations.reserve(conn,app_dir,source["kind"],digest,source["operation_country_code"],origin="studio",job_url=source["job"].get("url"),request_key=data.get("request_key"),fresh=data.get("fresh") is True,
        source_hash=metadata(source["job"]).get("content_hash") if nz else None,
        target_hash=canonical.digest({k:source["job"].get(k) for k in ("url","title","company")}) if nz else None,
        disclosure_hash=preference_dependency(conn,"NZ","work_authorization",snapshot={"config":{"work_authorization":source["profile"].get("work_authorization",{})},"revision":source["country_preferences_revision"]}) if nz else None)
    request_id=reservation["id"]
    if not reservation["new"]:
        if reservation["state"]=="completed" and reservation.get("result_id"):
            return document(conn,app_dir,reservation["result_id"])|{"cached":True,"request_id":request_id}
        if reservation["state"]=="reserved":
            return {"pending":True,"request_id":request_id,"state":"in_progress","cached":False}
        raise operations.OperationHeld("The previous identical generation is held after failure/expiry; use a new explicit request to retry")
    try:
        with operations.scope(app_dir,reservation),usage_context(app_dir, "studio:"+str(request_id)), task_context(source["kind"]):
            if source["kind"] == "cover":
                from applypilot.scoring.cover_letter import generate_cover_letter
                text = generate_cover_letter(source["resume"]+"\n"+source["facts"], source["job"], source["profile"], options={"min_words": source["min_words"], "max_words": source["max_words"], "instructions": source["instructions"]})
            else:
                from applypilot.scoring.supporting_statement import generate_supporting_statement
                text = generate_supporting_statement(source["resume"], source["job"], source["profile"], supplemental_facts=source["facts"], min_words=source["min_words"], max_words=source["max_words"])
            info["model"] = get_client().last_model
        out = save(conn, app_dir, data, generated=text, route=info,_source_snapshot=source)
    except Exception:
        operations.fail(conn,reservation)
        raise
    stored = json.loads(conn.execute("SELECT source_json FROM cc_writing_documents WHERE id=?", (out["document"]["id"],)).fetchone()[0])
    stored["generation_hash"] = digest
    conn.execute("UPDATE cc_writing_documents SET source_json=? WHERE id=?", (json.dumps(stored), out["document"]["id"]))
    conn.commit()
    operations.complete(conn,reservation,{"document_id":out["document"]["id"]},document_id=out["document"]["id"])
    return out | {"cached": False,"request_id":request_id}


def document(conn, app_dir: Path, ident: int) -> dict:
    row = conn.execute("SELECT * FROM cc_writing_documents WHERE id=?", (ident,)).fetchone()
    if not row:
        raise LookupError("Draft not found")
    item = dict(row)
    from applypilot.country_context import check_job,current
    stored_job=conn.execute("SELECT * FROM jobs WHERE url=?",(row["job_url"],)).fetchone() if row["job_url"] else None
    if stored_job:
        check_job(dict(stored_job),app_dir,country_code=current().country_code if current() else None)
    else:
        from applypilot.country_context import require_enabled,normalize_country
        saved_source=json.loads(row["source_json"])
        code=normalize_country(saved_source.get("country_code"))
        require_enabled(code,setup.read_searches_dict(app_dir))
        if current() and (code or "GB")!=(current().country_code or "GB"):raise ValueError("Draft is outside this country view")
    path = Path(item["text_path"])
    if not path.resolve().is_relative_to(app_dir.resolve()) or not path.is_file():
        raise LookupError("Draft file is unavailable")
    source = json.loads(item.pop("source_json"))
    item["validation"] = json.loads(item["validation"])
    return {"document": item, "text": path.read_text(encoding="utf-8"), "source": {k: source[k] for k in ("kind", "variant", "min_words", "max_words", "instructions", "structure")} | {"facts": source.get("facts_input", source["facts"]), "resume_text": source["resume"] if source["variant"] == "custom" else "", "nhs_plan": source.get("nhs_plan", {})},
            "job": {k: source["job"].get(k) for k in ("job_id", "title", "company", "full_description")}}


def listing(conn, kind: str) -> dict:
    if kind not in {"cover", "statement"}:
        raise ValueError("Unknown studio")
    from applypilot.country_context import current
    code = current().country_code if current() else None
    return {"documents": [dict(r) for r in conn.execute("""SELECT id,family,parent_id,kind,job_url,title,status,provider,model,created_at
        FROM cc_writing_documents d WHERE kind=? AND COALESCE(
        (SELECT country_code FROM jobs WHERE url=d.job_url),json_extract(d.source_json,'$.country_code'),'GB')=?
        ORDER BY id DESC LIMIT 150""", (kind, code or "GB"))]}


def request_status(conn,app_dir,ident,code):
    from applypilot.source_access import moment
    from datetime import datetime,timezone
    row=conn.execute("SELECT * FROM cc_writing_requests WHERE id=? AND kind IN ('cover','statement') AND (origin='studio' OR origin IS NULL)",(int(ident),)).fetchone()
    if not row or (row["country_code"] or "GB")!=code:raise ValueError("Generation request is outside this country/workspace")
    if row["state"]=="completed" and row["result_id"]:
        return document(conn,app_dir,row["result_id"])|{"cached":True,"request_id":row["id"]}
    state=row["state"]
    if state=="reserved" and (not moment(row["lease_until"]) or moment(row["lease_until"])<=datetime.now(timezone.utc)):state="needs_review"
    return {"pending":state=="reserved","state":state,"request_id":row["id"],"transport_attempts":row["transport_attempts"],"detail":"Request is still running" if state=="reserved" else "The request did not produce a reusable draft; explicitly request a new generation after review"}


def publish(conn, app_dir: Path, ident: int) -> dict:
    from applypilot.eligibility import exclusions
    row = conn.execute("SELECT * FROM cc_writing_documents WHERE id=?", (ident,)).fetchone()
    if not row or not row["job_url"]:
        raise ValueError("Choose a saved job before publishing an application document")
    job_row = conn.execute("SELECT rowid AS job_id,* FROM jobs WHERE url=?", (row["job_url"],)).fetchone()
    if not job_row or exclusions(conn, dict(job_row)):
        raise ValueError("The job is submitted, archived, blocked or active; save a draft instead")
    from applypilot.country_context import check_job
    check_job(dict(job_row), app_dir, preparation=True)
    source = json.loads(row["source_json"])
    current_profile = setup.read_profile(app_dir)
    if source.get("country_code")=="NZ":
        from applypilot.preparation import country_profile
        from applypilot.country_context import read_preferences
        current_profile=country_profile(current_profile,read_preferences(conn,"NZ")["config"])
    if source.get("profile_projection")=="without_canonical_v1":
        current_profile={k:v for k,v in current_profile.items() if k!="canonical_facts"}
    current_hash = hashlib.sha256(json.dumps(current_profile, sort_keys=True).encode()).hexdigest()
    if source.get("profile_hash") and source["profile_hash"] != current_hash:
        raise ValueError("Your professional profile changed; save a new draft version against current evidence before publication")
    if source["variant"] != "custom" and source.get("source_hash"):
        if source["variant"] == "tailored":
            path = Path(job_row["tailored_resume_path"] or "")
            if not path.resolve().is_relative_to(app_dir.resolve()) or not path.is_file():
                raise ValueError("The tailored CV is unavailable; choose a current CV source")
            live_text = path.read_text(encoding="utf-8")
        else:
            from applypilot.cv_library import source_text
            live_text = source_text(app_dir, source["variant"])
        if hashlib.sha256(live_text.encode()).hexdigest() != source["source_hash"]:
            raise ValueError("The CV source changed; save a new draft version against current evidence before publication")
    from applypilot.professional import enrich_evidence
    source["resume"], source["profile"] = enrich_evidence(source["resume"], setup.read_profile(app_dir), source["job"])
    text = document(conn, app_dir, ident)["text"]
    check = validate(text, source)
    if not check["passed"]:
        conn.execute("UPDATE cc_writing_documents SET validation=?,status='draft' WHERE id=?", (json.dumps(check), ident))
        conn.commit()
        raise ValueError("Resolve validation issues before using this draft: " + "; ".join(check["errors"][:3]))
    from applypilot.canonical import finalize
    manifest = finalize(conn,app_dir,Path(row["text_path"]),dict(job_row),kind=source["kind"])
    source["factual_finalisation"] = manifest
    conn.execute("UPDATE cc_writing_documents SET source_json=? WHERE id=?",(json.dumps(source),ident))
    from applypilot.pipeline_store import persist_document
    persist_document(conn, source["kind"], {"url": row["job_url"], "path": row["text_path"], "status": "approved"})
    requirements = json.loads(job_row["document_requirements"] or "{}")
    requirements[source["kind"]] = True
    conn.execute("UPDATE jobs SET document_requirements=? WHERE url=?", (json.dumps(requirements), row["job_url"]))
    conn.execute("UPDATE cc_writing_documents SET status='published' WHERE id=?", (ident,))
    conn.commit()
    return {"published": True, "validation": check}


def pdf(conn, app_dir: Path, ident: int) -> Path:
    document(conn, app_dir, ident)
    row = conn.execute("SELECT text_path,pdf_path,status,job_url,kind FROM cc_writing_documents WHERE id=?", (ident,)).fetchone()
    if row["status"]=="published":
        from applypilot.canonical import require_final
        job=dict(conn.execute("SELECT * FROM jobs WHERE url=?",(row["job_url"],)).fetchone())
        require_final(conn,app_dir,row["text_path"],job,kind=row["kind"])
    path = Path(row[0])
    output = path.with_suffix(".pdf")
    if not output.is_file():
        from applypilot.scoring.pdf import convert_to_pdf
        output = convert_to_pdf(path)
        conn.execute("UPDATE cc_writing_documents SET pdf_path=? WHERE id=?", (str(output), ident))
        conn.commit()
    if row["status"]=="published":
        from applypilot.canonical import bind_pdf
        bind_pdf(conn,app_dir,path,output,job,kind=row["kind"])
    return output
