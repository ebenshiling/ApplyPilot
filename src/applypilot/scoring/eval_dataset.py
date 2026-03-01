"""Formal scoring/tailoring eval dataset runner and trend reporting."""

from __future__ import annotations

import json
from datetime import datetime, timezone
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
DEFAULT_DATASET_PATH = _REPO_ROOT / "ops" / "evals" / "dataset" / "scoring_tailoring_eval.json"


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    return []


def _deep_replace(obj: Any, mapping: dict[str, str]) -> Any:
    if isinstance(obj, str):
        out = obj
        for key, val in mapping.items():
            out = out.replace("{" + key + "}", val)
        return out
    if isinstance(obj, list):
        return [_deep_replace(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _deep_replace(v, mapping) for k, v in obj.items()}
    return obj


def load_formal_eval_dataset(dataset_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(dataset_path) if dataset_path else DEFAULT_DATASET_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("formal eval dataset must be a JSON object")
    return data


def _suite_report(name: str, details: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(details)
    passed_count = sum(1 for d in details if d.get("passed"))
    return {
        "name": name,
        "total": total,
        "passed_count": passed_count,
        "failed_cases": [str(d.get("name") or "unknown") for d in details if not d.get("passed")],
        "pass_rate": (passed_count / total) if total else 1.0,
        "passed": passed_count == total,
        "cases": details,
    }


def run_formal_eval(*, strict: bool = True, dataset_path: str | Path | None = None) -> dict[str, Any]:
    dataset = load_formal_eval_dataset(dataset_path)
    suites = _as_dict(dataset.get("suites"))

    profile = _as_dict(dataset.get("profile_fixture"))
    resume_text = str(dataset.get("resume_text") or "")
    facts = build_fact_library(resume_text, profile, max_facts=16)
    fact_ids = [str(f.get("id") or "") for f in facts if f.get("id")]

    suite_reports: list[dict[str, Any]] = []

    parser_results: list[dict[str, Any]] = []
    for case in _as_list(suites.get("score_parser")):
        if not isinstance(case, dict):
            continue
        expected = _as_dict(case.get("expected"))
        parsed = _parse_score_response(str(case.get("raw") or ""))
        passed = (
            int(parsed.get("score") or 0) == int(expected.get("score") or 0)
            and abs(float(parsed.get("confidence") or 0.0) - float(expected.get("confidence") or 0.0)) < 1e-6
            and str(expected.get("keywords_contains") or "").lower() in str(parsed.get("keywords") or "").lower()
        )
        parser_results.append(
            {
                "name": str(case.get("name") or "unnamed"),
                "passed": passed,
                "details": [] if passed else [f"parsed={parsed}", f"expected={expected}"],
            }
        )
    suite_reports.append(_suite_report("score_parser", parser_results))

    citation_results: list[dict[str, Any]] = []
    for case in _as_list(suites.get("fact_citations")):
        if not isinstance(case, dict):
            continue
        req = max(1, int(case.get("required_fact_ids") or 1))
        should_pass = bool(case.get("should_pass"))
        base_data = _as_dict(case.get("data_template"))
        if len(fact_ids) < req:
            citation_results.append(
                {
                    "name": str(case.get("name") or "unnamed"),
                    "passed": False,
                    "details": ["insufficient_fact_ids"],
                }
            )
            continue
        mapping = {f"fact{i + 1}": fact_ids[i] for i in range(req)}
        data = _deep_replace(base_data, mapping)
        result = validate_fact_citations(_as_dict(data), set(fact_ids))
        observed = bool(result.get("passed"))
        passed = observed == should_pass
        citation_results.append(
            {
                "name": str(case.get("name") or "unnamed"),
                "passed": passed,
                "details": [] if passed else list(result.get("errors") or []),
            }
        )
    suite_reports.append(_suite_report("fact_citations", citation_results))

    role_results: list[dict[str, Any]] = []
    for case in _as_list(suites.get("role_pack")):
        if not isinstance(case, dict):
            continue
        job = _as_dict(case.get("job"))
        expected_pack = str(case.get("expected_pack") or "")
        got_pack = str(detect_role_pack(job, profile).get("pack") or "")
        passed = got_pack == expected_pack
        role_results.append(
            {
                "name": str(case.get("name") or "unnamed"),
                "passed": passed,
                "details": [] if passed else [f"expected={expected_pack} got={got_pack}"],
            }
        )
    suite_reports.append(_suite_report("role_pack", role_results))

    coverage_results: list[dict[str, Any]] = []
    for case in _as_list(suites.get("jd_coverage")):
        if not isinstance(case, dict):
            continue
        case_resume = str(case.get("resume_text") or resume_text)
        bundle = build_jd_targets(
            str(case.get("job_description") or ""),
            profile,
            _as_dict(case.get("keyword_bank")),
            resume_text=case_resume,
        )
        cov = score_jd_coverage(
            str(case.get("coverage_text") or ""),
            bundle.get("targets") or [],
            bundle.get("synonyms") or {},
        )
        ratio = float(cov.get("ratio") or 0.0)
        min_ratio = case.get("min_ratio")
        max_ratio = case.get("max_ratio")
        passed = True
        if min_ratio is not None:
            passed = passed and (ratio >= float(min_ratio))
        if max_ratio is not None:
            passed = passed and (ratio <= float(max_ratio))
        coverage_results.append(
            {
                "name": str(case.get("name") or "unnamed"),
                "passed": passed,
                "details": [] if passed else [f"ratio={ratio}", f"targets={bundle.get('targets')}"],
            }
        )
    suite_reports.append(_suite_report("jd_coverage", coverage_results))

    quant_results: list[dict[str, Any]] = []
    for case in _as_list(suites.get("quant_consistency")):
        if not isinstance(case, dict):
            continue
        should_pass = bool(case.get("should_pass"))
        result = check_quant_consistency(
            _as_dict(case.get("data")),
            {str(x) for x in _as_list(case.get("evidence"))},
        )
        observed = bool(result.get("passed"))
        passed = observed == should_pass
        quant_results.append(
            {
                "name": str(case.get("name") or "unnamed"),
                "passed": passed,
                "details": [] if passed else list(result.get("errors") or []),
            }
        )
    suite_reports.append(_suite_report("quant_consistency", quant_results))

    rank_results: list[dict[str, Any]] = []
    for case in _as_list(suites.get("rank_order")):
        if not isinstance(case, dict):
            continue
        good = _as_dict(case.get("good"))
        bad = _as_dict(case.get("bad"))
        good_rank = rank_candidate(
            text=str(good.get("text") or ""),
            coverage_ratio=float(good.get("coverage_ratio") or 0.0),
            validator_errors=[str(x) for x in _as_list(good.get("validator_errors"))],
            validator_warnings=[str(x) for x in _as_list(good.get("validator_warnings"))],
            citation_errors=[str(x) for x in _as_list(good.get("citation_errors"))],
            quant_errors=[str(x) for x in _as_list(good.get("quant_errors"))],
        )
        bad_rank = rank_candidate(
            text=str(bad.get("text") or ""),
            coverage_ratio=float(bad.get("coverage_ratio") or 0.0),
            validator_errors=[str(x) for x in _as_list(bad.get("validator_errors"))],
            validator_warnings=[str(x) for x in _as_list(bad.get("validator_warnings"))],
            citation_errors=[str(x) for x in _as_list(bad.get("citation_errors"))],
            quant_errors=[str(x) for x in _as_list(bad.get("quant_errors"))],
        )
        passed = float(good_rank.get("score") or 0.0) > float(bad_rank.get("score") or 0.0)
        rank_results.append(
            {
                "name": str(case.get("name") or "unnamed"),
                "passed": passed,
                "details": [] if passed else [f"good={good_rank.get('score')}", f"bad={bad_rank.get('score')}"],
            }
        )
    suite_reports.append(_suite_report("rank_order", rank_results))

    total_cases = sum(int(s.get("total") or 0) for s in suite_reports)
    passed_cases = sum(int(s.get("passed_count") or 0) for s in suite_reports)
    pass_rate = (passed_cases / total_cases) if total_cases else 1.0
    relaxed_threshold = float(dataset.get("relaxed_threshold") or 0.9)

    passed = all(bool(s.get("passed")) for s in suite_reports) if strict else pass_rate >= relaxed_threshold

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_version": int(dataset.get("version") or 1),
        "dataset_path": str(Path(dataset_path) if dataset_path else DEFAULT_DATASET_PATH),
        "strict": strict,
        "passed": passed,
        "total_cases": total_cases,
        "passed_cases": passed_cases,
        "pass_rate": pass_rate,
        "relaxed_threshold": relaxed_threshold,
        "failed_suites": [str(s.get("name") or "") for s in suite_reports if not s.get("passed")],
        "failed_cases": [
            f"{s.get('name')}::{c}"
            for s in suite_reports
            for c in _as_list(s.get("failed_cases"))
            if isinstance(c, str) and c
        ],
        "suite_pass_rates": {str(s.get("name") or ""): float(s.get("pass_rate") or 0.0) for s in suite_reports},
        "suites": suite_reports,
    }


def read_eval_history(history_path: str | Path) -> list[dict[str, Any]]:
    path = Path(history_path)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                out.append(data)
        except Exception:
            continue
    return out


def append_eval_history(report: dict[str, Any], history_path: str | Path) -> dict[str, Any]:
    path = Path(history_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    history = read_eval_history(path)
    prev = history[-1] if history else None

    current = {
        "created_at": str(report.get("created_at") or datetime.now(timezone.utc).isoformat()),
        "dataset_version": int(report.get("dataset_version") or 1),
        "passed": bool(report.get("passed")),
        "pass_rate": float(report.get("pass_rate") or 0.0),
        "total_cases": int(report.get("total_cases") or 0),
        "suite_pass_rates": _as_dict(report.get("suite_pass_rates")),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(current, ensure_ascii=True) + "\n")

    merged = history + [current]
    rolling = merged[-5:]
    rolling_avg = sum(float(x.get("pass_rate") or 0.0) for x in rolling) / max(1, len(rolling))

    prev_rate = float(prev.get("pass_rate") or 0.0) if isinstance(prev, dict) else None
    delta = (current["pass_rate"] - prev_rate) if prev_rate is not None else None

    suite_deltas: dict[str, float] = {}
    prev_suites = _as_dict(prev.get("suite_pass_rates")) if isinstance(prev, dict) else {}
    curr_suites = _as_dict(current.get("suite_pass_rates"))
    for name, now_val in curr_suites.items():
        if name in prev_suites:
            try:
                suite_deltas[str(name)] = float(now_val) - float(prev_suites[name])
            except Exception:
                continue

    return {
        "history_path": str(path),
        "run_count": len(merged),
        "previous_pass_rate": prev_rate,
        "delta_pass_rate": delta,
        "rolling_pass_rate_5": rolling_avg,
        "suite_deltas": suite_deltas,
    }
