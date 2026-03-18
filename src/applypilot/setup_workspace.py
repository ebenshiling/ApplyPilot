"""Workspace setup helpers used by the local dashboard.

This mirrors the CLI wizard outputs but is non-interactive and API-friendly.
All writes are scoped to a provided app_dir so tests can use temp dirs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from applypilot.config import load_sites_config


_ALLOWED_ROLE_PACKS = {"auto", "data_bi", "engineering", "support"}
_BOARD_ALLOWLIST = {"indeed", "linkedin", "glassdoor", "zip_recruiter", "google"}


def _clean_str(v: Any, *, max_len: int = 500) -> str:
    s = str(v or "").strip()
    if len(s) > max_len:
        raise ValueError(f"value too long (>{max_len})")
    return s


def _validate_string_list(name: str, value: Any, *, max_items: int = 300, item_max_len: int = 160) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    if len(value) > max_items:
        raise ValueError(f"{name} has too many items")
    out: list[str] = []
    for x in value:
        s = _clean_str(x, max_len=item_max_len)
        if not s:
            continue
        out.append(s)
    return out


def _validate_profile_patch(patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise ValueError("profile patch must be an object")

    allowed_top = {
        "account",
        "personal",
        "work_authorization",
        "availability",
        "compensation",
        "experience",
        "skills_boundary",
        "resume_facts",
        "resume_sections",
        "resume_validation",
        "tailoring",
        "eeo_voluntary",
    }

    unknown = [k for k in patch.keys() if k not in allowed_top]
    if unknown:
        raise ValueError(f"unknown profile fields: {', '.join(sorted(unknown)[:8])}")

    out = dict(patch)

    personal = out.get("personal")
    if personal is not None:
        if not isinstance(personal, dict):
            raise ValueError("personal must be an object")
        p2: dict[str, Any] = {}
        for k, v in personal.items():
            if k == "password":
                continue
            p2[k] = _clean_str(v, max_len=300)
        out["personal"] = p2

    for key in ("account", "work_authorization", "availability", "compensation", "experience", "eeo_voluntary"):
        sec = out.get(key)
        if sec is None:
            continue
        if not isinstance(sec, dict):
            raise ValueError(f"{key} must be an object")
        out[key] = {k: _clean_str(v, max_len=300) for k, v in sec.items()}

    skills = out.get("skills_boundary")
    if skills is not None:
        if not isinstance(skills, dict):
            raise ValueError("skills_boundary must be an object")
        s2: dict[str, list[str]] = {}
        for k, v in skills.items():
            kk = _clean_str(k, max_len=80)
            if not kk:
                continue
            s2[kk] = _validate_string_list(f"skills_boundary.{kk}", v, max_items=80, item_max_len=80)
        out["skills_boundary"] = s2

    facts = out.get("resume_facts")
    if facts is not None:
        if not isinstance(facts, dict):
            raise ValueError("resume_facts must be an object")
        out["resume_facts"] = {
            "preserved_companies": _validate_string_list(
                "resume_facts.preserved_companies", facts.get("preserved_companies"), max_items=80
            ),
            "preserved_projects": _validate_string_list(
                "resume_facts.preserved_projects", facts.get("preserved_projects"), max_items=80
            ),
            "preserved_school": _clean_str(facts.get("preserved_school", ""), max_len=200),
            "real_metrics": _validate_string_list("resume_facts.real_metrics", facts.get("real_metrics"), max_items=80),
        }

    sections = out.get("resume_sections")
    if sections is not None:
        if not isinstance(sections, dict):
            raise ValueError("resume_sections must be an object")
        rs: dict[str, list[str]] = {}
        for k, v in sections.items():
            kk = _clean_str(k, max_len=80)
            if not kk:
                continue
            rs[kk] = _validate_string_list(f"resume_sections.{kk}", v, max_items=120, item_max_len=400)
        out["resume_sections"] = rs

    rv = out.get("resume_validation")
    if rv is not None:
        if not isinstance(rv, dict):
            raise ValueError("resume_validation must be an object")
        out["resume_validation"] = rv

    tailoring = out.get("tailoring")
    if tailoring is not None:
        if not isinstance(tailoring, dict):
            raise ValueError("tailoring must be an object")
        t2 = dict(tailoring)
        rp = _clean_str(t2.get("role_pack_override", "auto"), max_len=32).lower() or "auto"
        if rp not in _ALLOWED_ROLE_PACKS:
            raise ValueError("tailoring.role_pack_override must be one of auto,data_bi,engineering,support")
        t2["role_pack_override"] = rp

        dc_raw = t2.get("draft_candidates", 3)
        try:
            dc = int(str(dc_raw).strip())
        except Exception:
            raise ValueError("tailoring.draft_candidates must be an integer")
        if dc < 2 or dc > 3:
            raise ValueError("tailoring.draft_candidates must be 2 or 3")
        t2["draft_candidates"] = dc

        syn = t2.get("safe_synonyms")
        if syn is not None:
            if not isinstance(syn, dict):
                raise ValueError("tailoring.safe_synonyms must be an object")
            syn2: dict[str, list[str]] = {}
            for k, v in syn.items():
                kk = _clean_str(k, max_len=80).lower()
                if not kk:
                    continue
                syn2[kk] = [s.lower() for s in _validate_string_list(f"tailoring.safe_synonyms.{kk}", v, max_items=30)]
            t2["safe_synonyms"] = syn2
        out["tailoring"] = t2

    return out


def _validate_searches_dict(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ValueError("searches.yaml must parse to an object")

    if "queries" not in data or not isinstance(data.get("queries"), list) or not data.get("queries"):
        raise ValueError("queries must be a non-empty list")

    for i, q in enumerate(data.get("queries") or [], start=1):
        if not isinstance(q, dict):
            raise ValueError(f"queries[{i}] must be an object")
        qq = _clean_str(q.get("query"), max_len=160)
        if not qq:
            raise ValueError(f"queries[{i}].query is required")
        tier = q.get("tier", 1)
        try:
            t = int(str(tier).strip())
        except Exception:
            raise ValueError(f"queries[{i}].tier must be an integer")
        if t < 1 or t > 3:
            raise ValueError(f"queries[{i}].tier must be in 1..3")

    locs = data.get("locations")
    if locs is not None:
        if not isinstance(locs, list):
            raise ValueError("locations must be a list")
        for i, l in enumerate(locs, start=1):
            if not isinstance(l, dict):
                raise ValueError(f"locations[{i}] must be an object")
            if not _clean_str(l.get("location", ""), max_len=160):
                raise ValueError(f"locations[{i}].location is required")
            if "remote" in l and not isinstance(l.get("remote"), bool):
                raise ValueError(f"locations[{i}].remote must be true/false")

    for key in ("exclude_titles", "skip_titles"):
        if key in data:
            _validate_string_list(key, data.get(key), max_items=400, item_max_len=120)

    boards = data.get("boards")
    if boards is not None:
        vals = [s.lower() for s in _validate_string_list("boards", boards, max_items=20, item_max_len=50)]
        bad = [b for b in vals if b not in _BOARD_ALLOWLIST]
        if bad:
            raise ValueError(f"unsupported boards: {', '.join(sorted(set(bad)))}")

    sites = data.get("sites")
    if sites is not None:
        vals = [s.lower() for s in _validate_string_list("sites", sites, max_items=20, item_max_len=50)]
        bad = [b for b in vals if b not in _BOARD_ALLOWLIST]
        if bad:
            raise ValueError(f"unsupported sites: {', '.join(sorted(set(bad)))}")

    smart = data.get("smart_sites")
    if smart is not None:
        vals = _validate_string_list("smart_sites", smart, max_items=120, item_max_len=120)
        cfg = load_sites_config() or {}
        known = {
            str(e.get("name") or "").strip().lower()
            for e in (cfg.get("sites") or [])
            if isinstance(e, dict) and str(e.get("name") or "").strip()
        }
        bad = []
        for v in vals:
            vv = v.lower()
            if vv in known:
                continue
            if vv.endswith(" uk") and vv[:-3].strip() in known:
                continue
            bad.append(v)
        if bad:
            raise ValueError(f"unknown smart_sites: {', '.join(bad[:8])}")

    defaults = data.get("defaults") or {}
    if defaults:
        if not isinstance(defaults, dict):
            raise ValueError("defaults must be an object")
        if "results_per_site" in defaults:
            raw = defaults.get("results_per_site")
            if raw is None:
                raise ValueError("defaults.results_per_site must be an integer")
            try:
                v = int(str(raw).strip())
            except Exception:
                raise ValueError("defaults.results_per_site must be an integer")
            if v < 1 or v > 300:
                raise ValueError("defaults.results_per_site must be in 1..300")
        if "hours_old" in defaults:
            raw = defaults.get("hours_old")
            if raw is None:
                raise ValueError("defaults.hours_old must be an integer")
            try:
                v = int(str(raw).strip())
            except Exception:
                raise ValueError("defaults.hours_old must be an integer")
            if v < 1 or v > 720:
                raise ValueError("defaults.hours_old must be in 1..720")

    if "country" in data and not re.match(r"^[A-Za-z][A-Za-z\s\-]{0,63}$", _clean_str(data.get("country"), max_len=64)):
        raise ValueError("country must be a short text value")


def ensure_app_dir(app_dir: Path) -> None:
    Path(app_dir).mkdir(parents=True, exist_ok=True)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _deep_merge(a: Any, b: Any) -> Any:
    """Merge b into a (dict-only deep merge)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            if k in out:
                out[k] = _deep_merge(out[k], v)
            else:
                out[k] = v
        return out
    return b


