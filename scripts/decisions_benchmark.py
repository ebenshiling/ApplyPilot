"""Run the synthetic Decisions trial in an isolated, disposable workspace.

No provider request is sent without --live. The live mode uses the configured
service OpenAI credential, evaluates at most nine synthetic cases, and does not
read or change candidate records or workspace preferences.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from applypilot import decision_observer as decisions
from applypilot.database import close_connection, init_db


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Send up to nine real Decisions requests")
    parser.add_argument("--parent", type=Path, default=Path("ops/implementations"), help="Existing directory for the disposable trial workspace")
    parser.add_argument("--out", type=Path, help="Optional JSON result file in an existing directory")
    args = parser.parse_args()
    if not args.parent.is_dir() or (args.out and not args.out.parent.is_dir()):
        parser.error("Trial and output parent directories must already exist")
    cases = decisions.fixtures()
    results = []
    if not args.live:
        results = [{"name": case["name"], "baseline": decisions.baseline(case["view"]), "expected": case["expected"]} for case in cases]
        output = {"mode": "offline_baseline_fixture_check", "provider_requests": 0, "cases": results}
        code = 0
    else:
        with tempfile.TemporaryDirectory(prefix="decisions-trial-", dir=args.parent) as temporary:
            root = Path(temporary)
            (root / ".ai-settings.json").write_text(json.dumps({"settings": {"APPLYPILOT_DECISIONS_OBSERVER": "1", "APPLYPILOT_DECISIONS_DAILY_BUDGET": "9"}, "credentials": {}}), encoding="utf-8")
            conn = init_db(root / "applypilot.db")
            try:
                for case in cases:
                    result = decisions.evaluate(conn, root, case["view"], run_id="isolated-live-trial", expected=case["expected"])
                    results.append({"name": case["name"], "expected": case["expected"], **result})
                    if result["status"] != "completed":
                        break
                report = decisions.report(conn, root)
                complete = len(results) == len(cases) and all(r["status"] == "completed" for r in results)
                output = {"mode": "live_synthetic_trial", "model": decisions.MODEL, "prompt_revision": decisions.PROMPT_REVISION,
                          "state": "completed" if complete else "incomplete", "planned_cases": len(cases),
                          "summary": report["summary"], "cases": results,
                          "scope": "Synthetic metadata only; isolated evaluation usage, not candidate workspace usage"}
                code = 0 if complete else 2
            finally:
                close_connection(root / "applypilot.db")
    text = json.dumps(output, indent=2)
    if args.out:
        args.out.write_text(text+"\n", encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
