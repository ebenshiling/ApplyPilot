"""UK visa sponsorship helpers.

This module does two things:
1) Caches the official UK Home Office register of licensed sponsors (workers).
2) Extracts sponsorship signals from job descriptions.

The goal is not to guarantee sponsorship (only employers can do that) but to:
- flag obvious "no sponsorship" roles early
- highlight employers that are licensed sponsors
"""

from __future__ import annotations

import csv
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from applypilot.config import APP_DIR


GOVUK_REGISTER_PAGE = "https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers"
CSV_URL_RE = re.compile(r"https://assets\.publishing\.service\.gov\.uk/[^\s\)\"]+Worker_and_Temporary_Worker\.csv")


_lock = threading.Lock()
_index: dict[str, str] | None = None  # normalized -> original
_index_loaded_at: float | None = None


def _cache_dir(app_dir: Path | None = None) -> Path:
    base = app_dir if app_dir is not None else APP_DIR
    return Path(base) / "cache" / "uk_sponsors"


def _norm_name(name: str) -> str:
    s = (name or "").strip().upper()
    if not s:
        return ""
    # Normalize punctuation/whitespace but keep words (to reduce false positives).
    s = re.sub(r"[&]", " AND ", s)
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _strip_suffixes(norm: str) -> str:
    """Remove common legal suffixes to improve exact matches."""
    s = (norm or "").strip()
    if not s:
        return ""
    # Order matters (longer first).
    suffixes = (
        " PUBLIC LIMITED COMPANY",
        " LIMITED LIABILITY PARTNERSHIP",
        " LIMITED",
        " LTD",
        " PLC",
        " LLP",
    )
    for suf in suffixes:
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
            break
    return s


def _get_latest_csv_url(timeout: float = 30.0) -> str | None:
    try:
        r = httpx.get(GOVUK_REGISTER_PAGE, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
    except Exception:
        return None
    m = CSV_URL_RE.search(r.text or "")
    return m.group(0) if m else None


def ensure_sponsor_register_cached(
    *,
    max_age_days: int = 7,
    app_dir: Path | None = None,
    timeout: float = 45.0,
) -> Path | None:
    """Ensure the sponsor CSV is cached locally.

    Returns the CSV path if available, else None.
    """
    cd = _cache_dir(app_dir)
    cd.mkdir(parents=True, exist_ok=True)

    csv_path = cd / "worker_and_temporary_worker.csv"
    meta_path = cd / "meta.json"

    # Fresh enough cache?
    try:
        if csv_path.exists():
            mtime = datetime.fromtimestamp(csv_path.stat().st_mtime, tz=timezone.utc)
            if datetime.now(timezone.utc) - mtime <= timedelta(days=max_age_days):
                return csv_path
    except Exception:
        pass

    csv_url = _get_latest_csv_url(timeout=timeout)
    if not csv_url:
        return csv_path if csv_path.exists() else None

    try:
        with httpx.stream("GET", csv_url, timeout=timeout, follow_redirects=True) as r:
            r.raise_for_status()
            tmp = csv_path.with_suffix(".tmp")
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes():
                    if chunk:
                        f.write(chunk)
            tmp.replace(csv_path)
    except Exception:
        return csv_path if csv_path.exists() else None

    try:
        meta = {
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "source_page": GOVUK_REGISTER_PAGE,
            "csv_url": csv_url,
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=True, indent=2), encoding="utf-8")
    except Exception:
        pass

    return csv_path


def _load_index(csv_path: Path) -> dict[str, str]:
    """Load sponsor register into a normalized lookup."""
    index: dict[str, str] = {}

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        # Column name varies slightly over time.
        org_col = None
        if reader.fieldnames:
            for c in reader.fieldnames:
                if not c:
                    continue
                cl = c.strip().lower()
                if "organisation" in cl and "name" in cl:
                    org_col = c
                    break
                if "organization" in cl and "name" in cl:
                    org_col = c
                    break
        if not org_col:
            # Fall back: first column.
            org_col = (reader.fieldnames or [""])[0]

        for row in reader:
            raw = str(row.get(org_col) or "").strip()
            if not raw:
                continue
            n = _norm_name(raw)
            if not n:
                continue
            # Prefer first seen (stable), store exact normalized.
            index.setdefault(n, raw)
            # Also store suffix-stripped variant for common "Ltd" mismatches.
            stripped = _strip_suffixes(n)
            if stripped and stripped != n:
                index.setdefault(stripped, raw)

    return index


