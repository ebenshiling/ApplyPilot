"""Deterministic regression checks for scoring and tailoring logic."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from applypilot.scoring.scorer import _parse_score_response
from applypilot.scoring.tailor_strategy import (
    build_fact_library,
    build_jd_targets,
    check_quant_consistency,
    detect_role_pack,
    rank_candidate,
    score_jd_coverage,
    validate_fact_citations,
)


_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOLDEN_DIR = _REPO_ROOT / "ops" / "evals" / "golden"


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    return []


def _default_parser_cases() -> list[dict[str, Any]]:
    return [
        {
            "name": "json_shape",
            "raw": '{"score": 8, "keywords": ["python", "sql"], "reasoning": "Strong overlap", "confidence": 0.82}',
            "expected": {"score": 8, "confidence": 0.82, "keywords_contains": "python"},
        },
        {
            "name": "legacy_shape",
            "raw": "SCORE: 7\nKEYWORDS: sql, tableau\nREASONING: Good fit\nCONFIDENCE: 0.55",
            "expected": {"score": 7, "confidence": 0.55, "keywords_contains": "sql"},
        },
        {
            "name": "fenced_json_clamped",
            "raw": '```json\n{"score": 99, "keywords": ["etl"], "reasoning": "ok", "confidence": 2}\n```',
            "expected": {"score": 10, "confidence": 1.0, "keywords_contains": "etl"},
        },
    ]


def _default_tailor_fixture() -> dict[str, Any]:
    return {
        "profile": {
            "skills_boundary": {
                "languages": ["SQL", "Python"],
                "analytics": ["Power BI", "Tableau"],
                "tools": ["Git"],
            },
            "resume_facts": {
                "preserved_companies": ["Acme Corp"],
                "preserved_projects": ["Sales Dashboard Revamp"],
                "preserved_school": "Example University",
                "real_metrics": ["40%", "12"],
            },
            "tailoring": {
                "role_pack_override": "auto",
                "draft_candidates": 3,
                "safe_synonyms": {"sql": ["query optimization"]},
            },
        },
        "resume_text": "Built SQL reports that reduced errors by 40%. Improved pipeline latency by 12%.",
        "citation_case": {
            "experience_header": "Data Analyst at Acme Corp",
            "experience_subtitle": "2022-2025",
            "bullets": [
                "Built SQL reporting workflows and reduced manual effort by 40% [{fact1}]",
                "Stabilized ETL checks with Python scripts [{fact2}]",
            ],
        },
        "jd_case": {
            "job_description": "Looking for SQL, Power BI dashboarding, and data analysis experience.",
            "keyword_bank": {"prompt_keywords": ["SQL", "Power BI", "stakeholder communication"]},
            "coverage_text": "Built SQL data models and Power BI dashboards for executive reporting.",
            "min_ratio": 0.5,
        },
        "quant_cases": {
            "evidence": ["40", "12", "40%"],
            "pass_data": {
                "summary": "Improved reporting reliability by 40%.",
                "experience": [{"bullets": ["Reduced errors by 40% [F1]"]}],
                "projects": [{"bullets": ["Shipped dashboard updates in 12 days [F2]"]}],
            },
            "fail_data": {
                "summary": "Improved reporting reliability by 99%.",
                "experience": [{"bullets": ["Reduced errors by 99% [F1]"]}],
                "projects": [],
            },
        },
        "role_cases": [
            {
                "job": {
                    "title": "Senior Data Analyst",
                    "full_description": "analytics dashboards reporting stakeholders",
                },
                "expected_pack": "data_bi",
            },
            {
                "job": {
                    "title": "IT Support Analyst",
                    "full_description": "service desk incident response and ticket handling",
                },
                "expected_pack": "support",
            },
        ],
        "rank_case": {
            "good": {
                "text": "Built SQL models and BI dashboards improving reporting quality with measurable impact.",
                "coverage_ratio": 0.9,
                "validator_errors": [],
                "validator_warnings": [],
                "citation_errors": [],
                "quant_errors": [],
            },
            "bad": {
                "text": "Worked on things.",
                "coverage_ratio": 0.1,
                "validator_errors": ["err1", "err2"],
                "validator_warnings": ["warn1"],
                "citation_errors": ["cite"],
                "quant_errors": ["q"],
            },
        },
    }


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _load_parser_cases() -> list[dict[str, Any]]:
    data = _load_json(_GOLDEN_DIR / "score_parser_cases.json")
    if data and isinstance(data.get("cases"), list):
        cases = [c for c in data["cases"] if isinstance(c, dict)]
        if cases:
            return cases
    return _default_parser_cases()


def _load_tailor_fixture() -> dict[str, Any]:
    data = _load_json(_GOLDEN_DIR / "tailor_strategy_cases.json")
    return data if data else _default_tailor_fixture()


def run_regression_eval(*, strict: bool = True) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    parser_cases = _load_parser_cases()
    parser_ok = True
    parser_details: list[str] = []
    for case in parser_cases:
        name = str(case.get("name") or "unnamed")
        raw = str(case.get("raw") or "")
        expected = _as_dict(case.get("expected"))
        parsed = _parse_score_response(raw)
        ok = (
            int(parsed.get("score") or 0) == int(expected.get("score") or 0)
            and abs(float(parsed.get("confidence") or 0.0) - float(expected.get("confidence") or 0.0)) < 1e-6
            and str(expected.get("keywords_contains") or "").lower() in str(parsed.get("keywords") or "").lower()
        )
        if not ok:
            parser_ok = False
            parser_details.append(f"{name}: got={parsed}")
    checks.append(
        {
            "name": "score_parser_regression",
            "passed": parser_ok,
            "details": parser_details or ["all parser cases passed"],
        }
    )

    fixture = _load_tailor_fixture()
    profile = _as_dict(fixture.get("profile"))
    resume_text = str(fixture.get("resume_text") or "")
    facts = build_fact_library(resume_text, profile, max_facts=8)
    fact_ids = [str(f.get("id") or "") for f in facts if f.get("id")]

    citation_case = _as_dict(fixture.get("citation_case"))
    citation_details: list[str] = []
    citations_ok = False
    if len(fact_ids) >= 2:
        bullets = [str(x) for x in _as_list(citation_case.get("bullets"))]
        b1 = (bullets[0] if len(bullets) >= 1 else "").replace("{fact1}", fact_ids[0])
        b2 = (bullets[1] if len(bullets) >= 2 else "").replace("{fact2}", fact_ids[1])
        citation_data = {
            "experience": [
                {
                    "header": str(citation_case.get("experience_header") or "Data Analyst at Acme Corp"),
                    "subtitle": str(citation_case.get("experience_subtitle") or "2022-2025"),
                    "bullets": [b1, b2],
                }
            ],
            "projects": [],
        }
        citation_result = validate_fact_citations(citation_data, set(fact_ids))
        citations_ok = bool(citation_result.get("passed"))
        if not citations_ok:
            citation_details.extend(list(citation_result.get("errors") or []))
    else:
        citation_details.append("insufficient_fact_ids")
    checks.append(
        {
            "name": "fact_citation_validation",
            "passed": citations_ok,
            "details": citation_details or ["citation checks passed"],
        }
    )

    jd_case = _as_dict(fixture.get("jd_case"))
    bundle = build_jd_targets(
        str(jd_case.get("job_description") or ""),
        profile,
        _as_dict(jd_case.get("keyword_bank")),
        resume_text=resume_text,
    )
    coverage = score_jd_coverage(
        str(jd_case.get("coverage_text") or ""),
        bundle.get("targets") or [],
        bundle.get("synonyms") or {},
    )
    coverage_ok = float(coverage.get("ratio") or 0.0) >= float(jd_case.get("min_ratio") or 0.5)
    checks.append(
        {
            "name": "jd_coverage_targets",
            "passed": coverage_ok,
            "details": [f"coverage_ratio={coverage.get('ratio')}", f"targets={bundle.get('targets')}"],
        }
    )

    quant_cases = _as_dict(fixture.get("quant_cases"))
    evidence = {str(x) for x in _as_list(quant_cases.get("evidence"))}
    q_ok = check_quant_consistency(_as_dict(quant_cases.get("pass_data")), evidence)
    q_bad = check_quant_consistency(_as_dict(quant_cases.get("fail_data")), evidence)
    quant_ok = bool(q_ok.get("passed")) and (not bool(q_bad.get("passed")))
    checks.append(
        {
            "name": "quant_consistency_guard",
            "passed": quant_ok,
            "details": list(q_bad.get("errors") or []) if not quant_ok else ["quant checks passed"],
        }
    )

    role_ok = True
    role_details: list[str] = []
    for case in _as_list(fixture.get("role_cases")):
        if not isinstance(case, dict):
            continue
        job = _as_dict(case.get("job"))
        expected_pack = str(case.get("expected_pack") or "")
        got_pack = str(detect_role_pack(job, profile).get("pack") or "")
        if expected_pack != got_pack:
            role_ok = False
            role_details.append(f"expected={expected_pack} got={got_pack}")
    checks.append(
        {
            "name": "role_pack_detection",
            "passed": role_ok,
            "details": role_details or ["role pack routing passed"],
        }
    )

    rank_case = _as_dict(fixture.get("rank_case"))
    good_cfg = _as_dict(rank_case.get("good"))
    bad_cfg = _as_dict(rank_case.get("bad"))
    good_rank = rank_candidate(
        text=str(good_cfg.get("text") or ""),
        coverage_ratio=float(good_cfg.get("coverage_ratio") or 0.0),
        validator_errors=[str(x) for x in _as_list(good_cfg.get("validator_errors"))],
        validator_warnings=[str(x) for x in _as_list(good_cfg.get("validator_warnings"))],
        citation_errors=[str(x) for x in _as_list(good_cfg.get("citation_errors"))],
        quant_errors=[str(x) for x in _as_list(good_cfg.get("quant_errors"))],
    )
    bad_rank = rank_candidate(
        text=str(bad_cfg.get("text") or ""),
        coverage_ratio=float(bad_cfg.get("coverage_ratio") or 0.0),
        validator_errors=[str(x) for x in _as_list(bad_cfg.get("validator_errors"))],
        validator_warnings=[str(x) for x in _as_list(bad_cfg.get("validator_warnings"))],
        citation_errors=[str(x) for x in _as_list(bad_cfg.get("citation_errors"))],
        quant_errors=[str(x) for x in _as_list(bad_cfg.get("quant_errors"))],
    )
    rank_ok = float(good_rank.get("score") or 0.0) > float(bad_rank.get("score") or 0.0)
    checks.append(
        {
            "name": "candidate_rank_ordering",
            "passed": rank_ok,
            "details": [f"good={good_rank.get('score')}", f"bad={bad_rank.get('score')}"],
        }
    )

    passed = all(bool(c.get("passed")) for c in checks)
    report = {
        "passed": passed,
        "strict": strict,
        "total_checks": len(checks),
        "failed_checks": [c["name"] for c in checks if not c.get("passed")],
        "checks": checks,
        "fixture_sources": {
            "parser": str(_GOLDEN_DIR / "score_parser_cases.json"),
            "tailor": str(_GOLDEN_DIR / "tailor_strategy_cases.json"),
        },
    }

    if strict:
        return report

    threshold = 0.8
    passed_count = sum(1 for c in checks if c.get("passed"))
    report["threshold"] = threshold
    report["passed"] = (passed_count / max(1, len(checks))) >= threshold
    return report