def profile_path(app_dir: Path) -> Path:
    return Path(app_dir) / "profile.json"


def resume_txt_path(app_dir: Path) -> Path:
    return Path(app_dir) / "resume.txt"


def resume_pdf_path(app_dir: Path) -> Path:
    return Path(app_dir) / "resume.pdf"


def resume_variants_dir(app_dir: Path) -> Path:
    return Path(app_dir) / "resume_variants"


def resume_variant_path(app_dir: Path, key: str) -> Path:
    k = str(key or "").strip().lower()
    return resume_variants_dir(app_dir) / f"{k}.txt"


def searches_path(app_dir: Path) -> Path:
    return Path(app_dir) / "searches.yaml"


def read_profile(app_dir: Path) -> dict[str, Any]:
    p = profile_path(app_dir)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_profile(app_dir: Path, patch: dict[str, Any]) -> dict[str, Any]:
    """Create or update profile.json.

    patch is deep-merged into the existing profile (dict-only merge).
    """
    if not isinstance(patch, dict):
        raise TypeError("profile patch must be a dict")
    patch = _validate_profile_patch(patch)
    ensure_app_dir(Path(app_dir))
    existing = read_profile(app_dir)
    merged = _deep_merge(existing, patch)
    if not isinstance(merged, dict):
        merged = patch

    # Never persist login passwords in profile.json.
    try:
        personal = merged.get("personal")
        if isinstance(personal, dict) and "password" in personal:
            personal.pop("password", None)
    except Exception:
        pass

    _atomic_write_text(profile_path(app_dir), json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
    return merged


def read_text(path: Path, *, max_chars: int = 400_000) -> tuple[str, bool]:
    if not path.exists():
        return "", False
    try:
        t = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return "", False
    if len(t) > max_chars:
        return t[:max_chars], True
    return t, False


def write_resume_text(app_dir: Path, text: str) -> None:
    ensure_app_dir(Path(app_dir))
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not t.strip():
        raise ValueError("resume text is empty")
    if not t.endswith("\n"):
        t += "\n"
    _atomic_write_text(resume_txt_path(app_dir), t)


def list_resume_variants(app_dir: Path) -> list[dict[str, Any]]:
    d = resume_variants_dir(app_dir)
    if not d.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("*.txt"), key=lambda x: x.name.lower()):
        try:
            st = p.stat()
            out.append(
                {
                    "key": p.stem,
                    "name": p.name,
                    "path": str(p),
                    "bytes": int(st.st_size),
                    "mtime": float(st.st_mtime),
                }
            )
        except Exception:
            continue
    return out


