"""Job fit scoring: LLM-powered evaluation of candidate-job match quality.

Scores jobs on a 1-10 scale by comparing the user's resume against each
job description. All personal data is loaded at runtime from the user's
profile and resume file.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta

from applypilot.config import RESUME_PATH
from applypilot.role_routing import route_resume_for_job
from applypilot.database import get_connection
from applypilot.llm import chat_json
from applypilot.ai_routing import routed_task
from applypilot.country_context import preparation_task

log = logging.getLogger(__name__)


# ── Scoring Prompt ────────────────────────────────────────────────────────

SCORE_PROMPT = """You are a job fit evaluator. Given a candidate's resume and a job description, score how well the candidate fits the role.

SCORING CRITERIA:
- 9-10: Perfect match. Candidate has direct experience in nearly all required skills and qualifications.
- 7-8: Strong match. Candidate has most required skills, minor gaps easily bridged.
- 5-6: Moderate match. Candidate has some relevant skills but missing key requirements.
- 3-4: Weak match. Significant skill gaps, would need substantial ramp-up.
- 1-2: Poor match. Completely different field or experience level.

IMPORTANT FACTORS:
- Weight technical skills heavily (programming languages, frameworks, tools)
- Consider transferable experience (automation, scripting, API work)
- Factor in the candidate's project experience
- Be realistic about experience level vs. job requirements (years of experience, seniority)

