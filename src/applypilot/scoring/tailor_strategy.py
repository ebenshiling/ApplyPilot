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

    support_hits = sum(
        1 for k in ("support", "helpdesk", "service desk", "incident", "desktop", "it operations") if k in hay
    )
    eng_hits = sum(
        1
        for k in (
            "engineer",
            "developer",
            "software",
            "backend",
            "frontend",
            "full stack",
            "devops",
            "sre",
            "platform",
            "architecture",
        )
        if k in hay
    )
    data_hits = sum(
        1
        for k in (
            "analyst",
            "analytics",
            "business intelligence",
            "bi ",
            "reporting",
            "dashboard",
            "data quality",
            "insight",
        )
        if k in hay
    )

    pack = "engineering"
    reason = "keyword_default"
    if support_hits > 0 and support_hits >= max(data_hits, eng_hits):
        pack = "support"
    elif data_hits > 0 and data_hits >= max(support_hits, eng_hits):
        pack = "data_bi"

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

    if role_pack == "support":
        mr = {"min": 4, "max": 6}
        ot = {"min": 2, "max": 4}
        proj = {"min": 1, "max": 2}
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
            "projects": True,
            "education": True,
        },
    }

    hint = (
        f"Most recent role: {mr['min']}-{mr['max']} bullets. "
        f"Other roles: {ot['min']}-{ot['max']} bullets. "
        f"Projects: {proj['min']}-{proj['max']} bullets each."
    )

    return {"runtime_rules": runtime_rules, "hint": hint, "senior": senior}


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
