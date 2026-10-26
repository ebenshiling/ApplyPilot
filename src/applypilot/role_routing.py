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
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from applypilot.config import APP_DIR, RESUME_PATH, RESUME_VARIANTS_DIR, load_search_config
from applypilot.read_model import reuse_read

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoutedResume:
    key: str
    path: str
    text: str
    score: int
    reason: str = "keyword match"
    ambiguous: bool = False
    candidates: tuple = ()

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    def public(self) -> dict:
        return {"key": self.key, "score": self.score, "reason": self.reason, "ambiguous": self.ambiguous,
                "candidates": list(self.candidates), "fingerprint": self.fingerprint}


_TEXT_CACHE: dict[str, tuple] = {}


def _read_text_cached(path: Path) -> str:
    k = str(path.resolve())
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        if k in _TEXT_CACHE and _TEXT_CACHE[k][0] == stamp:
            return _TEXT_CACHE[k][1]
        t = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    _TEXT_CACHE[k] = (stamp, t)
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


def _best_family(blob: str, families: list[dict] | None, *, title_blob: str = "") -> tuple[int, dict] | None:
    if not isinstance(families, list):
        return None
    best: tuple[int, dict] | None = None
    title_lower = str(title_blob or "").lower()
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
            if ks in title_lower:
                hits += 3
            elif ks in blob:
                hits += 1
        if hits <= 0:
            continue
        if best is None or hits > best[0]:
            best = (hits, fam)
    return best


def _resolve_routed_resume(
    best: tuple[int, dict] | None,
    variants: dict,
    app_dir: Path | None = None,
) -> RoutedResume | None:
    if not best:
        return None
    fam = best[1]
    variant_key = str(fam.get("variant") or "").strip()
    if not variant_key:
        return None
    variant_path_raw = variants.get(variant_key)
    vp = _resolve_variant_path(str(variant_path_raw or variant_key), app_dir)
    if not vp:
        return None
    txt = _read_text_cached(vp)
    if not txt.strip():
        return None
    return RoutedResume(key=variant_key, path=str(vp), text=txt, score=int(best[0]))


def _resolve_variant_key(variant_key: str, variants: dict, score: int, app_dir: Path | None = None) -> RoutedResume | None:
    vk = str(variant_key or "").strip()
    if not vk:
        return None
    variant_path_raw = variants.get(vk)
    vp = _resolve_variant_path(str(variant_path_raw or vk), app_dir)
    if not vp:
        return None
    txt = _read_text_cached(vp)
    if not txt.strip():
        return None
    return RoutedResume(key=vk, path=str(vp), text=txt, score=int(score))


def _resolve_variant_path(v: str, app_dir: Path | None = None) -> Path | None:
    vv = str(v or "").strip()
    if not vv:
        return None
    def allowed(path):
        root=app_dir or APP_DIR
        database=root/"applypilot.db"
        if database.is_file():
            from applypilot.database import get_connection
            from applypilot.country_context import current
            conn=get_connection(database)
            if "country_code" in {r[1] for r in conn.execute("PRAGMA table_info(cc_cv_sources)")}:
                row=conn.execute("SELECT country_code FROM cc_cv_sources WHERE source_key=?",(path.stem,)).fetchone()
                if row and row[0]=="NZ":return bool(current() and current().country_code=="NZ")
        return True

    # Allow absolute path.
    p = Path(vv).expanduser()
    if p.is_absolute():
        return p if p.is_file() and (app_dir is None or p.resolve().is_relative_to(app_dir.resolve())) and allowed(p) else None

    # Allow paths relative to ~/.applypilot.
    p2 = ((app_dir or APP_DIR) / vv).expanduser()
    if p2.is_file() and p2.resolve().is_relative_to((app_dir or APP_DIR).resolve()) and allowed(p2):
        return p2

    # Allow just a key, resolved to resume_variants/<key>.txt
    p3 = ((app_dir / "resume_variants" if app_dir else RESUME_VARIANTS_DIR) / f"{vv}.txt").expanduser()
    if p3.exists() and allowed(p3):
        return p3

    return None


