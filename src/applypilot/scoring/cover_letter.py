"""Cover letter generation: LLM-powered, profile-driven, with validation.

Generates concise, engineering-voice cover letters tailored to specific job
postings. All personal data (name, skills, achievements) comes from the user's
profile at runtime. No hardcoded personal information.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from datetime import datetime, timezone

from applypilot import naming
from applypilot.config import COVER_LETTER_DIR, RESUME_PATH, load_profile
from applypilot.database import get_connection, get_jobs_by_stage
from applypilot.llm import get_client
from applypilot.scoring.validator import (
    BANNED_WORDS,
    sanitize_text,
    validate_cover_letter,
)
from applypilot.scoring.keywords import build_keyword_bank

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5  # max cross-run retries before giving up


def _selected_only_enabled() -> bool:
    for key in ("APPLYPILOT_SELECTED_ONLY", "APPLYPILOT_APPLY_SELECTED_ONLY"):
        val = str(os.environ.get(key, "") or "").strip().lower()
        if val in ("1", "true", "yes", "y", "on"):
            return True
    return False


def _sign_off_name(profile: dict) -> str:
    personal = profile.get("personal", {})

    # Prefer a full name for the final sign-off because the validator expects
    # a name line with 2+ words. If preferred_name is single-word (e.g. first
    # name only), fall back to full_name when available.
    preferred = (personal.get("preferred_name") or "").strip()
    full = (personal.get("full_name") or "").strip()

    if preferred and len(preferred.split()) >= 2:
        name = preferred
    elif full:
        name = full
    else:
        name = preferred

    # If the resume uses an ALL-CAPS name header, keep resume formatting as-is,
    # but normalize the cover-letter sign-off to Title Case.
    letters_only = re.sub(r"[^A-Za-z]", "", name)
    if letters_only and letters_only.isupper():
        return name.title()
    return name


def _ensure_greeting_and_signoff(letter: str, name: str) -> str:
    """Make the letter look complete even if the model forgets basics."""
    text = (letter or "").strip()
    if not text:
        return text

    # Greeting
    if not text.lower().startswith("dear"):
        text = "Dear Hiring Manager,\n\n" + text

    if not name:
        return text

    lines = text.splitlines()
    trimmed = list(lines)
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()

    non_empty = [ln.strip() for ln in trimmed if ln.strip()]
    if not non_empty:
        return text

    closing_words = ("best", "regards", "sincerely", "thank you", "thanks")
    last = non_empty[-1].strip()

    # If the model already ended with the name, prefer inserting a closing line
    # above it rather than appending a second signature.
    if last.lower() == name.strip().lower():
        if len(non_empty) >= 2 and any(w in non_empty[-2].lower() for w in closing_words):
            return "\n".join(trimmed)

        # Insert "Best," immediately before the last name line.
        # Find the index of that last name line in the trimmed list.
        idx = None
        for i in range(len(trimmed) - 1, -1, -1):
            if trimmed[i].strip():
                idx = i
                break
        if idx is not None:
            # Ensure a blank line before the closing.
            if idx > 0 and trimmed[idx - 1].strip():
                trimmed.insert(idx, "")
                idx += 1
            trimmed.insert(idx, "Best,")
            return "\n".join(trimmed)

        return text

    # Canonical sign-off to satisfy validation reliably.
    return "\n".join(trimmed) + f"\n\nBest,\n{name}"


def _looks_truncated(text: str, signoff_name: str) -> bool:
    s = (text or "").strip()
    if not s:
        return True

    # If it ends with a sign-off name, it's not truncated.
    if signoff_name:
        non_empty = [ln.strip() for ln in s.splitlines() if ln.strip()]
        if non_empty and non_empty[-1].lower() == signoff_name.strip().lower():
            return False

    # Otherwise require the final visible character to close a sentence.
    return s[-1] not in (".", "!", "?")


# ── Prompt Builder (profile-driven) ──────────────────────────────────────


def _build_cover_letter_prompt(profile: dict) -> str:
    """Build the cover letter system prompt from the user's profile.

    All personal data, skills, and sign-off name come from the profile.
    """
    personal = profile.get("personal", {})
    boundary = profile.get("skills_boundary", {})
    resume_facts = profile.get("resume_facts", {})

    # Preferred name for the sign-off (falls back to full name)
    sign_off_name = personal.get("preferred_name") or personal.get("full_name", "")

    # Flatten all allowed skills
    all_skills: list[str] = []
    for items in boundary.values():
        if isinstance(items, list):
            all_skills.extend(items)
    skills_str = ", ".join(all_skills) if all_skills else "the tools listed in the resume"

    # Real metrics from resume_facts
    real_metrics = resume_facts.get("real_metrics", [])
    preserved_projects = resume_facts.get("preserved_projects", [])

    # Build achievement examples for the prompt
    projects_hint = ""
    if preserved_projects:
        projects_hint = f"\nKnown projects to reference: {', '.join(preserved_projects)}"

    metrics_hint = ""
    if real_metrics:
        metrics_hint = f"\nReal metrics to use: {', '.join(real_metrics)}"

    return f"""Write a cover letter for {sign_off_name}. The goal is to get an interview.

