"""Advanced strategy helpers for resume tailoring.

This module keeps deterministic ranking and guardrail logic separate from the
core LLM call loop in tailor.py.
"""

from __future__ import annotations

import re
from typing import Any


_ROLE_PACK_INSTRUCTIONS: dict[str, str] = {
    "data_bi": (
        "Focus on analytics impact, dashboard/reporting outcomes, stakeholder communication, "
        "and clear metric traceability. Prefer concise business-language bullets with concrete numbers."
    ),
    "engineering": (
        "Focus on system design, reliability, performance, architecture, and delivery ownership. "
        "Prefer implementation details (what was built, scale, constraints, and outcomes)."
    ),
    "support": (
        "Focus on service quality, incident reduction, SLA outcomes, user support, and operational reliability. "
        "Prefer practical troubleshooting outcomes and process improvements."
    ),
    "application_support": (
        "Focus on production support, incident triage, monitoring, log analysis, SQL-backed investigation, "
        "service continuity, and business impact. Prefer concrete issue-resolution and stability outcomes."
    ),
    "qa_testing": (
        "Focus on test coverage, defect prevention, regression safety, automation depth, and release confidence. "
        "Prefer bullets that show test design, defect quality, and measurable quality improvements."
    ),
    "cloud_platform": (
        "Focus on reliability, automation, infrastructure/platform ownership, observability, and incident response. "
        "Prefer bullets that show uptime, resilience, deployment quality, and operational scale."
    ),
    "business_analysis": (
        "Focus on requirements clarity, stakeholder alignment, process mapping, workflow improvement, "
        "and delivery support. Prefer bullets that show analysis, documentation, and business outcomes."
    ),
}


_DEFAULT_SAFE_SYNONYMS: dict[str, list[str]] = {
    "sql": ["sql querying", "query optimization", "postgresql", "mysql", "t-sql"],
    "python": ["python scripting", "automation scripts"],
    "power bi": ["bi dashboards", "dashboarding", "dax", "power query"],
    "tableau": ["data visualization", "dashboarding"],
    "excel": ["advanced excel", "pivot tables"],
    "etl": ["data pipelines", "data integration", "data transformation"],
    "data quality": ["data validation", "quality controls", "reconciliation"],
    "data governance": ["governance", "compliance", "gdpr"],
    "aws": ["amazon web services", "cloud infrastructure"],
    "azure": ["microsoft azure", "cloud platform"],
    "javascript": ["js", "typescript", "frontend"],
    "typescript": ["ts", "javascript"],
    "react": ["frontend", "ui components"],
    "incident management": ["incident response", "ticket handling", "service desk"],
    "active directory": ["ad", "identity management"],
}

_PACK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "application_support": (
        "application support",
        "production support",
        "application support engineer",
        "application support analyst",
        "support engineer",
        "support analyst",
        "incident management",
        "log analysis",
        "monitoring",
        "platform support",
        "sql",
    ),
    "qa_testing": (
        "quality assurance",
        "qa",
        "tester",
        "test analyst",
        "test automation",
        "selenium",
        "playwright",
        "cypress",
        "regression",
        "uat",
        "defect",
    ),
    "cloud_platform": (
        "cloud",
        "aws",
        "azure",
        "gcp",
        "devops",
        "sre",
        "platform engineer",
        "kubernetes",
        "terraform",
        "infrastructure",
        "observability",
    ),
    "business_analysis": (
        "business analyst",
        "requirements",
        "user stories",
        "acceptance criteria",
        "process mapping",
        "stakeholder",
        "workshop",
        "workflow",
        "gap analysis",
        "business process",
    ),
    "support": (
        "support",
        "helpdesk",
        "service desk",
        "desktop",
        "it operations",
        "ticket",
        "microsoft 365",
        "active directory",
    ),
    "engineering": (
        "engineer",
        "developer",
        "software",
        "backend",
        "frontend",
        "full stack",
        "architecture",
        "microservice",
    ),
    "data_bi": (
        "analyst",
        "analytics",
        "business intelligence",
        "bi ",
        "reporting",
        "dashboard",
        "data quality",
        "insight",
    ),
}

_PACK_PRIORITY: dict[str, int] = {
    "application_support": 1,
    "qa_testing": 2,
    "cloud_platform": 3,
    "business_analysis": 4,
    "support": 5,
    "data_bi": 6,
    "engineering": 7,
}

_HARD_REQUIREMENT_PHRASES: tuple[str, ...] = (
    "must have",
    "must-have",
    "required",
    "essential",
    "mandatory",
    "need to have",
    "you will need",
)

_SOFT_REQUIREMENT_PHRASES: tuple[str, ...] = (
    "preferred",
    "desirable",
    "nice to have",
    "bonus",
    "would be an advantage",
    "would be beneficial",
)

