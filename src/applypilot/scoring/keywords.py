"""Keyword extraction helpers for ATS alignment.

This module builds a *controlled* keyword bank derived from the job description
and constrained by what the candidate already has in their base/tailored resume
and skills boundary.

Two outputs:
- prompt_keywords: small list used to guide LLM generation (avoid stuffing)
- highlight_keywords: slightly larger list used only for PDF bold highlighting
"""

from __future__ import annotations

import re


_STOPWORDS: set[str] = {
    "the",
    "and",
    "with",
    "for",
    "you",
    "your",
    "our",
    "a",
    "an",
    "to",
    "of",
    "in",
    "on",
    "as",
    "at",
    "by",
    "from",
    "or",
    "is",
    "are",
    "be",
    "will",
    "can",
    "may",
    "must",
    "should",
    "able",
    "ability",
    "skills",
    "skill",
    "experience",
    "responsibilities",
    "responsibility",
    "requirements",
    "requirement",
    "preferred",
    "including",
    "plus",
    "position",
    "role",
}

_DENY: set[str] = {
    # HR / benefits / boilerplate
    "benefits",
    "pension",
    "holiday",
    "salary",
    "bonus",
    "bonuses",
    "remote",
    "hybrid",
    "flexible",
    "training",
    "development",
    "inclusive",
    "diversity",
    "equity",
    # common noise
    "competitive",
    "fast-paced",
    "dynamic",
}


def _clean(s: str) -> str:
    s2 = " ".join(str(s or "").split())
    return s2.strip().strip(".,;:|- ")


def _flatten_skills_boundary(profile: dict) -> list[str]:
    boundary = profile.get("skills_boundary", {}) or {}
    out: list[str] = []
    for items in boundary.values():
        if not isinstance(items, list):
            continue
        for it in items:
            s = _clean(str(it))
            if s:
                out.append(s)
    return out


def build_keyword_bank(
    job_description: str,
    profile: dict,
    resume_text: str = "",
    prompt_limit: int = 14,
    highlight_limit: int = 24,
    seeded_phrases: list[str] | None = None,
) -> dict:
    """Build a controlled keyword bank from JD + resume.

    Design goal: only suggest keywords the candidate already has evidence for
    (present in resume_text and/or in skills_boundary). This reduces fabrication
    risk while improving ATS mirroring.

    Returns:
        {"prompt_keywords": [...], "highlight_keywords": [...]}.
    """
    jd = str(job_description or "")
    res = str(resume_text or "")
    jd_l = jd.lower()
    res_l = res.lower()

    allowed_skills = _flatten_skills_boundary(profile)
    allowed_skills_l = [s.lower() for s in allowed_skills]

    # 1) Skills in both JD and resume (highest-signal + safest)
    skill_hits: list[str] = []
    for s, sl in zip(allowed_skills, allowed_skills_l):
        if len(sl) < 3:
            continue
        if sl in jd_l and (not res_l or sl in res_l):
            skill_hits.append(s)

    # 2) Acronyms that appear in both JD and resume (e.g. KPI, GDPR)
    acronyms: list[str] = []
    for a in re.findall(r"\b[A-Z]{2,6}\b", jd):
        if a in {"THE", "AND", "FOR", "WITH"}:
            continue
        if res and a in res:
            acronyms.append(a)

    # 3) Phrase overlap: ngrams from JD that appear in resume
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9+./-]*", jd)
    tokens_l = [t.lower() for t in tokens]

    phrase_counts: dict[str, int] = {}
    for n in (2, 3):
        for i in range(0, max(0, len(tokens_l) - n + 1)):
            seg = tokens_l[i : i + n]
            if not seg:
                continue

            # Skip phrases that are all stopwords
            if all(w in _STOPWORDS for w in seg):
                continue

            phrase = " ".join(seg)
            if len(phrase) < 6 or len(phrase) > 42:
                continue

            # Skip phrases containing deny terms
            if any(w in _DENY for w in seg):
                continue

            # Require the phrase to exist in the resume text (evidence)
            if res_l and phrase not in res_l:
                continue

            phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1

    phrase_ranked = sorted(phrase_counts.keys(), key=lambda p: (phrase_counts[p], len(p)), reverse=True)
    phrase_hits = [p for p in phrase_ranked if p not in _DENY][: max(0, highlight_limit)]

    seed_hits: list[str] = []
    for phrase in seeded_phrases or []:
        p = _clean(str(phrase))
        pl = p.lower()
        if not p or len(pl) < 5 or len(pl) > 80:
            continue
        words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9+./-]*", pl) if w]
        if words and all(w in _STOPWORDS or w in _DENY for w in words):
            continue
        if not res_l:
            continue
        words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9+./-]*", pl) if len(w) >= 3]
        if pl in jd_l and pl in res_l:
            seed_hits.append(p)
        elif words and sum(1 for w in words if w in jd_l and w in res_l) >= max(2, min(len(words), 3)):
            seed_hits.append(p)

    # De-dup, preserve order
    def _dedupe(seq: list[str], limit: int) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in seq:
            s = _clean(x)
            if not s:
                continue
            k = s.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(s)
            if len(out) >= limit:
                break
        return out

    prompt_keywords = _dedupe(skill_hits + acronyms + phrase_hits, prompt_limit)
    highlight_keywords = _dedupe(prompt_keywords + seed_hits + phrase_hits, highlight_limit)

    return {"prompt_keywords": prompt_keywords, "highlight_keywords": highlight_keywords}