def get_sponsor_index(*, app_dir: Path | None = None) -> dict[str, str] | None:
    """Return normalized sponsor index (cached in-memory)."""
    global _index, _index_loaded_at
    with _lock:
        if _index is not None:
            return _index

        csv_path = ensure_sponsor_register_cached(app_dir=app_dir)
        if not csv_path or not csv_path.exists():
            return None

        try:
            _index = _load_index(csv_path)
            _index_loaded_at = time.time()
            return _index
        except Exception:
            _index = None
            _index_loaded_at = None
            return None


@dataclass(frozen=True)
class SponsorshipSignals:
    sponsorship_explicit: str  # Yes|No|Unknown
    sponsorship_evidence: str
    sponsor_licensed: str  # Yes|No|Unknown
    sponsor_match_name: str
    sponsor_match_confidence: float
    sponsor_source: str


_NEGATIVE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(no|not)\s+(offer|provid(e|ing))\s+(visa\s+)?sponsorship\b", re.I),
    re.compile(r"\b(unable|cannot|can\s*not)\s+to\s+(offer|provide)\s+(visa\s+)?sponsorship\b", re.I),
    re.compile(r"\bwe\s+do\s+not\s+sponsor\b", re.I),
    re.compile(r"\b(no)\s+sponsorship\b", re.I),
    re.compile(r"\bwithout\s+(visa\s+)?sponsorship\b", re.I),
]

_CONDITIONAL_PATTERNS: list[re.Pattern] = [
    # Common NHS wording: conditional on salary/band/occupation rules.
    re.compile(r"\bunable\s+to\s+offer\s+sponsorship\s+for\s+roles\s+with\s+a\s+salary\s+of\s+less\s+than\b", re.I),
    re.compile(r"\bthis\s+rules?\s+out\s+sponsorship\s+for\s+band\s*2\b", re.I),
    re.compile(r"\brules?\s+out\s+sponsorship\b", re.I),
    re.compile(r"\bdetermine\s+the\s+likelihood\s+of\s+obtaining\s+sponsorship\b", re.I),
]

_POSITIVE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bvisa\s+sponsorship\s+(is\s+)?(available|provided|offered)\b", re.I),
    re.compile(r"\b(certificate|cert)\s+of\s+sponsorship\b", re.I),
    re.compile(r"\b\bCoS\b\b", re.I),
    re.compile(r"\bskilled\s+worker\s+visa\b", re.I),
    re.compile(r"\bhealth\s+and\s+care\s+worker\s+visa\b", re.I),
]


def detect_sponsorship_from_text(text: str | None) -> tuple[str, str]:
    """Return (Yes|No|Conditional|Unknown, evidence_snippet)."""
    t = (text or "").strip()
    if not t:
        return "Unknown", ""

    # Limit scanning cost.
    tl = t[:20000]

    for p in _NEGATIVE_PATTERNS:
        m = p.search(tl)
        if m:
            ev = (m.group(0) or "").strip()
            return "No", ev[:120]

    for p in _CONDITIONAL_PATTERNS:
        m = p.search(tl)
        if m:
            ev = (m.group(0) or "").strip()
            return "Conditional", ev[:160]

    for p in _POSITIVE_PATTERNS:
        m = p.search(tl)
        if m:
            ev = (m.group(0) or "").strip()
            return "Yes", ev[:120]

    return "Unknown", ""


def match_licensed_sponsor(company: str | None, *, app_dir: Path | None = None) -> tuple[str, str, float, str]:
    """Return (Yes|No|Unknown, match_name, confidence, source_label)."""
    raw = str(company or "").strip()
    if not raw:
        return "Unknown", "", 0.0, ""

    idx = get_sponsor_index(app_dir=app_dir)
    if not idx:
        return "Unknown", "", 0.0, ""

    n = _norm_name(raw)
    if not n:
        return "Unknown", "", 0.0, ""

    # Exact normalized match.
    m = idx.get(n)
    if m:
        return "Yes", str(m), 1.0, "GOVUK_Register_Licensed_Sponsors_Workers"

    # Common suffix variants.
    stripped = _strip_suffixes(n)
    m = idx.get(stripped)
    if m:
        return "Yes", str(m), 0.95, "GOVUK_Register_Licensed_Sponsors_Workers"

    return "No", "", 0.0, "GOVUK_Register_Licensed_Sponsors_Workers"


def annotate_sponsorship(
    *,
    company: str | None,
    text: str | None,
    app_dir: Path | None = None,
) -> SponsorshipSignals:
    policy, evidence = detect_sponsorship_from_text(text)
    licensed, match_name, conf, src = match_licensed_sponsor(company, app_dir=app_dir)
    return SponsorshipSignals(
        sponsorship_explicit=policy,
        sponsorship_evidence=evidence,
        sponsor_licensed=licensed,
        sponsor_match_name=match_name,
        sponsor_match_confidence=float(conf or 0.0),
        sponsor_source=src,
    )