_CREDENTIAL_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bitil(?:\s+foundation)?\b", "ITIL"),
    (r"\bccna\b", "CCNA"),
    (r"\bcomptia\s*a\+\b|\ba\+\s*cert", "CompTIA A+"),
    (r"\baws certified\b", "AWS Certified"),
    (r"\bazure certification\b|\baz-\d{3}\b", "Azure certification"),
    (r"\bpmp\b|project management professional", "PMP"),
    (r"\bprince2\b", "PRINCE2"),
    (r"\bcertified scrum master\b|\bcsm\b", "Certified Scrum Master"),
    (r"\bnmc registration\b", "NMC registration"),
    (r"\bhcpc registration\b", "HCPC registration"),
    (r"\bgmc registration\b", "GMC registration"),
)

_HARD_CONSTRAINT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bsecurity clearance\b|\bsc clearance\b|\bdv clearance\b|\bsc/dv\b", "security clearance"),
    (r"\bdriving licen[cs]e\b|\bdriver'?s licen[cs]e\b", "driving licence"),
    (r"\bright to work in the uk\b|\buk work authorization\b|\buk work authorisation\b", "UK work authorization"),
    (r"\bon-call\b|\bon call\b", "on-call availability"),
)

_DOMAIN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bnhs\b|\bhealthcare\b|\bclinical\b|\bhospital\b", "healthcare"),
    (r"\bcouncil\b|\blocal authority\b|\bcivil service\b|\bpublic sector\b|\bministry\b", "public sector"),
    (r"\buniversity\b|\bhigher education\b|\bcollege\b", "education"),
    (r"\bfinancial services\b|\bbanking\b|\binsurance\b|\bfintech\b", "financial services"),
)

_CREDENTIAL_ALIASES: dict[str, tuple[str, ...]] = {
    "itil": ("itil foundation",),
    "azure certification": ("microsoft certified",),
    "uk work authorization": ("sponsorship", "right to work"),
}

_RESPONSIBILITY_ACTIONS: tuple[str, ...] = (
    "support",
    "troubleshoot",
    "resolve",
    "manage",
    "monitor",
    "maintain",
    "deliver",
    "improve",
    "analyse",
    "analyze",
    "investigate",
    "coordinate",
    "design",
    "build",
    "develop",
    "test",
    "document",
    "configure",
    "administer",
    "deploy",
)

_JD_JUNK_PHRASES: tuple[str, ...] = (
    "committed to",
    "passionate about",
    "exciting opportunity",
    "your expertise is genuinely valued",
    "values shape how we support",
    "ambitious organisation",
    "strong culture",
    "great culture",
    "benefits package",
    "what we offer",
    "why join us",
    "about us",
    "about the company",
    "who we are",
    "on a mission to",
    "our values",
    "our people",
    "fast-paced environment",
)

_JD_JUNK_LINE_PATTERNS: tuple[str, ...] = (
    r"^job advert\b",
    r"\bwe are looking for\b",
    r"\bjoin our growing team\b",
    r"\bthis is an exciting opportunity\b",
    r"\byou'?ll join\b",
    r"\bwe value our employees\b",
    r"\bcompetitive salary\b",
    r"\bhealth, dental, and vision\b",
    r"\bremote-friendly\b",
    r"\bwork environment\b",
    r"^who we are\b",
    r"^the\s+.+\s+(department|team|organisation|business)\s+is\b",
)

_RESPONSIBILITY_CONTINUATION_PREFIXES: tuple[str, ...] = (
    "and ",
    "or ",
    "to ",
    "for ",
    "with ",
    "within ",
    "across ",
    "through ",
    "using ",
    "including ",
    "ensuring ",
    "working ",
    "providing ",
    "supporting ",
    "maintaining ",
    "monitoring ",
    "developing ",
    "building ",
    "gathering ",
    "projects,",
    "targets ",
)

_RESPONSIBILITY_DANGLING_ENDINGS: tuple[str, ...] = (
    " and",
    " or",
    " to",
    " for",
    " with",
    " within",
    " across",
    " into",
    " of",
    " the",
    " a",
    " an",
    " key",
)

_JD_BAD_SKILL_PHRASES: tuple[str, ...] = (
    "committed to",
    "attention to",
    "quality and",
    "to support",
    "to ensure",
    "and maintain",
    "and data",
    "within a",
    "using a",
    "for management",
    "making and",
    "approach to",
)


_CITATION_RE = re.compile(r"\[(F\d+(?:\s*,\s*F\d+)*)\]\s*[\.,;:!?)]?\s*$", re.IGNORECASE)
_CITATION_BLOCK_RE = re.compile(r"\s*\[(?:\s*F\d+\s*(?:,\s*F\d+\s*)*)\](?=[\s\.,;:!?)]|$)", re.IGNORECASE)