def read_resume_variant(app_dir: Path, key: str) -> tuple[str, bool]:
    p = resume_variant_path(app_dir, key)
    return read_text(p)


def write_resume_variant(app_dir: Path, key: str, text: str) -> None:
    k = str(key or "").strip().lower()
    if not re.match(r"^[a-z0-9_\-]{2,40}$", k):
        raise ValueError("variant key must be 2-40 chars (a-z, 0-9, _, -)")
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not t.strip():
        raise ValueError("variant text is empty")
    if len(t) > 600_000:
        raise ValueError("variant text too large")
    if not t.endswith("\n"):
        t += "\n"
    ensure_app_dir(Path(app_dir))
    resume_variants_dir(app_dir).mkdir(parents=True, exist_ok=True)
    _atomic_write_text(resume_variant_path(app_dir, k), t)


def write_resume_pdf(app_dir: Path, data: bytes) -> None:
    ensure_app_dir(Path(app_dir))
    if not data or len(data) < 5:
        raise ValueError("resume PDF is empty")
    if len(data) > 12 * 1024 * 1024:
        raise ValueError("resume PDF too large")
    if not data.startswith(b"%PDF-"):
        raise ValueError("not a PDF")
    _atomic_write_bytes(resume_pdf_path(app_dir), data)


def read_searches_yaml(app_dir: Path) -> tuple[str, bool]:
    return read_text(searches_path(app_dir))


def read_searches_dict(app_dir: Path) -> dict[str, Any]:
    """Parse searches.yaml into a dict (best-effort)."""
    p = searches_path(app_dir)
    if not p.exists():
        return {}
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_searches_yaml(app_dir: Path, text: str) -> None:
    ensure_app_dir(Path(app_dir))
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not t.strip():
        raise ValueError("searches.yaml is empty")
    if not t.endswith("\n"):
        t += "\n"

    try:
        import yaml  # type: ignore

        parsed = yaml.safe_load(t)
    except Exception as e:
        raise ValueError(f"invalid YAML: {e}")

    _validate_searches_dict(parsed if isinstance(parsed, dict) else {})

    _atomic_write_text(searches_path(app_dir), t)


def get_setup_status(app_dir: Path) -> dict[str, Any]:
    ad = Path(app_dir)
    tier = None
    try:
        from applypilot.config import get_tier

        tier = int(get_tier() or 0)
    except Exception:
        tier = None
    return {
        "app_dir": str(ad),
        "has_profile": profile_path(ad).exists(),
        "has_resume_txt": resume_txt_path(ad).exists(),
        "has_resume_pdf": resume_pdf_path(ad).exists(),
        "has_searches": searches_path(ad).exists(),
        "tier": tier,
    }
