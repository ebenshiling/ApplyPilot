"""Manual NZ legal-employer evidence and conservative recruitment observations.

No official-source network client, accreditation API, model call or visa calculator.
Technical fit, candidate rights and employer/vacancy assertions remain separate.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
import uuid

from applypilot import country_context as country, source_access as access

OFFICIAL_URL = "https://www.immigration.govt.nz/work/requirements-for-work-visas/approved-employers/accredited-employer-list/"
LABELS = {"explicit_support": "Overseas recruitment explicitly supported",
          "potential_review": "Potentially suitable; additional verification required",
          "explicit_restriction": "Overseas recruitment explicitly restricted",
          "unavailable_conflicting": "Evidence unavailable or conflicting"}
RESULTS = {"accredited", "not_accredited", "expired", "suspended", "revoked", "not_listed", "unknown"}
NEGATIVE = {"not_accredited", "expired", "suspended", "revoked"}
METHODS = {"manual_official_check", "employer_supplied", "user_note"}
STATES = {"success", "unavailable", "access_not_authorised", "identity_ambiguous", "conflicting"}


def init_schema(conn):
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS cc_employers(
        id TEXT PRIMARY KEY,country_code TEXT NOT NULL CHECK(country_code='NZ'),
        legal_name TEXT NOT NULL,trading_names TEXT NOT NULL DEFAULT '[]',nzbn TEXT,
        website_url TEXT,identity_state TEXT NOT NULL,identity_evidence_json TEXT NOT NULL,
        revision INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
      CREATE UNIQUE INDEX IF NOT EXISTS cc_employers_nzbn ON cc_employers(country_code,nzbn) WHERE nzbn IS NOT NULL;
      CREATE TABLE IF NOT EXISTS cc_employer_evidence(
        id TEXT PRIMARY KEY,employer_id TEXT NOT NULL REFERENCES cc_employers(id),
        kind TEXT NOT NULL,observation_json TEXT NOT NULL,observed_at TEXT,recorded_at TEXT NOT NULL,
        refresh_due_at TEXT,source_expires_at TEXT,result TEXT NOT NULL,supersedes_id TEXT);
      CREATE INDEX IF NOT EXISTS cc_employer_evidence_lookup ON cc_employer_evidence(employer_id,kind,observed_at);
      CREATE TRIGGER IF NOT EXISTS cc_employer_evidence_immutable_update BEFORE UPDATE ON cc_employer_evidence
        BEGIN SELECT RAISE(ABORT,'Employer evidence is immutable; append a correction'); END;
      CREATE TRIGGER IF NOT EXISTS cc_employer_evidence_immutable_delete BEFORE DELETE ON cc_employer_evidence
        BEGIN SELECT RAISE(ABORT,'Employer evidence history must be retained'); END;
    """)


def _require(app_dir):
    from applypilot.setup_workspace import read_searches_dict
    country.require_enabled("NZ", read_searches_dict(app_dir))
    active = country.current()
    if active and (active.country_code != "NZ" or Path(active.workspace) != Path(app_dir).resolve()):
        raise ValueError("NZ evidence is outside the captured country/workspace")


def _text(value, name, limit=2000, *, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f"Provide valid {name} (up to {limit} characters)")
    return value.strip()


def _instant(value, name, *, optional=False):
    if optional and value in {None, ""}:
        return None
    parsed = access.moment(value)
    if not parsed or parsed > datetime.now(timezone.utc) + timedelta(seconds=30):
        raise ValueError(f"{name} must be an actual zoned timestamp, not a future date")
    return parsed.isoformat()


def _source_date(value, name):
    if value in {None, ""}:
        return None
    from applypilot.discovery.observations import timestamp
    parsed, precision = timestamp(value)
    if parsed is None:
        raise ValueError(f"Provide a source-supplied date or zoned timestamp for {name}")
    return parsed


def _expired(value, now):
    instant = access.moment(value)
    return bool(value and (instant <= now if instant else value < now.date().isoformat()))


def _name(value):
    return re.sub(r"[^\w]+", " ", str(value).casefold()).strip()


def _employer(conn, ident):
    row = conn.execute("SELECT * FROM cc_employers WHERE id=? AND country_code='NZ'", (str(ident),)).fetchone()
    if not row:
        raise LookupError("NZ employer not found")
    return dict(row) | {"trading_names": json.loads(row["trading_names"]), "identity_evidence": json.loads(row["identity_evidence_json"])}