def _normalize_space(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def _extract_numbers(text: str) -> list[str]:
    out: list[str] = []
    for m in re.findall(r"\b\d[\d,]*(?:\.\d+)?%?\b", str(text or "")):
        s = m.replace(",", "").strip()
        if s:
            out.append(s)
    return out


def _flatten_skills(profile: dict[str, Any]) -> list[str]:
    boundary = profile.get("skills_boundary") or {}
    out: list[str] = []
    if not isinstance(boundary, dict):
        return out
    for items in boundary.values():
        if not isinstance(items, list):
            continue
        for it in items:
            s = _normalize_space(str(it))
            if s:
                out.append(s)
    return out


def detect_role_pack(job: dict[str, Any], profile: dict[str, Any]) -> dict[str, str]:
    """Return role-pack selection and instructions.

    Result shape: {"pack": "data_bi|engineering|support", "reason": str, "instructions": str}
    """
    cfg = profile.get("tailoring") if isinstance(profile, dict) else {}
    override = str((cfg or {}).get("role_pack_override") or "").strip().lower()
    if override in _ROLE_PACK_INSTRUCTIONS:
        return {
            "pack": override,
            "reason": "profile_override",
            "instructions": _ROLE_PACK_INSTRUCTIONS[override],
        }

    title = str(job.get("title") or "").lower()
    desc = str(job.get("full_description") or "").lower()
    hay = f"{title}\n{desc}"

    scores: dict[str, int] = {}
    for pack_name, keywords in _PACK_KEYWORDS.items():
        scores[pack_name] = sum(1 for k in keywords if k in hay)

    pack = "engineering"
    reason = "keyword_default"
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], _PACK_PRIORITY.get(kv[0], 99), kv[0]))
    if ranked and ranked[0][1] > 0:
        pack = ranked[0][0]

    return {
        "pack": pack,
        "reason": reason,
        "instructions": _ROLE_PACK_INSTRUCTIONS[pack],
    }


def build_adaptive_budget(job: dict[str, Any], profile: dict[str, Any], role_pack: str) -> dict[str, Any]:
    """Build runtime validation rules and prompt hint for section budgeting."""
    title = str(job.get("title") or "").lower()
    desc = str(job.get("full_description") or "").lower()
    hay = f"{title}\n{desc}"
    senior = any(k in hay for k in ("senior", "lead", "principal", "staff", "manager", "architect"))

    require_projects = True

    if role_pack == "support":
        mr = {"min": 4, "max": 6}
        ot = {"min": 2, "max": 4}
        proj = {"min": 0, "max": 1}
        require_projects = False
    elif role_pack == "application_support":
        mr = {"min": 4, "max": 6}
        ot = {"min": 2, "max": 4}
        proj = {"min": 0, "max": 2}
        require_projects = False
    elif role_pack == "qa_testing":
        mr = {"min": 4, "max": 6}
        ot = {"min": 2, "max": 4}
        proj = {"min": 1, "max": 2}
    elif role_pack == "cloud_platform":
        mr = {"min": 5, "max": 7}
        ot = {"min": 3, "max": 5}
        proj = {"min": 1, "max": 3}
    elif role_pack == "business_analysis":
        mr = {"min": 4, "max": 6}
        ot = {"min": 2, "max": 4}
        proj = {"min": 0, "max": 1}
        require_projects = False
    elif role_pack == "data_bi":
        mr = {"min": 5, "max": 6}
        ot = {"min": 3, "max": 4}
        proj = {"min": 1, "max": 2}
    else:
        mr = {"min": 5, "max": 7}
        ot = {"min": 3, "max": 5}
        proj = {"min": 1, "max": 3}

    if senior:
        mr["max"] = int(mr["max"]) + 1
        ot["max"] = int(ot["max"]) + 1

    runtime_rules = {
        "experience_bullets": {
            "most_recent": mr,
            "other": ot,
            "enforce_most_recent_max": True,
        },
        "project_bullets": proj,
        "required_sections": {
            "projects": require_projects,
            "education": True,
        },
    }

    hint = (
        f"Most recent role: {mr['min']}-{mr['max']} bullets. "
        f"Other roles: {ot['min']}-{ot['max']} bullets. "
        f"Projects: {proj['min']}-{proj['max']} bullets each."
    )

    return {"runtime_rules": runtime_rules, "hint": hint, "senior": senior}


def _profile_evidence_blob(resume_text: str, profile: dict[str, Any]) -> str:
    parts: list[str] = [str(resume_text or "")]
    if isinstance(profile, dict):
        for top in (
            "skills_boundary",
            "resume_facts",
            "resume_sections",
            "experience",
            "work_authorization",
            "personal",
        ):
            parts.append(str(profile.get(top) or ""))
    return "\n".join(parts).lower()