@reuse_read
def route_resume_for_job(job: dict, *, app_dir: Path | None = None, cfg: dict | None = None) -> RoutedResume:
    """Pick the best resume variant for this job (keyword match), else default resume.txt."""
    from applypilot.country_context import check_job
    context = check_job(job, app_dir, preparation=False)
    if context.country_code == "NZ":
        from applypilot.nz_documents import route
        return route(job, app_dir or APP_DIR)
    if cfg is None:
        if app_dir is not None:
            from applypilot.setup_workspace import read_searches_dict
            cfg = read_searches_dict(app_dir)
        else:
            cfg = load_search_config() or {}
    default_path = app_dir / "resume.txt" if app_dir else RESUME_PATH
    rr = cfg.get("resume_routing") if isinstance(cfg, dict) else None
    inferred = False
    if not isinstance(rr, dict):
        rr = suggested_routing(app_dir or APP_DIR)
        inferred = bool(rr["families"])
    if not rr.get("families") and not rr.get("title_routes"):
        base = _read_text_cached(default_path)
        return RoutedResume(key="default", path=str(default_path), text=base, score=0, reason="No routing configured")

    variants = rr.get("variants")
    families = rr.get("families")
    context_families = rr.get("context_families")
    context_routing = rr.get("context_routing")
    blended_routes = rr.get("blended_routes")
    if not isinstance(variants, dict) or not isinstance(families, list):
        base = _read_text_cached(default_path)
        return RoutedResume(key="default", path=str(default_path), text=base, score=0, reason="Routing configuration incomplete")

    blob = _job_blob(job)
    role_best = _best_family(blob, families, title_blob=str(job.get("title") or ""))
    context_best = _best_family(blob, context_families, title_blob=str(job.get("title") or ""))
    ranked = []
    for family in families:
        hit = _best_family(blob, [family], title_blob=str(job.get("title") or ""))
        if hit:
            ranked.append({"key": family.get("variant"), "score": hit[0],
                           "matched": [str(k) for k in family.get("keywords", []) if str(k).lower() in blob]})
    ranked.sort(key=lambda item: item["score"], reverse=True)
    ambiguous = len(ranked) > 1 and ranked[0]["score"] == ranked[1]["score"] and ranked[0]["key"] != ranked[1]["key"]
    override = str(job.get("resume_route_override") or "").strip()
    if override:
        if override == "default":
            return RoutedResume("default", str(default_path), _read_text_cached(default_path), 0, "Explicit job override", False, tuple(ranked))
        chosen = _resolve_variant_key(override, variants, 0, app_dir)
        if chosen:
            return RoutedResume(chosen.key, chosen.path, chosen.text, chosen.score, "Explicit job override", False, tuple(ranked))
        raise ValueError("Chosen resume variant is unavailable")
    def normalized(value):
        return " "+re.sub(r"[^\w]+", " ", str(value).lower().replace("_", " ")).strip()+" "
    title = normalized(job.get("title") or "")
    title_routes = [(str(phrase), key) for phrase, key in rr.get("title_routes", {}).items()
                    if normalized(phrase) in title]
    if title_routes:
        title_routes.sort(key=lambda pair: len(pair[0]), reverse=True)
        chosen = _resolve_variant_key(title_routes[0][1], variants, 3, app_dir)
        tied = len(title_routes) > 1 and len(title_routes[0][0]) == len(title_routes[1][0]) and title_routes[0][1] != title_routes[1][1]
        if chosen and not tied:
            return RoutedResume(chosen.key, chosen.path, chosen.text, chosen.score, "Saved role mapping: " + title_routes[0][0], False, tuple(ranked))
        return RoutedResume("default", str(default_path), _read_text_cached(default_path), 0, "Role mapping is ambiguous or unavailable", True, tuple(ranked))
    if ambiguous:
        return RoutedResume("default", str(default_path), _read_text_cached(default_path), ranked[0]["score"],
                            "Ambiguous role match: choose a resume override", True, tuple(ranked))

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

    if blend_enabled and ctx_enabled and role_best and context_best and context_best[0] >= ctx_min_hits:
        role_variant = str((role_best[1] or {}).get("variant") or "").strip()
        context_variant = str((context_best[1] or {}).get("variant") or "").strip()
        combo_score = int(role_best[0]) + int(context_best[0])
        for combo in blend_combos:
            if str(combo.get("role_variant") or "").strip() != role_variant:
                continue
            if str(combo.get("context_variant") or "").strip() != context_variant:
                continue
            combo_variant = str(combo.get("variant") or "").strip()
            routed = _resolve_variant_key(combo_variant, variants, combo_score, app_dir)
            if routed is not None:
                return RoutedResume(routed.key, routed.path, routed.text, routed.score, "Role + sector blend", False, tuple(ranked))

    choice_order: list[tuple[int, dict] | None] = []
    if ctx_enabled and ctx_override and context_best:
        ctx_score = int(context_best[0])
        role_score = int(role_best[0]) if role_best else 0
        if ctx_score >= ctx_min_hits and ctx_score >= (role_score + ctx_min_margin):
            choice_order = [context_best, role_best]

    if not choice_order:
        if ctx_enabled:
            choice_order = [role_best, context_best if context_best and context_best[0] >= ctx_min_hits else None]
        else:
            choice_order = [role_best]

    for best in choice_order:
        routed = _resolve_routed_resume(best, variants, app_dir)
        if routed is not None:
            return RoutedResume(routed.key, routed.path, routed.text, routed.score,
                                "Matched variant filename (review inferred rules)" if inferred else "Matched configured keywords", False, tuple(ranked))

    fallback_score = 0
    if role_best:
        fallback_score = int(role_best[0])
    elif context_best:
        fallback_score = int(context_best[0])
    base = _read_text_cached(default_path)
    return RoutedResume(key="default", path=str(default_path), text=base, score=fallback_score,
                        reason="Matching resume unavailable: choose an override" if role_best else "No matching family; default resume",
                        ambiguous=bool(role_best), candidates=tuple(ranked))


