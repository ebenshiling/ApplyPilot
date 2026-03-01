"""Run deterministic AI regression checks for scoring/tailoring."""

from __future__ import annotations

import argparse
import json

from applypilot.scoring.eval_harness import run_regression_eval


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ApplyPilot AI regression checks.")
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Require all checks to pass (default).",
    )
    parser.add_argument(
        "--relaxed",
        action="store_true",
        help="Allow >=80% checks to pass.",
    )
    args = parser.parse_args()

    strict = not args.relaxed
    if args.strict:
        strict = True

    report = run_regression_eval(strict=strict)
    failed = list(report.get("failed_checks") or [])

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=True))
    else:
        mode = "strict" if strict else "relaxed"
        print(f"[ai-eval] mode={mode} checks={report.get('total_checks')} failed={len(failed)}")
        for check in report.get("checks") or []:
            status = "PASS" if check.get("passed") else "FAIL"
            print(f"[ai-eval] {status} {check.get('name')}")
            if not check.get("passed"):
                for detail in check.get("details") or []:
                    print(f"  - {detail}")

    if report.get("passed"):
        print("[ai-eval] ok")
        return 0

    print("[ai-eval] failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
