from pathlib import Path

from applypilot.scoring.eval_dataset import append_eval_history, run_formal_eval


def test_formal_eval_dataset_strict_passes() -> None:
    report = run_formal_eval(strict=True)
    assert report["strict"] is True
    assert int(report["total_cases"]) >= 10
    assert report["passed"] is True
    assert report["failed_cases"] == []


def test_formal_eval_history_trend(tmp_path: Path) -> None:
    history_path = tmp_path / "ai_eval_history.jsonl"

    r1 = {
        "created_at": "2026-01-01T00:00:00+00:00",
        "dataset_version": 1,
        "passed": True,
        "pass_rate": 0.8,
        "total_cases": 10,
        "suite_pass_rates": {"score_parser": 1.0},
    }
    t1 = append_eval_history(r1, history_path)
    assert t1["run_count"] == 1
    assert t1["previous_pass_rate"] is None

    r2 = {
        "created_at": "2026-01-01T00:05:00+00:00",
        "dataset_version": 1,
        "passed": True,
        "pass_rate": 0.9,
        "total_cases": 10,
        "suite_pass_rates": {"score_parser": 1.0},
    }
    t2 = append_eval_history(r2, history_path)
    assert t2["run_count"] == 2
    assert abs(float(t2["previous_pass_rate"]) - 0.8) < 1e-9
    assert abs(float(t2["delta_pass_rate"]) - 0.1) < 1e-9
