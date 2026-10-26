"""Country-specific master presentation on the shared CV/fact libraries."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from applypilot import canonical, country_context as country, setup_workspace as setup

CATEGORIES={"software":("software developer","python developer","django developer","full stack developer","react developer","typescript developer","application developer","junior software engineer","backend developer"),
            "data_bi":("data analyst","business intelligence analyst","reporting analyst","insights analyst","junior data engineer","analytics engineer","data quality analyst"),
            "support":("it support analyst","it technician","application support engineer","technical support engineer","systems support analyst","service desk analyst")}


def _source(conn,key):
    row=conn.execute("SELECT * FROM cc_cv_sources WHERE source_key=? AND country_code='NZ' AND archived=0",(key,)).fetchone()
    if not row:raise ValueError("Choose an available NZ master source; no UK/default fallback is permitted")
    return dict(row)


def reconcile(conn,app_dir,key):
    country.require_enabled("NZ",setup.read_searches_dict(app_dir))
    from applypilot.cv_library import source_text
    item=_source(conn,key)
    text=source_text(app_dir,key)
    job={"country_code":"NZ","title":item["role"],"company":""}
    check=canonical.check(conn,app_dir,text,job,kind="source_resume")
    result={k:check[k] for k in ("text_sha256","dependency_hash","fact_dependencies")}|{"source_key":key,"category":item["category"],"status":"reconciled" if check["passed"] else "unresolved","errors":check["errors"]}
    if check["passed"]:
        result["audit_event_id"]=canonical._audit(conn,"canonical_source_reconciled",{"proof_hash":canonical.digest({k:result[k] for k in ("text_sha256","dependency_hash","source_key","category")})},"deterministic_finaliser")
    conn.execute("UPDATE cc_cv_sources SET reconciliation_json=? WHERE source_key=?",(json.dumps(result),key))
    conn.commit()
    return result


def ready(conn,app_dir,item):
    from applypilot.cv_library import source_text
    try:
        saved=json.loads(item.get("reconciliation_json") or "{}")
        check=canonical.check(conn,app_dir,source_text(app_dir,item["source_key"]),{"country_code":"NZ","title":item["role"],"company":""},kind="source_resume")
        fields=("text_sha256","dependency_hash","source_key","category")
        current={"text_sha256":check["text_sha256"],"dependency_hash":check["dependency_hash"],"source_key":item["source_key"],"category":item["category"]}
        return check["passed"] and saved.get("status")=="reconciled" and all(saved.get(k)==current[k] for k in fields) and canonical._audit_valid(conn,saved.get("audit_event_id"),"canonical_source_reconciled",canonical.digest(current))
    except (OSError,ValueError,LookupError,KeyError):return False


def route(job,app_dir):
    from applypilot.database import get_connection
    from applypilot.cv_library import source_text
    from applypilot.role_routing import RoutedResume
    root=Path(app_dir)
    conn=get_connection(root/"applypilot.db")
    country.require_enabled("NZ",setup.read_searches_dict(root))
    override=job.get("resume_route_override")
    title=" "+re.sub(r"[^\w]+"," ",str(job.get("title") or "").casefold()).strip()+" "
    if override:
        item=_source(conn,override)
        reason="Explicit reconciled NZ master override"
    else:
        matches=[(len(phrase),category) for category,phrases in CATEGORIES.items() for phrase in phrases if " "+phrase+" " in title]
        if not matches:
            families={category for category,words in {"software":("software","python","django","react","typescript","backend","full stack"),"data_bi":("data","analytics","reporting","business intelligence"),"support":("it support","technical support","application support","service desk","systems support")}.items() if any(" "+w+" " in title for w in words)}
            if len(families)!=1:raise ValueError("Unknown/ambiguous NZ role: choose a reconciled NZ master explicitly")
            category=families.pop()
        else:
            longest=max(v[0] for v in matches)
            categories={v[1] for v in matches if v[0]==longest}
            if len(categories)!=1:raise ValueError("Ambiguous NZ title route; choose a master explicitly")
            category=categories.pop()
        items=[dict(r) for r in conn.execute("SELECT * FROM cc_cv_sources WHERE country_code='NZ' AND category=? AND archived=0",(category,))]
        if len(items)!=1:raise ValueError("NZ category needs one reconciled master or an explicit source override")
        item=items[0]
        reason="NZ title/category mapping: "+category
    if not ready(conn,root,item):raise ValueError("NZ master claims require current canonical reconciliation")
    path=setup.resume_variant_path(root,item["source_key"])
    return RoutedResume(item["source_key"],str(path),source_text(root,item["source_key"]),3,reason)


def state(conn,app_dir):
    country.require_enabled("NZ",setup.read_searches_dict(app_dir))
    items=[dict(r) for r in conn.execute("SELECT * FROM cc_cv_sources WHERE country_code='NZ'")]
    return {"masters":[v|{"reconciled":False if v["archived"] else ready(conn,app_dir,v)} for v in items],"categories":CATEGORIES,"candidate_import_complete":all(any(v["category"]==category and not v["archived"] and ready(conn,app_dir,v) for v in items) for category in CATEGORIES),"canonical":canonical.state(conn,app_dir)}


def _campaign_references(conn,app_dir,old,new):
    from copy import deepcopy
    from applypilot.discovery import nz
    root=setup.read_searches_dict(app_dir)
    campaign=next((c for c in root.get("campaigns",[]) if c.get("id")==nz.CAMPAIGN_ID and c.get("country_code")=="NZ"),None)
    if not campaign:return
    proposed=deepcopy(campaign)
    rr=proposed.get("resume_routing") or {}
    variants=rr.get("variants",{})
    if old in variants:
        variants.pop(old)
        if new:variants[new]="resume_variants/"+new+".txt"
    rr["title_routes"]={k:(new if v==old else v) for k,v in rr.get("title_routes",{}).items() if new or v!=old}
    for field in ("families","context_families"):
        rr[field]=[dict(f,variant=new) if f.get("variant")==old and new else f for f in rr.get(field,[]) if new or f.get("variant")!=old]
    blends=rr.get("blended_routes") or {}
    if "combos" in blends:
        blends["combos"]=[{k:(new if v==old else v) for k,v in combo.items()} for combo in blends["combos"] if new or old not in combo.values()]
    proposed["resume_routing"]=rr
    if proposed!=campaign:nz.save_campaign(conn,app_dir,{"expected_revision":nz.revision(root),"campaign":proposed})


def source_action(conn,app_dir,data):
    import uuid
    from applypilot.cv_library import revision,source_text,import_files
    import base64
    country.require_enabled("NZ",setup.read_searches_dict(app_dir))
    key=str(data.get("key") or "")
    row=conn.execute("SELECT * FROM cc_cv_sources WHERE source_key=? AND country_code='NZ'",(key,)).fetchone()
    if not row:raise ValueError("NZ master not found")
    operation=data.get("action")
    if operation=="default":raise ValueError("An NZ master cannot replace the shared UK/legacy default resume")
    if operation=="edit":
        setup.write_resume_variant(app_dir,key,str(data.get("text") or ""))
    elif operation=="rename":
        new=str(data.get("new_key") or "").strip().lower()
        if conn.execute("SELECT 1 FROM cc_cv_sources WHERE source_key=?",(new,)).fetchone():raise ValueError("CV source key already exists")
        setup.rename_resume_variant(app_dir,key,new)
        try:_campaign_references(conn,app_dir,key,new)
        except Exception:
            old_path,new_path=setup.resume_variant_path(app_dir,key),setup.resume_variant_path(app_dir,new)
            if not old_path.exists():new_path.replace(old_path)
            raise
        conn.execute("UPDATE cc_cv_sources SET source_key=?,reconciliation_json=NULL WHERE source_key=?",(new,key))
        conn.execute("UPDATE cc_cv_revisions SET source_key=? WHERE source_key=?",(new,key))
        conn.execute("UPDATE jobs SET resume_route_override=? WHERE country_code='NZ' AND resume_route_override=?",(new,key))
        key=new
    elif operation=="archive":
        text=source_text(app_dir,key)
        revision(conn,key,text)
        _campaign_references(conn,app_dir,key,None)
        path=setup.resume_variant_path(app_dir,key)
        target=Path(app_dir)/"cv_archive"/(uuid.uuid4().hex+".txt")
        target.parent.mkdir(parents=True,exist_ok=True)
        path.replace(target)
        conn.execute("UPDATE cc_cv_sources SET archived=1 WHERE source_key=?",(key,))
    elif operation=="restore_revision":
        revision_row=conn.execute("SELECT text FROM cc_cv_revisions WHERE id=? AND source_key=?",(data.get("revision_id"),key)).fetchone()
        if not revision_row:raise ValueError("Choose a revision belonging to this NZ source")
        setup.write_resume_variant(app_dir,key,revision_row[0])
        conn.execute("UPDATE cc_cv_sources SET archived=0 WHERE source_key=?",(key,))
    elif operation=="duplicate":
        return import_files(conn,app_dir,[{"name":key+".txt","key":data.get("new_key"),"b64":base64.b64encode(source_text(app_dir,key).encode()).decode(),"country_code":"NZ","category":row["category"],"role":row["role"]}])
    elif operation=="map":
        role=str(data.get("role") or "").strip()
        if not 1<=len(role)<=160:raise ValueError("Provide the intended NZ master headline/role")
        conn.execute("UPDATE cc_cv_sources SET role=? WHERE source_key=?",(role,key))
    else:raise ValueError("Unsupported NZ master action")
    conn.execute("UPDATE cc_cv_sources SET reconciliation_json=NULL WHERE source_key=?",(key,))
    conn.commit()
    from applypilot.role_routing import clear_resume_text_cache
    clear_resume_text_cache()
    return state(conn,app_dir)
