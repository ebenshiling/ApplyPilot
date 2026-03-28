"""Resume and cover letter validation: banned words, fabrication detection, structural checks.

All validation is profile-driven -- no hardcoded personal data. The validator receives
a profile dict (from applypilot.config.load_profile()) and validates against the user's
actual skills, companies, projects, and school.
"""

import re
import logging

from applypilot.config import load_search_config

log = logging.getLogger(__name__)


# ── Universal Constants (not personal data) ───────────────────────────────

BANNED_WORDS: list[str] = [
    "passionate",
    "dedicated",
    "committed to",
    "utilizing",
    "utilize",
    "harnessing",
    "spearheaded",
    "spearhead",
    "orchestrated",
    "championed",
    "pioneered",
    "robust",
    "adept at",
    "scalable solutions",
    "cutting-edge",
    "state-of-the-art",
    "best-in-class",
    "proven track record",
    "track record of success",
    "demonstrated ability",
    "strong communicator",
    "team player",
    "fast learner",
    "self-starter",
    "go-getter",
    "synergy",
    "cross-functional collaboration",
    "holistic",
    "transformative",
    "innovative solutions",
    "paradigm",
    "ecosystem",
    "proactive",
    "detail-oriented",
    "highly motivated",
    "seamless",
    "full lifecycle",
    "deep understanding",
    "extensive experience",
    "comprehensive knowledge",
    "thrives in",
    "excels at",
    "adept at",
    "well-versed in",
    "i am confident",
    "i believe",
    "i am excited",
    "plays a critical role",
    "instrumental in",
    "integral part of",
    "strong track record",
    "eager to",
    "eager",
    # Cover-letter-specific additions
    "this demonstrates",
    "this reflects",
    "i have experience with",
    "furthermore",
    "additionally",
    "moreover",
]

LLM_LEAK_PHRASES: list[str] = [
    "i am sorry",
    "i apologize",
    "i will try",
    "let me try",
    "i am at a loss",
    "i am truly sorry",
    "apologies for",
    "i keep fabricating",
    "i will have to admit",
    "one final attempt",
    "one last time",
    "if it fails again",
    "persistent errors",
    "i am having difficulty",
    "i made an error",
    "my mistake",
    "here is the corrected",
    "here is the revised",
    "here is the updated",
    "here is my",
    "below is the",
    "as requested",
    "note:",
    "disclaimer:",
    "important:",
    "i have rewritten",
    "i have removed",
    "i have fixed",
    "i have replaced",
    "i have updated",
    "i have corrected",
    "per your feedback",
    "based on your feedback",
    "as per the instructions",
    "the following resume",
    "the resume below",
    "the following cover letter",
    "the letter below",
]

# Known fabrication markers: completely unrelated tools/languages.
# Reasonable stretches (K8s, Terraform, Redis, Kafka etc.) are ALLOWED.
FABRICATION_WATCHLIST: set[str] = {
    # Languages with zero relation to the candidate's stack
    "c#",
    "c++",
    "golang",
    "rust",
    "ruby",
    "kotlin",
    "swift",
    "scala",
    "matlab",
    # Frameworks for wrong languages
    "spring",
    "django",
    "rails",
    "angular",
    "vue",
    "svelte",
    # Hard lies: certifications can't be stretched
    "certif",
    "certified",
    "pmp",
    "scrum master",
    "aws certified",
}

REQUIRED_SECTIONS: set[str] = {"SUMMARY", "TECHNICAL SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"}


# ── Helpers ───────────────────────────────────────────────────────────────


def _build_skills_set(profile: dict) -> set[str]:
    """Build the set of allowed skills from the profile's skills_boundary."""
    boundary = profile.get("skills_boundary", {})
    allowed: set[str] = set()
    for category in boundary.values():
        if isinstance(category, list):
            allowed.update(s.lower().strip() for s in category)
        elif isinstance(category, set):
            allowed.update(s.lower().strip() for s in category)
    return allowed


