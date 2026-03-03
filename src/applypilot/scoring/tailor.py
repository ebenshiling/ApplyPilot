"""Resume tailoring: LLM-powered ATS-optimized resume generation per job.

THIS IS THE HEAVIEST REFACTOR. Every piece of personal data -- name, email, phone,
skills, companies, projects, school -- is loaded at runtime from the user's profile.
Zero hardcoded personal information.

The LLM returns structured JSON, code assembles the final text. Header (name, contact)
is always code-injected, never LLM-generated. Each retry starts a fresh conversation
to avoid apologetic spirals.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from applypilot.config import RESUME_PATH, TAILORED_DIR, load_profile, load_search_config
from applypilot.database import get_connection, get_jobs_by_stage
from applypilot.llm import chat_json, get_client
from applypilot import naming
from applypilot.scoring.validator import (
    FABRICATION_WATCHLIST,
    sanitize_text,
    validate_json_fields,
    validate_tailored_resume,
    BANNED_WORDS,
)
from applypilot.scoring.keywords import build_keyword_bank
from applypilot.scoring.tailor_strategy import (
    build_adaptive_budget,
    build_fact_library,
    build_jd_targets,
    check_quant_consistency,
    collect_evidence_numbers,
    detect_role_pack,
    format_fact_library_for_prompt,
    rank_candidate,
    score_jd_coverage,
    strip_fact_citations,
    validate_fact_citations,
)

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5  # max cross-run retries before giving up


def _selected_only_enabled() -> bool:
    for key in ("APPLYPILOT_SELECTED_ONLY", "APPLYPILOT_APPLY_SELECTED_ONLY"):
        val = str(os.environ.get(key, "") or "").strip().lower()
        if val in ("1", "true", "yes", "y", "on"):
            return True
    return False


def _lenient_tailor_enabled(profile: dict | None = None) -> bool:
    """Return True when lenient tailoring mode is enabled.

    Enable via:
    - env: APPLYPILOT_TAILOR_LENIENT=1
    - profile: tailoring.mode = "lenient" or tailoring.lenient = true
    """
    ev = str(os.environ.get("APPLYPILOT_TAILOR_LENIENT", "") or "").strip().lower()
    if ev in ("1", "true", "yes", "y", "on"):
        return True
    if ev in ("0", "false", "no", "n", "off"):
        return False

    try:
        cfg = (profile or {}).get("tailoring") or {}
        if isinstance(cfg, dict):
            mode = str(cfg.get("mode") or "").strip().lower()
            if mode == "lenient":
                return True
            if mode == "strict":
                return False
            lv = cfg.get("lenient")
            if isinstance(lv, bool):
                return lv
            ls = str(lv or "").strip().lower()
            if ls in ("1", "true", "yes", "y", "on"):
                return True
            if ls in ("0", "false", "no", "n", "off"):
                return False
    except Exception:
        pass

    return False


def _is_fatal_json_error(msg: str) -> bool:
    s = str(msg or "").strip().lower()
    if not s:
        return False
    fatal_markers = (
        "missing required field",
        "fabricated skill",
        "banned words",
        "llm self-talk",
        "invented tools not in base resume",
    )
    return any(m in s for m in fatal_markers)


def _is_fatal_full_validation_error(msg: str) -> bool:
    s = str(msg or "").strip().lower()
    if not s:
        return False
    fatal_markers = (
        "fabricated skill",
        "banned words",
        "llm self-talk",
        "contains em dash or en dash",
        "education '",
        "current company",
    )
    return any(m in s for m in fatal_markers)


def _strict_evidence_enabled(profile: dict) -> bool:
    """Whether citation/quant checks are hard blockers.

    Default is lenient (False) to reduce false negatives in real-world tailoring.
    Enable with profile.tailoring.strict_evidence=true or env APPLYPILOT_TAILOR_STRICT_EVIDENCE=1.
    """
    if _lenient_tailor_enabled(profile):
        return False

    try:
        cfg = (profile or {}).get("tailoring") or {}
        if isinstance(cfg, dict):
            v = cfg.get("strict_evidence")
            if isinstance(v, bool):
                return v
            s = str(v or "").strip().lower()
            if s in ("1", "true", "yes", "y", "on"):
                return True
            if s in ("0", "false", "no", "n", "off"):
                return False
    except Exception:
        pass

    ev = str(os.environ.get("APPLYPILOT_TAILOR_STRICT_EVIDENCE", "") or "").strip().lower()
    return ev in ("1", "true", "yes", "y", "on")


def _load_skip_titles() -> list[str]:
    """Load `skip_titles` from searches.yaml (case-insensitive substrings)."""
    try:
        cfg = load_search_config() or {}
    except Exception:
        return []

    raw = cfg.get("skip_titles", []) or []
    out: list[str] = []
    for v in raw:
        s = str(v).strip().lower()
        if s:
            out.append(s)
    return out


def _matches_skip_titles(title: str | None, skip_titles: list[str]) -> bool:
    if not title or not skip_titles:
        return False
    t_low = str(title).lower()
    return any(st in t_low for st in skip_titles if st)


# ── Prompt Builders (profile-driven) ──────────────────────────────────────


def _build_tailor_prompt(
    profile: dict,
    *,
    role_pack_instructions: str = "",
    adaptive_budget_hint: str = "",
    fact_library_text: str = "",
    jd_targets_text: str = "",
) -> str:
    """Build the resume tailoring system prompt from the user's profile.

    All skills boundaries, preserved entities, and formatting rules are
    derived from the profile -- nothing is hardcoded.
    """
    boundary = profile.get("skills_boundary", {})
    resume_facts = profile.get("resume_facts", {})

    # Format skills boundary for the prompt
    skills_lines = []
    for category, items in boundary.items():
        if isinstance(items, list) and items:
            label = category.replace("_", " ").title()
            skills_lines.append(f"{label}: {', '.join(items)}")
    skills_block = "\n".join(skills_lines)

    # Preserved entities
    companies = resume_facts.get("preserved_companies", [])
    projects = resume_facts.get("preserved_projects", [])
    school = resume_facts.get("preserved_school", "")
    real_metrics = resume_facts.get("real_metrics", [])

    companies_str = ", ".join(companies) if companies else "N/A"
    projects_str = ", ".join(projects) if projects else "N/A"
    metrics_str = ", ".join(real_metrics) if real_metrics else "N/A"

    education = profile.get("experience", {})
    education_level = education.get("education_level", "")
    official_title = str(education.get("current_job_title", "") or "").strip()
    current_company = str(education.get("current_company", "") or "").strip()

    role_pack_block = role_pack_instructions.strip()
    adaptive_block = adaptive_budget_hint.strip()
    fact_block = fact_library_text.strip()
    targets_block = jd_targets_text.strip()

    return f"""You are a senior technical recruiter rewriting a resume to get this person an interview.

Take the base resume and job description. Return a tailored resume as a JSON object.

The resume layout is fixed by the system. You MUST populate these sections:
- SUMMARY (a single paragraph, no bullets)
- CORE TECHNICAL SKILLS (a list; 8-12 items)
- PROFESSIONAL EXPERIENCE (reverse chronological; bullets)
- PROJECTS (optional)
- EDUCATION (handled by profile; do NOT invent)
- CERTIFICATIONS (handled by profile; do NOT invent)
- TECHNICAL ENVIRONMENT (handled by profile; do NOT invent)

