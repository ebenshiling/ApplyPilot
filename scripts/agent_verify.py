"""Agent verification runner.

Runs a small, repeatable set of checks that should pass before an agent marks
an implementation phase as complete.
"""

from __future__ import annotations

import subprocess
import sys


def _run(cmd: list[str]) -> int:
    p = subprocess.run(cmd)
    return int(p.returncode)


def main() -> int:
    checks = [
        ([sys.executable, "-m", "compileall", "-q", "src"], "compile"),
        ([sys.executable, "-m", "pytest", "-q"], "pytest"),
        ([sys.executable, "scripts/ai_regression_eval.py", "--strict"], "ai_eval"),
        ([sys.executable, "scripts/ai_eval_dataset.py", "--strict"], "ai_formal_eval"),
    ]

    for cmd, name in checks:
        rc = _run(cmd)
        if rc != 0:
            print(f"[verify] {name} failed (rc={rc})")
            return rc
    print("[verify] ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