def _job(conn, ident):
    row = conn.execute("SELECT rowid AS id,* FROM jobs WHERE rowid=? AND country_code='NZ'", (int(ident),)).fetchone()
    if not row:
        raise ValueError("Choose an NZ vacancy; UK/legacy records cannot be linked here")
    return dict(row)


def save_employer(conn, app_dir, data, *, actor):
    _require(app_dir)
    from applypilot.source_registry import validate_url
    from applypilot.command_center import event
    legal_name = _text(data.get("legal_name"), "legal name", 300, required=True)
    trading = data.get("trading_names", [])
    if not isinstance(trading, list) or len(trading) > 20:
        raise ValueError("Use up to twenty trading names")
    trading = [_text(v, "trading name", 300, required=True) for v in trading]
    nzbn = data.get("nzbn") or None
    if nzbn is not None and (not isinstance(nzbn, str) or not re.fullmatch(r"\d{13}", nzbn)):
        raise ValueError("NZBN must be a 13-digit string; format does not prove ownership")
    website = _text(data.get("website_url", ""), "website URL") or None
    if website:
        validate_url(website)
    state = data.get("identity_state", "ambiguous")
    if state not in {"confirmed", "ambiguous"}:
        raise ValueError("Identity must be confirmed or ambiguous")
    evidence = data.get("identity_evidence") or {}
    if not isinstance(evidence, dict):
        raise ValueError("Identity evidence must be an object")
    clean = {"source_url": _text(evidence.get("source_url", ""), "identity source URL"),
             "quote": _text(evidence.get("quote", ""), "identity quote", 4000),
             "observed_at": _instant(evidence.get("observed_at"), "Identity observation", optional=state != "confirmed"),
             "actor": _text(actor, "server actor", 200, required=True), "recorded_at": access.now()}
    if clean["source_url"]:
        validate_url(clean["source_url"])
    if state == "confirmed" and (data.get("identity_confirmed") is not True or not clean["source_url"] or not clean["quote"]):
        raise ValueError("Confirm the legal identity against a quoted source; NZBN format/name similarity is insufficient")
    expected = data.get("expected_revision", 0)
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        raise ValueError("Provide the expected employer revision")
    ident = data.get("id") or uuid.uuid4().hex
    previous = _employer(conn, ident) if data.get("id") else None
    if (previous["revision"] if previous else 0) != expected:
        raise ValueError("Employer identity changed; reload before saving")
    now = access.now()
    try:
        if previous:
            changed = conn.execute("UPDATE cc_employers SET legal_name=?,trading_names=?,nzbn=?,website_url=?,identity_state=?,identity_evidence_json=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?",
                (legal_name, json.dumps(trading), nzbn, website, state, json.dumps(clean), now, ident, expected)).rowcount
            if changed != 1:
                raise ValueError("Employer identity changed during editing")
        else:
            conn.execute("INSERT INTO cc_employers(id,country_code,legal_name,trading_names,nzbn,website_url,identity_state,identity_evidence_json,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (ident, "NZ", legal_name, json.dumps(trading), nzbn, website, state, json.dumps(clean), 1, now, now))
        event(conn, None, "nz_employer_identity", "NZ legal-employer identity recorded; linked vacancies need current corroboration", source="manual", payload={"employer_id":ident,"actor":actor,"revision":expected+1,"identity_snapshot":{"legal_name":legal_name,"nzbn":nzbn,"identity_state":state,"evidence":clean}})
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ValueError("This NZBN already belongs to another recorded identity; review it instead of merging by name") from exc
    return _employer(conn, ident)


def link_job(conn, app_dir, data, *, actor):
    _require(app_dir)
    from applypilot.discovery.observations import metadata
    from applypilot.source_registry import validate_url
    job, employer = _job(conn, data.get("id")), _employer(conn, data.get("employer_id"))
    relationship = data.get("relationship", "ambiguous")
    if relationship not in {"hiring_employer", "recruiter", "brand", "parent", "subsidiary", "ambiguous"}:
        raise ValueError("Choose the observed relationship to the vacancy")
    source_url = _text(data.get("source_url", ""), "hiring relationship source")
    quote = _text(data.get("quote", ""), "hiring relationship quote", 4000)
    if source_url: validate_url(source_url)
    confirmed = data.get("confirmed") is True
    if confirmed and (relationship != "hiring_employer" or employer["identity_state"] != "confirmed" or not source_url or not quote):
        raise ValueError("Only a corroborated hiring legal employer can be confirmed; agencies/groups do not transfer accreditation")
    meta = metadata(job)
    meta["employer_link"] = {"employer_id":employer["id"],"identity_revision":employer["revision"], "legal_name":employer["legal_name"],"nzbn":employer["nzbn"],
        "relationship":relationship,"confirmed":confirmed,"source_url":source_url,"quote":quote,"actor":actor,"recorded_at":access.now()}
    conn.execute("UPDATE jobs SET employer_id=?,vacancy_metadata=? WHERE url=?", (employer["id"],json.dumps(meta),job["url"]))
    from applypilot.command_center import event
    event(conn,job["url"],"nz_employer_link","Hiring identity relationship recorded",source="manual",payload=meta["employer_link"])
    conn.commit()
    return assess_job(conn, app_dir, _job(conn, job["id"]), persist=True)


def _artifact(app_dir, raw):
    if raw is None: return None
    if not isinstance(raw, dict) or raw.get("storage_allowed") is not True:
        raise ValueError("Evidence attachment needs explicit storage permission")
    name = _text(raw.get("name"), "artifact filename", 200, required=True)
    extension = Path(name).suffix.lower()
    blob = base64.b64decode(raw.get("b64") or "", validate=True)
    if extension not in {".png", ".jpg", ".jpeg", ".pdf", ".txt"} or not 0 < len(blob) <= 2_000_000:
        raise ValueError("Use a permitted PNG/JPEG/PDF/TXT evidence excerpt up to 2 MB")
    signatures = {".png":b"\x89PNG\r\n\x1a\n", ".jpg":b"\xff\xd8", ".jpeg":b"\xff\xd8", ".pdf":b"%PDF-"}
    if extension in signatures and not blob.startswith(signatures[extension]):
        raise ValueError("Artifact bytes do not match their declared type")
    if extension == ".txt": blob.decode("utf-8")
    path = Path("evidence") / "nz" / (uuid.uuid4().hex + extension)
    from applypilot.setup_workspace import _atomic_write_bytes
    _atomic_write_bytes(Path(app_dir) / path, blob)
    return {"path":path.as_posix(),"name":name,"sha256":hashlib.sha256(blob).hexdigest(),"size":len(blob)}


def _artifact_valid(app_dir, item):
    if not item: return True
    try:
        root = Path(app_dir).resolve()
        path = (root / item["path"]).resolve()
        return path.is_relative_to(root / "evidence" / "nz") and path.is_file() and path.stat().st_size <= 2_000_000 and hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    except (OSError, KeyError, ValueError):
        return False


def history(conn, app_dir, employer_id):
    _require(app_dir)
    _employer(conn, employer_id)
    output = []
    for row in conn.execute("SELECT * FROM cc_employer_evidence WHERE employer_id=? ORDER BY recorded_at,id", (employer_id,)):
        observation = json.loads(row["observation_json"])
        output.append(dict(row) | {"observation":observation,"artifact_integrity":_artifact_valid(app_dir, observation.get("artifact"))})
    return output


def record(conn, app_dir, data, *, actor):
    _require(app_dir)
    from applypilot.source_registry import validate_url
    employer = _employer(conn, data.get("employer_id"))
    kind = data.get("kind", "accreditation")
    method, state, result = data.get("method"), data.get("verification_state", "success"), data.get("result", "unknown")
    if kind not in {"accreditation", "employer_identity", "adverse_status"} or method not in METHODS or state not in STATES or result not in RESULTS:
        raise ValueError("Unsupported evidence kind, provenance, result or verification state; authorised feeds are unavailable")
    if state != "success" and result != "unknown":
        raise ValueError("Failed/ambiguous checks must retain an unknown result")
    source_url = _text(data.get("source_url", ""), "evidence source URL")
    if source_url: validate_url(source_url)
    quote = _text(data.get("quote", ""), "minimal quoted result", 4000)
    observed = _instant(data.get("observed_at"), "Actual check time")
    query = _text(data.get("query", ""), "actual official query", 300)
    query_type = data.get("query_type")
    snapshot = {"legal_name":_text(data.get("observed_legal_name", employer["legal_name"]),"observed legal name",300),
                "nzbn":data.get("observed_nzbn", employer["nzbn"]), "employer_revision":employer["revision"]}
    if method == "manual_official_check":
        if not access.domain_is((urlsplit(source_url).hostname or "").lower(), "immigration.govt.nz"):
            raise ValueError("Manual official accreditation checks require an INZ source reference; no official fetch is performed")
        if query_type not in {"nzbn", "legal_name", "trading_name"}:
            raise ValueError("Record whether the actual query was NZBN, legal name or trading name")
        if query_type == "nzbn" and (not employer["nzbn"] or query != employer["nzbn"]):
            raise ValueError("The checked NZBN must match the recorded hiring identity")
        if query_type != "nzbn" and (len(query) < 3 or _name(query) not in {_name(employer["legal_name"]), *(_name(n) for n in employer["trading_names"])}):
            raise ValueError("The actual name query must match a recorded legal/trading name")
        if state == "success" and result != "unknown":
            if not quote or _name(snapshot["legal_name"]) != _name(employer["legal_name"]) or snapshot["nzbn"] != employer["nzbn"]:
                raise ValueError("Successful official evidence needs a matching identity snapshot and exact result quote")
    if state == "success" and result != "unknown" and (not source_url or not quote):
        raise ValueError("A result needs a source reference and exact quoted evidence, not an inferred status")
    expires = _source_date(data.get("source_expires_at"), "source expiry")
    effective = _source_date(data.get("source_effective_at"), "source effective date")
    if effective and effective[:10] > access.now()[:10]:
        raise ValueError("Future-effective evidence cannot be recorded as current proof")
    supersedes = data.get("supersedes_ids", [])
    note = _text(data.get("resolution_note", ""), "correction/resolution note", 2000)
    if not isinstance(supersedes, list) or len(supersedes) > 30 or any(not isinstance(v,str) for v in supersedes):
        raise ValueError("Choose up to thirty same-employer evidence IDs to explicitly supersede")
    if supersedes and not note:
        raise ValueError("An explicit correction/resolution note is required")
    if supersedes and state != "success":
        raise ValueError("An unavailable/failed check cannot supersede successful evidence")
    for ident in supersedes:
        old = conn.execute("SELECT * FROM cc_employer_evidence WHERE id=? AND employer_id=? AND kind=?", (ident,employer["id"],kind)).fetchone()
        if not old or (old["observed_at"] and old["observed_at"] > observed):
            raise ValueError("Only earlier observations of this employer and kind can be superseded")
        if json.loads(old["observation_json"])["method"] == "manual_official_check" and method != "manual_official_check":
            raise ValueError("A note/employer assertion cannot override official evidence")
    pref = country.read_preferences(conn,"NZ")["config"].get("search_preferences", {})
    hours = pref.get("evidence_freshness_hours",24)
    if isinstance(hours,bool) or not isinstance(hours,(int,float)) or not 1 <= hours <= 168:
        raise ValueError("Evidence freshness must be between one and 168 hours")
    recorded, ident = access.now(), uuid.uuid4().hex
    observation = {"version":1,"method":method,"verification_state":state,"source_url":source_url,"issuing_authority":"Immigration New Zealand" if method=="manual_official_check" else "Employer assertion" if method=="employer_supplied" else "User note",
        "quote":quote,"quote_hash":hashlib.sha256(quote.encode()).hexdigest(),"query":query,"query_type":query_type,"identity_snapshot":snapshot,
        "actor":_text(actor,"server actor",200,required=True),"observed_at":observed,"recorded_at":recorded,"source_effective_at":effective,
        "supersedes_ids":supersedes,"resolution_note":note,"artifact":_artifact(app_dir,data.get("artifact"))}
    from applypilot.command_center import event
    event(conn,None,"nz_employer_evidence","Manual NZ employer evidence appended",source="manual",payload={"evidence_id":ident,"employer_id":employer["id"],"actor":actor,"method":method,"result":result})
    observation["audit_event_id"] = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO cc_employer_evidence(id,employer_id,kind,observation_json,observed_at,recorded_at,refresh_due_at,source_expires_at,result,supersedes_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (ident,employer["id"],kind,json.dumps(observation,allow_nan=False),observed,recorded,(access.moment(observed)+timedelta(hours=hours)).isoformat(),expires,result,supersedes[0] if supersedes else None))
    conn.commit()
    for row in conn.execute("SELECT rowid AS id,* FROM jobs WHERE country_code='NZ' AND employer_id=?", (employer["id"],)).fetchall():
        assess_job(conn,app_dir,dict(row),persist=True)
    return {"evidence_id":ident,"employer":reduce(conn,app_dir,employer["id"])}


def reduce(conn, app_dir, employer_id, *, at=None):
    _require(app_dir)
    employer, rows = _employer(conn,employer_id), history(conn,app_dir,employer_id)
    now = at or datetime.now(timezone.utc)
    superseded = {ident for r in rows for ident in r["observation"].get("supersedes_ids",[]) if r["artifact_integrity"]}
    successful = [r for r in rows if r["observation"]["method"]=="manual_official_check" and r["observation"]["verification_state"]=="success" and r["kind"] in {"accreditation","adverse_status"}]
    last_success = max((r["observed_at"] for r in successful),default=None)
    official=[r for r in successful if r["artifact_integrity"]]
    active = [r for r in official if r["id"] not in superseded and r["observation"]["identity_snapshot"]["legal_name"]==employer["legal_name"] and r["observation"]["identity_snapshot"]["nzbn"]==employer["nzbn"]]
    current = [r for r in active if access.moment(r["refresh_due_at"])>now and not _expired(r["source_expires_at"],now)]
    results = {r["result"] for r in current if r["result"] not in {"not_listed","unknown"}}
    conflict = len(results)>1 or any(r["id"] not in superseded and r["observation"]["verification_state"]=="conflicting" for r in rows)
    valid_identity = employer["identity_state"]=="confirmed"
    state = "conflicting" if conflict else "fresh" if current else "source_expired" if any(_expired(r["source_expires_at"],now) for r in active) else "stale" if active else "unavailable"
    result = next(iter(results)) if len(results)==1 and not conflict and valid_identity else "unknown"
    if not valid_identity: state = "identity_ambiguous"
    failures = [r for r in rows if r["observation"]["verification_state"]!="success"]
    return {"employer":employer,"accreditation_result":result,"evidence_state":state,"last_successful_check":last_success,
        "latest_attempt":rows[-1]["observed_at"] if rows else None,"latest_failure":failures[-1]["observation"]["verification_state"] if failures else None,
        "evidence_ids":[r["id"] for r in current],"source_expired":any(_expired(r["source_expires_at"],now) for r in active),
        "not_listed_limitation":"Some accredited employers choose not to appear publicly; no result does not establish non-accreditation.",
        "employer_assertions":[{"id":r["id"],"result":r["result"],"observed_at":r["observed_at"]} for r in rows if r["observation"]["method"]=="employer_supplied" and r["observation"]["verification_state"]=="success" and r["id"] not in superseded and r["artifact_integrity"] and access.moment(r["refresh_due_at"])>now and not _expired(r["source_expires_at"],now)],
        "artifact_integrity_errors":[r["id"] for r in rows if not r["artifact_integrity"]],"official_source_url":OFFICIAL_URL}


def extract(text, metadata):
    """Literal candidates only; conditions, negation, jurisdiction and scope retained."""
    digest = hashlib.sha256(text.encode()).hexdigest()
    result = []
    for match in re.finditer(r"[^.!?\n]+(?:[.!?](?=\s|$)|$)", text):
        quote = match.group(0).strip()
        lower = quote.casefold()
        if not re.search(r"overseas|visa|sponsor|work rights|right to work|citizen|residen|aewv",lower): continue
        if len(quote)>1500: continue  # no truncated quote can establish a conclusion
        foreign = bool(re.search(r"\b(?:australia|australian|uk|united kingdom|united states|canada|canadian)\b",lower))
        nz = bool(re.search(r"\b(?:nz|new zealand|aewv)\b",lower))
        jurisdiction = "NZ" if nz or (not foreign and metadata.get("advertised_country")=="NZ") else "unknown"
        conditional = bool(re.search(r"\b(?:may|might|subject to|depending|if eligible|case.by.case|some roles|some positions)\b",lower))
        generic = bool(re.search(r"other roles|some positions|as a (?:group|company)|globally|global workforce|equal opportunit",lower))
        restriction = bool(re.search(r"(?:no|not (?:offer|provide)|cannot (?:offer|provide)|unable to (?:offer|provide)).{0,35}(?:visa sponsorship|sponsor visas|visa support)|(?:overseas|international) (?:applicants|candidates).{0,30}(?:not (?:accepted|considered|eligible)|cannot)|(?:must|require|only|need).{0,70}(?:existing|current|unrestricted|full).{0,35}(?:nz|new zealand).{0,20}(?:work rights|right to work|residen|citizen)|(?:must|require|only).{0,30}(?:nz|new zealand) (?:citizen|residen)",lower))
        restriction = restriction or bool(re.search(r"(?:must|require|only|need).{0,50}(?:existing|current|unrestricted|full|already).{0,30}(?:work rights|right to work).{0,20}(?:nz|new zealand)",lower))
        if re.search(r"(?:do not|don't|not) (?:need|require)|\bno (?:need|requirement)\b|(?:work rights|right to work).{0,30}(?:not required|not necessary)",lower):
            restriction=False
        support = bool(re.search(r"(?:welcome|encourage|accept|consider|open to).{0,40}(?:overseas|international) (?:applicants|candidates)|(?:overseas|international) (?:applicants|candidates).{0,30}(?:welcome|encouraged)|visa sponsorship (?:(?:is|may be|might be|can be) )?(?:available|provided|offered)|we (?:offer|provide|support).{0,30}(?:visa sponsorship|aewv|work visa)",lower))
        negated = bool(re.search(r"\b(?:not|no|never|cannot|unable|don't|do not|does not)\b",lower))
        if support and negated: support=False
        ambiguous = negated and not restriction or (not support and not restriction)
        result.append({"quote":quote,"start":match.start()+len(match.group(0))-len(match.group(0).lstrip()),"source_hash":digest,
            "source_url":metadata.get("original_listing_url"),"observed_at":metadata.get("last_description_fetch_at") or metadata.get("description_recorded_at"),
            "jurisdiction":jurisdiction,"scope":"employer_boilerplate" if generic else "vacancy","conditional":conditional,"negated":negated,
            "claim":"restriction" if restriction else "support" if support else "ambiguous", "ambiguous":ambiguous,"rule_version":"nz-recruitment-v1"})
        if len(result)>=40: break
    return result


def assess_job(conn, app_dir, job, *, persist=False, at=None):
    from applypilot.discovery.observations import metadata
    if job.get("country_code")!="NZ": raise ValueError("NZ recruitment evidence cannot annotate UK/legacy jobs")
    meta = metadata(job)
    now = at or datetime.now(timezone.utc)
    claims = meta.get("recruitment_evidence")
    if claims is None:
        claims = extract(job.get("full_description") or job.get("description") or "",meta)
    fresh = [c for c in claims if c.get("jurisdiction")=="NZ" and access.moment(c.get("observed_at")) and timedelta(0)<=now-access.moment(c["observed_at"])<=timedelta(days=7)]
    if _expired(meta.get("source_valid_through"),now): fresh=[]
    supports = [c for c in fresh if c["claim"]=="support" and c["scope"]=="vacancy" and not c["conditional"]]
    restrictions = [c for c in fresh if c["claim"]=="restriction" and c["scope"]=="vacancy" and not c["conditional"]]
    indirect = [c for c in fresh if c["claim"]=="support" and (c["conditional"] or c["scope"]!="vacancy")]
    employer = reduce(conn,app_dir,job["employer_id"],at=now) if job.get("employer_id") else None
    link = meta.get("employer_link") or {}
    identity_confirmed = bool(employer and employer["employer"]["identity_state"]=="confirmed" and link.get("confirmed") and link.get("relationship")=="hiring_employer" and link.get("identity_revision")==employer["employer"]["revision"])
    code, reason = "unavailable_conflicting", "No current unambiguous NZ vacancy evidence; identity/accreditation may need review"
    if supports and restrictions:
        reason="Current vacancy support and restriction quotes conflict; review both"
    elif employer and employer["evidence_state"]=="conflicting":
        reason="Current employer evidence conflicts; no current claim is selected silently"
    elif employer and not identity_confirmed:
        reason="Hiring legal-employer identity is ambiguous or changed; corroborate the vacancy relationship"
    elif employer and employer["accreditation_result"] in NEGATIVE and (supports or indirect):
        reason="Vacancy support assertions conflict with current official adverse employer evidence; clarification is required"
    elif restrictions:
        code,reason="explicit_restriction","Current NZ role text explicitly restricts recruitment; candidate rights require a separate review"
    elif supports and (not employer or identity_confirmed):
        code,reason="explicit_support","Current vacancy text explicitly supports overseas recruitment; this is an advert assertion, not visa approval"
    elif indirect or (identity_confirmed and ((employer["accreditation_result"]=="accredited" and employer["evidence_state"]=="fresh") or any(r["result"]=="accredited" for r in employer["employer_assertions"]))):
        code,reason="potential_review","Conditional/indirect evidence only; role-specific overseas recruitment support still needs verification"
    assessment={"version":1,"code":code,"label":LABELS[code],"reason":reason,"claims":claims,"identity_confirmed":identity_confirmed,"employer_evidence":employer,
        "evaluated_at":now.isoformat(),"technical_fit_affected":False,"candidate_work_rights":"not_assessed", "visa_or_job_check_approval":"not_established"}
    if persist:
        conn.execute("UPDATE jobs SET immigration_assessment=? WHERE url=? AND country_code='NZ'",(json.dumps(assessment,allow_nan=False),job["url"]))
        conn.commit()
    return assessment


def state(conn, app_dir):
    _require(app_dir)
    employers=[reduce(conn,app_dir,r[0]) for r in conn.execute("SELECT id FROM cc_employers WHERE country_code='NZ' ORDER BY legal_name,id")]
    jobs=[dict(r) for r in conn.execute("SELECT rowid AS id,* FROM jobs WHERE country_code='NZ' ORDER BY discovered_at DESC")]
    return {"employers":employers,"jobs":[{"id":j["id"],"title":j["title"],"company":j["company"],"employer_id":j.get("employer_id"),"assessment":assess_job(conn,app_dir,j)} for j in jobs],
            "labels":LABELS,"candidate_disclosures":country.read_preferences(conn,"NZ")["config"].get("work_authorization",{}),"official_source_url":OFFICIAL_URL,"official_access":"manual_only","stale_reminders":[{"employer_id":e["employer"]["id"],"legal_name":e["employer"]["legal_name"],"last_successful_check":e["last_successful_check"]} for e in employers if e["evidence_state"] in {"stale","source_expired"}]}


def suggestions(conn, app_dir, query="", nzbn=None):
    _require(app_dir)
    from difflib import SequenceMatcher
    output=[]
    for row in conn.execute("SELECT id FROM cc_employers WHERE country_code='NZ'"):
        item=_employer(conn,row[0])
        exact_nzbn=bool(nzbn and nzbn==item["nzbn"])
        exact_name=bool(query and _name(query) in {_name(item["legal_name"]),*(_name(n) for n in item["trading_names"])})
        similarity=SequenceMatcher(None,_name(query),_name(item["legal_name"])).ratio() if query else 0
        if exact_nzbn or exact_name or similarity>=.6:
            output.append({"employer":item,"match":"exact_nzbn" if exact_nzbn else "exact_name" if exact_name else "fuzzy_suggestion_only","requires_hiring_identity_confirmation":True})
    return sorted(output,key=lambda v:({"exact_nzbn":0,"exact_name":1,"fuzzy_suggestion_only":2}[v["match"]],v["employer"]["legal_name"]))[:30]


def artifact(conn, app_dir, ident):
    _require(app_dir)
    row=conn.execute("SELECT observation_json FROM cc_employer_evidence WHERE id=?",(str(ident),)).fetchone()
    item=json.loads(row[0]).get("artifact") if row else None
    if not item or not _artifact_valid(app_dir,item):
        raise ValueError("Evidence artifact is missing, changed or outside this workspace")
    body=(Path(app_dir)/item["path"]).read_bytes()
    suffix=Path(item["path"]).suffix
    return body,{".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".pdf":"application/pdf",".txt":"text/plain; charset=utf-8"}[suffix]


def review(conn, app_dir, data, *, actor):
    _require(app_dir)
    from applypilot.discovery.observations import metadata
    job=_job(conn,data.get("id"))
    decision=data.get("decision")
    if decision not in {"needs_clarification","reviewed_continue","declined"}:
        raise ValueError("Choose a separate manual review decision")
    note=_text(data.get("note"),"review note",2000,required=True)
    assessment=assess_job(conn,app_dir,job)
    value={"decision":decision,"note":note,"actor":actor,"recorded_at":access.now(),"evidence_class_at_review":assessment["code"]}
    meta=metadata(job)
    meta["recruitment_review"]=value
    conn.execute("UPDATE jobs SET vacancy_metadata=? WHERE url=?",(json.dumps(meta),job["url"]))
    from applypilot.command_center import event
    event(conn,job["url"],"nz_recruitment_review","Candidate review recorded separately from evidence classification",source="manual",payload=value)
    conn.commit()
    return {"review":value,"assessment":assessment}