Formatting rules:
- Do NOT use markdown (no **bold**, no *italics*).
- Output plain text only. Keyword emphasis (bold keywords) is added during PDF generation.

Forbidden phrases (auto-rejected): {", ".join(BANNED_WORDS)}

## RECRUITER SCAN (6 seconds):
1. Title -- matches what they're hiring?
2. Summary -- 2 sentences proving you've done this work
3. First 3 bullets of most recent role -- verbs and outcomes match?
4. Skills -- must-haves visible immediately?

## SKILLS BOUNDARY (real skills only):
{skills_block}

You may NOT add new tools/technologies that aren't already present in the base resume.
If a tool/technology is not explicitly in the base resume, do NOT mention it (even if it's in the job description).
In particular, do NOT mention Power Automate, Copilot Studio, Power Apps, ALM, or API integration unless they appear in the base resume.

## TAILORING RULES:

TITLE: Match the target role. Keep seniority (Senior/Lead/Staff). Drop company suffixes and team names.

SUMMARY: Rewrite from scratch. Lead with the 1-2 skills that matter most for THIS role. Sound like someone who's done this job.

CORE TECHNICAL SKILLS: Provide 8-12 short items (not inline). Focus on role-relevant skills from the base resume.

Reframe EVERY bullet for this role. Same real work, different angle. Every bullet must be reworded. Never copy verbatim.

 PROJECTS: Reorder by relevance. Drop irrelevant projects entirely.

 EXPERIENCE ORDER: Reverse chronological. The FIRST experience entry is the current/most recent role.

 MOST RECENT ROLE TITLE (important):
 - Current company (must remain exact): {current_company}
 - Official internal title (for context only): {official_title}
 - For the FIRST experience entry (current role), choose a market-equivalent title for THIS job family.
 - Keep the company name exactly the same.
 - Do NOT use parentheses.
 - Do NOT copy the target job title verbatim.
 - Format should be: "<Market Title> at <Company>".

 BULLETS: Strong verb + what you built + quantified impact. Vary verbs (Built, Designed, Implemented, Reduced, Automated, Deployed, Operated, Optimized). Most relevant first.

 BULLET ALLOCATION (non-negotiable):
 - Follow the adaptive section budget provided below.
 - The current/most recent role MUST have >= bullet count than any other role.

## VOICE:
- Write like a real engineer. Short, direct.
- GOOD: "Automated financial reporting with Python + API integrations, cut processing time from 10 hours to 2"
- BAD: "Leveraged cutting-edge AI technologies to drive transformative operational efficiencies"
 - NEVER use: passionate, dedicated, leveraging, spearheaded, robust, cutting-edge, proven track record, strong track record, eager, synergy, seamless, streamlined, end-to-end, detail-oriented, results-driven, I am confident, I believe, I am excited
- No em dashes. Use commas, periods, or hyphens.

## HARD RULES:
- Do NOT invent work, companies, degrees, or certifications
- Do NOT change real numbers ({metrics_str})
- Preserved companies: {companies_str} -- names stay as-is
- Preserved school: {school}
 - Must fit 1 page.

## ROLE-SPECIFIC PROMPT PACK:
{role_pack_block or "Use a balanced professional tone with measurable outcomes."}

## ADAPTIVE SECTION BUDGET:
{adaptive_block or "Most recent role should receive the highest bullet density."}

## EVIDENCE FACT LIBRARY (bullet claims must cite IDs):
{fact_block or "No fact library provided."}

Citation rule (non-negotiable):
- Every bullet in EXPERIENCE and PROJECTS must end with at least one source citation in this exact format: [F12] or [F4, F9]
- Use only fact IDs from the fact library above.

## JD COVERAGE TARGETS:
{targets_block or "Prioritize role-relevant terms that are truthful and present in evidence."}

Quant rule:
- Every numeric claim in summary/experience/projects must come from source evidence.
- If you compute a derived number, mark it explicitly with "(derived)".

## OUTPUT: Return ONLY valid JSON. No markdown fences. No commentary. No "here is" preamble.

{{"title":"Role Title","summary":"2-3 tailored sentences.","core_skills":["skill 1","skill 2"],"skills":{{"Languages":"...","Analytics":"...","Data":"...","Tools":"...","Governance":"..."}},"experience":[{{"header":"Title at Company","subtitle":"Dates | Location","bullets":["bullet 1","bullet 2","bullet 3","bullet 4"]}}],"projects":[{{"header":"Project Name - Description","subtitle":"Tech | Dates","bullets":["bullet 1","bullet 2"]}}],"education":"{school} | {education_level}"}}"""


def _build_judge_prompt(profile: dict) -> str:
    """Build the LLM judge prompt from the user's profile."""
    boundary = profile.get("skills_boundary", {})
    resume_facts = profile.get("resume_facts", {})
    extras = profile.get("resume_sections", {}) or {}

    # Flatten allowed skills for the judge
    all_skills: list[str] = []
    for items in boundary.values():
        if isinstance(items, list):
            all_skills.extend(items)
    skills_str = ", ".join(all_skills) if all_skills else "N/A"

    real_metrics = resume_facts.get("real_metrics", [])
    metrics_str = ", ".join(real_metrics) if real_metrics else "N/A"

    # Technical Environment is profile-driven and may include items not present
    # in the base resume text; the judge must not treat those as fabrication.
    tech_env = extras.get("technical_environment")
    tech_env_items: list[str] = []
    if isinstance(tech_env, list):
        tech_env_items = [sanitize_text(str(x)) for x in tech_env if str(x).strip()]
    tech_env_str = ", ".join(tech_env_items) if tech_env_items else "N/A"

    return f"""You are a resume quality judge. A tailoring engine rewrote a resume to target a specific job. Your job is to catch LIES, not style changes.

You must answer with EXACTLY this format:
VERDICT: PASS or FAIL
ISSUES: (list any problems, or "none")

## CONTEXT -- what the tailoring engine was instructed to do (all of this is ALLOWED):
- Change the title to match the target role
- Rewrite the summary from scratch for the target job
- Reorder bullets and projects to put the most relevant first
- Reframe bullets to use the job's language
- Drop low-relevance bullets and replace with more relevant ones from other sections
- Reorder the skills section to put job-relevant skills first
- Change tone and wording extensively

## WHAT IS FABRICATION (FAIL for these):
1. Adding tools, languages, or frameworks to CORE TECHNICAL SKILLS / TECHNICAL SKILLS that aren't in the original. The allowed skills are ONLY: {skills_str}
2. Inventing NEW metrics or numbers not in the original. The real metrics are: {metrics_str}
3. Inventing work that has no basis in any original bullet (completely new achievements).
4. Adding companies, roles, or degrees that don't exist.
5. Changing real numbers (inflating 80% to 95%, 500 nodes to 1000 nodes).

## WHAT IS NOT FABRICATION (do NOT fail for these):
- Rewording any bullet, even heavily, as long as the underlying work is real
- Combining two original bullets into one
- Splitting one original bullet into two
- Describing the same work with different emphasis
- Dropping bullets entirely
- Reordering anything
- Changing the title or summary completely

## PROFILE-DRIVEN SECTIONS (do NOT treat as fabrication):
- TECHNICAL ENVIRONMENT is code-injected from the user's profile (not generated by the model) and may include: {tech_env_str}
- Do NOT fail solely because TECHNICAL ENVIRONMENT mentions items not present in the original resume text.

## TOLERANCE RULE:
The goal is to get interviews, not to be a perfect fact-checker. Allow up to 3 minor stretches per resume:
- Adding a closely related tool the candidate could realistically know is a MINOR STRETCH, not fabrication.
- Reframing a metric with slightly different wording is a MINOR STRETCH.
- Adding any LEARNABLE skill given their existing stack is a MINOR STRETCH.
- Only FAIL if there are MAJOR lies: completely invented projects, fake companies, fake degrees, wildly inflated numbers, or skills from a completely different domain.

## IMPORTANT:
- Do NOT fail a resume just because it mentions generic responsibilities like "API integration" or "workflow automation" when the candidate already has Python/SQL/ETL experience. Only fail if a clearly unrelated tool appears (e.g., Scala, Spring, AWS Certified).

Be strict about major lies. Be lenient about minor stretches and learnable skills. Do not fail for style, tone, or restructuring."""


# ── JSON Extraction ───────────────────────────────────────────────────────


def extract_json(raw: str) -> dict:
    """Robustly extract JSON from LLM response (handles fences, preamble).

    Args:
        raw: Raw LLM response text.

    Returns:
        Parsed JSON dict.

    Raises:
        ValueError: If no valid JSON found.
    """
    raw = raw.strip()

    # Direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Markdown fences
    if "```" in raw:
        for part in raw.split("```")[1::2]:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except json.JSONDecodeError:
                continue

    # Find outermost { ... }
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError("No valid JSON found in LLM response")


# ── Resume Assembly (profile-driven header) ──────────────────────────────


def assemble_resume_text(data: dict, profile: dict) -> str:
    """Convert JSON resume data to formatted plain text.

    Header (name, location, contact) is ALWAYS code-injected from the profile,
    never LLM-generated. All text fields are sanitized.

    Args:
        data: Parsed JSON resume from the LLM.
        profile: User profile dict from load_profile().

    Returns:
        Formatted resume text.
    """
    personal = profile.get("personal", {})
    lines: list[str] = []

    # Header -- always code-injected from profile
    lines.append(personal.get("full_name", ""))
    lines.append(sanitize_text(data.get("title", "Software Engineer")))

    # Location from search config or profile -- leave blank if not available
    # The location line is optional; the original used a hardcoded city.
    # We omit it here; the LLM prompt can include it if the user sets it.

    # Contact line
    contact_parts: list[str] = []
    if personal.get("email"):
        contact_parts.append(personal["email"])
    if personal.get("phone"):
        contact_parts.append(personal["phone"])
    if personal.get("github_url"):
        contact_parts.append(personal["github_url"])
    if personal.get("linkedin_url"):
        contact_parts.append(personal["linkedin_url"])
    if contact_parts:
        lines.append(" | ".join(contact_parts))
    lines.append("")

    # Summary
    lines.append("SUMMARY")
    lines.append(sanitize_text(data["summary"]))
    lines.append("")

    # Core Technical Skills (prefer explicit list; fall back to skills dict)
    lines.append("CORE TECHNICAL SKILLS")
    core = data.get("core_skills")
    core_items: list[str] = []
    if isinstance(core, list):
        core_items = [sanitize_text(str(s)) for s in core if str(s).strip()]

    if not core_items and isinstance(data.get("skills"), dict):
        # Derive a simple list from the skills dict (comma-split, de-duped)
        seen: set[str] = set()
        for val in data["skills"].values():
            for part in str(val).split(","):
                p = sanitize_text(part)
                if not p:
                    continue
                key = p.lower()
                if key in seen:
                    continue
                seen.add(key)
                core_items.append(p)
                if len(core_items) >= 12:
                    break
            if len(core_items) >= 12:
                break

    for item in core_items:
        lines.append(f"- {item}")
    lines.append("")

    # Profile-driven sections (accept list and legacy newline-delimited strings)
    extras = profile.get("resume_sections", {}) or {}

    def _section_lines(value: object) -> list[str]:
        if isinstance(value, list):
            raw_items = value
        elif isinstance(value, str):
            raw_items = value.splitlines()
        else:
            return []
        out: list[str] = []
        for x in raw_items:
            s = sanitize_text(str(x))
            if s:
                out.append(s)
        return out

    # Technical Environment (optional, profile-driven)
    tech_env_lines = _section_lines(extras.get("technical_environment"))
    if tech_env_lines:
        lines.append("TECHNICAL ENVIRONMENT")
        for s in tech_env_lines:
            if s:
                lines.append(f"- {s}" if not s.startswith("-") else s)
        lines.append("")

    # Professional Experience
    lines.append("PROFESSIONAL EXPERIENCE")
    for entry in data.get("experience", []):
        lines.append(sanitize_text(entry.get("header", "")))
        if entry.get("subtitle"):
            lines.append(sanitize_text(entry["subtitle"]))
        for b in entry.get("bullets", []):
            lines.append(f"- {sanitize_text(strip_fact_citations(str(b)))}")
        lines.append("")

    # Projects
    lines.append("PROJECTS")

    def _allowed_skills_lower(p: dict) -> set[str]:
        boundary = p.get("skills_boundary", {}) or {}
        out: set[str] = set()
        for items in boundary.values():
            if isinstance(items, list):
                for it in items:
                    s = str(it).strip().lower()
                    if s:
                        out.add(s)
        return out

    def _guess_year_from_education(extras_obj: dict) -> str:
        for line in _section_lines(extras_obj.get("education")):
            m = re.search(r"\b(20\d{2})\b", str(line))
            if m:
                return m.group(1)
        return ""

    def _fallback_project_subtitle(allowed: set[str], year: str) -> str:
        tech: list[str] = []
        if "power bi" in allowed:
            tech.append("Power BI")
        if "sql" in allowed:
            tech.append("SQL")
        if "python" in allowed:
            tech.append("Python")
        left = ", ".join(tech[:3])
        if year and left:
            return f"{left} | {year}"
        if year:
            return year
        return left

    def _fallback_project_bullets(project_name: str, allowed: set[str]) -> list[str]:
        name_l = project_name.lower()
        has_pbi = "power bi" in allowed
        has_sql = "sql" in allowed
        if "supply chain" in name_l:
            b1 = "Built a dashboard to analyse and visualise supply chain performance and bottlenecks."
        elif has_pbi:
            b1 = "Built a Power BI dashboard to analyse performance trends and support decision-making."
        else:
            b1 = "Delivered an analytics project focused on turning raw data into actionable insights."

        if has_sql:
            b2 = "Used SQL to model, validate, and prepare datasets for reliable reporting."
        else:
            b2 = "Applied structured data validation and clear storytelling to communicate outcomes."

        return [b1, b2]

    allowed = _allowed_skills_lower(profile)
    year = _guess_year_from_education(extras)

    # Prefer LLM-provided projects, but ensure each has at least 1 bullet.
    project_entries: list[dict] = []
    raw_projects = data.get("projects")
    if isinstance(raw_projects, list):
        for entry in raw_projects:
            header = sanitize_text(str(entry.get("header", "") or ""))
            if not header:
                continue
            subtitle = sanitize_text(str(entry.get("subtitle", "") or ""))
            bullets = [sanitize_text(str(b)) for b in (entry.get("bullets", []) or []) if str(b).strip()]
            if not bullets:
                bullets = _fallback_project_bullets(header, allowed)
            project_entries.append({"header": header, "subtitle": subtitle, "bullets": bullets[:2]})

    # Hardening: if no projects were generated, inject a safe project from the profile.
    if not project_entries:
        preserved_projects = (profile.get("resume_facts", {}) or {}).get("preserved_projects", []) or []
        if preserved_projects:
            proj_name = str(preserved_projects[0]).strip()
            if proj_name:
                dissertation_line = ""
                edu_lines = extras.get("education")
                if isinstance(edu_lines, list):
                    for ln in edu_lines:
                        s = str(ln)
                        if s.lower().startswith("dissertation:") and proj_name.lower() in s.lower():
                            dissertation_line = s
                            break

                header = proj_name
                if dissertation_line and "dissertation" not in header.lower():
                    header = f"{proj_name} - Master's Dissertation"

                project_entries.append(
                    {
                        "header": header,
                        "subtitle": _fallback_project_subtitle(allowed, year),
                        "bullets": _fallback_project_bullets(proj_name, allowed)[:2],
                    }
                )

    # Last-resort hardening: mirror proven work from recent experience so
    # PROJECTS is never rendered as an empty section.
    if not project_entries:
        exp_entries = data.get("experience")
        if isinstance(exp_entries, list):
            for e in exp_entries:
                if not isinstance(e, dict):
                    continue
                header_raw = sanitize_text(str(e.get("header", "") or ""))
                if not header_raw:
                    continue
                title_only = header_raw.split(" at ", 1)[0].strip() or header_raw
                proj_header = f"Selected {title_only} Project Work"
                proj_subtitle = sanitize_text(str(e.get("subtitle", "") or "")) or _fallback_project_subtitle(
                    allowed, year
                )
                proj_bullets: list[str] = []
                for b in e.get("bullets", []) or []:
                    s = sanitize_text(strip_fact_citations(str(b)))
                    if s:
                        proj_bullets.append(s)
                    if len(proj_bullets) >= 2:
                        break
                if not proj_bullets:
                    proj_bullets = _fallback_project_bullets(title_only, allowed)[:2]
                project_entries.append({"header": proj_header, "subtitle": proj_subtitle, "bullets": proj_bullets})
                break

    for entry in project_entries:
        lines.append(entry["header"])
        if entry.get("subtitle"):
            lines.append(entry["subtitle"])
        for b in entry.get("bullets", []) or []:
            lines.append(f"- {sanitize_text(strip_fact_citations(str(b)))}")
        lines.append("")

    # Education (prefer profile-driven entries so courses/certs are preserved)
    lines.append("EDUCATION")
    education_lines = _section_lines(extras.get("education"))
    if not education_lines:
        raw_edu = str(data.get("education", "") or "")
        education_lines = [sanitize_text(x) for x in raw_edu.splitlines() if sanitize_text(x)]

    # Ensure key education context survives even when model output is sparse.
    resume_facts = profile.get("resume_facts", {}) or {}
    exp_ctx = profile.get("experience", {}) or {}
    preserved_school = sanitize_text(str(resume_facts.get("preserved_school", "") or ""))
    education_level = sanitize_text(str(exp_ctx.get("education_level", "") or ""))
    edu_blob = " ".join(education_lines).lower()
    if preserved_school and preserved_school.lower() not in edu_blob:
        education_lines.append(preserved_school)
        edu_blob = " ".join(education_lines).lower()
    if education_level and education_level.lower() not in edu_blob:
        if preserved_school and preserved_school.lower() in edu_blob:
            education_lines.append(education_level)
        elif preserved_school:
            education_lines.append(f"{preserved_school} | {education_level}")
        else:
            education_lines.append(education_level)

    for s in education_lines:
        if s:
            lines.append(s)

    # Certifications (optional, profile-driven)
    certs = _section_lines(extras.get("certifications"))
    if certs:
        lines.append("")
        lines.append("CERTIFICATIONS")
        for s in certs:
            if s:
                lines.append(f"- {s}")

    return "\n".join(lines)


# ── LLM Judge ────────────────────────────────────────────────────────────


def judge_tailored_resume(original_text: str, tailored_text: str, job_title: str, profile: dict) -> dict:
    """LLM judge layer: catches subtle fabrication that programmatic checks miss.

    Args:
        original_text: Base resume text.
        tailored_text: Tailored resume text.
        job_title: Target job title.
        profile: User profile for building the judge prompt.

    Returns:
        {"passed": bool, "verdict": str, "issues": str, "raw": str}
    """
    judge_prompt = _build_judge_prompt(profile)

    messages = [
        {"role": "system", "content": judge_prompt},
        {
            "role": "user",
            "content": (
                f"JOB TITLE: {job_title}\n\n"
                f"ORIGINAL RESUME:\n{original_text}\n\n---\n\n"
                f"TAILORED RESUME:\n{tailored_text}\n\n"
                "Judge this tailored resume:"
            ),
        },
    ]

    client = get_client()
    response = client.chat(messages, max_tokens=512, temperature=0.1)

    passed = "VERDICT: PASS" in response.upper()
    issues = "none"
    if "ISSUES:" in response.upper():
        issues_idx = response.upper().index("ISSUES:")
        issues = response[issues_idx + 7 :].strip()

    return {
        "passed": passed,
        "verdict": "PASS" if passed else "FAIL",
        "issues": issues,
        "raw": response,
    }


# ── Core Tailoring ───────────────────────────────────────────────────────


def tailor_resume(resume_text: str, job: dict, profile: dict, max_retries: int = 3) -> tuple[str, dict]:
    """Generate a tailored resume via JSON output + fresh context on each retry.

    Key design choices:
    - LLM returns structured JSON, code assembles the text (no header leaks)
    - Each retry starts a FRESH conversation (no apologetic spiral)
    - Issues from previous attempts are noted in the system prompt
    - Em dashes and smart quotes are auto-fixed, not rejected

    Args:
        resume_text: Base resume text.
        job: Job dict with title, site, location, full_description.
        profile: User profile dict.
        max_retries: Maximum retry attempts.

    Returns:
        (tailored_text, report) where report contains validation details.
    """
    job_desc = str(job.get("full_description") or "")[:6000]
    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job['site']}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{job_desc}"
    )

    # Keyword bank: prompt guidance + PDF highlighting.
    kw_bank = None
    try:
        kw_bank = build_keyword_bank(
            job_description=(job.get("full_description") or ""),
            profile=profile,
            resume_text=resume_text,
        )
    except Exception:
        kw_bank = None

    prompt_keywords: list[str] = []
    highlight_keywords: list[str] = []
    if isinstance(kw_bank, dict):
        prompt_keywords = [str(x).strip() for x in (kw_bank.get("prompt_keywords") or []) if str(x).strip()]
        highlight_keywords = [str(x).strip() for x in (kw_bank.get("highlight_keywords") or []) if str(x).strip()]

    role_pack = detect_role_pack(job, profile)
    adaptive = build_adaptive_budget(job, profile, role_pack=role_pack.get("pack", "engineering"))
    runtime_rules = adaptive.get("runtime_rules") if isinstance(adaptive, dict) else {}

    fact_library = build_fact_library(resume_text, profile, max_facts=72)
    fact_ids = {str(f.get("id") or "").upper() for f in fact_library if f.get("id")}
    fact_block = format_fact_library_for_prompt(fact_library, limit=40)

    jd_targets_bundle = build_jd_targets(
        job_description=job_desc,
        profile=profile,
        keyword_bank=(kw_bank if isinstance(kw_bank, dict) else None),
        resume_text=resume_text,
        limit=14,
    )
    jd_targets = [str(x) for x in (jd_targets_bundle.get("targets") or []) if str(x).strip()]
    jd_synonyms = dict(jd_targets_bundle.get("synonyms") or {})
    jd_targets_text = ", ".join(jd_targets) if jd_targets else "No explicit targets extracted."

    evidence_numbers = collect_evidence_numbers(resume_text, profile, fact_library)

    tailoring_cfg = profile.get("tailoring") if isinstance(profile, dict) else {}
    try:
        draft_count = int((tailoring_cfg or {}).get("draft_candidates") or 3)
    except Exception:
        draft_count = 3
    draft_count = max(2, min(3, draft_count))
    draft_temps = [0.25, 0.45, 0.65][:draft_count]

    report: dict = {
        "attempts": 0,
        "validator": None,
        "judge": None,
        "status": "pending",
        "keywords": highlight_keywords,
        "keyword_bank": {"prompt": prompt_keywords, "highlight": highlight_keywords},
        "strategy": {
            "role_pack": role_pack,
            "adaptive_budget": adaptive,
            "fact_library_count": len(fact_library),
            "jd_targets": jd_targets,
            "draft_candidates": draft_count,
        },
        "draft_ranking": [],
    }
    lenient_tailor = _lenient_tailor_enabled(profile)
    strict_evidence = _strict_evidence_enabled(profile)
    report["mode"] = "lenient" if lenient_tailor else "strict"

    def _normalize_title(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()

    def _should_align_current_role(job_title: str) -> bool:
        t = _normalize_title(job_title)
        # Only align for data/analytics/BI type roles.
        return any(k in t for k in ["data", "analytics", "analyst", "bi", "business intelligence", "report"])

    def _align_current_role_header(data_obj: dict) -> None:
        try:
            exp = data_obj.get("experience")
            if not isinstance(exp, list) or not exp:
                return

            current_company = str(profile.get("experience", {}).get("current_company", "") or "").strip()
            official_title = str(profile.get("experience", {}).get("current_job_title", "") or "").strip()
            if not current_company or not official_title:
                return

            if not _should_align_current_role(str(job.get("title", "") or "")):
                return

            header0 = str(exp[0].get("header", "") or "")
            if current_company.lower() not in header0.lower():
                return

            def _title_tokens(s: str) -> set[str]:
                toks = [t for t in _normalize_title(s).split() if len(t) >= 3]
                stop = {"and", "the", "for", "with", "role", "team"}
                return {t for t in toks if t not in stop}

            def _sanitize_market_title(raw_title: str) -> str:
                s = sanitize_text(raw_title)
                s = re.sub(r"\([^)]*\)", "", s).strip()  # drop parentheses content
                s = re.sub(r"\s+", " ", s).strip()
                # Strip a trailing "at ..." if the model included it.
                if " at " in s.lower():
                    s = s[: s.lower().rfind(" at ")].strip()
                # Keep it short and headline-y.
                if len(s) > 64:
                    s = s[:64].rstrip()
                return s

            def _pick_allowed_market_title(job_title: str, job_desc: str, official: str, proposed: str) -> str:
                allowed = [
                    "Data & Quality Systems Analyst",
                    "Data Analyst",
                    "BI Analyst",
                    "Reporting Analyst",
                ]

                def _toks(s: str) -> set[str]:
                    return {t for t in _normalize_title(s).split() if len(t) >= 3}

                jt = _normalize_title(job_title)
                jd = _normalize_title((job_desc or "")[:6000])

                # Score title + description separately (title signals count more).
                scores: dict[str, int] = {a: 0 for a in allowed}

                def _add_if(
                    title_phrases: list[str], desc_phrases: list[str], bucket: str, w_title: int, w_desc: int
                ) -> None:
                    for ph in title_phrases:
                        if ph and ph in jt:
                            scores[bucket] += w_title
                    for ph in desc_phrases:
                        if ph and ph in jd:
                            scores[bucket] += w_desc

                _add_if(
                    title_phrases=["business intelligence", " power bi ", " bi "],
                    desc_phrases=["power bi", "dax", "tabular", "semantic model", "tableau"],
                    bucket="BI Analyst",
                    w_title=6,
                    w_desc=2,
                )

                _add_if(
                    title_phrases=["report", "reporting", "dashboard"],
                    desc_phrases=["report", "reporting", "dashboard", "kpi", "insight", "metrics"],
                    bucket="Reporting Analyst",
                    w_title=5,
                    w_desc=2,
                )

                _add_if(
                    title_phrases=["data analyst", "analytics"],
                    desc_phrases=["analysis", "analytics", "ad hoc", "sql", "python"],
                    bucket="Data Analyst",
                    w_title=5,
                    w_desc=2,
                )

                _add_if(
                    title_phrases=["quality", "governance", "compliance", "controls"],
                    desc_phrases=[
                        "data quality",
                        "reconciliation",
                        "controls",
                        "audit",
                        "compliance",
                        "governance",
                        "gdpr",
                    ],
                    bucket="Data & Quality Systems Analyst",
                    w_title=4,
                    w_desc=2,
                )

                # Keep it honest: tiny bias toward staying close to official title,
                # but do not let it override job relevance.
                off_t = _toks(official)
                for a in allowed:
                    if off_t & _toks(a):
                        scores[a] += 1

                # If the model proposed one of the allowed titles, give it a small boost.
                prop = _sanitize_market_title(proposed)
                for a in allowed:
                    if _normalize_title(a) == _normalize_title(prop):
                        scores[a] += 1

                best = max(allowed, key=lambda a: (scores[a], -len(a)))
                return best

            # Prefer whatever the model wrote as the title, then map to an allowed
            # market-equivalent title list.
            title_part = header0
            if " at " in header0.lower():
                title_part = header0[: header0.lower().rfind(" at ")]
            market = _sanitize_market_title(title_part)

            market = _pick_allowed_market_title(
                str(job.get("title", "") or ""),
                str(job.get("full_description", "") or ""),
                official_title,
                market,
            )

            # Enforce: not empty; do not copy target title verbatim.
            target_title = str(job.get("title", "") or "")
            if not market:
                market = official_title

            # If the model copied the target title exactly, soften it by removing
            # seniority qualifiers and forcing an Analyst-type market title.
            if _normalize_title(market) == _normalize_title(target_title):
                market = re.sub(r"\b(junior|jr|senior|sr|lead|principal|staff)\b", "", market, flags=re.IGNORECASE)
                market = re.sub(r"\s+", " ", market).strip()

            market = _sanitize_market_title(market)
            exp[0]["header"] = f"{market} at {current_company}"
        except Exception:
            return

    def _soften_banned_phrases(data_obj: dict) -> None:
        """Rewrite a few chronic banned phrases to reduce retry churn."""

        repl: list[tuple[str, str]] = [
            (r"\bextensive experience\b", "experience"),
            (r"\bscalable solutions\b", "scalable systems"),
            (r"\brobust\b", "reliable"),
        ]

        def _fix(s: str) -> str:
            out = str(s)
            for pat, rep in repl:
                out = re.sub(pat, rep, out, flags=re.IGNORECASE)
            return out

        try:
            if isinstance(data_obj.get("summary"), str):
                data_obj["summary"] = _fix(data_obj["summary"])

            core = data_obj.get("core_skills")
            if isinstance(core, list):
                data_obj["core_skills"] = [_fix(str(x)) for x in core]

            skills = data_obj.get("skills")
            if isinstance(skills, dict):
                for k, v in list(skills.items()):
                    skills[k] = _fix(str(v))

            exp = data_obj.get("experience")
            if isinstance(exp, list):
                for e in exp:
                    if not isinstance(e, dict):
                        continue
                    if isinstance(e.get("header"), str):
                        e["header"] = _fix(e["header"])
                    if isinstance(e.get("subtitle"), str):
                        e["subtitle"] = _fix(e["subtitle"])
                    bs = e.get("bullets")
                    if isinstance(bs, list):
                        e["bullets"] = [_fix(str(b)) for b in bs]

            projs = data_obj.get("projects")
            if isinstance(projs, list):
                for p in projs:
                    if not isinstance(p, dict):
                        continue
                    if isinstance(p.get("header"), str):
                        p["header"] = _fix(p["header"])
                    if isinstance(p.get("subtitle"), str):
                        p["subtitle"] = _fix(p["subtitle"])
                    bs = p.get("bullets")
                    if isinstance(bs, list):
                        p["bullets"] = [_fix(str(b)) for b in bs]
        except Exception:
            return

    avoid_notes: list[str] = []
    tailored = ""
    tailor_prompt_base = _build_tailor_prompt(
        profile,
        role_pack_instructions=str(role_pack.get("instructions") or ""),
        adaptive_budget_hint=str((adaptive or {}).get("hint") or ""),
        fact_library_text=fact_block,
        jd_targets_text=jd_targets_text,
    )

    for attempt in range(max_retries + 1):
        report["attempts"] = attempt + 1

        prompt = tailor_prompt_base
        if avoid_notes:
            prompt += "\n\n## AVOID THESE ISSUES (from previous attempt):\n" + "\n".join(
                f"- {n}" for n in avoid_notes[-8:]
            )
        if prompt_keywords:
            prompt += "\n\n## KEYWORD BANK (use only if truthful; do not stuff):\n"
            prompt += ", ".join(prompt_keywords)

        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    f"ORIGINAL RESUME:\n{resume_text}\n\n---\n\nTARGET JOB:\n{job_text}\n\n"
                    "Return ONLY valid JSON. Ensure every experience/project bullet cites fact IDs."
                ),
            },
        ]

        candidates: list[dict] = []
        llm_errors: list[str] = []

        for idx, temp in enumerate(draft_temps, start=1):
            raw = ""
            data: dict | None = None
            parse_error: str | None = None
            validation = {"passed": False, "errors": ["not_validated"], "warnings": []}
            citation_check = {"passed": False, "errors": ["not_validated"], "citations": {}}
            quant_check = {"passed": False, "errors": ["not_validated"], "missing": []}
            coverage = {"ratio": 0.0, "covered": [], "missing": jd_targets}
            tailored_candidate = ""

            try:
                raw = chat_json(messages, max_tokens=2300, temperature=float(temp))
            except Exception as e:
                err = f"LLM error (draft {idx}): {e}"
                llm_errors.append(err)
                candidates.append(
                    {
                        "draft": idx,
                        "score": -999.0,
                        "data": None,
                        "tailored": "",
                        "validation": validation,
                        "citation": citation_check,
                        "quant": quant_check,
                        "coverage": coverage,
                        "errors": [err],
                        "warnings": [],
                    }
                )
                continue

            try:
                data = extract_json(raw)
            except ValueError as e:
                parse_error = str(e)

            if isinstance(data, dict):
                _align_current_role_header(data)
                _soften_banned_phrases(data)

                # Keep the strict anti-invention guard for specific chronic tools.
                try:
                    base_lower = resume_text.lower()

                    def _data_text_blob(d: dict) -> str:
                        parts: list[str] = [str(d.get("summary", "") or "")]
                        core = d.get("core_skills") or []
                        if isinstance(core, list):
                            parts.extend(str(x) for x in core)
                        skills = d.get("skills") or {}
                        if isinstance(skills, dict):
                            parts.extend(str(v) for v in skills.values())
                        for e in d.get("experience") or []:
                            if isinstance(e, dict):
                                parts.append(str(e.get("subtitle", "") or ""))
                                for b in e.get("bullets") or []:
                                    parts.append(str(b))
                        for p in d.get("projects") or []:
                            if isinstance(p, dict):
                                parts.append(str(p.get("subtitle", "") or ""))
                                for b in p.get("bullets") or []:
                                    parts.append(str(b))
                        return "\n".join(parts)

                    gen_blob = _data_text_blob(data).lower()
                    forbidden = [
                        ("Power Automate", r"\bpower\s+automate\b"),
                        ("Copilot Studio", r"\bcopilot\s+studio\b"),
                        ("Power Apps", r"\bpower\s+apps\b"),
                        ("ALM", r"\balm\b|application\s+lifecycle\s+management"),
                        ("API integration", r"\bapi\s+integration(s)?\b"),
                    ]
                    injected: list[str] = []
                    for label, pat in forbidden:
                        if re.search(pat, gen_blob, flags=re.IGNORECASE) and not re.search(
                            pat, base_lower, flags=re.IGNORECASE
                        ):
                            injected.append(label)
                    if injected:
                        parse_error = "Invented tools not in base resume: " + ", ".join(sorted(set(injected)))
                except Exception:
                    pass

            if parse_error:
                candidates.append(
                    {
                        "draft": idx,
                        "score": -900.0,
                        "data": None,
                        "tailored": "",
                        "validation": validation,
                        "citation": citation_check,
                        "quant": quant_check,
                        "coverage": coverage,
                        "errors": [parse_error],
                        "warnings": [],
                    }
                )
                continue

            assert isinstance(data, dict)
            citation_check = validate_fact_citations(data, fact_ids)
            validation = validate_json_fields(data, profile, runtime_rules=runtime_rules)
            citation_gate_ok = bool(citation_check.get("passed") or not strict_evidence)
            if validation.get("passed") and citation_gate_ok:
                tailored_candidate = assemble_resume_text(data, profile)
                coverage = score_jd_coverage(tailored_candidate, jd_targets, jd_synonyms)
                quant_check = check_quant_consistency(data, evidence_numbers)
            else:
                coverage = {"ratio": 0.0, "covered": [], "missing": jd_targets}
                quant_check = {"passed": False, "errors": ["skipped_due_to_validation"], "missing": []}

            rank = rank_candidate(
                text=tailored_candidate,
                coverage_ratio=float(coverage.get("ratio") or 0.0),
                validator_errors=list(validation.get("errors") or []),
                validator_warnings=list(validation.get("warnings") or []),
                citation_errors=list(citation_check.get("errors") or []),
                quant_errors=list(quant_check.get("errors") or []),
            )

            candidates.append(
                {
                    "draft": idx,
                    "score": float(rank.get("score") or 0.0),
                    "rank": rank,
                    "data": data,
                    "tailored": tailored_candidate,
                    "validation": validation,
                    "citation": citation_check,
                    "quant": quant_check,
                    "coverage": coverage,
                    "errors": list(validation.get("errors") or []) + list(citation_check.get("errors") or []),
                    "warnings": list(validation.get("warnings") or []),
                }
            )

        if not candidates:
            avoid_notes.append("No candidate drafts produced.")
            if attempt < max_retries:
                continue
            report["status"] = "error"
            report["validator"] = {"passed": False, "errors": ["No candidate drafts produced"], "warnings": []}
            return tailored, report

        ranked = sorted(candidates, key=lambda c: float(c.get("score") or -9999.0), reverse=True)
        report.setdefault("draft_ranking", []).append(
            {
                "attempt": attempt + 1,
                "drafts": [
                    {
                        "draft": int(c.get("draft") or 0),
                        "score": float(c.get("score") or 0.0),
                        "coverage": float((c.get("coverage") or {}).get("ratio") or 0.0),
                        "errors": list(c.get("errors") or []),
                        "warnings": list(c.get("warnings") or []),
                    }
                    for c in ranked
                ],
            }
        )

        best = ranked[0]
        best_data = best.get("data")
        if not isinstance(best_data, dict):
            notes = list(best.get("errors") or []) + llm_errors
            avoid_notes.extend(notes[:4] if notes else ["Draft parsing failed"])
            if attempt < max_retries:
                continue
            report["status"] = "failed_json"
            report["validator"] = {"passed": False, "errors": notes or ["Draft parsing failed"], "warnings": []}
            return tailored, report

        report["validator"] = best.get("validation")
        report["citation"] = best.get("citation")
        report["coverage"] = best.get("coverage")
        report["quant_check"] = best.get("quant")

        hard_errors: list[str] = []
        validation_errors = list((best.get("validation") or {}).get("errors") or [])
        if lenient_tailor:
            hard_errors.extend([e for e in validation_errors if _is_fatal_json_error(str(e))])
            soft_json = [str(e) for e in validation_errors if not _is_fatal_json_error(str(e))]
            if soft_json:
                report.setdefault("lenient_warnings", [])
                report["lenient_warnings"].extend(soft_json)
        else:
            hard_errors.extend(validation_errors)
        if strict_evidence:
            hard_errors.extend(list((best.get("citation") or {}).get("errors") or []))
            hard_errors.extend(list((best.get("quant") or {}).get("errors") or []))
        else:
            evidence_warnings: list[str] = []
            evidence_warnings.extend(list((best.get("citation") or {}).get("errors") or []))
            evidence_warnings.extend(list((best.get("quant") or {}).get("errors") or []))
            if evidence_warnings:
                report["evidence_warnings"] = evidence_warnings

        if hard_errors:
            avoid_notes.extend(hard_errors[:6])
            if attempt < max_retries:
                continue
            tailored = str(best.get("tailored") or "")
            if not tailored:
                try:
                    tailored = assemble_resume_text(best_data, profile)
                except Exception:
                    tailored = ""
            report["status"] = "failed_validation"
            vobj = report.get("validator")
            if not isinstance(vobj, dict):
                vobj = {"passed": False, "errors": [], "warnings": []}
                report["validator"] = vobj
            vobj["passed"] = False
            vobj["errors"] = hard_errors
            return tailored, report

        tailored = str(best.get("tailored") or "")
        if not tailored:
            tailored = assemble_resume_text(best_data, profile)

        # Refresh highlight keywords using the final selected draft.
        try:
            kw2 = build_keyword_bank(
                job_description=(job.get("full_description") or ""),
                profile=profile,
                resume_text=tailored,
            )
            if isinstance(kw2, dict) and isinstance(kw2.get("highlight_keywords"), list):
                highlight_keywords = [str(x).strip() for x in (kw2.get("highlight_keywords") or []) if str(x).strip()]
                report["keywords"] = highlight_keywords
                report.setdefault("keyword_bank", {})
                if isinstance(report.get("keyword_bank"), dict):
                    report["keyword_bank"]["highlight"] = highlight_keywords
        except Exception:
            pass

        tailored_lower = tailored.lower()
        found_banned = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", tailored_lower)]
        if found_banned:
            avoid_notes.append(f"Banned words: {', '.join(found_banned[:5])}")
            if attempt < max_retries:
                continue
            report["status"] = "failed_validation"
            vobj = report.get("validator")
            if not isinstance(vobj, dict):
                vobj = {"passed": False, "errors": [], "warnings": []}
                report["validator"] = vobj
            vobj["passed"] = False
            errs = vobj.get("errors")
            if not isinstance(errs, list):
                errs = []
                vobj["errors"] = errs
            errs.append(f"Banned words: {', '.join(found_banned[:5])}")
            return tailored, report

        full_validation = validate_tailored_resume(
            tailored,
            profile,
            original_text=resume_text,
            runtime_rules=runtime_rules,
        )
        report["full_validator"] = full_validation
        if not full_validation["passed"]:
            fv_errors = [str(e) for e in (full_validation.get("errors") or [])]
            if lenient_tailor:
                fatal_fv = [e for e in fv_errors if _is_fatal_full_validation_error(e)]
                nonfatal_fv = [e for e in fv_errors if e not in fatal_fv]
                if nonfatal_fv:
                    report.setdefault("lenient_warnings", [])
                    report["lenient_warnings"].extend(nonfatal_fv)
                if fatal_fv:
                    avoid_notes.extend(fatal_fv)
                    if attempt < max_retries:
                        continue
                    report["status"] = "failed_full_validation"
                    return tailored, report
            else:
                avoid_notes.extend(fv_errors)
                if attempt < max_retries:
                    continue
                report["status"] = "failed_full_validation"
                return tailored, report

        if not lenient_tailor:
            judge = judge_tailored_resume(resume_text, tailored, job.get("title", ""), profile)
            report["judge"] = judge
            if not judge["passed"]:
                avoid_notes.append(f"Judge rejected: {judge['issues']}")
                if attempt < max_retries:
                    continue
                report["status"] = "failed_judge"
                return tailored, report
        else:
            report["judge"] = {"passed": True, "verdict": "SKIPPED_IN_LENIENT_MODE", "issues": "", "raw": ""}

        report["status"] = "approved"
        report["keywords"] = highlight_keywords
        return tailored, report

    report["status"] = "exhausted_retries"
    return tailored, report


