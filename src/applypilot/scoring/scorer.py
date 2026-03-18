"""Job fit scoring: LLM-powered evaluation of candidate-job match quality.

Scores jobs on a 1-10 scale by comparing the user's resume against each
job description. All personal data is loaded at runtime from the user's
profile and resume file.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone

from applypilot.config import RESUME_PATH
from applypilot.role_routing import route_resume_for_job
from applypilot.database import get_connection, get_jobs_by_stage
from applypilot.llm import chat_json

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


def score_job(resume_text: str, job: dict) -> dict:
    """Score a single job against the resume.

    Args:
        resume_text: The candidate's full resume text.
        job: Job dict with keys: title, site, location, full_description.

    Returns:
        {"score": int, "keywords": str, "reasoning": str}
    """
    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job.get('company') or job.get('site') or 'N/A'}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
    )

    messages = [
        {"role": "system", "content": SCORE_PROMPT},
        {"role": "user", "content": f"RESUME:\n{resume_text}\n\n---\n\nJOB POSTING:\n{job_text}"},
    ]

    try:
        response = chat_json(messages, max_tokens=768, temperature=0.0)
        return _parse_score_response(response)
    except Exception as e:
        log.error("LLM error scoring job '%s': %s", job.get("title", "?"), e)
        return {"score": 0, "keywords": "", "reasoning": f"LLM error: {e}", "confidence": 0.0}


def _repair_zero_scores(conn) -> int:
    """Backfill fit_score for rows where LLM JSON was truncated but score is recoverable."""
    rows = conn.execute(
        "SELECT url, score_reasoning FROM jobs "
        "WHERE fit_score = 0 AND score_reasoning IS NOT NULL "
        "AND score_reasoning LIKE '%\"score\"%'"
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
        "AND score_reasoning LIKE '%\"score\"%'"
    ).fetchone()[0]

    recovered = _repair_zero_scores(conn)
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
    base_resume_text = RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()
    recovered = _repair_zero_scores(conn)

    if rescore:
        query = "SELECT * FROM jobs WHERE full_description IS NOT NULL"
        if limit > 0:
            query += f" LIMIT {limit}"
        jobs = conn.execute(query).fetchall()
    else:
        jobs = get_jobs_by_stage(conn=conn, stage="pending_score", limit=limit)

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
    results: list[dict] = []

    for job in jobs:
        # Deterministic multi-role support: choose the best base resume variant.
        try:
            routed = route_resume_for_job(job)
            resume_text = routed.text.strip() or base_resume_text
        except Exception:
            resume_text = base_resume_text

        result = score_job(resume_text, job)
        result["url"] = job["url"]
        completed += 1

        if result["score"] == 0:
            errors += 1

        results.append(result)

        log.info(
            "[%d/%d] score=%d  %s",
            completed,
            len(jobs),
            result["score"],
            job.get("title", "?")[:60],
        )

    # Write scores to DB
    now = datetime.now(timezone.utc).isoformat()
    for r in results:
        conn.execute(
            "UPDATE jobs SET fit_score = ?, score_confidence = ?, score_reasoning = ?, scored_at = ? WHERE url = ?",
            (r["score"], float(r.get("confidence") or 0.0), f"{r['keywords']}\n{r['reasoning']}", now, r["url"]),
        )
    conn.commit()

    elapsed = time.time() - t0
    log.info(
        "Done: %d scored in %.1fs (%.1f jobs/sec)", len(results), elapsed, len(results) / elapsed if elapsed > 0 else 0
    )

    # Score distribution
    dist = conn.execute("""
        SELECT fit_score, COUNT(*) FROM jobs
        WHERE fit_score IS NOT NULL
        GROUP BY fit_score ORDER BY fit_score DESC
    """).fetchall()
    distribution = [(row[0], row[1]) for row in dist]

    return {
        "scored": len(results),
        "errors": errors,
        "recovered": recovered,
        "elapsed": elapsed,
        "distribution": distribution,
    }
