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
  context_families:
    - name: "public_sector_main"
      variant: "public_sector_main"
      keywords: ["nhs", "council", "civil service", "university"]
  context_routing:
    enabled: true
    override_on_strong_match: true
    min_hits: 2
    min_margin_vs_role: 1
  blended_routes:
    enabled: true
    combos:
      - role_variant: "application_support_engineer"
        context_variant: "public_sector_main"
        variant: "public_application_support"
      - role_variant: "technical_systems_analyst"
        context_variant: "commercial_main"
        variant: "commercial_technical_support"
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


def _best_family(blob: str, families: list[dict] | None) -> tuple[int, dict] | None:
    if not isinstance(families, list):
        return None
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
    return best


def _resolve_routed_resume(
    best: tuple[int, dict] | None,
    variants: dict,
) -> RoutedResume | None:
    if not best:
        return None
    fam = best[1]
    variant_key = str(fam.get("variant") or "").strip()
    if not variant_key:
        return None
    variant_path_raw = variants.get(variant_key)
    vp = _resolve_variant_path(str(variant_path_raw or variant_key))
    if not vp:
        return None
    txt = _read_text_cached(vp)
    if not txt.strip():
        return None
    return RoutedResume(key=variant_key, path=str(vp), text=txt, score=int(best[0]))


def _resolve_variant_key(variant_key: str, variants: dict, score: int) -> RoutedResume | None:
    vk = str(variant_key or "").strip()
    if not vk:
        return None
    variant_path_raw = variants.get(vk)
    vp = _resolve_variant_path(str(variant_path_raw or vk))
    if not vp:
        return None
    txt = _read_text_cached(vp)
    if not txt.strip():
        return None
    return RoutedResume(key=vk, path=str(vp), text=txt, score=int(score))


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
    context_families = rr.get("context_families")
    context_routing = rr.get("context_routing")
    blended_routes = rr.get("blended_routes")
    if not isinstance(variants, dict) or not isinstance(families, list):
        base = _read_text_cached(RESUME_PATH)
        return RoutedResume(key="default", path=str(RESUME_PATH), text=base, score=0)

    blob = _job_blob(job)
    role_best = _best_family(blob, families)
    context_best = _best_family(blob, context_families)

    ctx_enabled = True
    ctx_override = True
    ctx_min_hits = 2
    ctx_min_margin = 1
    if isinstance(context_routing, dict):
        if "enabled" in context_routing:
            ctx_enabled = bool(context_routing.get("enabled"))
        if "override_on_strong_match" in context_routing:
            ctx_override = bool(context_routing.get("override_on_strong_match"))
        try:
            ctx_min_hits = max(1, int(context_routing.get("min_hits", 2)))
        except Exception:
            ctx_min_hits = 2
        try:
            ctx_min_margin = max(0, int(context_routing.get("min_margin_vs_role", 1)))
        except Exception:
            ctx_min_margin = 1

    blend_enabled = False
    blend_combos: list[dict] = []
    if isinstance(blended_routes, dict):
        blend_enabled = bool(blended_routes.get("enabled"))
        combos = blended_routes.get("combos")
        if isinstance(combos, list):
            blend_combos = [c for c in combos if isinstance(c, dict)]

    if blend_enabled and role_best and context_best:
        role_variant = str((role_best[1] or {}).get("variant") or "").strip()
        context_variant = str((context_best[1] or {}).get("variant") or "").strip()
        combo_score = int(role_best[0]) + int(context_best[0])
        for combo in blend_combos:
            if str(combo.get("role_variant") or "").strip() != role_variant:
                continue
            if str(combo.get("context_variant") or "").strip() != context_variant:
                continue
            combo_variant = str(combo.get("variant") or "").strip()
            routed = _resolve_variant_key(combo_variant, variants, combo_score)
            if routed is not None:
                return routed

    choice_order: list[tuple[int, dict] | None] = []
    if ctx_enabled and ctx_override and context_best:
        ctx_score = int(context_best[0])
        role_score = int(role_best[0]) if role_best else 0
        if ctx_score >= ctx_min_hits and ctx_score >= (role_score + ctx_min_margin):
            choice_order = [context_best, role_best]

    if not choice_order:
        if ctx_enabled:
            choice_order = [role_best, context_best]
        else:
            choice_order = [role_best]

    for best in choice_order:
        routed = _resolve_routed_resume(best, variants)
        if routed is not None:
            return routed

    fallback_score = 0
    if role_best:
        fallback_score = int(role_best[0])
    elif context_best:
        fallback_score = int(context_best[0])
    base = _read_text_cached(RESUME_PATH)
    return RoutedResume(key="default", path=str(RESUME_PATH), text=base, score=fallback_score)


def clear_resume_text_cache() -> None:
    _TEXT_CACHE.clear()
