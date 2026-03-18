"""Naming helpers for filenames and display names.

We keep the resume/CV header as the legal full name, but recruiter-visible
filenames should follow a UK-friendly "First_Last_..." style.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata


def display_name(personal: dict) -> str:
    """Preferred first name + legal last name.

    - preferred_name: optional first name used for day-to-day
    - full_name: legal name; we use its last token as the surname
    """
    full = (personal.get("full_name") or "").strip()
    preferred = (personal.get("preferred_name") or "").strip()

    parts = [p for p in full.split(" ") if p]
    last = parts[-1] if parts else ""

    first = preferred or (parts[0] if parts else "")
    out = f"{first} {last}".strip()
    return out or full


def slug_for_filename(name: str) -> str:
    """Make a filesystem-safe ASCII slug for recruiter-visible filenames."""
    if not name:
        return "Candidate"

    # Normalize to ASCII (drop accents), then sanitize.
    norm = unicodedata.normalize("NFKD", str(name))
    ascii_name = norm.encode("ascii", "ignore").decode("ascii")
    ascii_name = ascii_name.strip()

    ascii_name = re.sub(r"\s+", "_", ascii_name)
    ascii_name = re.sub(r"[^A-Za-z0-9_-]", "", ascii_name)
    ascii_name = re.sub(r"_+", "_", ascii_name).strip("_")
    return ascii_name or "Candidate"


def _safe_token(text: str, *, max_len: int = 28, fallback: str = "item") -> str:
    t = slug_for_filename(text or "")
    if not t:
        t = fallback
    return t[:max_len] if len(t) > max_len else t


def _job_number(job: dict | None) -> str:
    """Return a stable job number token for filenames when available."""
    if not isinstance(job, dict) or not job:
        return ""

    raw = job.get("job_id")
    if raw is None:
        raw = job.get("id")
    if raw is None:
        raw = job.get("rowid")

    try:
        num = int(str(raw).strip())
    except Exception:
        return ""

    if num <= 0:
        return ""
    return f"J{num}"


def _job_suffix(job: dict | None) -> str:
    if not isinstance(job, dict) or not job:
        return ""
    role = str(job.get("search_query") or job.get("title") or "")
    site = str(job.get("site") or "")
    url = str(job.get("url") or job.get("application_url") or "")
    job_num = _job_number(job)
    uid = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8] if url else "unknown"
    role_t = _safe_token(role, max_len=24, fallback="role")
    site_t = _safe_token(site, max_len=16, fallback="site")
    if job_num:
        return f"{job_num}_{role_t}_{site_t}_{uid}"
    return f"{role_t}_{site_t}_{uid}"


def _filename_prefix(personal: dict, username: str = "") -> str:
    """Choose recruiter-visible filename prefix.

    Prefer the person's name. Fall back to username only when the name is
    missing/invalid and would otherwise collapse to "Candidate".
    """
    user_slug = _safe_token(username, max_len=20, fallback="") if username else ""
    base_slug = slug_for_filename(display_name(personal))
    if base_slug and base_slug != "Candidate":
        return base_slug
    return user_slug or base_slug


def cv_filename(personal: dict, ext: str = "pdf", *, username: str = "", job: dict | None = None) -> str:
    prefix = _filename_prefix(personal, username=username)
    job_suf = _job_suffix(job)
    if job_suf:
        return f"{prefix}_CV_{job_suf}.{ext}"
    return f"{prefix}_CV.{ext}"


def cover_letter_filename(personal: dict, ext: str = "pdf", *, username: str = "", job: dict | None = None) -> str:
    prefix = _filename_prefix(personal, username=username)
    job_suf = _job_suffix(job)
    if job_suf:
        return f"{prefix}_Cover_Letter_{job_suf}.{ext}"
    return f"{prefix}_Cover_Letter.{ext}"


def supporting_statement_filename(
    personal: dict, ext: str = "txt", *, username: str = "", job: dict | None = None
) -> str:
    prefix = _filename_prefix(personal, username=username)
    job_suf = _job_suffix(job)
    if job_suf:
        return f"{prefix}_Supporting_Statement_{job_suf}.{ext}"
    return f"{prefix}_Supporting_Statement.{ext}"