def _text_similarity_tokens(text: str) -> set[str]:
    toks = re.findall(r"[a-z0-9][a-z0-9+.#/-]*", str(text or "").lower())
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "that",
        "this",
        "will",
        "your",
        "our",
        "their",
        "using",
        "within",
        "across",
        "role",
        "team",
        "work",
    }
    return {t for t in toks if len(t) >= 3 and t not in stop}


def _similarity_score(a: str, b: str) -> int:
    at = _text_similarity_tokens(a)
    bt = _text_similarity_tokens(b)
    if not at or not bt:
        return 0
    overlap = at & bt
    return len(overlap)


def _line_looks_like_responsibility(line: str) -> bool:
    s = _normalize_space(line)
    if len(s) < 24 or len(s) > 220:
        return False
    low = s.lower()
    if any(p in low for p in _JD_JUNK_PHRASES):
        return False
    if any(re.search(p, low, flags=re.IGNORECASE) for p in _JD_JUNK_LINE_PATTERNS):
        return False
    if not any(a in low for a in _RESPONSIBILITY_ACTIONS):
        return False
    if any(low.endswith(p) for p in _RESPONSIBILITY_DANGLING_ENDINGS):
        return False
    if low.startswith(("about ", "benefits", "salary", "location", "company", "requirements")):
        return False
    return True


def _merge_wrapped_jd_lines(job_description: str) -> list[str]:
    raw_lines = [ln.rstrip() for ln in re.split(r"[\r\n]+", str(job_description or "")) if ln.strip()]
    merged: list[str] = []
    for raw_line in raw_lines:
        s = _normalize_space(raw_line.strip(" -\t"))
        if not s:
            continue
        bullet_like = bool(re.match(r"^\s*(?:[-*•]|\d+[\.)])\s+", raw_line))
        if not merged or bullet_like:
            merged.append(s)
            continue
        low = s.lower()
        prev = merged[-1]
        if not re.search(r"[.!?:]$", prev) and (
            s[:1].islower() or any(low.startswith(p) for p in _RESPONSIBILITY_CONTINUATION_PREFIXES)
        ):
            merged[-1] = f"{prev} {s}"
            continue
        merged.append(s)
    return merged


def _clean_requirement_skill(text: str) -> str:
    s = _normalize_space(str(text or ""))
    if not s:
        return ""
    low = s.lower().strip("* -,:;.")
    if len(low) < 3 or len(low) > 64:
        return ""
    if low in _JD_BAD_SKILL_PHRASES:
        return ""
    if any(p in low for p in _JD_JUNK_PHRASES):
        return ""
    if re.fullmatch(r"[a-z]{1,2}", low):
        return ""
    if len(re.findall(r"[a-z]", low)) < 3:
        return ""
    return s.strip("* -,:;.")


