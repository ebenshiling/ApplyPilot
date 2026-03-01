#!/usr/bin/env python3
"""Bootstrap ApplyPilot on a fresh machine from a repository checkout.

Usage:
  python scripts/bootstrap.py
  python scripts/bootstrap.py --with-playwright-browsers
  python scripts/bootstrap.py --venv .venv --skip-jobspy
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def _run(cmd: list[str], cwd: Path) -> None:
    print("[bootstrap]", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd), check=True)


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up ApplyPilot quickly in a local virtual environment.")
    parser.add_argument("--venv", default=".venv", help="Virtual environment directory (default: .venv)")
    parser.add_argument(
        "--skip-jobspy",
        action="store_true",
        help="Skip optional JobSpy installation workaround packages.",
    )
    parser.add_argument(
        "--with-playwright-browsers",
        action="store_true",
        help="Install Playwright Chromium browser binaries.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    venv_dir = (repo_root / args.venv).resolve()

    if not venv_dir.exists():
        _run([sys.executable, "-m", "venv", str(venv_dir)], cwd=repo_root)

    py = _venv_python(venv_dir)
    if not py.exists():
        raise RuntimeError(f"Virtualenv python not found: {py}")

    _run([str(py), "-m", "pip", "install", "--upgrade", "pip"], cwd=repo_root)
    _run([str(py), "-m", "pip", "install", "-e", "."], cwd=repo_root)

    if not args.skip_jobspy:
        _run([str(py), "-m", "pip", "install", "--no-deps", "python-jobspy"], cwd=repo_root)
        _run(
            [
                str(py),
                "-m",
                "pip",
                "install",
                "pydantic",
                "tls-client",
                "requests",
                "markdownify",
                "regex",
            ],
            cwd=repo_root,
        )

    if args.with_playwright_browsers:
        _run([str(py), "-m", "playwright", "install", "chromium"], cwd=repo_root)

    if os.name == "nt":
        activate_hint = f"{args.venv}\\Scripts\\activate"
    else:
        activate_hint = f"source {args.venv}/bin/activate"

    print("\n[bootstrap] Done.")
    print(f"[bootstrap] Activate venv: {activate_hint}")
    print("[bootstrap] Then run: applypilot init")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