def _load_resume_validation(profile: dict, runtime_rules: dict | None = None) -> dict:
    """Load resume validation rules (configurable).

    Precedence:
    1) profile["resume_validation"]
    2) searches.yaml["resume_validation"]
    3) defaults (current behavior)
    """

    defaults = {
        "experience_bullets": {
            "most_recent": {"min": 3, "max": 6},
            "other": {"min": 2, "max": 4},
            "enforce_most_recent_max": False,
        },
        "project_bullets": {
            "min": 1,
            "max": 2,
        },
        "required_sections": {
            "projects": False,
            "education": True,
        },
        "bullet_lint": {
            "enabled": True,
            "min_words": 8,
            "error_phrases": [
                "responsible for",
                "worked on",
                "helped",
                "assisted",
            ],
        },
    }

    cfg = {}
    try:
        cfg = load_search_config() or {}
    except Exception:
        cfg = {}

    merged = dict(defaults)
    for src in (cfg.get("resume_validation") or {}, profile.get("resume_validation") or {}, runtime_rules or {}):
        if not isinstance(src, dict):
            continue
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = {**merged[k], **v}
            else:
                merged[k] = v

    # Defensive normalization
    if not isinstance(merged.get("experience_bullets"), dict):
        merged["experience_bullets"] = defaults["experience_bullets"]
    if not isinstance(merged.get("required_sections"), dict):
        merged["required_sections"] = defaults["required_sections"]
    if not isinstance(merged.get("project_bullets"), dict):
        merged["project_bullets"] = defaults["project_bullets"]
    if not isinstance(merged.get("bullet_lint"), dict):
        merged["bullet_lint"] = defaults["bullet_lint"]

    return merged


