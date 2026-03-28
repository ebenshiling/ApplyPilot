"""Cover letter generation with stronger deterministic scaffolding.

Uses profile and routed resume evidence to build role-aware cover letters with
less prompt randomness and stronger paragraph guidance.
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
from applypilot.role_routing import route_resume_for_job
from applypilot.database import get_connection, get_jobs_by_stage
from applypilot.llm import get_client
from applypilot.scoring.validator import (
    BANNED_WORDS,
    sanitize_text,
    validate_cover_letter,
)
from applypilot.scoring.keywords import build_keyword_bank
from applypilot.scoring.tailor_strategy import extract_job_responsibilities

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5  # max cross-run retries before giving up


_ROLE_LETTER_PACKS: dict[str, dict[str, object]] = {
    "it_support": {
        "keywords": ("it support", "service desk", "helpdesk", "desktop support", "microsoft 365", "entra id"),
        "opening": "Open by stating direct alignment with first-line and second-line IT support, then mention higher education/commercial support context.",
        "body_focus": "Use University of Derby and current SIKE support work to prove user support, onboarding, Microsoft 365, Entra ID, MFA, device, and service desk capability.",
        "skills_focus": "Microsoft 365, Entra ID, Windows, macOS, MFA, DNS, VPN, service desk, ticket handling, incident escalation.",
        "motivation": "Close on reliable IT support, user service, and continued technical growth.",
    },
    "application_support": {
        "keywords": (
            "application support",
            "production support",
            "application support engineer",
            "incident management",
            "monitoring",
            "sql",
        ),
        "opening": "Open by stating direct alignment with application support, production systems, and incident resolution.",
        "body_focus": "Use SIKE production application support first, then support experience from University of Derby or NHIA to reinforce troubleshooting, monitoring, SQL, APIs, and continuity.",
        "skills_focus": "incident management, log analysis, SQL, PostgreSQL, APIs, monitoring, issue resolution, service continuity.",
        "motivation": "Close on stable service delivery, application reliability, and supporting business-critical platforms.",
    },
    "technical_systems": {
        "keywords": (
            "technical support engineer",
            "systems support analyst",
            "technical systems analyst",
            "api",
            "integration",
            "saas",
        ),
        "opening": "Open by stating alignment with systems analysis, technical support, and troubleshooting business-critical platforms.",
        "body_focus": "Use SIKE systems/application investigations first, then University of Derby or NHIA examples to show incident investigation, workflow analysis, API/integration awareness, and stakeholder support.",
        "skills_focus": "systems troubleshooting, SQL, APIs, integration support, root cause analysis, workflows, stakeholder communication.",
        "motivation": "Close on improving systems, supporting operational outcomes, and contributing to technical problem-solving.",
    },
    "public_it_support": {
        "keywords": ("nhs", "trust", "university", "council", "public sector", "service desk", "it support"),
        "opening": "Open by stating direct alignment with IT support in structured public-facing environments.",
        "body_focus": "Use University of Derby support experience first, then current SIKE or NHIA examples to show support for large user groups, onboarding, Microsoft 365, and service continuity.",
        "skills_focus": "Microsoft 365, Entra ID, user support, onboarding, ticket handling, service desk, incident escalation.",
        "motivation": "Close on reliable user support, public service delivery, and contributing to a structured support team.",
    },
    "public_application_support": {
        "keywords": (
            "nhs",
            "trust",
            "university",
            "council",
            "public sector",
            "application support",
            "production support",
        ),
        "opening": "Open by stating direct alignment with application support in public-facing or service-critical environments.",
        "body_focus": "Use SIKE production support first, then University of Derby or NHIA examples to show incident handling, access support, and continuity in structured organisations.",
        "skills_focus": "incident management, application troubleshooting, SQL, monitoring, user access, Microsoft 365, service continuity.",
        "motivation": "Close on supporting reliable public-facing services and helping teams maintain stable operational systems.",
    },
    "commercial_technical_support": {
        "keywords": ("saas", "product", "platform", "customer", "commercial", "technical support engineer", "api"),
        "opening": "Open by stating direct alignment with technical support for customer-facing or platform-based systems.",
        "body_focus": "Use SIKE platform and investigation work first, then reinforce with broader support experience that shows troubleshooting, incident response, and operational reliability.",
        "skills_focus": "technical troubleshooting, APIs, integration support, SQL, SaaS-style platforms, incident triage, root cause analysis.",
        "motivation": "Close on helping commercial teams deliver reliable technical support and stronger customer outcomes.",
    },
    "data_reporting": {
        "keywords": (
            "reporting analyst",
            "data analyst",
            "insight analyst",
            "mi analyst",
            "kpi",
            "power bi",
            "reporting",
        ),
        "opening": "Open by stating direct fit for reporting analyst work, naming reporting, KPI analysis, and turning operational or financial data into decisions. Avoid generic wording like 'aligns closely with this opportunity'.",
        "body_focus": "Use the strongest reporting and dashboard evidence from SIKE first, then add one earlier analytics/reporting example from Derby, NHIA, or ECG.",
        "skills_focus": "Power BI, Excel, SQL, reporting, KPI tracking, data validation, dashboard development, stakeholder reporting.",
        "motivation": "Close on one concrete employer detail from the JD, such as financial reporting ownership, KPI visibility, or performance analysis. Avoid generic praise like 'forward-thinking team' or 'supportive team'.",
    },
    "public_reporting": {
        "keywords": (
            "nhs",
            "trust",
            "healthcare analytics",
            "reporting analyst",
            "analysis and reporting",
            "power bi",
            "insight",
        ),
        "opening": "Open by stating direct fit for reporting and analytical support in healthcare or public-sector settings, using the real team or function name when present.",
        "body_focus": "Use healthcare or public-sector-style reporting evidence first, then reinforce with Power BI, SQL, validation, and stakeholder reporting examples from other roles.",
        "skills_focus": "Power BI, SQL, reporting, healthcare analytics, data quality, stakeholder reporting, ad-hoc analysis, dashboard development.",
        "motivation": "Close on one specific NHS or team detail from the JD, such as Healthcare Analytics, cancer reporting, or the EPR programme, and the contribution you want to make. Avoid generic praise like 'supportive team' or 'impactful team'.",
    },
}


_ROLE_LETTER_FALLBACK = {
    "opening": "Open by stating direct alignment with the role and the closest proven area of experience.",
    "body_focus": "Use the most relevant current role evidence first, then one prior role that supports the same type of work.",
    "skills_focus": "Mention only skills clearly present in the routed resume and directly relevant to the JD.",
    "motivation": "Close on the team's work and how your experience supports their needs.",
}


_COVER_BANNED_SOFT_PHRASES: tuple[str, ...] = (
    "robust",
    "dedicated",
    "adept at",
)

_COVER_REWRITE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bI have experience with\b", "My experience includes"),
    (r"\bI have experience in\b", "My work includes"),
    (r"\bI believe\b", "I know"),
    (r"\bI am particularly interested in this opportunity at\b", "I am drawn to"),
    (r"\bI am interested in this opportunity at\b", "I am drawn to"),
    (r"\bI am interested in joining\b", "I want to support"),
    (r"\bI am interested in\b", "I value"),
    (r"\bI am keen to contribute to\b", "I can contribute to"),
    (r"\bI am keen to contribute my skills to\b", "I can bring this experience to"),
)

_BROKEN_I_AM_PREFIXES: tuple[str, ...] = (
    "stakeholder reporting",
    "ad-hoc analysis",
    "adhoc analysis",
    "regular reporting",
    "new report development",
    "analytical support",
    "data quality",
    "dashboard development",
    "incident management",
    "user support",
)

_BROKEN_I_AM_GERUNDS: tuple[str, ...] = (
    "producing",
    "delivering",
    "supporting",
    "managing",
    "building",
    "developing",
    "designing",
    "maintaining",
    "providing",
    "creating",
    "conducting",
    "tracking",
    "driving",
    "analysing",
    "analyzing",
)


_GENERIC_PHRASES: tuple[str, ...] = (
    "i built and maintained",
    "i developed automated",
    "i am interested in this",
    "i am interested in",
    "i am keen to contribute",
    "particularly the opportunity to contribute",
    "forward-thinking team",
    "supportive team",
    "impactful team",
    "aligns closely with this opportunity",
    "let's discuss how my experience can benefit",
)


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


def _pick_role_letter_pack(job: dict) -> dict[str, object]:
    hay = f"{job.get('title', '')}\n{job.get('full_description', '')}".lower()
    public_hits = sum(
        1 for k in ("nhs", "trust", "university", "council", "public sector", "civil service") if k in hay
    )
    commercial_hits = sum(
        1 for k in ("saas", "product", "platform", "customer", "commercial", "startup", "fintech") if k in hay
    )
    strong_public_reporting = any(
        k in hay
        for k in (
            "nhs",
            "healthcare analytics",
            "integrated care system",
            "public sector",
            "civil service",
            "council",
            "university",
        )
    )

    if public_hits >= 2 and any(k in hay for k in ("service desk", "it support", "helpdesk", "desktop support")):
        chosen = dict(_ROLE_LETTER_PACKS["public_it_support"])
        chosen["name"] = "public_it_support"
        return chosen
    if (strong_public_reporting or public_hits >= 2) and any(
        k in hay for k in ("reporting analyst", "insight analyst", "data analyst", "power bi", "analysis and reporting")
    ):
        chosen = dict(_ROLE_LETTER_PACKS["public_reporting"])
        chosen["name"] = "public_reporting"
        return chosen
    if public_hits >= 2 and any(k in hay for k in ("application support", "production support", "incident management")):
        chosen = dict(_ROLE_LETTER_PACKS["public_application_support"])
        chosen["name"] = "public_application_support"
        return chosen
    if any(
        k in hay
        for k in (
            "reporting analyst",
            "data analyst",
            "insight analyst",
            "mi analyst",
            "business intelligence",
            "power bi",
            "kpi",
        )
    ):
        chosen = dict(_ROLE_LETTER_PACKS["data_reporting"])
        chosen["name"] = "data_reporting"
        return chosen
    if commercial_hits >= 2 and any(
        k in hay for k in ("technical support engineer", "support engineer", "api", "integration", "saas")
    ):
        chosen = dict(_ROLE_LETTER_PACKS["commercial_technical_support"])
        chosen["name"] = "commercial_technical_support"
        return chosen

    best_name = "application_support"
    best_hits = -1
    for name, cfg in _ROLE_LETTER_PACKS.items():
        if name in ("public_it_support", "public_application_support", "commercial_technical_support"):
            continue
        hits = sum(1 for k in cfg.get("keywords", ()) if str(k) in hay)
        if hits > best_hits:
            best_name = name
            best_hits = hits
    chosen = dict(_ROLE_LETTER_PACKS.get(best_name) or {})
    chosen["name"] = best_name
    return chosen


def _extract_job_signals(job: dict, *, max_items: int = 4) -> list[str]:
    jd = str(job.get("full_description") or "")
    if not jd.strip():
        return []

    lines = [re.sub(r"\s+", " ", ln).strip(" -\t") for ln in re.split(r"[\r\n]+", jd)]
    lines = [ln for ln in lines if len(ln) >= 28]

    keywords = (
        "responsible",
        "own",
        "build",
        "develop",
        "maintain",
        "improve",
        "analy",
        "insight",
        "stakeholder",
        "governance",
        "quality",
        "pipeline",
        "dashboard",
        "sql",
        "python",
        "power bi",
    )

    out: list[str] = []
    seen: set[str] = set()
    for ln in lines:
        low = ln.lower()
        if not any(k in low for k in keywords):
            continue
        item = ln[:140]
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= max_items:
            break

    # Fall back to first informative lines if keyword extraction is sparse.
    if not out:
        for ln in lines[:max_items]:
            item = ln[:140]
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(item)

    return out[:max_items]


def _sanitize_cover_letter_text(text: str) -> str:
    s = str(text or "")
    for pattern, replacement in _COVER_REWRITE_PATTERNS:
        s = re.sub(pattern, replacement, s, flags=re.IGNORECASE)
    for bad in _COVER_BANNED_SOFT_PHRASES:
        s = re.sub(r"\b" + re.escape(bad) + r"\b", "", s, flags=re.IGNORECASE)
    for prefix in _BROKEN_I_AM_PREFIXES:
        s = re.sub(
            r"\bI am\s+" + re.escape(prefix) + r"\b",
            "My experience includes " + prefix,
            s,
            flags=re.IGNORECASE,
        )
    s = re.sub(
        r"\bI am\s+(" + "|".join(_BROKEN_I_AM_GERUNDS) + r")\b",
        r"My work includes \1",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"\s+,", ",", s)
    s = re.sub(r"[^\S\r\n]{2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"\bMy experience includes includes\b", "My experience includes", s, flags=re.IGNORECASE)
    s = re.sub(r"\bMy work includes includes\b", "My work includes", s, flags=re.IGNORECASE)
    lines = [ln.rstrip() for ln in s.splitlines()]
    return "\n".join(lines).strip()


def _polish_reporting_closer(letter: str, job: dict, plan: dict[str, object] | None = None) -> str:
    role_pack_name = str((plan or {}).get("role_pack", {}).get("name") or "")
    if role_pack_name not in {"data_reporting", "public_reporting"}:
        return letter

    text = str(letter or "").strip()
    if not text:
        return text

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) < 2:
        return text

    name = ""
    if paragraphs[-1] and len(paragraphs[-1].splitlines()) == 1 and len(paragraphs[-1].split()) >= 2:
        name = paragraphs[-1].strip()
    signoff_idx = len(paragraphs) - 1 if paragraphs and paragraphs[-1].strip().lower() == "best," else None
    if signoff_idx is None and len(paragraphs) >= 2 and paragraphs[-1].count("\n") == 1:
        first, second = [x.strip() for x in paragraphs[-1].split("\n", 1)]
        if first.lower() == "best,":
            name = second
            signoff_idx = len(paragraphs) - 1
    body_end = signoff_idx if signoff_idx is not None else len(paragraphs)
    if body_end <= 1:
        return text

    closing = paragraphs[body_end - 1]
    company_name = str((plan or {}).get("company_name") or _extract_company_name(job))
    responsibilities = [str(x).strip() for x in ((plan or {}).get("responsibilities") or []) if str(x).strip()]
    job_signals = _extract_job_signals(job)
    anchor_source = responsibilities + job_signals
    anchor = next((x for x in anchor_source if x), "")

    if role_pack_name == "data_reporting":
        sentence_1 = f"I am drawn to {company_name} because this role centres on turning operational and financial data into clear reporting that helps teams track performance and act quickly."
        sentence_2 = "I can bring accurate KPI reporting, variance analysis, and dashboard delivery that gives senior stakeholders reliable visibility."
    else:
        sentence_1 = f"I want to support {company_name} because the role sits within Healthcare Analytics and contributes directly to timely, evidence-based decision-making."
        sentence_2 = "I can bring reliable reporting, ad-hoc analysis, and dashboard development that support accurate outputs across the Analysis and Reporting function."

    anchor_low = anchor.lower()
    if role_pack_name == "public_reporting" and "epr" in anchor_low:
        sentence_1 = f"I want to support {company_name} because the role contributes to Healthcare Analytics and the wider EPR programme through timely, evidence-based reporting."
    elif role_pack_name == "data_reporting" and "timescales" in anchor_low:
        sentence_2 = "I can bring accurate KPI reporting, variance analysis, and dashboard delivery that help reporting deadlines and quality standards stay on track."

    paragraphs[body_end - 1] = sentence_1 + " " + sentence_2
    rebuilt = "\n\n".join(paragraphs[:body_end])
    if signoff_idx is not None:
        rebuilt += "\n\nBest,"
    if name:
        rebuilt += f"\n{name}"
    return rebuilt.strip()


def _polish_support_closer(letter: str, job: dict, plan: dict[str, object] | None = None) -> str:
    role_pack_name = str((plan or {}).get("role_pack", {}).get("name") or "")
    if role_pack_name not in {
        "it_support",
        "application_support",
        "technical_systems",
        "public_it_support",
        "public_application_support",
        "commercial_technical_support",
    }:
        return letter

    text = str(letter or "").strip()
    if not text:
        return text

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) < 2:
        return text

    name = ""
    if paragraphs[-1] and len(paragraphs[-1].splitlines()) == 1 and len(paragraphs[-1].split()) >= 2:
        name = paragraphs[-1].strip()
    signoff_idx = len(paragraphs) - 1 if paragraphs and paragraphs[-1].strip().lower() == "best," else None
    if signoff_idx is None and len(paragraphs) >= 2 and paragraphs[-1].count("\n") == 1:
        first, second = [x.strip() for x in paragraphs[-1].split("\n", 1)]
        if first.lower() == "best,":
            name = second
            signoff_idx = len(paragraphs) - 1
    body_end = signoff_idx if signoff_idx is not None else len(paragraphs)
    if body_end <= 1:
        return text

    company_name = str((plan or {}).get("company_name") or _extract_company_name(job))
    responsibilities = [str(x).strip() for x in ((plan or {}).get("responsibilities") or []) if str(x).strip()]
    job_signals = _extract_job_signals(job)
    anchor = " ".join(responsibilities + job_signals).lower()

    close_map = {
        "it_support": (
            f"I am drawn to {company_name} because the role focuses on dependable user support, onboarding, and resolving day-to-day technical issues quickly.",
            "I can bring calm ticket handling, clear communication, and practical Microsoft 365 support that keeps users productive.",
        ),
        "application_support": (
            f"I am drawn to {company_name} because the role focuses on stable application support, incident resolution, and keeping critical services running.",
            "I can bring careful troubleshooting, SQL-based investigation, and dependable follow-through that helps services stay available.",
        ),
        "technical_systems": (
            f"I am drawn to {company_name} because the role combines systems analysis with technical troubleshooting for business-critical platforms.",
            "I can bring structured investigation, stakeholder communication, and practical problem-solving that improves system reliability.",
        ),
        "public_it_support": (
            f"I want to support {company_name} because the role contributes directly to reliable user support in a structured public-facing environment.",
            "I can bring responsive ticket handling, onboarding support, and clear communication that helps staff stay productive.",
        ),
        "public_application_support": (
            f"I want to support {company_name} because the role helps maintain reliable public-facing services and stable operational systems.",
            "I can bring disciplined incident handling, access support, and troubleshooting that keep essential services running smoothly.",
        ),
        "commercial_technical_support": (
            f"I am drawn to {company_name} because the role supports customer-facing systems where fast investigation and reliable technical support matter.",
            "I can bring structured troubleshooting, API-aware investigation, and clear communication that improve customer and platform outcomes.",
        ),
    }
    sentence_1, sentence_2 = close_map[role_pack_name]

    if "microsoft 365" in anchor and role_pack_name in {"it_support", "public_it_support"}:
        sentence_2 = "I can bring responsive Microsoft 365 support, clear ticket handling, and practical user guidance that keeps staff productive."
    elif "incident" in anchor and role_pack_name in {
        "application_support",
        "public_application_support",
        "commercial_technical_support",
    }:
        sentence_2 = "I can bring disciplined incident handling, structured troubleshooting, and dependable follow-through that keep services stable."
    elif "api" in anchor and role_pack_name in {"technical_systems", "commercial_technical_support"}:
        sentence_2 = "I can bring structured troubleshooting, API-aware investigation, and clear communication that resolve issues efficiently."

    paragraphs[body_end - 1] = sentence_1 + " " + sentence_2
    rebuilt = "\n\n".join(paragraphs[:body_end])
    if signoff_idx is not None:
        rebuilt += "\n\nBest,"
    if name:
        rebuilt += f"\n{name}"
    return rebuilt.strip()


def _extract_resume_paragraph_evidence(resume_text: str, *, max_items: int = 12) -> list[str]:
    lines = [re.sub(r"\s+", " ", ln).strip(" -\t") for ln in str(resume_text or "").splitlines() if ln.strip()]
    out: list[str] = []
    seen: set[str] = set()
    for ln in lines:
        if len(ln) < 24 or len(ln) > 220:
            continue
        low = ln.lower()
        if ln == ln.upper() and len(ln.split()) <= 5:
            continue
        if low.startswith(
            ("summary", "core technical skills", "professional experience", "education", "certifications")
        ):
            continue
        key = low.strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(ln)
        if len(out) >= max_items:
            break
    return out


def _score_line_for_job(line: str, job_text: str) -> int:
    lt = set(re.findall(r"[a-z0-9][a-z0-9+./-]*", str(line or "").lower()))
    jt = set(re.findall(r"[a-z0-9][a-z0-9+./-]*", str(job_text or "").lower()))
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "role",
        "team",
        "work",
        "using",
        "across",
        "will",
        "your",
    }
    lt = {t for t in lt if len(t) >= 3 and t not in stop}
    jt = {t for t in jt if len(t) >= 3 and t not in stop}
    return len(lt & jt)


def _select_cover_letter_evidence(job: dict, resume_text: str, *, max_items: int = 4) -> list[str]:
    hay = f"{job.get('title', '')}\n{job.get('full_description', '')}"
    candidates = _extract_resume_paragraph_evidence(resume_text, max_items=18)
    ranked = sorted(candidates, key=lambda line: (-_score_line_for_job(line, hay), -len(line)))
    out: list[str] = []
    seen: set[str] = set()
    for ln in ranked:
        if _score_line_for_job(ln, hay) <= 0 and out:
            continue
        key = ln.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(ln)
        if len(out) >= max_items:
            break
    return out or candidates[:max_items]


def _opening_sentence(text: str) -> str:
    body = (text or "").strip()
    if not body:
        return ""
    body = re.sub(r"^\s*Dear[^\n]*\n+", "", body, flags=re.IGNORECASE)
    body = re.sub(r"\s+", " ", body).strip()
    m = re.search(r"(.+?[.!?])(?:\s|$)", body)
    if not m:
        return body[:140]
    return m.group(1)[:140]


def _recent_openings(limit: int = 6) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    try:
        files = sorted(COVER_LETTER_DIR.glob("*_CL.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return items

    for p in files:
        if len(items) >= limit:
            break
        try:
            txt = p.read_text(encoding="utf-8")
        except Exception:
            continue
        opening = _opening_sentence(txt)
        if not opening:
            continue
        key = opening.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(opening)
    return items


def _generic_issues(letter: str, recent_openings: list[str]) -> list[str]:
    low = (letter or "").lower()
    issues: list[str] = []

    hits = [p for p in _GENERIC_PHRASES if p in low]
    if len(hits) >= 2:
        issues.append("Letter reads templated. Use a less generic narrative and a different close.")

    if any(
        p in low for p in ("i am interested in", "i am keen to contribute", "forward-thinking team", "supportive team")
    ):
        issues.append(
            "Closing paragraph still reads generic. Use one concrete employer detail and a more specific closing."
        )

    opening = _opening_sentence(letter).lower()
    if opening:
        for prev in recent_openings:
            prev_low = prev.lower()
            if opening == prev_low:
                issues.append("Opening sentence repeats a recent cover letter. Use a new opener.")
                break
            # Soft near-duplicate check on first 7 words.
            o7 = " ".join(opening.split()[:7])
            p7 = " ".join(prev_low.split()[:7])
            if o7 and o7 == p7:
                issues.append("Opening is too similar to recent letters. Change sentence structure.")
                break

    return issues


def _extract_company_name(job: dict) -> str:
    for key in ("company",):
        val = str(job.get(key) or "").strip()
        if val:
            return val
    title = str(job.get("title") or "")
    m = re.search(r"\|\s*([^|]+)$", title)
    if m:
        guess = m.group(1).strip()
        if guess and len(guess) >= 3:
            return guess
    jd = str(job.get("full_description") or "")
    jd_lines = [ln.strip() for ln in jd.splitlines() if ln.strip()]
    for ln in jd_lines[:12]:
        match = re.search(
            r"join\s+(?:our|the)\s+.+?\s+at\s+([A-Z][A-Za-z0-9&'., -]{2,80}?)(?:[!.]|$)", ln, flags=re.IGNORECASE
        )
        if match:
            guess = match.group(1).strip(" ,.!-")
            if guess and guess.lower() not in {"the organisation", "our organisation", "our business"}:
                return guess
        match = re.search(r"\bAt\s+([A-Z][A-Za-z0-9&'., -]{2,80}?),(?:\s+we\b|\s+we're\b)", ln)
        if match:
            guess = match.group(1).strip(" ,.!-")
            if guess and guess.lower() not in {"the organisation", "our organisation", "our business"}:
                return guess
    site = str(job.get("site") or "").strip()
    if site and site.lower() not in {"gov.uk find a job", "linkedin", "indeed", "glassdoor", "adzuna uk"}:
        return site
    return "the organisation"


def _build_paragraph_plan(job: dict, resume_text: str, profile: dict) -> dict[str, object]:
    role_pack = _pick_role_letter_pack(job)
    company_name = _extract_company_name(job)
    responsibilities = extract_job_responsibilities(str(job.get("full_description") or ""), limit=4)
    evidence = _select_cover_letter_evidence(job, resume_text, max_items=5)
    boundary = profile.get("skills_boundary", {}) or {}
    skills: list[str] = []
    for items in boundary.values():
        if isinstance(items, list):
            skills.extend(str(x).strip() for x in items if str(x).strip())
    relevant_skills = [
        s
        for s in skills
        if s.lower() in str(job.get("full_description") or "").lower() or s.lower() in resume_text.lower()
    ]
    relevant_skills = relevant_skills[:8]
    return {
        "role_pack": role_pack,
        "company_name": company_name,
        "responsibilities": responsibilities,
        "evidence": evidence,
        "relevant_skills": relevant_skills,
    }


# ── Prompt Builder (profile-driven) ──────────────────────────────────────


def _build_cover_letter_prompt(
    profile: dict,
    *,
    style: dict[str, str] | None = None,
    job_signals: list[str] | None = None,
    recent_openings: list[str] | None = None,
    plan: dict[str, object] | None = None,
) -> str:
    """Build the cover letter system prompt from the user's profile.

    All personal data, skills, and sign-off name come from the profile.
    """
    personal = profile.get("personal", {})
    boundary = profile.get("skills_boundary", {})
    resume_facts = profile.get("resume_facts", {})

    # Preferred name for the sign-off (falls back to full name)
    sign_off_name = personal.get("preferred_name") or personal.get("full_name", "")
    style_cfg = dict(_ROLE_LETTER_FALLBACK)
    if isinstance(style, dict):
        style_cfg.update(style)

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

    signals_block = ""
    if job_signals:
        signals_block = "\nJob-specific signals to anchor against:\n" + "\n".join(
            f"- {s}" for s in job_signals[:4] if str(s).strip()
        )

    recent_openings_block = ""
    if recent_openings:
        recent_openings_block = "\nDo NOT reuse these recent opening sentences:\n" + "\n".join(
            f"- {s}" for s in recent_openings[:4] if str(s).strip()
        )

    plan = plan or {}
    evidence_block = ""
    evidence = plan.get("evidence") or []
    if isinstance(evidence, list) and evidence:
        evidence_block = "\nResume evidence to prioritize:\n" + "\n".join(
            f"- {str(s)}" for s in evidence[:4] if str(s).strip()
        )

    responsibilities_block = ""
    responsibilities = plan.get("responsibilities") or []
    if isinstance(responsibilities, list) and responsibilities:
        responsibilities_block = "\nTop job responsibilities to address:\n" + "\n".join(
            f"- {str(s)}" for s in responsibilities[:4] if str(s).strip()
        )

    skills_focus = str(style_cfg.get("skills_focus") or "").strip()
    company_name = str(plan.get("company_name") or "the organisation")

    return f"""Write a cover letter for {sign_off_name}. The goal is to get an interview.

