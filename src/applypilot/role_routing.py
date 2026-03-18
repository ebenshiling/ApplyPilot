"""Deterministic routing helpers for multi-role job searches.

This module chooses a base resume variant per job using keyword matching.

Configuration lives in ~/.applypilot/searches.yaml (loaded via load_search_config),
so it is user-controlled and not hardcoded in code.

Example:

resume_routing:
  variants:
    it_support: "resume_variants/it_support.txt"
    data_analyst: "resume_variants/data_analyst.txt"
    software_testing: "resume_variants/software_testing.txt"
  families:
    - name: "data"
      variant: "data_analyst"
      keywords: ["data analyst", "power bi", "sql", "python", "reporting"]
    - name: "support"
      variant: "it_support"
      keywords: ["service desk", "helpdesk", "m365", "microsoft 365"]
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from applypilot.config import APP_DIR, RESUME_PATH, RESUME_VARIANTS_DIR, load_search_config

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoutedResume:
    key: str
    path: str
    text: str
    score: int


_TEXT_CACHE: dict[str, str] = {}


def _read_text_cached(path: Path) -> str:
    k = str(path.resolve())
    if k in _TEXT_CACHE:
        return _TEXT_CACHE[k]
    try:
        t = path.read_text(encoding="utf-8")
    except Exception:
        t = ""
    _TEXT_CACHE[k] = t
    return t


def _job_blob(job: dict) -> str:
    parts = [
        str(job.get("title") or ""),
        str(job.get("company") or ""),
        str(job.get("site") or ""),
        str(job.get("description") or ""),
        str(job.get("full_description") or ""),
    ]
    return "\n".join(p for p in parts if p).lower()


def _resolve_variant_path(v: str) -> Path | None:
    vv = str(v or "").strip()
    if not vv:
        return None

    # Allow absolute path.
    p = Path(vv).expanduser()
    if p.is_absolute():
        return p if p.exists() else None

    # Allow paths relative to ~/.applypilot.
    p2 = (APP_DIR / vv).expanduser()
    if p2.exists():
        return p2

    # Allow just a key, resolved to resume_variants/<key>.txt
    p3 = (RESUME_VARIANTS_DIR / f"{vv}.txt").expanduser()
    if p3.exists():
        return p3

    return None


def route_resume_for_job(job: dict) -> RoutedResume:
    """Pick the best resume variant for this job (keyword match), else default resume.txt."""
    cfg = load_search_config() or {}
    rr = cfg.get("resume_routing") if isinstance(cfg, dict) else None
    if not isinstance(rr, dict):
        base = _read_text_cached(RESUME_PATH)
        return RoutedResume(key="default", path=str(RESUME_PATH), text=base, score=0)

    variants = rr.get("variants")
    families = rr.get("families")
    if not isinstance(variants, dict) or not isinstance(families, list):
        base = _read_text_cached(RESUME_PATH)
        return RoutedResume(key="default", path=str(RESUME_PATH), text=base, score=0)

    blob = _job_blob(job)
    best: tuple[int, dict] | None = None
    for fam in families:
        if not isinstance(fam, dict):
            continue
        kw = fam.get("keywords")
        if not isinstance(kw, list) or not kw:
            continue
        hits = 0
        for k in kw:
            ks = str(k or "").strip().lower()
            if not ks:
                continue
            if ks in blob:
                hits += 1
        if hits <= 0:
            continue
        if best is None or hits > best[0]:
            best = (hits, fam)

    if not best:
        base = _read_text_cached(RESUME_PATH)
        return RoutedResume(key="default", path=str(RESUME_PATH), text=base, score=0)

    fam = best[1]
    variant_key = str(fam.get("variant") or "").strip()
    if not variant_key:
        base = _read_text_cached(RESUME_PATH)
        return RoutedResume(key="default", path=str(RESUME_PATH), text=base, score=int(best[0]))

    variant_path_raw = variants.get(variant_key)
    vp = _resolve_variant_path(str(variant_path_raw or variant_key))
    if not vp:
        base = _read_text_cached(RESUME_PATH)
        return RoutedResume(key="default", path=str(RESUME_PATH), text=base, score=int(best[0]))

    txt = _read_text_cached(vp)
    if not txt.strip():
        base = _read_text_cached(RESUME_PATH)
        return RoutedResume(key="default", path=str(RESUME_PATH), text=base, score=int(best[0]))

    return RoutedResume(key=variant_key, path=str(vp), text=txt, score=int(best[0]))


def clear_resume_text_cache() -> None:
    _TEXT_CACHE.clear()
