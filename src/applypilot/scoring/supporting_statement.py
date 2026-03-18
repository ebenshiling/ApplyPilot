"""Supporting statement generation (NHS-style).

Deterministic, profile-driven generation from:
- the user's resume text (prefer tailored resume per job when available)
- the job description / person specification text (full_description)

This intentionally avoids hardcoded personal content.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from applypilot import naming
from applypilot.config import RESUME_PATH, STATEMENT_DIR, load_profile
from applypilot.database import get_connection
from applypilot.llm import chat_json
from applypilot.role_routing import route_resume_for_job
from applypilot.scoring.validator import BANNED_WORDS, sanitize_text

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


def _statement_cache_path(resume_text: str, job: dict) -> Path:
    # Stable cache key: resume + role + org + description.
    blob = "\n".join(
        [
            str(job.get("title") or ""),
            str(job.get("company") or job.get("site") or ""),
            str(job.get("full_description") or ""),
            str(resume_text or ""),
        ]
    )
    h = hashlib.sha1(blob.encode("utf-8", errors="ignore")).hexdigest()
    return STATEMENT_DIR / "cache" / f"{h}.txt"


_STRUCTURE_VARIANTS: tuple[dict[str, str], ...] = (
    {
        "name": "criteria-led",
        "guidance": "Use short sections mapped to the person specification criteria. Keep evidence concrete.",
    },
    {
        "name": "story-then-criteria",
        "guidance": "Open with a brief first-week impact plan, then map evidence to criteria.",
    },
    {
        "name": "values-first",
        "guidance": "Open with NHS values in action (brief), then criteria mapping with evidence.",
    },
)


def _pick_variant(job: dict) -> dict[str, str]:
    seed = f"{job.get('url', '')}|{job.get('title', '')}|{job.get('site', '')}|statement"
    idx = int(hashlib.sha1(seed.encode("utf-8")).hexdigest(), 16) % len(_STRUCTURE_VARIANTS)
    return dict(_STRUCTURE_VARIANTS[idx])


def _extract_person_spec_criteria(text: str) -> list[str]:
    """Deterministic heuristic extraction of criteria lines."""
    t = (text or "").replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in t.split("\n")]
    lines = [ln for ln in lines if ln]

    # Prefer sections that look like person spec.
    keys = (
        "essential",
        "desirable",
        "knowledge",
        "skills",
        "experience",
        "qualifications",
        "ability",
        "competenc",
        "person specification",
    )
    out: list[str] = []
    seen: set[str] = set()
    in_spec = False

    for ln in lines:
        low = ln.lower()
        if any(k in low for k in keys) and len(ln) <= 80:
            in_spec = True
            continue
        if in_spec:
            # Stop if we hit a new high-level heading.
            if (
                len(ln) <= 60
                and re.match(r"^[A-Z][A-Za-z /&-]{2,}$", ln)
                and any(k in low for k in ("job", "about", "benefit"))
            ):
                in_spec = False
                continue
            # Bullet-ish criteria lines.
            if ln.startswith(("-", "•", "*")):
                item = ln.lstrip("-*• ").strip()
            else:
                item = ln

            if 12 <= len(item) <= 140:
                key = item.lower()
                if key not in seen:
                    seen.add(key)
                    out.append(item)
        if len(out) >= 18:
            break

    # Fallback: pick requirement-looking lines anywhere.
    if not out:
        req_words = ("must", "able to", "experience", "knowledge", "understanding", "proven", "demonstrate")
        for ln in lines:
            low = ln.lower()
            if any(w in low for w in req_words) and 18 <= len(ln) <= 140:
                key = ln.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(ln)
            if len(out) >= 12:
                break

    return out[:18]


def _build_prompt(
    *, variant: dict[str, str], resume_text: str, job: dict, criteria: list[str], profile: dict
) -> list[dict]:
    personal = profile.get("personal", {}) if isinstance(profile, dict) else {}
    name = (personal.get("preferred_name") or personal.get("full_name") or "").strip()
    role = str(job.get("title") or "").strip()
    org = str(job.get("company") or job.get("site") or "").strip()
    criteria_txt = "\n".join(f"- {c}" for c in (criteria or []))

    banned = ", ".join(sorted({w.lower() for w in BANNED_WORDS}))

    system = (
        "You write UK supporting statements for competitive public-sector roles. "
        "Output must be human, specific, evidence-led, and truthful. "
        "Do not invent qualifications, registrations, employers, dates, metrics, or tools. "
        "Avoid generic filler and avoid banned phrases. "
        "Do not include personal contact details (address/phone/email) in the statement."
    )

    user = f"""