STRUCTURE: Exactly 5 short paragraphs. 180-250 words. Every sentence must earn its place.

ROLE LETTER PACK ({style_cfg.get("name", "fallback")}):
- Paragraph 1: {style_cfg.get("opening", "")}
- Paragraph 2: {style_cfg.get("body_focus", "")}
- Paragraph 3: Highlight one additional relevant role or transferable support example.
- Paragraph 4: Mention role-relevant skills only: {skills_focus or "Use only evidence-backed skills."}
- Paragraph 5: Explain interest in {company_name} and close briefly.

PARAGRAPH 1 (2 sentences): State that you are applying for the exact role at the exact company, then say it aligns closely with your experience. Keep it direct and natural.

PARAGRAPH 2 (2-3 sentences): Use the strongest current or recent experience example from the resume that matches the role.{projects_hint}{metrics_hint}

PARAGRAPH 3 (2 sentences): Add another supporting example from an earlier role or academic/professional environment that reinforces fit.

PARAGRAPH 4 (2 sentences): Summarize the most relevant tools, support areas, or analytical strengths from the resume, without listing everything.

PARAGRAPH 5 (2 sentences): Say why you are interested in {company_name}, based on the role/team/mission in the JD, then close politely. Name one concrete employer, team, mission, or programme detail from the JD. Do not use generic praise such as "forward-thinking team", "supportive team", or "impactful team".