def _skill_candidates(profile: dict[str, Any], keyword_bank: dict[str, Any] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        s = _normalize_space(text)
        if not s:
            return
        key = s.lower()
        if len(key) < 2 or key in seen:
            return
        seen.add(key)
        out.append(s)

    for s in _flatten_skills(profile):
        _add(s)

    if isinstance(keyword_bank, dict):
        for s in keyword_bank.get("prompt_keywords") or []:
            _add(str(s))

    return out


def _term_in_blob(term: str, blob: str, synonyms: dict[str, list[str]] | None = None) -> bool:
    t = _normalize_space(term).lower()
    if not t:
        return False
    alts = [t]
    if synonyms and t in synonyms:
        alts.extend(str(x).lower() for x in synonyms.get(t) or [])
    if t in _CREDENTIAL_ALIASES:
        alts.extend(_CREDENTIAL_ALIASES[t])
    return any(a and a in blob for a in alts)


def extract_jd_requirements(
    *,
    job_title: str,
    job_description: str,
    profile: dict[str, Any],
    keyword_bank: dict[str, Any] | None,
    limit_skills: int = 12,
) -> dict[str, Any]:
    """Extract structured requirement hints from the JD using deterministic rules."""
    title = str(job_title or "")
    desc = str(job_description or "")
    hay = f"{title}\n{desc}".lower()
    lines = [ln.strip() for ln in re.split(r"[\n\r;]+", hay) if ln.strip()]
    skills = _skill_candidates(profile, keyword_bank)
    synonyms = build_safe_synonyms(profile)

    must_have: list[str] = []
    nice_to_have: list[str] = []
    required_credentials: list[str] = []
    hard_constraints: list[str] = []
    domains: list[str] = []
    seen_must: set[str] = set()
    seen_nice: set[str] = set()
    seen_cred: set[str] = set()
    seen_hard: set[str] = set()
    seen_domain: set[str] = set()

    def _add_unique(dst: list[str], seen: set[str], value: str) -> None:
        v = _clean_requirement_skill(value)
        if not v:
            return
        key = v.lower()
        if key in seen:
            return
        seen.add(key)
        dst.append(v)

    seniority = "mid"
    if any(k in hay for k in ("principal", "head of", "director", "vp ", "vice president")):
        seniority = "executive"
    elif any(k in hay for k in ("manager", "team lead", "line management", "people management")):
        seniority = "manager"
    elif any(k in hay for k in ("senior", "lead", "staff")):
        seniority = "senior"
    elif any(k in hay for k in ("junior", "entry level", "graduate", "trainee", "apprentice")):
        seniority = "junior"

    for pat, label in _DOMAIN_PATTERNS:
        if re.search(pat, hay, flags=re.IGNORECASE):
            _add_unique(domains, seen_domain, label)

    hard_lines = [ln for ln in lines if any(p in ln for p in _HARD_REQUIREMENT_PHRASES)]
    soft_lines = [ln for ln in lines if any(p in ln for p in _SOFT_REQUIREMENT_PHRASES)]

    for line in hard_lines:
        for skill in skills:
            sk = skill.lower()
            syns = synonyms.get(sk, [])
            if sk in line or any(s in line for s in syns):
                _add_unique(must_have, seen_must, skill)
        for pat, label in _CREDENTIAL_PATTERNS:
            if re.search(pat, line, flags=re.IGNORECASE):
                _add_unique(required_credentials, seen_cred, label)
        for pat, label in _HARD_CONSTRAINT_PATTERNS:
            if re.search(pat, line, flags=re.IGNORECASE):
                _add_unique(hard_constraints, seen_hard, label)

    for line in soft_lines:
        for skill in skills:
            sk = skill.lower()
            syns = synonyms.get(sk, [])
            if sk in line or any(s in line for s in syns):
                if sk not in seen_must:
                    _add_unique(nice_to_have, seen_nice, skill)

    if not must_have:
        for skill in skills:
            sk = skill.lower()
            syns = synonyms.get(sk, [])
            if sk in hay or any(s in hay for s in syns):
                _add_unique(must_have, seen_must, skill)
            if len(must_have) >= max(1, int(limit_skills)):
                break

    must_have = [m for m in must_have if _clean_requirement_skill(m)]
    nice_to_have = [n for n in nice_to_have if _clean_requirement_skill(n)]

    return {
        "must_have_skills": must_have[: max(1, int(limit_skills))],
        "nice_to_have_skills": nice_to_have[: max(0, int(limit_skills // 2 or 1))],
        "required_credentials": required_credentials,
        "hard_constraints": hard_constraints,
        "domains": domains,
        "seniority": seniority,
    }


def extract_job_responsibilities(job_description: str, *, limit: int = 6) -> list[str]:
    """Extract likely top responsibilities from the JD for bullet ordering guidance."""
    raw = str(job_description or "")
    chunks: list[str] = []
    for line in _merge_wrapped_jd_lines(raw):
        chunks.extend(c.strip(" -\t") for c in re.split(r"[;]", line) if c.strip())
    out: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        # Further split long bullet-like clauses.
        parts = [p.strip(" -\t") for p in re.split(r"(?<=[\.!?])\s+", chunk) if p.strip()]
        for part in parts:
            s = _normalize_space(part)
            if not _line_looks_like_responsibility(s):
                continue
            if s.startswith("*"):
                s = s.lstrip("* ")
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
            if len(out) >= max(1, int(limit)):
                return out
    return out


def map_responsibilities_to_evidence(
    responsibilities: list[str],
    facts: list[dict[str, Any]],
    *,
    per_resp_limit: int = 2,
) -> list[dict[str, Any]]:
    """Map JD responsibilities to closest fact-library evidence lines."""
    out: list[dict[str, Any]] = []
    for resp in responsibilities:
        scored: list[tuple[int, dict[str, Any]]] = []
        for fact in facts:
            text = str(fact.get("text") or "").strip()
            if not text:
                continue
            score = _similarity_score(resp, text)
            if score <= 0:
                continue
            scored.append((score, fact))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("id") or "")))
        evidence = [
            {
                "id": str(f.get("id") or ""),
                "text": str(f.get("text") or ""),
                "score": int(score),
            }
            for score, f in scored[: max(1, int(per_resp_limit))]
        ]
        out.append({"responsibility": resp, "evidence": evidence})
    return out


def format_responsibility_map_for_prompt(mapped: list[dict[str, Any]], *, limit: int = 5) -> str:
    rows: list[str] = []
    for item in mapped[: max(1, int(limit))]:
        resp = _normalize_space(str(item.get("responsibility") or ""))
        evidence = item.get("evidence") or []
        if not resp:
            continue
        if not evidence:
            rows.append(f"- Responsibility: {resp}\n  Closest evidence: none found, keep wording conservative.")
            continue
        ev_text = "; ".join(
            f"{str(ev.get('id') or '').strip()}: {str(ev.get('text') or '').strip()}"
            for ev in evidence
            if str(ev.get("text") or "").strip()
        )
        rows.append(f"- Responsibility: {resp}\n  Closest evidence: {ev_text}")
    return "\n".join(rows)


def build_summary_gap_guidance(requirement_gaps: dict[str, Any]) -> str:
    """Return prompt guidance so the summary stays strong without overclaiming."""
    gaps = requirement_gaps if isinstance(requirement_gaps, dict) else {}
    missing_must = [str(x) for x in (gaps.get("missing_must_have_skills") or []) if str(x).strip()]
    missing_hard = [str(x) for x in (gaps.get("missing_hard_requirements") or []) if str(x).strip()]
    missing_domains = [str(x) for x in (gaps.get("missing_domains") or []) if str(x).strip()]

    if not (missing_must or missing_hard or missing_domains):
        return (
            "Summary should lead with the strongest direct match areas and sound confident, "
            "but remain grounded in evidence."
        )

    notes: list[str] = [
        "Summary must stay strong on overlapping experience but avoid implying missing credentials or domain experience.",
        "Do not present gaps as if they are already satisfied.",
    ]
    if missing_hard:
        notes.append("Missing hard requirements: " + ", ".join(missing_hard[:4]))
    if missing_must:
        notes.append("Missing must-have skills: " + ", ".join(missing_must[:5]))
    if missing_domains:
        notes.append("Missing domain context: " + ", ".join(missing_domains[:3]))
    notes.append("Use adjacent strengths, transferable support, and evidence-backed responsibilities instead.")
    return " ".join(notes)


def evaluate_requirement_gaps(
    requirements: dict[str, Any], resume_text: str, profile: dict[str, Any]
) -> dict[str, Any]:
    """Compare extracted JD requirements against resume/profile evidence."""
    req = requirements if isinstance(requirements, dict) else {}
    blob = _profile_evidence_blob(resume_text, profile)
    synonyms = build_safe_synonyms(profile)

    must_have = [str(x) for x in (req.get("must_have_skills") or []) if str(x).strip()]
    nice_to_have = [str(x) for x in (req.get("nice_to_have_skills") or []) if str(x).strip()]
    creds = [str(x) for x in (req.get("required_credentials") or []) if str(x).strip()]
    hard_constraints = [str(x) for x in (req.get("hard_constraints") or []) if str(x).strip()]
    domains = [str(x) for x in (req.get("domains") or []) if str(x).strip()]

    matched_must = [x for x in must_have if _term_in_blob(x, blob, synonyms)]
    missing_must = [x for x in must_have if x not in matched_must]
    matched_nice = [x for x in nice_to_have if _term_in_blob(x, blob, synonyms)]
    matched_creds = [x for x in creds if _term_in_blob(x, blob)]
    missing_creds = [x for x in creds if x not in matched_creds]
    matched_hard = [x for x in hard_constraints if _term_in_blob(x, blob)]
    missing_hard = [x for x in hard_constraints if x not in matched_hard]
    matched_domains = [x for x in domains if _term_in_blob(x, blob)]
    missing_domains = [x for x in domains if x not in matched_domains]

    warnings: list[str] = []
    if missing_must:
        warnings.append("Potentially missing must-have skills: " + ", ".join(missing_must[:6]))
    if missing_domains:
        warnings.append("Potential domain gap: " + ", ".join(missing_domains[:4]))

    return {
        "matched_must_have_skills": matched_must,
        "missing_must_have_skills": missing_must,
        "matched_nice_to_have_skills": matched_nice,
        "matched_credentials": matched_creds,
        "missing_credentials": missing_creds,
        "matched_hard_constraints": matched_hard,
        "missing_hard_requirements": missing_creds + missing_hard,
        "matched_domains": matched_domains,
        "missing_domains": missing_domains,
        "warnings": warnings,
    }


def build_fact_library(resume_text: str, profile: dict[str, Any], *, max_facts: int = 64) -> list[dict[str, Any]]:
    """Build evidence facts from base resume + profile facts."""
    lines = [
        _normalize_space(ln.lstrip("- ").strip())
        for ln in str(resume_text or "").splitlines()
        if _normalize_space(ln).strip()
    ]

    facts_raw: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if len(line) < 22 or len(line) > 240:
            continue
        if line.isupper() and len(line.split()) <= 4:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        facts_raw.append(line)
        if len(facts_raw) >= max_facts:
            break

    rf = profile.get("resume_facts") or {}
    for c in rf.get("preserved_companies") or []:
        s = _normalize_space(str(c))
        if s:
            facts_raw.append(f"Company history includes {s}.")
    for p in rf.get("preserved_projects") or []:
        s = _normalize_space(str(p))
        if s:
            facts_raw.append(f"Project history includes {s}.")
    school = _normalize_space(str(rf.get("preserved_school") or ""))
    if school:
        facts_raw.append(f"Education includes {school}.")
    for m in rf.get("real_metrics") or []:
        s = _normalize_space(str(m))
        if s:
            facts_raw.append(f"Known metric: {s}.")

    out: list[dict[str, Any]] = []
    seen2: set[str] = set()
    i = 0
    for line in facts_raw:
        key = _normalize_space(line).lower()
        if not key or key in seen2:
            continue
        seen2.add(key)
        i += 1
        out.append({"id": f"F{i}", "text": line, "numbers": _extract_numbers(line)})
        if len(out) >= max_facts:
            break
    return out


def format_fact_library_for_prompt(facts: list[dict[str, Any]], *, limit: int = 40) -> str:
    rows: list[str] = []
    for f in facts[: max(0, int(limit))]:
        rows.append(f"{f.get('id')}: {f.get('text')}")
    return "\n".join(rows)


def validate_fact_citations(data: dict[str, Any], valid_fact_ids: set[str]) -> dict[str, Any]:
    """Require each experience/project bullet to cite one or more fact IDs."""
    errors: list[str] = []
    citations: dict[str, list[str]] = {}

    def _scan(section: str, entries: Any) -> None:
        if not isinstance(entries, list):
            return
        for i, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                continue
            bullets = entry.get("bullets") or []
            if not isinstance(bullets, list):
                continue
            for j, b in enumerate(bullets, start=1):
                text = str(b or "").strip()
                key = f"{section}.{i}.{j}"
                m = _CITATION_RE.search(text)
                if not m:
                    errors.append(f"Missing fact citation on {key}")
                    continue
                ids = [x.upper() for x in re.findall(r"F\d+", m.group(1), flags=re.IGNORECASE)]
                if not ids:
                    errors.append(f"Invalid fact citation on {key}")
                    continue
                unknown = [x for x in ids if x not in valid_fact_ids]
                if unknown:
                    errors.append(f"Unknown fact IDs on {key}: {', '.join(unknown)}")
                    continue
                citations[key] = ids

    _scan("experience", data.get("experience"))
    _scan("projects", data.get("projects"))
    return {"passed": len(errors) == 0, "errors": errors, "citations": citations}


def strip_fact_citations(text: str) -> str:
    s = str(text or "")
    # Remove citation blocks like [F1] / [F2, F7] even when punctuation follows.
    s = re.sub(_CITATION_BLOCK_RE, "", s)
    # Normalize spacing around punctuation after block removal.
    s = re.sub(r"\s+([\.,;:!?])", r"\1", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def build_safe_synonyms(profile: dict[str, Any]) -> dict[str, list[str]]:
    """Return safe canonical->synonyms map constrained by profile skill boundary."""
    allowed = {s.lower() for s in _flatten_skills(profile)}
    cfg = (profile.get("tailoring") or {}).get("safe_synonyms")

    merged: dict[str, list[str]] = {k: list(v) for k, v in _DEFAULT_SAFE_SYNONYMS.items()}
    if isinstance(cfg, dict):
        for k, v in cfg.items():
            ck = _normalize_space(str(k)).lower()
            if not ck:
                continue
            vals: list[str] = []
            if isinstance(v, list):
                vals = [_normalize_space(str(x)).lower() for x in v if _normalize_space(str(x))]
            elif isinstance(v, str):
                vals = [_normalize_space(x).lower() for x in v.split(",") if _normalize_space(x)]
            if vals:
                merged[ck] = vals

    out: dict[str, list[str]] = {}
    for k, vals in merged.items():
        if allowed and k not in allowed:
            continue
        cleaned = []
        seen: set[str] = set()
        for v in vals:
            s = _normalize_space(v).lower()
            if not s or s in seen:
                continue
            seen.add(s)
            cleaned.append(s)
        if cleaned:
            out[k] = cleaned
    return out


def build_jd_targets(
    job_description: str,
    profile: dict[str, Any],
    keyword_bank: dict[str, Any] | None,
    *,
    resume_text: str,
    limit: int = 14,
) -> dict[str, Any]:
    """Build JD target terms and synonym map for coverage scoring."""
    jd_l = str(job_description or "").lower()
    res_l = str(resume_text or "").lower()
    skills = _flatten_skills(profile)
    synonyms = build_safe_synonyms(profile)

    targets: list[str] = []
    seen: set[str] = set()

    def _add(term: str) -> None:
        t = _normalize_space(term)
        if not t:
            return
        k = t.lower()
        if k in seen:
            return
        seen.add(k)
        targets.append(t)

    for s in skills:
        sl = s.lower()
        syns = synonyms.get(sl, [])
        if sl in jd_l or any(x in jd_l for x in syns):
            if sl in res_l or not res_l:
                _add(s)

    if isinstance(keyword_bank, dict):
        for k in keyword_bank.get("prompt_keywords") or []:
            ks = _normalize_space(str(k))
            if not ks:
                continue
            if ks.lower() in jd_l:
                _add(ks)

    targets = targets[: max(1, int(limit))]
    syn_map = {t.lower(): synonyms.get(t.lower(), []) for t in targets}
    return {"targets": targets, "synonyms": syn_map}


def score_jd_coverage(text: str, targets: list[str], synonyms: dict[str, list[str]]) -> dict[str, Any]:
    content = str(text or "").lower()
    covered: list[str] = []
    missing: list[str] = []
    for t in targets:
        tl = t.lower()
        syns = synonyms.get(tl, [])
        hit = (tl in content) or any(s in content for s in syns)
        if hit:
            covered.append(t)
        else:
            missing.append(t)
    ratio = (len(covered) / len(targets)) if targets else 0.0
    return {
        "ratio": ratio,
        "covered": covered,
        "missing": missing,
    }


def collect_evidence_numbers(resume_text: str, profile: dict[str, Any], facts: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set(_extract_numbers(resume_text))
    for f in facts:
        for n in f.get("numbers") or []:
            out.add(str(n))

    rf = profile.get("resume_facts") or {}
    for m in rf.get("real_metrics") or []:
        for n in _extract_numbers(str(m)):
            out.add(n)

    rs = profile.get("resume_sections") or {}
    if isinstance(rs, dict):
        for key in ("education", "technical_environment"):
            val = rs.get(key)
            if isinstance(val, list):
                for line in val:
                    for n in _extract_numbers(str(line)):
                        out.add(n)
    return out


def check_quant_consistency(data: dict[str, Any], evidence_numbers: set[str]) -> dict[str, Any]:
    """Reject numbers that are not in source evidence unless explicitly marked derived."""
    errors: list[str] = []

    lines: list[str] = []
    lines.append(str(data.get("summary") or ""))

    for section in ("experience", "projects"):
        entries = data.get(section) or []
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict):
                continue
            for b in e.get("bullets") or []:
                lines.append(str(b))

    missing: set[str] = set()
    for line in lines:
        ll = line.lower()
        if "derived" in ll:
            continue
        for n in _extract_numbers(line):
            if n not in evidence_numbers:
                missing.add(n)

    if missing:
        errors.append("Numbers not in evidence: " + ", ".join(sorted(missing)[:8]))

    return {"passed": len(errors) == 0, "errors": errors, "missing": sorted(missing)}


def estimate_readability_score(text: str) -> float:
    """Very small readability heuristic score in [0, 1]."""
    t = _normalize_space(text)
    if not t:
        return 0.0
    sents = [s.strip() for s in re.split(r"[.!?]+", t) if s.strip()]
    words = re.findall(r"[A-Za-z0-9%$+-]+", t)
    if not sents or not words:
        return 0.0
    avg = len(words) / max(1, len(sents))
    # Reward 10-24 words/sentence; decay outside range.
    if 10 <= avg <= 24:
        return 1.0
    if avg < 10:
        return max(0.0, avg / 10.0)
    return max(0.0, 1.0 - min(1.0, (avg - 24.0) / 24.0))


def rank_candidate(
    *,
    text: str,
    coverage_ratio: float,
    validator_errors: list[str],
    validator_warnings: list[str],
    citation_errors: list[str],
    quant_errors: list[str],
) -> dict[str, Any]:
    """Return deterministic ranking score and breakdown."""
    readability = estimate_readability_score(text)
    score = 0.0
    score += max(0.0, min(1.0, float(coverage_ratio))) * 60.0
    score += readability * 20.0
    score -= min(70.0, float(len(validator_errors)) * 8.0)
    score -= min(25.0, float(len(validator_warnings)) * 1.5)
    score -= min(40.0, float(len(citation_errors)) * 10.0)
    score -= min(35.0, float(len(quant_errors)) * 10.0)
    return {
        "score": score,
        "readability": readability,
        "coverage": coverage_ratio,
        "error_count": len(validator_errors) + len(citation_errors) + len(quant_errors),
    }