ROLE: {role}
ORGANISATION: {org}
CANDIDATE NAME (for reference only): {name}

PERSON SPEC CRITERIA (if present):
{criteria_txt or "- (none extracted)"}

JOB DESCRIPTION (verbatim):
{sanitize_text(job.get("full_description") or "")[:9000]}

CANDIDATE CV/RESUME (verbatim):
{sanitize_text(resume_text or "")[:9000]}

WRITE a supporting statement suitable for NHS / UK public sector application forms.

CONSTRAINTS:
- Length: 900 to 1400 words unless the job text clearly requests otherwise.
- Structure: {variant["name"]} ({variant["guidance"]}).
- Use plain English, UK spelling.
- Do not include personal details or duplicate contact information already in the application.
- Use concrete examples with scope/actions/outcomes.
- If criteria are available, explicitly cover them without listing them as a checklist.
- Do NOT use or echo these phrases (banned): {banned}

Return JSON only:
{{"statement":"..."}}
""".strip()

    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _validate_statement(text: str) -> list[str]:
    errors: list[str] = []
    t = (text or "").strip()
    if not t:
        return ["Empty statement"]
    wc = len(re.findall(r"[A-Za-z0-9%]+", t))
    if wc < 650:
        errors.append(f"Too short ({wc} words)")
    if wc > 1800:
        errors.append(f"Too long ({wc} words)")

    low = t.lower()
    for w in BANNED_WORDS:
        wl = str(w).lower().strip()
        if wl and re.search(r"\b" + re.escape(wl) + r"\b", low):
            errors.append(f"Contains banned phrase: '{wl}'")
            break

    # Avoid obvious LLM meta.
    if any(p in low for p in ("as an ai", "i cannot", "language model")):
        errors.append("Contains AI meta language")

    return errors


def generate_supporting_statement(resume_text: str, job: dict, profile: dict) -> str:
    # Deterministic cache: if inputs are identical, reuse the same statement.
    try:
        cp = _statement_cache_path(resume_text, job)
        if cp.exists():
            cached = cp.read_text(encoding="utf-8").strip()
            if cached:
                return cached
    except Exception:
        pass

    variant = _pick_variant(job)
    criteria = _extract_person_spec_criteria(str(job.get("full_description") or ""))

    last = ""
    for attempt in range(MAX_ATTEMPTS):
        msgs = _build_prompt(variant=variant, resume_text=resume_text, job=job, criteria=criteria, profile=profile)
        try:
            out = chat_json(msgs, max_tokens=1800, temperature=0.0)
            data = None
            try:
                data = __import__("json").loads((out or "").strip())
            except Exception:
                data = None
            statement = ""
            if isinstance(data, dict):
                statement = str(data.get("statement") or "").strip()
            if not statement:
                statement = (out or "").strip()
            statement = statement.strip()
            last = statement
        except Exception as e:
            last = last or ""
            log.debug("Statement LLM error: %s", e)
            continue

        errs = _validate_statement(last)
        if not errs:
            try:
                cp = _statement_cache_path(resume_text, job)
                cp.parent.mkdir(parents=True, exist_ok=True)
                cp.write_text(last + "\n", encoding="utf-8")
            except Exception:
                pass
            return last

        # Simple deterministic refinement: append errors to job dict for next retry.
        job = dict(job)
        job["_statement_avoid"] = "; ".join(errs[:4])

    return last


def run_supporting_statements(min_score: int = 7, limit: int = 0) -> dict:
    """Generate supporting statements for high-fit jobs (best-effort)."""
    profile = load_profile()
    default_resume = RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()

    where = (
        "fit_score >= ? AND tailored_resume_path IS NOT NULL "
        "AND full_description IS NOT NULL "
        "AND (supporting_statement_path IS NULL OR supporting_statement_path = '') "
        "AND COALESCE(statement_attempts, 0) < ?"
    )
    params: list[object] = [min_score, MAX_ATTEMPTS]
    query = "SELECT rowid AS job_id, * FROM jobs WHERE " + where + " ORDER BY fit_score DESC"
    if limit > 0:
        query += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(query, tuple(params)).fetchall()
    if not rows:
        log.info("No jobs needing supporting statements (score >= %d).", min_score)
        return {"generated": 0, "errors": 0, "elapsed": 0.0}

    cols = rows[0].keys()
    jobs = [dict(zip(cols, r)) for r in rows]

    STATEMENT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    completed = 0
    results: list[dict] = []
    errors = 0

    for job in jobs:
        completed += 1
        try:
            # Prefer tailored resume.
            resume_text = default_resume
            trp = str(job.get("tailored_resume_path") or "").strip()
            if trp:
                try:
                    resume_text = Path(trp).read_text(encoding="utf-8")
                except Exception:
                    resume_text = default_resume
            else:
                # If there is no tailored resume (unexpected), choose best base resume variant.
                try:
                    resume_text = route_resume_for_job(job).text.strip() or default_resume
                except Exception:
                    resume_text = default_resume

            statement = generate_supporting_statement(resume_text, job, profile)
            v_errs = _validate_statement(statement)
            if v_errs:
                raise RuntimeError("Statement failed validation: " + "; ".join(v_errs[:3]))

            username = str(os.environ.get("APPLYPILOT_USER", "") or "").strip()
            stem = naming.supporting_statement_filename(
                profile.get("personal", {}), ext="txt", username=username, job=job
            )
            prefix = Path(stem).stem
            out_path = STATEMENT_DIR / f"{prefix}_SS.txt"
            out_path.write_text(statement, encoding="utf-8")

            results.append(
                {"url": job["url"], "path": str(out_path), "title": job.get("title"), "site": job.get("site")}
            )
            elapsed = time.time() - t0
            rate = completed / elapsed if elapsed > 0 else 0
            log.info(
                "%d/%d [OK] | %.1f jobs/min | %s", completed, len(jobs), rate * 60, str(job.get("title") or "")[:40]
            )
        except Exception as e:
            errors += 1
            results.append(
                {
                    "url": job.get("url"),
                    "path": None,
                    "title": job.get("title"),
                    "site": job.get("site"),
                    "error": str(e),
                }
            )
            log.error("%d/%d [ERROR] %s -- %s", completed, len(jobs), str(job.get("title") or "")[:40], e)

    now = datetime.now(timezone.utc).isoformat()
    saved = 0
    for r in results:
        if r.get("path"):
            conn.execute(
                "UPDATE jobs SET supporting_statement_path=?, supporting_statement_at=?, "
                "statement_attempts=COALESCE(statement_attempts,0)+1 WHERE url=?",
                (r["path"], now, r["url"]),
            )
            saved += 1
        else:
            conn.execute(
                "UPDATE jobs SET statement_attempts=COALESCE(statement_attempts,0)+1, supporting_statement_path=NULL WHERE url=?",
                (r.get("url"),),
            )
    conn.commit()

    elapsed = time.time() - t0
    log.info("Supporting statements done in %.1fs: %d generated, %d errors", elapsed, saved, errors)
    return {"generated": saved, "errors": errors, "elapsed": elapsed}
