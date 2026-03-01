"""Capture a minimal, stable repo state snapshot.

This is intended for agent workflows and implementation tracking.
It avoids reading large files and only captures:
- current git HEAD (if available)
- python version
- key file existence

It does not write secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _git(cmd: list[str]) -> str | None:
    try:
        p = subprocess.run(["git", *cmd], capture_output=True, text=True, check=False)
        if p.returncode != 0:
            return None
        return (p.stdout or "").strip() or None
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="Output JSON path")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    state = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "python": {
            "version": sys.version.split()[0],
            "executable": sys.executable,
        },
        "git": {
            "head": _git(["rev-parse", "HEAD"]),
            "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"]),
            "dirty": bool(_git(["status", "--porcelain"])),
        },
        "repo": {
            "root": str(root),
        },
        "key_files": {
            "pyproject.toml": (root / "pyproject.toml").exists(),
            "README.md": (root / "README.md").exists(),
            "src/applypilot/view.py": (root / "src" / "applypilot" / "view.py").exists(),
            "src/applypilot/dashboard_server.py": (root / "src" / "applypilot" / "dashboard_server.py").exists(),
            "tests": (root / "tests").exists(),
        },
        "env": {
            "APPLYPILOT_DIR": os.environ.get("APPLYPILOT_DIR"),
            "APPLYPILOT_SEARCHES_PATH": os.environ.get("APPLYPILOT_SEARCHES_PATH"),
        },
    }

    out.write_text(json.dumps(state, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