Specificity and anti-template rules:
- Mention at least one concrete JD detail from the job-specific signals (verbatim or very close wording).
- Do not reuse the same opening pattern repeatedly across letters.
- Use a natural UK-style application tone similar to a strong real cover letter, not a sales pitch.
- Use "I am writing to apply for the [Job Title] role at [Company Name]" style only when it fits naturally.
- Do not use the exact phrase "I built and maintained".
- Do not use the words "robust", "dedicated", or the phrase "adept at".
- Do not use generic closer phrases like "I am keen to contribute" or "I am interested in this opportunity".
{signals_block}
{responsibilities_block}
{evidence_block}
{recent_openings_block}

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
- Sound like a genuine applicant, not an AI prompt completion.
- NEVER narrate or explain what you're doing. BAD: "This demonstrates my commitment to X." GOOD: Just state the fact and move on.
- NEVER hedge. BAD: "might address some of your challenges." GOOD: "solves the same problem your team is facing."
- NEVER use "Also," to start a sentence. NEVER use "Furthermore," or "Additionally,".
- Most sentences should contain a concrete tool, support area, environment, or outcome.
- Keep the company/organisation name exact. Do not replace it with the board name when a real employer is present.
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
    style = _pick_role_letter_pack(job)
    job_signals = _extract_job_signals(job)
    recent_openings = _recent_openings()
    plan = _build_paragraph_plan(job, resume_text, profile)
    cl_prompt_base = _build_cover_letter_prompt(
        profile,
        style=style,
        job_signals=job_signals,
        recent_openings=recent_openings,
        plan=plan,
    )
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
            "- Exactly 5 short paragraphs\n"
            "- 180-250 words\n"
            "- Include at least 2 concrete numbers from the resume\n"
            "- Mention at least 1 specific detail from the target job description\n"
            "- End with a closing line (e.g. Best,) then your name on its own line\n"
            "- Output plain text only\n"
            "- Do not stop early"
        )

        # Gemini can spend output budget on hidden thinking and truncate visible text.
        thinking_budget = 0 if (client.provider or "").lower() == "gemini" else None
        letter = client.chat(messages, max_tokens=1200, temperature=0.7, thinking_budget=thinking_budget)
        letter = sanitize_text(letter)  # auto-fix em dashes, smart quotes
        letter = _sanitize_cover_letter_text(letter)
        letter = _polish_reporting_closer(letter, job, plan)
        letter = _polish_support_closer(letter, job, plan)
        letter = _ensure_greeting_and_signoff(letter, name)
        # If the model truncated mid-sentence, treat as failure and retry.
        if _looks_truncated(letter, name):
            avoid_notes.append("Output looked truncated. Do not stop early; end with closing + name.")
            continue

        # Ensure sign-off survives sanitize and truncation guard.
        letter = _ensure_greeting_and_signoff(letter, name)

        generic_issues = _generic_issues(letter, recent_openings)
        if generic_issues:
            avoid_notes.extend(generic_issues)
            log.debug(
                "Cover letter attempt %d/%d flagged as templated: %s",
                attempt + 1,
                max_retries + 1,
                generic_issues,
            )
            continue

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