# ── Batch Entry Point ────────────────────────────────────────────────────


def run_tailoring(min_score: int = 7, limit: int = 0) -> dict:
    """Generate tailored resumes for high-scoring jobs.

    Args:
        min_score: Minimum fit_score to tailor for.
        limit: Maximum jobs to process.

    Returns:
        {"approved": int, "failed": int, "errors": int, "elapsed": float}
    """
    profile = load_profile()
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()
    selected_only = _selected_only_enabled()

    skip_titles = _load_skip_titles()

    jobs = get_jobs_by_stage(
        conn=conn,
        stage="pending_tailor",
        min_score=min_score,
        limit=limit,
        selected_only=selected_only,
    )

    if jobs and skip_titles:
        # Mark known-bad titles as skipped so they don't remain "pending".
        kept: list[dict] = []
        skipped = 0
        now = datetime.now(timezone.utc).isoformat()
        for j in jobs:
            if _matches_skip_titles(j.get("title"), skip_titles):
                conn.execute(
                    "UPDATE jobs SET apply_status=?, apply_error=?, last_attempted_at=? WHERE url=?",
                    ("skipped", "skip_titles", now, j.get("url")),
                )
                skipped += 1
            else:
                kept.append(j)
        conn.commit()
        if skipped:
            log.info("Skipped %d jobs due to skip_titles.", skipped)
        jobs = kept

    if not jobs:
        if selected_only:
            log.info("No selected untailored jobs with score >= %d.", min_score)
        else:
            log.info("No untailored jobs with score >= %d.", min_score)
        return {"approved": 0, "failed": 0, "errors": 0, "elapsed": 0.0}

    TAILORED_DIR.mkdir(parents=True, exist_ok=True)
    mode_label = "lenient" if _lenient_tailor_enabled(profile) else "strict"
    if selected_only:
        log.info("Tailoring resumes for %d selected jobs (score >= %d, mode=%s)...", len(jobs), min_score, mode_label)
    else:
        log.info("Tailoring resumes for %d jobs (score >= %d, mode=%s)...", len(jobs), min_score, mode_label)
    t0 = time.time()
    completed = 0
    results: list[dict] = []
    stats: dict[str, int] = {"approved": 0, "failed_validation": 0, "failed_judge": 0, "error": 0}

    for job in jobs:
        completed += 1
        try:
            tailored, report = tailor_resume(resume_text, job, profile)

            # Build filename prefix: username/name + role/site + unique URL hash.
            username = str(os.environ.get("APPLYPILOT_USER", "") or "").strip()
            stem = naming.cv_filename(profile.get("personal", {}), ext="txt", username=username, job=job)
            prefix = Path(stem).stem

            # Save tailored resume text
            txt_path = TAILORED_DIR / f"{prefix}.txt"
            txt_path.write_text(tailored, encoding="utf-8")

            # Save job description for traceability
            job_path = TAILORED_DIR / f"{prefix}_JOB.txt"
            job_desc = (
                f"Title: {job['title']}\n"
                f"Company: {job['site']}\n"
                f"Location: {job.get('location', 'N/A')}\n"
                f"Score: {job.get('fit_score', 'N/A')}\n"
                f"URL: {job['url']}\n\n"
                f"{job.get('full_description', '')}"
            )
            job_path.write_text(job_desc, encoding="utf-8")

            # Save validation report
            report_path = TAILORED_DIR / f"{prefix}_REPORT.json"
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

            # Generate PDF for approved resumes (best-effort)
            pdf_path = None
            if report["status"] == "approved":
                try:
                    from applypilot.scoring.pdf import convert_to_pdf

                    pdf_path = str(convert_to_pdf(txt_path))
                except Exception:
                    log.debug("PDF generation failed for %s", txt_path, exc_info=True)

            result = {
                "url": job["url"],
                "path": str(txt_path),
                "pdf_path": pdf_path,
                "title": job["title"],
                "site": job["site"],
                "status": report["status"],
                "attempts": report["attempts"],
                "failure_detail": "",
            }

            if result["status"] in ("failed_validation", "failed_full_validation"):
                full_v = report.get("full_validator") if isinstance(report, dict) else None
                base_v = report.get("validator") if isinstance(report, dict) else None
                err_list = []
                if isinstance(full_v, dict) and isinstance(full_v.get("errors"), list):
                    err_list = [str(e).strip() for e in full_v.get("errors", []) if str(e).strip()]
                if not err_list and isinstance(base_v, dict) and isinstance(base_v.get("errors"), list):
                    err_list = [str(e).strip() for e in base_v.get("errors", []) if str(e).strip()]
                if err_list:
                    result["failure_detail"] = "; ".join(err_list[:3])
            elif result["status"] == "failed_judge":
                judge = report.get("judge") if isinstance(report, dict) else None
                issues = judge.get("issues") if isinstance(judge, dict) else ""
                if issues:
                    result["failure_detail"] = str(issues)
        except Exception as e:
            result = {
                "url": job["url"],
                "title": job["title"],
                "site": job["site"],
                "status": "error",
                "attempts": 0,
                "path": None,
                "pdf_path": None,
                "failure_detail": str(e),
            }
            log.error("%d/%d [ERROR] %s -- %s", completed, len(jobs), job["title"][:40], e)

        results.append(result)
        stats[result.get("status", "error")] = stats.get(result.get("status", "error"), 0) + 1

        elapsed = time.time() - t0
        rate = completed / elapsed if elapsed > 0 else 0
        log.info(
            "%d/%d [%s] attempts=%s | %.1f jobs/min | %s",
            completed,
            len(jobs),
            result["status"].upper(),
            result.get("attempts", "?"),
            rate * 60,
            result["title"][:40],
        )
        if result.get("failure_detail") and result["status"] != "approved":
            log.info("  detail: %s", result["failure_detail"])

    # Persist to DB: increment attempt counter for ALL, save path only for approved
    now = datetime.now(timezone.utc).isoformat()
    for r in results:
        if r["status"] == "approved":
            conn.execute(
                "UPDATE jobs SET tailored_resume_path=?, tailored_at=?, "
                "tailor_attempts=COALESCE(tailor_attempts,0)+1 WHERE url=?",
                (r["path"], now, r["url"]),
            )
        else:
            conn.execute(
                "UPDATE jobs SET tailor_attempts=COALESCE(tailor_attempts,0)+1 WHERE url=?",
                (r["url"],),
            )
    conn.commit()

    elapsed = time.time() - t0
    log.info(
        "Tailoring done in %.1fs: %d approved, %d failed_validation, %d failed_judge, %d errors",
        elapsed,
        stats.get("approved", 0),
        stats.get("failed_validation", 0),
        stats.get("failed_judge", 0),
        stats.get("error", 0),
    )

    return {
        "approved": stats.get("approved", 0),
        "failed": stats.get("failed_validation", 0) + stats.get("failed_judge", 0),
        "errors": stats.get("error", 0),
        "elapsed": elapsed,
    }
