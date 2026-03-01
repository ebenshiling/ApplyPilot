from applypilot.scoring.eval_harness import run_regression_eval


def test_ai_regression_eval_strict_passes() -> None:
    report = run_regression_eval(strict=True)
    assert report["strict"] is True
    assert int(report["total_checks"]) >= 6
    assert report["passed"] is True
    assert report["failed_checks"] == []