def generate_cover_letter_with_diagnostics(
    resume_text: str,
    job: dict,
    profile: dict,
    max_retries: int = 3,
) -> tuple[str, dict]:
    """Generate a cover letter and return deterministic diagnostics used."""
    plan = _build_paragraph_plan(job, resume_text, profile)
    role_pack = dict(plan.get("role_pack") or {})
    job_signals = _extract_job_signals(job)
    recent_openings = _recent_openings()
    company_name = str(plan.get("company_name") or _extract_company_name(job))
    diagnostics = {
        "role_pack": str(role_pack.get("name") or "fallback"),
        "company_name": company_name,
        "job_signals": job_signals,
        "responsibilities": list(plan.get("responsibilities") or []),
        "evidence": list(plan.get("evidence") or []),
        "relevant_skills": list(plan.get("relevant_skills") or []),
        "recent_openings_guard": recent_openings[:4],
    }
    letter = generate_cover_letter(resume_text, job, profile, max_retries=max_retries)
    return letter, diagnostics


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
    default_resume_text = RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()
    selected_only = _selected_only_enabled()

    # Fetch jobs that have tailored resumes but no cover letter yet.
    if selected_only:
        where = (
            "apply_status = 'selected' "
            "AND tailored_resume_path IS NOT NULL "
            "AND full_description IS NOT NULL "
            "AND (cover_letter_path IS NULL OR cover_letter_path = '') "
            "AND COALESCE(cover_attempts, 0) < ?"
        )
        params: list[object] = [MAX_ATTEMPTS]
        query = "SELECT rowid AS job_id, * FROM jobs WHERE " + where + " ORDER BY fit_score DESC"
    else:
        where = (
            "fit_score >= ? AND tailored_resume_path IS NOT NULL "
            "AND full_description IS NOT NULL "
            "AND (cover_letter_path IS NULL OR cover_letter_path = '') "
            "AND COALESCE(cover_attempts, 0) < ?"
        )
        params = [min_score, MAX_ATTEMPTS]
        query = "SELECT rowid AS job_id, * FROM jobs WHERE " + where + " ORDER BY fit_score DESC"
    if limit > 0:
        query += " LIMIT ?"
        params.append(limit)
    jobs = conn.execute(query, tuple(params)).fetchall()

    if not jobs:
        if selected_only:
            log.info("No selected jobs needing cover letters.")
        else:
            log.info("No jobs needing cover letters (score >= %d).", min_score)
        return {"generated": 0, "errors": 0, "elapsed": 0.0}

    # Convert rows to dicts
    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    COVER_LETTER_DIR.mkdir(parents=True, exist_ok=True)
    if selected_only:
        log.info("Generating cover letters for %d selected jobs...", len(jobs))
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
            tailored_text = default_resume_text
            trp = job.get("tailored_resume_path")
            if trp:
                try:
                    tailored_text = Path(trp).read_text(encoding="utf-8")
                except Exception:
                    # Fall back to best base resume variant.
                    try:
                        tailored_text = route_resume_for_job(job).text.strip() or default_resume_text
                    except Exception:
                        tailored_text = default_resume_text

            letter, diagnostics = generate_cover_letter_with_diagnostics(tailored_text, job, profile)

            username = str(os.environ.get("APPLYPILOT_USER", "") or "").strip()
            stem = naming.cover_letter_filename(profile.get("personal", {}), ext="txt", username=username, job=job)
            prefix = Path(stem).stem

            # Validate against both tailored + base resume text.
            # Tailoring may omit some general metrics/skills, but the cover letter can
            # still reference them if they exist in the candidate's main resume.
            validation_text = (tailored_text or "").strip() + "\n\n" + (default_resume_text or "").strip()
            validation = validate_cover_letter(letter, profile=profile, resume_text=validation_text)
            if not validation["passed"]:
                raise RuntimeError("Cover letter failed validation: " + "; ".join(validation["errors"][:3]))

            cl_path = COVER_LETTER_DIR / f"{prefix}_CL.txt"
            cl_path.write_text(letter, encoding="utf-8")
            report_path = cl_path.with_name(cl_path.stem + "_REPORT.json")
            report_path.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")

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
                "report_path": str(report_path),
                "title": job["title"],
                "site": job["site"],
                "status": "approved",
                "failure_detail": "",
                "diagnostics": diagnostics,
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
                "report_path": None,
                "error": str(e),
                "status": "failed",
                "failure_detail": str(e),
                "diagnostics": None,
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
                "UPDATE jobs SET cover_letter_path=?, cover_letter_at=?, cover_letter_status=?, cover_letter_failure_detail=?, cover_letter_report_path=?, cover_letter_diagnostics=?, "
                "cover_attempts=COALESCE(cover_attempts,0)+1 WHERE url=?",
                (
                    r["path"],
                    now,
                    r.get("status") or "approved",
                    None,
                    r.get("report_path"),
                    json.dumps(r.get("diagnostics") or {}, ensure_ascii=False),
                    r["url"],
                ),
            )
            saved += 1
        else:
            conn.execute(
                "UPDATE jobs SET cover_attempts=COALESCE(cover_attempts,0)+1, cover_letter_path=NULL, cover_letter_status=?, cover_letter_failure_detail=?, cover_letter_report_path=?, cover_letter_diagnostics=? WHERE url=?",
                (
                    r.get("status") or "failed",
                    str(r.get("failure_detail") or r.get("error") or "")[:1200] or None,
                    r.get("report_path"),
                    json.dumps(r.get("diagnostics") or {}, ensure_ascii=False),
                    r["url"],
                ),
            )
    conn.commit()

    elapsed = time.time() - t0
    log.info("Cover letters done in %.1fs: %d generated, %d errors", elapsed, saved, error_count)

    return {
        "generated": saved,
        "errors": error_count,
        "elapsed": elapsed,
    }