@reuse_read
def score_fingerprint(job: dict, app_dir: Path | None = None, *, profile_snapshot=None,route_snapshot=None,fresh=False) -> str:
    from applypilot.setup_workspace import read_profile
    from applypilot.ai_routing import route_info
    from applypilot.country_context import current
    root = app_dir or (Path(current().workspace) if current() else APP_DIR)
    if route_snapshot is not None:
        route=route_snapshot
    elif app_dir:
        from applypilot.pipeline_workspace import campaign_config
        try:
            cfg = campaign_config(root, job.get("campaign_id"))
        except ValueError:
            from applypilot.setup_workspace import read_searches_dict
            cfg = read_searches_dict(root)
        route = route_resume_for_job(job, app_dir=root, cfg=cfg)
    else:
        route = route_resume_for_job(job)
    if job.get("country_code")=="NZ":
        from applypilot.preparation import input_fingerprint
        return input_fingerprint(job,root,route)
    profile=profile_snapshot if profile_snapshot is not None else read_profile(root)
    legacy_profile={k:v for k,v in profile.items() if k!="canonical_facts"}
    legacy_payload = [legacy_profile, route.key, route.fingerprint, job.get("full_description"), job.get("title"),
               job.get("company"), job.get("location"), job.get("resume_route_override"), route_info("score")]
    legacy_hash=hashlib.sha256(json.dumps(legacy_payload,sort_keys=True,default=str).encode()).hexdigest()
    if not fresh and job.get("score_fingerprint")==legacy_hash:
        return legacy_hash  # exact pre-activation inputs proven; not fact verification
    from applypilot.professional import enrich_evidence
    evidence,_=enrich_evidence(route.text,profile,job)
    payload=["score-input-v2",evidence,route.key,route.fingerprint,{k:job.get(k) for k in ("title","company","site","location","full_description")},route_info("score")]
    return "score-v2:"+hashlib.sha256(json.dumps(payload,sort_keys=True,default=str).encode()).hexdigest()


def score_inputs_changed(job,app_dir=None):
    old=str(job.get("score_fingerprint") or "")
    known=old.startswith("score-v2:") or bool(re.fullmatch(r"[a-f0-9]{64}",old))
    return known and old!=score_fingerprint(job,app_dir)


@reuse_read
def suggested_routing(app_dir: Path) -> dict:
    files = sorted((app_dir / "resume_variants").glob("*.txt"))
    database = app_dir / "applypilot.db"
    if database.is_file():
        from applypilot.database import get_connection
        conn = get_connection(database)
        columns = {r[1] for r in conn.execute("PRAGMA table_info(cc_cv_sources)")}
        if "country_code" in columns:
            nz_keys = {r[0] for r in conn.execute("SELECT source_key FROM cc_cv_sources WHERE country_code='NZ'")}
            files = [p for p in files if p.stem not in nz_keys]
    variants = {p.stem: str(p.relative_to(app_dir)).replace("\\", "/") for p in files}
    families = [{"name": p.stem, "variant": p.stem, "keywords": [p.stem.replace("_", " ").replace("-", " ")]}
                for p in files if not any(k in p.stem.lower().split("_") for k in ("main", "public", "commercial", "sector", "default"))]
    return {"variants": variants, "families": families, "context_routing": {"enabled": False}, "blended_routes": {"enabled": False}}


def clear_resume_text_cache() -> None:
    _TEXT_CACHE.clear()