STRUCTURE: 3 short paragraphs. Under 250 words. Every sentence must earn its place.

PARAGRAPH 1 (2-3 sentences): Open with a specific thing YOU built that solves THEIR problem. Not "I'm excited about this role." Not "This role aligns with my experience." Start with the work.

PARAGRAPH 2 (3-4 sentences): Pick 2 achievements from the resume that are MOST relevant to THIS job. Use numbers. Frame as solving their problem, not listing your accomplishments.{projects_hint}{metrics_hint}

PARAGRAPH 3 (1-2 sentences): One specific thing about the company from the job description (a product, a technical challenge, a team structure). Then close. "Happy to walk through any of this in more detail." or "Let's discuss." Nothing else.

BANNED WORDS/PHRASES (using ANY of these = instant rejection):
"resonated", "aligns with", "passionate", "eager", "eager to", "excited to apply", "I am confident",
"I believe", "proven track record", "strong track record", "cutting-edge", "innovative", "innovative solutions",
"leverage", "leveraging", "robust", "driven", "dedicated", "committed to",
"I look forward to hearing from you", "great fit", "unique opportunity",
"commitment to excellence", "dynamic team", "fast-paced environment",
"I am writing to express", "caught my eye", "caught my attention"

BANNED PUNCTUATION: No em dashes. Use commas or periods.

VOICE:
- Write like a real engineer emailing someone they respect. Not formal, not casual. Just direct.
- NEVER narrate or explain what you're doing. BAD: "This demonstrates my commitment to X." GOOD: Just state the fact and move on.
- NEVER hedge. BAD: "might address some of your challenges." GOOD: "solves the same problem your team is facing."
- NEVER use "Also," to start a sentence. NEVER use "Furthermore," or "Additionally,".
- Every sentence should contain either a number, a tool name, or a specific outcome. If it doesn't, cut it.
- Read it out loud. If it sounds like a robot wrote it, rewrite it.

ADDITIONAL BANNED PHRASES:
"This demonstrates", "This reflects", "This showcases", "This shows",
"This experience translates", "which aligns with", "which is relevant to",
"as demonstrated by", "showing experience with", "reflecting the need for",
"which directly addresses", "I have experience with",
"Also,", "Furthermore,", "Additionally,", "Moreover,"

FABRICATION = INSTANT REJECTION:
The candidate's real tools are ONLY: {skills_str}.
Do NOT mention ANY tool not in this list. If the job asks for tools not listed, talk about the work you did, not the tools.

Sign off: exactly 2 lines:
Best,
{sign_off_name}