RESPOND WITH JSON ONLY (no markdown):
{
  "score": 1-10,
  "keywords": ["keyword 1", "keyword 2"],
  "reasoning": "2-3 concise sentences",
  "confidence": 0.0-1.0
}"""


def _parse_score_response(response: str) -> dict:
    """Parse the LLM's score response into structured data.

    Args:
        response: Raw LLM response text.

    Returns:
        {"score": int, "keywords": str, "reasoning": str}
    """
    score = 0
    keywords = ""
    reasoning = response
    confidence = 0.0

    # First try strict JSON shape.
    try:
        txt = (response or "").strip()
        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", txt, flags=re.IGNORECASE | re.DOTALL)
        if fenced:
            txt = fenced.group(1).strip()
        if txt.startswith("```"):
            txt = re.sub(r"^```(?:json)?\s*", "", txt, flags=re.IGNORECASE)
            txt = re.sub(r"\n?```$", "", txt)
            txt = txt.strip()
        data = json.loads(txt)
        if isinstance(data, dict):
            raw_score = data.get("score", 0)
            try:
                score = int(raw_score)
            except Exception:
                score = 0
            score = max(1, min(10, score)) if score else 0

            kws = data.get("keywords")
            if isinstance(kws, list):
                keywords = ", ".join(str(x).strip() for x in kws if str(x).strip())
            elif isinstance(kws, str):
                keywords = kws.strip()

            reasoning = str(data.get("reasoning") or "").strip() or reasoning

            try:
                confidence = float(data.get("confidence", 0.0) or 0.0)
            except Exception:
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))

            if score > 0:
                return {"score": score, "keywords": keywords, "reasoning": reasoning, "confidence": confidence}
    except Exception:
        pass

    # Gemini can occasionally truncate JSON output after hidden reasoning,
    # leaving a partial object like: {"score": 7, "keywords": [
    # Recover the score from partial JSON so we don't mark valid responses as 0.
    try:
        m = re.search(r'"score"\s*:\s*(\d{1,2})', response or "")
        if m:
            score = max(1, min(10, int(m.group(1))))

            kw_match = re.search(r'"keywords"\s*:\s*\[(.*?)\]', response or "", flags=re.DOTALL)
            if kw_match:
                kws = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', kw_match.group(1))
                keywords = ", ".join(k.strip() for k in kws if k.strip())

            conf_match = re.search(r'"confidence"\s*:\s*([0-9]+(?:\.[0-9]+)?)', response or "")
            if conf_match:
                try:
                    confidence = max(0.0, min(1.0, float(conf_match.group(1))))
                except Exception:
                    confidence = 0.0
            if confidence <= 0.0:
                confidence = 0.6

            return {"score": score, "keywords": keywords, "reasoning": reasoning, "confidence": confidence}
    except Exception:
        pass

    for line in response.split("\n"):
        line = line.strip()
        if line.startswith("SCORE:"):
            try:
                nums = re.findall(r"\d+", line)
                score = int(nums[0]) if nums else 0
                score = max(1, min(10, score))
            except (AttributeError, ValueError):
                score = 0
        elif line.startswith("KEYWORDS:"):
            keywords = line.replace("KEYWORDS:", "").strip()
        elif line.startswith("REASONING:"):
            reasoning = line.replace("REASONING:", "").strip()
        elif line.startswith("CONFIDENCE:"):
            try:
                nums = re.findall(r"\d+(?:\.\d+)?", line)
                confidence = float(nums[0]) if nums else 0.0
            except Exception:
                confidence = 0.0

    if score > 0 and confidence <= 0.0:
        confidence = 0.6

    return {"score": score, "keywords": keywords, "reasoning": reasoning, "confidence": confidence}


@routed_task("score")
@preparation_task
def score_job(resume_text: str, job: dict) -> dict:
    """Score a single job against the resume.

    Args:
        resume_text: The candidate's full resume text.
        job: Job dict with keys: title, site, location, full_description.

    Returns:
        {"score": int, "keywords": str, "reasoning": str}
    """
    if job.get("country_code")=="NZ":
        from applypilot.preparation import technical_job
        if not job.get("_technical_resume_frozen"):
            raise ValueError("NZ scoring requires a verified, captured technical source")
        technical_key=job["_technical_resume_key"]
        job=technical_job(job)
    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job.get('company') or job.get('site') or 'N/A'}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
    )

    messages = [
        {"role": "system", "content": SCORE_PROMPT+("\nNZ technical-only assessment: use verified technical skills, genuine projects/qualifications and role seniority. Location, salary, employer accreditation, sponsorship/recruitment wording and candidate immigration rights must not change this technical score." if job.get("_technical_only") else "")},
        {"role": "user", "content": f"RESUME:\n{resume_text}\n\n---\n\nJOB POSTING:\n{job_text}"},
    ]
    from applypilot.ai_routing import route_info
    request_info=route_info("score")

    try:
        response = chat_json(messages, max_tokens=768, temperature=0.0)
        result = _parse_score_response(response)
        from applypilot.ai_routing import route_info
        info = route_info("score")
        try:
            from applypilot.llm import get_client
            info["model"] = get_client().last_model
        except (ValueError, RuntimeError, AttributeError):
            pass
        output=result | {"provider": info.get("provider"), "model": info.get("model")}
        from applypilot.operations import current as current_operation
        operation=current_operation()
        receipt=(operation or {}).get("receipts",{}).get("score")
        if receipt:output.update(provider=receipt["provider"],model=receipt["model"])
        if job.get("_technical_only"):
            from applypilot.preparation import technical_fingerprint
            output["input_fingerprint"]=technical_fingerprint(job,resume_text,technical_key,request_info)
        return output
    except Exception as e:
        log.error("LLM error scoring job '%s': %s", job.get("title", "?"), e)
        from applypilot.llm import ProviderAccessError
        return {"score": 0, "keywords": "", "reasoning": str(e) if isinstance(e, ProviderAccessError) else "Scoring request unavailable or returned invalid output",
                "confidence": 0.0, "error_category": getattr(e, "category", None), "provider_blocked": isinstance(e, ProviderAccessError)}


def _country_where(conn=None):
    from applypilot.country_context import current
    from applypilot.discovery_scope import processing_where
    base="1=1" if conn is not None and "country_code" not in {r[1] for r in conn.execute("PRAGMA table_info(jobs)")} else "country_code='NZ'" if current() and current().country_code=="NZ" else "(country_code IS NULL OR country_code='GB')"
    scope=processing_where()
    return base+(' AND '+scope if scope else '')


def _repair_zero_scores(conn) -> int:
    """Backfill fit_score for rows where LLM JSON was truncated but score is recoverable."""
    rows = conn.execute(
        "SELECT url, score_reasoning FROM jobs "
        "WHERE fit_score = 0 AND score_reasoning IS NOT NULL "
        "AND score_reasoning LIKE '%\"score\"%' AND "+_country_where(conn)
    ).fetchall()

    repaired = 0
    for row in rows:
        parsed = _parse_score_response(str(row[1] or ""))
        score = int(parsed.get("score") or 0)
        if score <= 0:
            continue
        conn.execute(
            "UPDATE jobs SET fit_score = ?, score_confidence = ? WHERE url = ?",
            (score, float(parsed.get("confidence") or 0.0), row[0]),
        )
        repaired += 1

    if repaired:
        conn.commit()
        log.info("Recovered %d previously-zero scores from stored LLM output.", repaired)

    return repaired


def run_score_repair() -> dict:
    """Repair previously stored zero scores when score is recoverable from score_reasoning."""
    conn = get_connection()

    candidates = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE fit_score = 0 AND score_reasoning IS NOT NULL "
        "AND score_reasoning LIKE '%\"score\"%' AND "+_country_where(conn)
    ).fetchone()[0]

    recovered = _repair_zero_scores(conn)
    # Legacy provider failures used score=0; recover them as explicit retries.
    conn.execute("UPDATE jobs SET fit_score=NULL,score_status='retryable',score_attempts=0,score_retry_at=NULL WHERE applied_at IS NULL AND fit_score=0 AND (score_reasoning LIKE '%LLM error:%' OR score_reasoning IS NULL) AND "+_country_where())
    conn.commit()
    remaining_zero = conn.execute("SELECT COUNT(*) FROM jobs WHERE fit_score = 0").fetchone()[0]

    return {
        "candidates": int(candidates or 0),
        "recovered": int(recovered or 0),
        "remaining_zero": int(remaining_zero or 0),
    }


def run_scoring(limit: int = 0, rescore: bool = False) -> dict:
    """Score unscored jobs that have full descriptions.

    Args:
        limit: Maximum number of jobs to score in this run.
        rescore: If True, re-score all jobs (not just unscored ones).

    Returns:
        {"scored": int, "errors": int, "recovered": int, "elapsed": float, "distribution": list}
    """
    from applypilot.country_context import current as current_country
    nz=bool(current_country() and current_country().country_code=="NZ")
    base_resume_text = "" if nz else RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()
    from applypilot.pipeline_workspace import invalidate_scores
    invalidate_scores(conn, RESUME_PATH.parent)
    recovered = _repair_zero_scores(conn)
    conn.execute("UPDATE jobs SET fit_score=NULL,score_status='retryable',score_attempts=0,score_retry_at=NULL WHERE applied_at IS NULL AND fit_score=0 AND (score_reasoning LIKE '%LLM error:%' OR score_reasoning IS NULL) AND "+_country_where())
    conn.commit()

    if rescore:
        query = "SELECT * FROM jobs WHERE full_description IS NOT NULL AND "+_country_where()
        if limit > 0:
            query += f" LIMIT {limit}"
        jobs = conn.execute(query).fetchall()
    else:
        from applypilot.eligibility import candidates
        jobs = candidates(conn, "score", limit=limit)

    if not jobs:
        log.info("No unscored jobs with descriptions found.")
        return {"scored": 0, "errors": 0, "recovered": recovered, "elapsed": 0.0, "distribution": []}

    # Convert sqlite3.Row to dicts if needed
    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    log.info("Scoring %d jobs sequentially...", len(jobs))
    t0 = time.time()
    completed = 0
    errors = 0
    deferred = 0
    results: list[dict] = []

    for job_index, job in enumerate(jobs):
        routed = None
        # Deterministic multi-role support: choose the best base resume variant.
        try:
            routed = route_resume_for_job(job)
            if routed.ambiguous:
                conn.execute("UPDATE jobs SET score_status='needs_route',score_error=?,resume_route=? WHERE url=?",
                             (routed.reason, json.dumps(routed.public()), job["url"]))
                conn.commit()
                errors += 1
                continue
            resume_text = routed.text.strip() or base_resume_text
        except Exception:
            conn.execute("UPDATE jobs SET score_status='needs_route',score_error='Resume route unavailable; review mapping' WHERE url=?", (job["url"],))
            conn.commit()
            errors += 1
            continue

        from applypilot.professional import enrich_evidence
        from applypilot.setup_workspace import read_profile

        profile_snapshot=read_profile(RESUME_PATH.parent)
        if not nz:resume_text, _ = enrich_evidence(resume_text, profile_snapshot, job)
        from applypilot.role_routing import score_fingerprint
        input_hash=score_fingerprint(job,RESUME_PATH.parent,profile_snapshot=profile_snapshot,route_snapshot=routed,fresh=True)
        result = score_job(resume_text, job)
        result["url"] = job["url"]
        completed += 1

        if result["score"] == 0:
            errors += 1

        from applypilot.role_routing import score_fingerprint
        now = datetime.now(timezone.utc)
        good = result["score"] > 0
        attempt = int(job.get("score_attempts") or 0) + 1
        retry = (now + timedelta(seconds=900 if result.get("provider_blocked") else min(3600, 30 * 2 ** min(attempt - 1, 7)))).isoformat()
        conn.execute("UPDATE jobs SET fit_score=?,score_confidence=?,score_reasoning=?,scored_at=?,score_status=?,score_error=?,score_retry_at=?,score_attempts=?,score_fingerprint=?,resume_route=? WHERE url=?",
                     (result["score"] if good else None, float(result.get("confidence") or 0),
                      f"{result['keywords']}\n{result['reasoning']}" if good else job.get("score_reasoning"),
                      now.isoformat() if good else job.get("scored_at"), "completed" if good else "provider_blocked" if result.get("provider_blocked") else "retryable" if attempt < 5 else "failed",
                      None if good else result["reasoning"], None if good else retry,
                       0 if good else attempt, result.get("input_fingerprint",input_hash) if good else job.get("score_fingerprint"),
                      json.dumps(routed.public()) if routed is not None else None, job["url"]))
        conn.commit()

        results.append(result)
        if good:
            conn.execute("UPDATE jobs SET score_provider=?,score_model=? WHERE url=?", (result.get("provider"), result.get("model"), job["url"]))
            conn.commit()

        log.info(
            "[%d/%d] fit=%s  %s",
            completed,
            len(jobs),
            result["score"] if result["score"] else "unavailable",
            job.get("title", "?")[:60],
        )
        if result.get("provider_blocked"):
            remaining = jobs[job_index+1:]
            for pending in remaining:
                conn.execute("UPDATE jobs SET score_status='provider_blocked',score_error=?,score_retry_at=? WHERE url=?",
                             (result["reasoning"], retry, pending["url"]))
            conn.commit()
            deferred = len(remaining)
            log.warning("Scoring paused: provider account/configuration failure; %d jobs deferred without another request. %s", deferred, result["reasoning"])
            break

    elapsed = time.time() - t0
    succeeded = sum(r["score"] > 0 for r in results)
    log.info("Done: %d successfully scored, %d errors, %d deferred in %.1fs", succeeded, errors, deferred, elapsed)

    # Score distribution
    dist = conn.execute("""
        SELECT fit_score, COUNT(*) FROM jobs
        WHERE fit_score IS NOT NULL
        GROUP BY fit_score ORDER BY fit_score DESC
    """).fetchall()
    distribution = [(row[0], row[1]) for row in dist]

    return {
        "scored": succeeded,
        "attempted": completed,
        "deferred": deferred,
        "errors": errors,
        "recovered": recovered,
        "elapsed": elapsed,
        "distribution": distribution,
    }