def _lint_bullet(bullet: str, allowed_skills: set[str], cfg: dict) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for a single bullet."""

    errors: list[str] = []
    warnings: list[str] = []

    s = str(bullet or "").strip()
    if not s:
        return errors, warnings

    s_l = s.lower()

    # Hard reject: low-signal boilerplate.
    for phrase in cfg.get("error_phrases") or []:
        p = str(phrase).strip().lower()
        if not p:
            continue
        if re.search(r"\b" + re.escape(p) + r"\b", s_l):
            errors.append(f"Low-signal bullet phrase: '{p}'")
            break

    # Minimum length.
    min_words = int(cfg.get("min_words") or 0)
    if min_words:
        wc = len([w for w in re.findall(r"[A-Za-z0-9%$]+", s) if w])
        if wc < min_words:
            warnings.append(f"Bullet is short ({wc} words)")

    # Heuristic: should have a concrete signal (number or tool) somewhere.
    has_number = bool(re.search(r"\b\d+\b|\d+%|\$\s*\d+", s))
    has_tool = False
    if allowed_skills:
        for sk in allowed_skills:
            if not sk or len(sk) < 3:
                continue
            # Treat multi-word skills as phrases; single words as boundaries.
            if " " in sk:
                if re.search(r"\b" + re.escape(sk) + r"\b", s_l):
                    has_tool = True
                    break
            else:
                if re.search(r"\b" + re.escape(sk) + r"\b", s_l):
                    has_tool = True
                    break

    if not (has_number or has_tool):
        warnings.append("Bullet lacks a clear metric/tool signal")

    return errors, warnings


def sanitize_text(text: str) -> str:
    """Auto-fix common LLM output issues instead of rejecting."""
    text = text.replace(" \u2014 ", ", ").replace("\u2014", ", ")  # em dash -> comma
    text = text.replace("\u2013", "-")  # en dash -> hyphen
    text = text.replace("\u201c", '"').replace("\u201d", '"')  # smart double quotes
    text = text.replace("\u2018", "'").replace("\u2019", "'")  # smart single quotes
    return text.strip()


# ── JSON Field Validation ─────────────────────────────────────────────────


def validate_json_fields(data: dict, profile: dict, runtime_rules: dict | None = None) -> dict:
    """Validate individual JSON fields from an LLM-generated tailored resume.

    Args:
        data: Parsed JSON from the LLM (title, summary, skills, experience, projects, education).
        profile: User profile dict from load_profile().

    Returns:
        {"passed": bool, "errors": list[str], "warnings": list[str]}
    """
    errors: list[str] = []
    warnings: list[str] = []

    rules = _load_resume_validation(profile, runtime_rules=runtime_rules)
    allowed_skills = _build_skills_set(profile)
    bullet_cfg = rules.get("bullet_lint") or {}

    # Required keys
    # NOTE: `projects` is allowed to be empty/missing (section is optional in the prompt).
    required_keys = ("title", "summary", "skills", "experience", "education", "core_skills")
    for key in required_keys:
        if key not in data:
            errors.append(f"Missing required field: {key}")
    if errors:
        return {"passed": False, "errors": errors, "warnings": warnings}

    # Defensive defaults for optional keys.
    # Older/failing generations sometimes omit these fields.
    if "projects" not in data or data.get("projects") is None:
        data["projects"] = []

    # Collect all text for bulk checks
    all_text_parts: list[str] = [data["summary"]]

    # Basic content checks to avoid "blank" resumes that still pass key presence.
    summary = str(data.get("summary", "") or "")
    if len(summary.strip()) < 40:
        errors.append("Summary too short/empty.")

    skills_obj = data.get("skills")
    if not isinstance(skills_obj, dict) or not any(str(v).strip() for v in skills_obj.values()):
        errors.append("Skills section empty.")

    # Skills: check for fabrication
    if isinstance(data["skills"], dict):
        skills_text = " ".join(str(v) for v in data["skills"].values()).lower()
        for fake in FABRICATION_WATCHLIST:
            if len(fake) <= 2:
                continue
            if fake in skills_text:
                errors.append(f"Fabricated skill: '{fake}'")

    # Experience: avoid hallucinated companies. We prefer (but do not require)
    # that all preserved companies appear, since 1-page resumes may omit older roles.
    resume_facts = profile.get("resume_facts", {})
    preserved_companies = resume_facts.get("preserved_companies", [])

    if isinstance(data["experience"], list):
        # Ensure we keep a minimally complete work history.
        if len(data["experience"]) < 2:
            errors.append("Too few experience entries (need at least 2).")

        # Ensure the current company appears somewhere in Experience.
        current_company = str(profile.get("experience", {}).get("current_company", "") or "").strip()
        if current_company:
            headers = "\n".join(str(e.get("header", "") or "") for e in data["experience"]).lower()
            if current_company.lower() not in headers:
                errors.append(f"Current company '{current_company}' missing from experience")

        preserved_lower = [str(c).lower() for c in preserved_companies if c]

        # If we have an allowlist, ensure each experience header mentions a known company.
        if preserved_lower:
            for e in data["experience"]:
                header = str(e.get("header", "") or "")
                header_l = header.lower()
                if not header.strip():
                    errors.append("Experience entry missing header")
                    continue
                if not any(c in header_l for c in preserved_lower):
                    warnings.append(f"Unrecognized company in experience header: '{header}'")

            # Soft check: missing preserved companies is a warning, not an error.
            for company in preserved_companies:
                has_company = any(str(company).lower() in str(e.get("header", "")).lower() for e in data["experience"])
                if not has_company:
                    warnings.append(f"Company '{company}' not present in experience")

        for entry in data["experience"]:
            bullets = entry.get("bullets", []) or []
            if isinstance(bullets, list) and not any(str(b).strip() for b in bullets):
                errors.append("Experience entry has no bullets")
            for b in entry.get("bullets", []):
                all_text_parts.append(b)

                if bullet_cfg.get("enabled", True):
                    e2, w2 = _lint_bullet(b, allowed_skills, bullet_cfg)
                    errors.extend(e2)
                    warnings.extend(w2)

        # Bullet allocation rules (configurable):
        # defaults preserve existing UK CV preset behavior.
        if data["experience"]:
            try:
                counts: list[int] = []
                for e in data["experience"]:
                    bs = e.get("bullets", []) or []
                    counts.append(len([b for b in bs if str(b).strip()]))

                exp_cfg = rules.get("experience_bullets") or {}
                most_cfg = (exp_cfg.get("most_recent") or {}) if isinstance(exp_cfg, dict) else {}
                other_cfg = (exp_cfg.get("other") or {}) if isinstance(exp_cfg, dict) else {}
                mr_min = int((most_cfg.get("min") or 0) if isinstance(most_cfg, dict) else 0)
                mr_max = int((most_cfg.get("max") or 0) if isinstance(most_cfg, dict) else 0)
                ot_min = int((other_cfg.get("min") or 0) if isinstance(other_cfg, dict) else 0)
                ot_max = int((other_cfg.get("max") or 0) if isinstance(other_cfg, dict) else 0)
                enforce_mr_max = bool(exp_cfg.get("enforce_most_recent_max", True))

                most_recent = counts[0]
                if mr_min and most_recent < mr_min:
                    errors.append(f"Most recent role must have >= {mr_min} bullets")
                if mr_max and most_recent > mr_max:
                    errors.append(f"Most recent role must have <= {mr_max} bullets")

                for c in counts[1:]:
                    if ot_min and c < ot_min:
                        errors.append(f"Non-current roles must have >= {ot_min} bullets")
                    if ot_max and c > ot_max:
                        errors.append(f"Non-current roles must have <= {ot_max} bullets")

                if enforce_mr_max and any(c > most_recent for c in counts[1:]):
                    errors.append("Most recent role must have at least as many bullets as any previous role")
            except Exception:
                errors.append("Could not validate experience bullet allocation")

    # Core skills list should exist and be reasonably sized.
    core = data.get("core_skills")
    if not isinstance(core, list) or len([x for x in core if str(x).strip()]) < 6:
        errors.append("Core technical skills list too short/invalid")

    # Projects: collect bullets (optional)
    projects_obj = data.get("projects")
    if isinstance(projects_obj, list):
        for entry in projects_obj:
            for b in entry.get("bullets", []):
                all_text_parts.append(b)

                if bullet_cfg.get("enabled", True):
                    e2, w2 = _lint_bullet(b, allowed_skills, bullet_cfg)
                    errors.extend(e2)
                    warnings.extend(w2)

        # Projects should be concise (configurable)
        proj_cfg = rules.get("project_bullets") or {}
        p_min = int((proj_cfg.get("min") or 0) if isinstance(proj_cfg, dict) else 0)
        p_max = int((proj_cfg.get("max") or 0) if isinstance(proj_cfg, dict) else 0)
        for entry in projects_obj:
            bullets = entry.get("bullets", []) or []
            c = len([b for b in bullets if str(b).strip()])
            if c and p_min and c < p_min:
                errors.append(f"Project entries must have >= {p_min} bullets")
            if c and p_max and c > p_max:
                errors.append(f"Project entries must have <= {p_max} bullets")

    # Education: preserved school must be present
    preserved_school = resume_facts.get("preserved_school", "")
    if preserved_school:
        edu = str(data.get("education", ""))
        if preserved_school.lower() not in edu.lower():
            errors.append(f"Education '{preserved_school}' missing")

    # Bulk checks on all text (word-boundary matching)
    all_text = " ".join(all_text_parts).lower()

    found_banned = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", all_text)]
    if found_banned:
        errors.append(f"Banned words: {', '.join(found_banned[:3])}")

    found_leaks = [p for p in LLM_LEAK_PHRASES if p in all_text]
    if found_leaks:
        errors.append(f"LLM self-talk: '{found_leaks[0]}'")

    return {"passed": len(errors) == 0, "errors": errors, "warnings": warnings}


# ── Full Resume Text Validation ───────────────────────────────────────────


def validate_tailored_resume(
    text: str,
    profile: dict,
    original_text: str = "",
    runtime_rules: dict | None = None,
) -> dict:
    """Programmatic validation of a tailored resume against the user's profile.

    Args:
        text: The tailored resume text to validate.
        profile: User profile dict from load_profile().
        original_text: The original base resume text (for fabrication comparison).

    Returns:
        {"passed": bool, "errors": list[str], "warnings": list[str]}
    """
    errors: list[str] = []
    warnings: list[str] = []
    text_lower = text.lower()

    personal = profile.get("personal", {})
    resume_facts = profile.get("resume_facts", {})

    # 1. Check required sections exist (flexible matching)
    section_variants_required: dict[str, list[str]] = {
        "SUMMARY": ["summary", "professional summary", "profile"],
        "CORE TECHNICAL SKILLS": ["core technical skills", "technical skills", "skills", "tech stack", "core skills"],
        "PROFESSIONAL EXPERIENCE": ["professional experience", "experience", "work experience"],
        "PROJECTS": ["projects", "personal projects", "key projects", "selected projects"],
        "EDUCATION": ["education", "academic background"],
    }

    rules = _load_resume_validation(profile, runtime_rules=runtime_rules)
    required_cfg = rules.get("required_sections") or {}
    require_projects = bool(required_cfg.get("projects", False))
    require_education = bool(required_cfg.get("education", True))

    for section, variants in section_variants_required.items():
        if section == "PROJECTS" and not require_projects:
            continue
        if section == "EDUCATION" and not require_education:
            continue
        if not any(v in text_lower for v in variants):
            errors.append(f"Missing required section: {section} (or variant)")

    # Optional sections: only require them if profile config provides content.
    resume_sections = profile.get("resume_sections", {}) or {}
    if isinstance(resume_sections.get("certifications"), list) and any(
        str(x).strip() for x in resume_sections.get("certifications", [])
    ):
        if "certifications" not in text_lower:
            errors.append("Missing required section: CERTIFICATIONS")
    if isinstance(resume_sections.get("technical_environment"), list) and any(
        str(x).strip() for x in resume_sections.get("technical_environment", [])
    ):
        if "technical environment" not in text_lower:
            errors.append("Missing required section: TECHNICAL ENVIRONMENT")

    # 2. Check name preserved (warn, don't error -- we can inject it)
    full_name = personal.get("full_name", "")
    if full_name and full_name.lower() not in text_lower:
        warnings.append(f"Name '{full_name}' missing -- will be injected")

    # 3. Check companies preserved (warning only). 1-page resumes may omit
    # older roles; we enforce the current company separately.
    for company in resume_facts.get("preserved_companies", []):
        if company.lower() not in text_lower:
            warnings.append(f"Company '{company}' missing")

    # 4. Check projects preserved
    for project in resume_facts.get("preserved_projects", []):
        if project.lower() not in text_lower:
            warnings.append(f"Project '{project}' not found -- may have been renamed")

    # 5. Check school preserved
    preserved_school = resume_facts.get("preserved_school", "")
    if preserved_school and preserved_school.lower() not in text_lower:
        errors.append(f"Education '{preserved_school}' missing")

    # 6. Check contact info preserved (warn, don't error -- we can inject)
    email = personal.get("email", "")
    phone = personal.get("phone", "")
    if email and email.lower() not in text_lower:
        warnings.append("Email missing -- will be injected")
    if phone and phone not in text:
        warnings.append("Phone missing -- will be injected")

    # 7. Scan skills section for fabricated tools
    skills_start = text_lower.find("core technical skills")
    if skills_start == -1:
        skills_start = text_lower.find("technical skills")

    skills_end = text_lower.find("professional experience", skills_start) if skills_start != -1 else -1
    if skills_end == -1 and skills_start != -1:
        skills_end = text_lower.find("experience", skills_start)
    if skills_start != -1 and skills_end != -1:
        skills_block = text_lower[skills_start:skills_end]
        for fake in FABRICATION_WATCHLIST:
            if len(fake) <= 2:
                continue
            if fake in skills_block:
                errors.append(f"FABRICATED SKILL in Technical Skills: '{fake}'")

    # 8. Scan full document for fabrication watchlist items not in original
    if original_text:
        original_lower = original_text.lower()
        for fake in FABRICATION_WATCHLIST:
            if len(fake) <= 2:
                continue
            if fake in text_lower and fake not in original_lower:
                warnings.append(f"New tool/skill appeared: '{fake}' (not in original)")

    # 9. Em dashes (should be auto-fixed by sanitize_text, but safety net)
    if "\u2014" in text or "\u2013" in text:
        errors.append("Contains em dash or en dash.")

    # 10. Banned words (word-boundary matching)
    found_banned = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", text_lower)]
    if found_banned:
        errors.append(f"Banned words: {', '.join(found_banned[:5])}")

    # 11. LLM self-talk leak detection
    found_leaks = [p for p in LLM_LEAK_PHRASES if p in text_lower]
    if found_leaks:
        errors.append(f"LLM self-talk: '{found_leaks[0]}'")

    # 12. Duplicate section detection
    for section_name in ["summary", "experience", "education", "projects"]:
        count = text_lower.count(f"\n{section_name}\n") + text_lower.count(f"\n{section_name} \n")
        if text_lower.startswith(f"{section_name}\n"):
            count += 1
        if count > 1:
            errors.append(f"Section '{section_name}' appears {count} times.")

    return {
        "passed": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


# ── Cover Letter Validation ──────────────────────────────────────────────


def validate_cover_letter(text: str, profile: dict | None = None, resume_text: str = "") -> dict:
    """Programmatic validation of a cover letter.

    Args:
        text: The cover letter text to validate.

    Returns:
        {"passed": bool, "errors": list[str]}
    """
    errors: list[str] = []
    text_lower = text.lower()

    # 1. Em dashes
    if "\u2014" in text or "\u2013" in text:
        errors.append("Contains em dash or en dash.")

    # 2. Banned words (word-boundary matching)
    found = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", text_lower)]
    if found:
        errors.append(f"Banned words: {', '.join(found[:5])}")

    # 3. Too long
    words = len(text.split())
    if words > 250:
        errors.append(f"Too long ({words} words). Max 250.")

    # 3b. Too short (these look empty in PDFs)
    if words < 120:
        errors.append(f"Too short ({words} words). Min 120.")

    # 3c. Must have at least 3 paragraphs (separated by blank lines)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if len(paragraphs) < 3:
        errors.append(f"Too few paragraphs ({len(paragraphs)}). Need at least 3.")

    # 4. LLM self-talk
    found_leaks = [p for p in LLM_LEAK_PHRASES if p in text_lower]
    if found_leaks:
        errors.append(f"LLM self-talk: '{found_leaks[0]}'")

    # 5. Must start with "Dear"
    stripped = text.strip()
    if not stripped.lower().startswith("dear"):
        errors.append("Must start with 'Dear Hiring Manager,'")

    # 6. Must include a sign-off line (closing word + a name line)
    non_empty = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    tail = "\n".join(non_empty[-6:]).lower()
    has_closing_word = any(k in tail for k in ["sincerely", "best", "regards", "thank you"])
    has_name_line = False
    if non_empty:
        last = non_empty[-1]
        # Heuristic: at least two words, contains letters, not ending with comma.
        if len(last.split()) >= 2 and re.search(r"[A-Za-z]", last) and not last.endswith(","):
            has_name_line = True

    if not (has_closing_word and has_name_line):
        errors.append("Missing closing/sign-off (e.g. 'Best,' then name)")

    # Optional: consistency guard against the resume.
    if resume_text and profile:
        c = validate_cover_letter_consistency(text, resume_text, profile)
        errors.extend(c.get("errors") or [])

    return {"passed": len(errors) == 0, "errors": errors}


def validate_cover_letter_consistency(text: str, resume_text: str, profile: dict) -> dict:
    """Guard against claims/tools not present in the resume.

    This intentionally errs on the side of preventing new tool claims.
    """

    errors: list[str] = []
    t = text or ""
    r = resume_text or ""
    if not t.strip() or not r.strip():
        return {"passed": True, "errors": []}

    t_l = t.lower()
    r_l = r.lower()
    allowed = _build_skills_set(profile)

    def _is_toolish_skill(sk: str) -> bool:
        """Heuristic: treat vendor/tech terms as "tools"; ignore process phrases.

        This guard exists to prevent brand-new tooling claims (e.g., Jira, ServiceNow)
        from appearing in a cover letter when they are not present anywhere in the
        candidate materials.

        Process phrases like "incident resolution" or "root cause analysis" should
        not fail this check.
        """

        s = (sk or "").strip().lower()
        if not s or len(s) < 3:
            return False
        # Avoid treating long capability sentences as "tools".
        if len(s.split()) > 5:
            return False
        if any(ch.isdigit() for ch in s):
            return True
        vendor_tokens = (
            "microsoft",
            "entra",
            "azure",
            "active directory",
            "sharepoint",
            "onedrive",
            "teams",
            "outlook",
            "power bi",
            "tableau",
            "postgres",
            "postgre",
            "windows",
            "macos",
            "dns",
            "vpn",
            "tcp/ip",
            "wifi",
            "git",
            "sql",
            "python",
            "vba",
            "pandas",
            "numpy",
            "intune",
            "jira",
            "servicenow",
        )
        if any(tok in s for tok in vendor_tokens):
            return True
        # Single-token skills (e.g. "excel") are usually tool/tech keywords.
        if len(s.split()) == 1:
            return True
        return False

    # If the letter mentions an allowed tool, ensure the resume also mentions it.
    mentioned_missing: list[str] = []
    for sk in sorted(allowed):
        if not sk or len(sk) < 3:
            continue
        if not _is_toolish_skill(sk):
            continue
        pat = r"\b" + re.escape(sk) + r"\b"
        if re.search(pat, t_l) and not re.search(pat, r_l):
            mentioned_missing.append(sk)

    if mentioned_missing:
        # Cap to keep retry notes short.
        items = ", ".join(mentioned_missing[:6])
        errors.append(f"Cover letter mentions tools not in resume: {items}")

    # Numbers should come from the resume.
    # Only enforce for 3+ digit numbers to avoid flagging common text like 24/7.
    nums = re.findall(r"\b\d{3,}\b", t)
    if nums:
        missing = [n for n in nums if n not in r]
        if missing:
            errors.append("Cover letter uses numbers not present in resume")

    return {"passed": len(errors) == 0, "errors": errors}