Output ONLY the letter. Start with "Dear Hiring Manager," and end with:
Best,
{sign_off_name}
"""


# ── Core Generation ──────────────────────────────────────────────────────


def generate_cover_letter(resume_text: str, job: dict, profile: dict, max_retries: int = 3) -> str:
    """Generate a cover letter with fresh context on each retry + auto-sanitize.

    Same design as tailor_resume: fresh conversation per attempt, issues noted
    in the prompt, no conversation history stacking.

    Args:
        resume_text: The candidate's resume text (base or tailored).
        job: Job dict with title, site, location, full_description.
        profile: User profile dict.
        max_retries: Maximum retry attempts.

    Returns:
        The cover letter text (best attempt even if validation failed).
    """
    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job['site']}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
    )

    avoid_notes: list[str] = []
    letter = ""
    client = get_client()
    cl_prompt_base = _build_cover_letter_prompt(profile)
    name = _sign_off_name(profile)

    # Keyword bank derived from JD + (tailored) resume.
    kw_bank = None
    try:
        kw_bank = build_keyword_bank(
            job_description=(job.get("full_description") or ""),
            profile=profile,
            resume_text=resume_text,
        )
    except Exception:
        kw_bank = None

    for attempt in range(max_retries + 1):
        # Fresh conversation every attempt
        prompt = cl_prompt_base
        if avoid_notes:
            prompt += "\n\n## AVOID THESE ISSUES:\n" + "\n".join(f"- {n}" for n in avoid_notes[-5:])

        if kw_bank and kw_bank.get("prompt_keywords"):
            prompt += "\n\n## KEYWORD BANK (use only if truthful; do not stuff):\n"
            prompt += ", ".join(str(k) for k in (kw_bank.get("prompt_keywords") or []) if str(k).strip())

        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (f"RESUME:\n{resume_text}\n\n---\n\nTARGET JOB:\n{job_text}\n\nWrite the cover letter:"),
            },
        ]

        # Always enforce structure/length. Gemini can truncate, so be explicit.
        messages[-1]["content"] += (
            "\n\nHard requirements:\n"
            "- Exactly 3 paragraphs\n"
            "- 160-220 words\n"
            "- Include at least 2 concrete numbers from the resume\n"
            "- End with a closing line (e.g. Best,) then your name on its own line\n"
            "- Output plain text only\n"
            "- Do not stop early"
        )

        # Gemini can spend output budget on hidden thinking and truncate visible text.
        thinking_budget = 0 if (client.provider or "").lower() == "gemini" else None
        letter = client.chat(messages, max_tokens=1200, temperature=0.7, thinking_budget=thinking_budget)
        letter = sanitize_text(letter)  # auto-fix em dashes, smart quotes
        letter = _ensure_greeting_and_signoff(letter, name)
        # If the model truncated mid-sentence, treat as failure and retry.
        if _looks_truncated(letter, name):
            avoid_notes.append("Output looked truncated. Do not stop early; end with closing + name.")
            continue

        # Ensure sign-off survives sanitize and truncation guard.
        letter = _ensure_greeting_and_signoff(letter, name)

        validation = validate_cover_letter(letter, profile=profile, resume_text=resume_text)
        if validation["passed"]:
            return letter

        avoid_notes.extend(validation["errors"])
        log.debug(
            "Cover letter attempt %d/%d failed: %s",
            attempt + 1,
            max_retries + 1,
            validation["errors"],
        )

    return letter  # last attempt even if failed


# ── Batch Entry Point ────────────────────────────────────────────────────


def run_cover_letters(min_score: int = 7, limit: int = 0) -> dict:
    """Generate cover letters for high-scoring jobs that have tailored resumes.

    Args:
        min_score: Minimum fit_score threshold.
        limit: Maximum jobs to process.

    Returns:
        {"generated": int, "errors": int, "elapsed": float}
    """
    profile = load_profile()
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()
    selected_only = _selected_only_enabled()

    # Fetch jobs that have tailored resumes but no cover letter yet
    where = (
        "fit_score >= ? AND tailored_resume_path IS NOT NULL "
        "AND full_description IS NOT NULL "
        "AND (cover_letter_path IS NULL OR cover_letter_path = '') "
        "AND COALESCE(cover_attempts, 0) < ?"
    )
    params: list[object] = [min_score, MAX_ATTEMPTS]
    if selected_only:
        where += " AND apply_status = 'selected'"
    query = "SELECT rowid AS job_id, * FROM jobs WHERE " + where + " ORDER BY fit_score DESC"
    if limit > 0:
        query += " LIMIT ?"
        params.append(limit)
    jobs = conn.execute(query, tuple(params)).fetchall()

    if not jobs:
        if selected_only:
            log.info("No selected jobs needing cover letters (score >= %d).", min_score)
        else:
            log.info("No jobs needing cover letters (score >= %d).", min_score)
        return {"generated": 0, "errors": 0, "elapsed": 0.0}

    # Convert rows to dicts
    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    COVER_LETTER_DIR.mkdir(parents=True, exist_ok=True)
    if selected_only:
        log.info("Generating cover letters for %d selected jobs (score >= %d)...", len(jobs), min_score)
    else:
        log.info("Generating cover letters for %d jobs (score >= %d)...", len(jobs), min_score)
    t0 = time.time()
    completed = 0
    results: list[dict] = []
    error_count = 0

    for job in jobs:
        completed += 1
        try:
            # Prefer the tailored resume for this specific job.
            tailored_text = resume_text
            trp = job.get("tailored_resume_path")
            if trp:
                try:
                    tailored_text = Path(trp).read_text(encoding="utf-8")
                except Exception:
                    tailored_text = resume_text

            letter = generate_cover_letter(tailored_text, job, profile)

            username = str(os.environ.get("APPLYPILOT_USER", "") or "").strip()
            stem = naming.cover_letter_filename(profile.get("personal", {}), ext="txt", username=username, job=job)
            prefix = Path(stem).stem

            validation = validate_cover_letter(letter, profile=profile, resume_text=tailored_text)
            if not validation["passed"]:
                raise RuntimeError("Cover letter failed validation: " + "; ".join(validation["errors"][:3]))

            cl_path = COVER_LETTER_DIR / f"{prefix}_CL.txt"
            cl_path.write_text(letter, encoding="utf-8")

            # Generate PDF (best-effort)
            pdf_path = None
            try:
                from applypilot.scoring.pdf import convert_to_pdf

                pdf_path = str(convert_to_pdf(cl_path))
            except Exception:
                log.debug("PDF generation failed for %s", cl_path, exc_info=True)

            result = {
                "url": job["url"],
                "path": str(cl_path),
                "pdf_path": pdf_path,
                "title": job["title"],
                "site": job["site"],
            }
            results.append(result)

            elapsed = time.time() - t0
            rate = completed / elapsed if elapsed > 0 else 0
            log.info(
                "%d/%d [OK] | %.1f jobs/min | %s",
                completed,
                len(jobs),
                rate * 60,
                result["title"][:40],
            )
        except Exception as e:
            result = {
                "url": job["url"],
                "title": job["title"],
                "site": job["site"],
                "path": None,
                "pdf_path": None,
                "error": str(e),
            }
            error_count += 1
            results.append(result)
            log.error("%d/%d [ERROR] %s -- %s", completed, len(jobs), job["title"][:40], e)

    # Persist to DB: increment attempt counter for ALL, save path only for successes
    now = datetime.now(timezone.utc).isoformat()
    saved = 0
    for r in results:
        if r.get("path"):
            conn.execute(
                "UPDATE jobs SET cover_letter_path=?, cover_letter_at=?, "
                "cover_attempts=COALESCE(cover_attempts,0)+1 WHERE url=?",
                (r["path"], now, r["url"]),
            )
            saved += 1
        else:
            conn.execute(
                "UPDATE jobs SET cover_attempts=COALESCE(cover_attempts,0)+1, cover_letter_path=NULL WHERE url=?",
                (r["url"],),
            )
    conn.commit()

    elapsed = time.time() - t0
    log.info("Cover letters done in %.1fs: %d generated, %d errors", elapsed, saved, error_count)

    return {
        "generated": saved,
        "errors": error_count,
        "elapsed": elapsed,
    }
