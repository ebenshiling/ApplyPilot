"""Run formal scoring/tailoring eval dataset and emit trend report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from applypilot.config import LOG_DIR, ensure_dirs
from applypilot.scoring.eval_dataset import append_eval_history, run_formal_eval


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ApplyPilot formal eval dataset.")
    parser.add_argument("--dataset-path", type=str, default="", help="Path to eval dataset JSON.")
    parser.add_argument("--history-path", type=str, default="", help="Path to JSONL trend history file.")
    parser.add_argument("--no-history", action="store_true", help="Skip writing trend history.")
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    parser.add_argument("--strict", action="store_true", help="Require all suite cases to pass (default).")
    parser.add_argument("--relaxed", action="store_true", help="Use dataset relaxed threshold.")
    args = parser.parse_args()

    strict = not args.relaxed
    if args.strict:
        strict = True

    dataset_path = args.dataset_path.strip() or None
    report = run_formal_eval(strict=strict, dataset_path=dataset_path)

    trend = None
    if not args.no_history:
        ensure_dirs()
        history_path = Path(args.history_path) if args.history_path.strip() else (LOG_DIR / "ai_eval_history.jsonl")
        trend = append_eval_history(report, history_path)
        report["trend"] = trend

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=True))
    else:
        mode = "strict" if strict else "relaxed"
        print(
            f"[ai-formal-eval] mode={mode} cases={report.get('total_cases')} "
            f"passed={report.get('passed_cases')} rate={float(report.get('pass_rate') or 0.0):.3f}"
        )
        for suite in report.get("suites") or []:
            print(
                f"[ai-formal-eval] suite={suite.get('name')} "
                f"pass={suite.get('passed_count')}/{suite.get('total')} "
                f"rate={float(suite.get('pass_rate') or 0.0):.3f}"
            )
        if trend:
            prev = trend.get("previous_pass_rate")
            delta = trend.get("delta_pass_rate")
            prev_str = f"{float(prev):.3f}" if prev is not None else "n/a"
            delta_str = f"{float(delta):+.3f}" if delta is not None else "n/a"
            print(
                f"[ai-formal-eval] trend runs={trend.get('run_count')} prev={prev_str} "
                f"delta={delta_str} rolling5={float(trend.get('rolling_pass_rate_5') or 0.0):.3f}"
            )

    if report.get("passed"):
        print("[ai-formal-eval] ok")
        return 0
    print("[ai-formal-eval] failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
