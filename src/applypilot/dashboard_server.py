"""Local dashboard server with live DB actions.

This serves the generated HTML dashboard over HTTP so browser buttons can
update the SQLite database (mark applied/failed, block/archive jobs).

It intentionally binds to localhost by default.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from collections import deque
import base64
import re
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from applypilot.config import APP_DIR
from applypilot.database import add_blocked_url, get_connection
from applypilot.multiuser import (
    authenticate,
    create_session,
    create_user,
    delete_user,
    ensure_workspace_for_user,
    get_user_by_session,
    init_auth_db,
    list_admin_audit_logs,
    list_users,
    revoke_session,
    reset_user_password,
    set_user_admin,
    set_user_active,
)
from applypilot.view import generate_dashboard


_RUN_HISTORY_LOCK = threading.Lock()
_RUN_HISTORY_MAX = 20
_RUN_LOG_MAX_AGE_DAYS = 30

_LOGIN_RATE_LOCK = threading.Lock()
_LOGIN_WINDOW_SECONDS = 10 * 60
_LOGIN_MAX_FAILURES_PER_WINDOW = 8
_LOGIN_LOCKOUT_SECONDS = 5 * 60
_LOGIN_FAILURES: dict[str, deque[float]] = {}
_LOGIN_LOCKED_UNTIL: dict[str, float] = {}


def _run_history_path(app_dir: Path | None = None) -> Path:
    base = Path(app_dir or APP_DIR)
    log_dir = base / "logs" / "dashboard_runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "pipeline_history.json"


def _load_run_history(app_dir: Path | None = None) -> list[dict[str, Any]]:
    path = _run_history_path(app_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        runs = data.get("runs") if isinstance(data, dict) else None
        if isinstance(runs, list):
            return [r for r in runs if isinstance(r, dict)]
    except Exception:
        return []
    return []


def _save_run_history(runs: list[dict[str, Any]], app_dir: Path | None = None) -> None:
    path = _run_history_path(app_dir)
    tmp = path.with_suffix(path.suffix + ".tmp")
    keep = runs[:_RUN_HISTORY_MAX]
    removed = runs[_RUN_HISTORY_MAX:]

    log_dir = path.parent
    keep_paths: set[str] = set()
    for r in keep:
        lp = str(r.get("log_path") or "")
        if lp:
            keep_paths.add(lp)
        sp = str(r.get("searches_path") or "")
        if sp:
            keep_paths.add(sp)

    # Delete per-run artifacts that fell out of the retained window.
    for r in removed:
        for key, prefix, suffix in (
            ("log_path", "pipeline_", ".log"),
            ("searches_path", "searches_", ".yaml"),
        ):
            lp = str(r.get(key) or "")
            if not lp:
                continue
            try:
                p = Path(lp)
                if (
                    p.exists()
                    and p.is_file()
                    and p.parent == log_dir
                    and p.name.startswith(prefix)
                    and p.name.endswith(suffix)
                ):
                    p.unlink()
            except Exception:
                continue

    # Age-based cleanup for any unreferenced pipeline logs.
    try:
        cutoff = time.time() - (_RUN_LOG_MAX_AGE_DAYS * 86400)
        for p in list(log_dir.glob("pipeline_*.log")) + list(log_dir.glob("searches_*.yaml")):
            try:
                if str(p) in keep_paths:
                    continue
                st = p.stat()
                if st.st_mtime < cutoff:
                    p.unlink()
            except Exception:
                continue
    except Exception:
        pass

    payload = {"runs": keep}
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


def _upsert_run_history(record: dict[str, Any], app_dir: Path | None = None) -> None:
    run_id = str(record.get("run_id") or "").strip()
    if not run_id:
        return
    with _RUN_HISTORY_LOCK:
        runs = _load_run_history(app_dir)
        updated = False
        for i, r in enumerate(runs):
            if str(r.get("run_id") or "") == run_id:
                nr = dict(r)
                nr.update(record)
                runs[i] = nr
                updated = True
                break
        if not updated:
            runs.insert(0, dict(record))
        _save_run_history(runs, app_dir)


def _get_run_from_history(run_id: str, app_dir: Path | None = None) -> dict[str, Any] | None:
    rid = str(run_id or "").strip()
    if not rid:
        return None
    with _RUN_HISTORY_LOCK:
        for r in _load_run_history(app_dir):
            if str(r.get("run_id") or "") == rid:
                return dict(r)
    return None


def _get_latest_run_from_history(app_dir: Path | None = None) -> dict[str, Any] | None:
    with _RUN_HISTORY_LOCK:
        runs = _load_run_history(app_dir)
        if not runs:
            return None
        return dict(runs[0])


class _PipelineRunner:
    """Run ApplyPilot pipeline in a subprocess and capture logs."""

    def __init__(self, app_dir: Path | None = None) -> None:
        # RLock because we append logs while holding runner state.
        self._lock = threading.RLock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._app_dir = Path(app_dir or APP_DIR)

        self._starting = False
        self._start_error: str | None = None
        self._cancel_requested = False

        self._run_id: str | None = None
        self._started_at: float | None = None
        self._ended_at: float | None = None
        self._exit_code: int | None = None
        self._cmd: list[str] | None = None

        self._seq = 0
        self._max_lines = 2500
        self._lines: list[tuple[int, str]] = []
        self._dropped = 0
        self._log_path: str | None = None

        self._hydrated = False

    def _repo_root(self) -> Path:
        # src/applypilot/dashboard_server.py -> repo_root
        # Avoid Path.resolve() here; on some Windows/WSL UNC paths it can be slow.
        return Path(__file__).absolute().parents[2]

    def _resolve_launch_context(self) -> tuple[str | None, dict[str, str]]:
        """Return (cwd, env_overrides) for launching `python -m applypilot`.

        Prefer running from a source checkout when available so the pipeline
        subprocess uses the same code as this server.
        """
        env_overrides: dict[str, str] = {"PYTHONUNBUFFERED": "1"}

        # If we're running from a Windows Python interpreter against a WSL UNC
        # checkout (\\wsl.localhost\...), avoid filesystem probing here; it can
        # be slow enough to block HTTP requests.
        try:
            file_path = str(Path(__file__))
        except Exception:
            file_path = ""
        if os.name == "nt" and file_path.startswith("\\\\"):
            return None, env_overrides

        repo_root = self._repo_root()
        if (repo_root / "pyproject.toml").exists() and (repo_root / "src" / "applypilot").exists():
            src_dir = str((repo_root / "src").absolute())
            old_pp = os.environ.get("PYTHONPATH", "")
            env_overrides["PYTHONPATH"] = src_dir + (os.pathsep + old_pp if old_pp else "")
            cwd = str(repo_root)
            # On some Windows/WSL setups, `Path.resolve()` can produce UNC-like
            # paths (e.g. \\wsl.localhost\...). Using those as cwd can cause
            # subprocess startup to hang. If it looks like a UNC path, avoid
            # setting cwd and let the child inherit.
            if cwd.startswith("\\\\"):
                cwd = None
            return cwd, env_overrides

        return None, env_overrides

    def _coerce_bool(self, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        s = str(v or "").strip().lower()
        return s in ("1", "true", "yes", "y", "on")

    def _split_csv(self, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            parts = v
        else:
            parts = str(v).split(",")
        out: list[str] = []
        for p in parts:
            s = str(p or "").strip()
            if s:
                out.append(s)
        return out

    def _write_search_override_if_needed(
        self, payload: dict[str, Any], *, run_id: str, log_dir: Path
    ) -> tuple[str | None, dict[str, Any]]:
        """Create a per-run searches.yaml override file when requested.

        This file is based on the user's current searches.yaml, with requested
        replacements applied (so we don't accidentally erase unrelated settings
        like locations/country/exclude_titles).

        Returns (path_or_none, meta).
        """
        query = str(payload.get("search_query") or "").strip()
        boards = self._split_csv(payload.get("jobspy_sites") or payload.get("boards"))
        smarte_sites = self._split_csv(payload.get("smarte_sites") or payload.get("smart_sites"))

        try:
            results_per_site = int(payload.get("results_per_site") or 0)
        except Exception:
            results_per_site = 0
        try:
            hours_old = int(payload.get("hours_old") or 0)
        except Exception:
            hours_old = 0

        # If nothing is set, don't override the user's searches.yaml.
        if not (query or boards or smarte_sites or results_per_site or hours_old):
            return None, {}

        allowed_boards = {"indeed", "linkedin", "glassdoor", "zip_recruiter", "google"}
        norm_boards: list[str] = []
        for b in boards:
            nb = b.strip().lower().replace("-", "_")
            if nb in allowed_boards and nb not in norm_boards:
                norm_boards.append(nb)

        smart_name_map: dict[str, str] = {}
        try:
            from applypilot.config import load_sites_config

            sc = load_sites_config()
            entries = sc.get("sites") if isinstance(sc, dict) else []
            if isinstance(entries, list):
                for s in entries:
                    if not isinstance(s, dict):
                        continue
                    nm = str(s.get("name") or "").strip()
                    if nm:
                        smart_name_map[nm.lower()] = nm
        except Exception:
            smart_name_map = {}

        norm_smarte_sites: list[str] = []
        seen_s: set[str] = set()
        for s in smarte_sites:
            key = str(s or "").strip().lower()
            if not key:
                continue
            out = smart_name_map.get(key)
            if not out:
                # Allow direct custom names as-is when not in registry.
                out = str(s).strip()
            lk = out.lower()
            if lk in seen_s:
                continue
            seen_s.add(lk)
            norm_smarte_sites.append(out)

        # Base config: current user's searches.yaml.
        base: dict[str, Any] = {}
        try:
            from applypilot.setup_workspace import read_searches_dict

            loaded = read_searches_dict(self._app_dir)
            if isinstance(loaded, dict):
                base = dict(loaded)
        except Exception:
            base = {}

        merged: dict[str, Any] = dict(base)

        # Replace queries with a single tier-1 query.
        if query:
            merged["queries"] = [{"query": query, "tier": 1}]

        # Replace JobSpy boards/sites.
        if norm_boards:
            merged["boards"] = list(norm_boards)
            merged["sites"] = list(norm_boards)

        # Optional Smart Extract site allow-list by human site name.
        if norm_smarte_sites:
            merged["smart_sites"] = list(norm_smarte_sites)

        # Replace crawl defaults.
        if results_per_site or hours_old:
            d = merged.get("defaults")
            if not isinstance(d, dict):
                d = {}
            if results_per_site:
                d["results_per_site"] = int(max(1, results_per_site))
            if hours_old:
                d["hours_old"] = int(max(1, hours_old))
            merged["defaults"] = d

        content = "# Auto-generated by applypilot dashboard-serve\n" + f"# run_id: {run_id}\n"
        try:
            import yaml

            content += yaml.safe_dump(
                merged,
                sort_keys=False,
                default_flow_style=False,
                allow_unicode=False,
            )
        except Exception:
            # Fallback: minimal override only.
            lines: list[str] = []
            if query:
                lines.append("queries:")
                q_esc = query.replace('"', '\\"')
                lines.append(f'  - query: "{q_esc}"')
                lines.append("    tier: 1")
            if norm_boards:
                lines.append("boards:")
                for b in norm_boards:
                    lines.append(f"  - {b}")
            if norm_smarte_sites:
                lines.append("smart_sites:")
                for s in norm_smarte_sites:
                    s_esc = s.replace('"', '\\"')
                    lines.append(f'  - "{s_esc}"')
            if results_per_site or hours_old:
                lines.append("defaults:")
                if results_per_site:
                    lines.append(f"  results_per_site: {int(max(1, results_per_site))}")
                if hours_old:
                    lines.append(f"  hours_old: {int(max(1, hours_old))}")
            content += "\n".join(lines) + "\n"
        path = log_dir / f"searches_{run_id}.yaml"
        try:
            path.write_text(content, encoding="utf-8")
        except Exception as e:
            return None, {"search_override_error": str(e)}

        meta: dict[str, Any] = {
            "search_query": query or None,
            "jobspy_sites": norm_boards or None,
            "smarte_sites": norm_smarte_sites or None,
            "results_per_site": int(results_per_site) if results_per_site else None,
            "hours_old": int(hours_old) if hours_old else None,
        }
        return str(path), meta

    def _append(self, line: str) -> None:
        s = (line or "").rstrip("\n")
        with self._lock:
            self._seq += 1
            self._lines.append((self._seq, s))
            if len(self._lines) > self._max_lines:
                extra = len(self._lines) - self._max_lines
                if extra > 0:
                    self._lines = self._lines[extra:]
                    self._dropped += extra

    def _hydrate_from_history(self) -> None:
        with self._lock:
            if self._hydrated:
                return
            self._hydrated = True

        latest = _get_latest_run_from_history(self._app_dir)
        if not latest:
            return

        with self._lock:
            # Only hydrate if we don't already have a run in memory.
            if self._run_id is not None:
                return
            self._run_id = latest.get("run_id")
            self._started_at = latest.get("started_at")
            self._ended_at = latest.get("ended_at")
            self._exit_code = latest.get("exit_code")
            self._start_error = latest.get("start_error")
            self._cmd = latest.get("cmd")
            self._log_path = latest.get("log_path")

    def _load_log_into_memory_if_needed(self) -> None:
        self._hydrate_from_history()
        with self._lock:
            if self._lines:
                return
            log_path = self._log_path
        if not log_path:
            return
        path = Path(log_path)
        if not path.exists():
            return

        dq: deque[str] = deque(maxlen=self._max_lines)
        total = 0
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    total += 1
                    dq.append((line or "").rstrip("\n"))
        except Exception:
            return

        dropped = max(0, total - len(dq))
        oldest = dropped + 1 if total > 0 else 1

        with self._lock:
            self._lines = [(oldest + i, s) for i, s in enumerate(dq)]
            self._seq = (oldest - 1) + len(dq)
            self._dropped = dropped

    def select_run(self, run_id: str) -> tuple[bool, dict[str, Any]]:
        rid = str(run_id or "").strip()
        if not rid:
            return False, {"error": "bad_request"}
        with self._lock:
            if self._starting or (self._proc is not None and self._proc.poll() is None):
                return False, {"error": "busy"}

        r = _get_run_from_history(rid, self._app_dir)
        if not r:
            return False, {"error": "not_found"}

        with self._lock:
            self._run_id = r.get("run_id")
            self._started_at = r.get("started_at")
            self._ended_at = r.get("ended_at")
            self._exit_code = r.get("exit_code")
            self._start_error = r.get("start_error")
            self._cmd = r.get("cmd")
            self._log_path = r.get("log_path")
            self._seq = 0
            self._lines = []
            self._dropped = 0
        self._load_log_into_memory_if_needed()
        return True, {"ok": True, "run_id": rid}

    def start(self, payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        stages = payload.get("stages")
        if not stages:
            stages = ["all"]
        if isinstance(stages, str):
            stages = [stages]
        if not isinstance(stages, list):
            return False, {"error": "bad_request"}

        stages = [str(s).strip().lower() for s in stages if str(s).strip()]
        if not stages:
            stages = ["all"]

        try:
            min_score = int(payload.get("min_score") or 7)
        except Exception:
            min_score = 7
        try:
            workers = int(payload.get("workers") or 1)
        except Exception:
            workers = 1
        stream = bool(payload.get("stream") or False)
        dry_run = bool(payload.get("dry_run") or False)
        selected_only = self._coerce_bool(payload.get("selected_only"))
        tailor_lenient = self._coerce_bool(payload.get("tailor_lenient"))

        # Optional per-run discovery config (dashboard-driven overrides).
        skip_jobspy = self._coerce_bool(payload.get("discover_skip_jobspy"))
        skip_workday = self._coerce_bool(payload.get("discover_skip_workday"))
        skip_smarte = self._coerce_bool(payload.get("discover_skip_smarte"))

        # Use -u to force unbuffered stdout so the dashboard can stream logs.
        cmd: list[str] = [sys.executable, "-u", "-m", "applypilot", "run"]
        if "all" not in stages:
            cmd.extend(stages)
        cmd.extend(["--min-score", str(min_score), "--workers", str(max(1, workers))])
        if stream:
            cmd.append("--stream")
        if dry_run:
            cmd.append("--dry-run")

        env = os.environ.copy()
        cwd, env_overrides = self._resolve_launch_context()
        env.update(env_overrides)
        env["APPLYPILOT_DIR"] = str(self._app_dir)
        env["APPLYPILOT_USER"] = str(self._app_dir.name or "")

        with self._lock:
            if self._starting or (self._proc is not None and self._proc.poll() is None):
                return False, {"error": "already_running"}

            self._run_id = str(int(time.time()))
            self._started_at = time.time()
            self._ended_at = None
            self._exit_code = None
            self._start_error = None
            self._cancel_requested = False

            self._seq = 0
            self._lines = []
            self._dropped = 0

            log_dir = self._app_dir / "logs" / "dashboard_runs"
            log_dir.mkdir(parents=True, exist_ok=True)
            self._log_path = str((log_dir / f"pipeline_{self._run_id}.log").resolve())
            self._cmd = list(cmd)
            self._starting = True
            cmd_line = "$ " + " ".join(cmd)
            self._append(cmd_line)

            run_id = self._run_id
            log_path = self._log_path

        # Write a per-run searches.yaml when overrides are provided.
        searches_path, searches_meta = self._write_search_override_if_needed(
            payload, run_id=run_id, log_dir=self._app_dir / "logs" / "dashboard_runs"
        )
        if searches_path:
            env["APPLYPILOT_SEARCHES_PATH"] = searches_path
        if skip_jobspy:
            env["DISCOVER_SKIP_JOBSPY"] = "1"
        if skip_workday:
            env["DISCOVER_SKIP_WORKDAY"] = "1"
        if skip_smarte:
            env["DISCOVER_SKIP_SMARTE"] = "1"
        if selected_only:
            env["APPLYPILOT_SELECTED_ONLY"] = "1"
            env["APPLYPILOT_APPLY_SELECTED_ONLY"] = "1"
        if tailor_lenient:
            env["APPLYPILOT_TAILOR_LENIENT"] = "1"

        # Persist run metadata immediately.
        _upsert_run_history(
            {
                "run_id": run_id,
                "started_at": self._started_at,
                "ended_at": None,
                "exit_code": None,
                "start_error": None,
                "cmd": cmd,
                "log_path": log_path,
                "searches_path": searches_path,
                "stages": stages,
                "min_score": min_score,
                "workers": int(max(1, workers)),
                "stream": bool(stream),
                "dry_run": bool(dry_run),
                "selected_only": bool(selected_only),
                "tailor_lenient": bool(tailor_lenient),
                "discover_skip_jobspy": bool(skip_jobspy),
                "discover_skip_workday": bool(skip_workday),
                "discover_skip_smarte": bool(skip_smarte),
                **(searches_meta or {}),
            },
            self._app_dir,
        )

        # Ensure the command appears in the persistent log (so refresh/restart can replay it).
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(cmd_line + "\n")
        except Exception:
            pass

        # Launch in a background thread so the HTTP handler returns immediately.
        def _run() -> None:
            log_f = None
            proc: subprocess.Popen[bytes] | None = None
            try:
                with self._lock:
                    if self._cancel_requested:
                        self._starting = False
                        self._exit_code = -1
                        self._ended_at = time.time()
                        return

                log_f = open(log_path, "a", encoding="utf-8")
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=False,
                        bufsize=0,
                        cwd=cwd,
                        env=env,
                    )
                except Exception as e:
                    try:
                        log_f.write(f"Failed to start pipeline: {e}\n")
                    except Exception:
                        pass
                    with self._lock:
                        self._starting = False
                        self._start_error = str(e)
                        self._proc = None
                        self._exit_code = None
                        self._ended_at = time.time()

                    try:
                        self._append(f"Failed to start pipeline: {e}")
                    except Exception:
                        pass

                    _upsert_run_history(
                        {
                            "run_id": run_id,
                            "ended_at": self._ended_at,
                            "exit_code": None,
                            "start_error": str(e),
                        },
                        self._app_dir,
                    )
                    return

                with self._lock:
                    self._proc = proc
                    self._starting = False

                try:
                    assert proc is not None
                    _upsert_run_history({"run_id": run_id, "pid": int(proc.pid)}, self._app_dir)
                except Exception:
                    pass

                assert proc is not None
                out = proc.stdout
                if out is not None:
                    buf = b""
                    while True:
                        try:
                            chunk = out.read(4096)
                        except Exception:
                            chunk = b""
                        if isinstance(chunk, str):
                            chunk = chunk.encode("utf-8", errors="replace")
                        if not chunk:
                            break
                        buf += chunk

                        # Split on either \n, \r, or \r\n so progress-style output
                        # (carriage-return updates) shows up live in the dashboard.
                        while True:
                            i_n = buf.find(b"\n")
                            i_r = buf.find(b"\r")
                            if i_n == -1 and i_r == -1:
                                break
                            if i_n == -1:
                                i = i_r
                            elif i_r == -1:
                                i = i_n
                            else:
                                i = i_n if i_n < i_r else i_r

                            line_bytes = buf[:i]
                            # Consume separator (handle CRLF).
                            if buf[i : i + 2] == b"\r\n":
                                buf = buf[i + 2 :]
                            else:
                                buf = buf[i + 1 :]

                            if not line_bytes:
                                continue
                            line = line_bytes.decode("utf-8", errors="replace")
                            line = line.rstrip("\n").rstrip("\r")

                            try:
                                log_f.write(line + "\n")
                                log_f.flush()
                            except Exception:
                                pass
                            self._append(line)

                    # Flush any trailing partial line.
                    if buf:
                        try:
                            tail = buf.decode("utf-8", errors="replace")
                            tail = tail.rstrip("\n").rstrip("\r")
                        except Exception:
                            tail = ""
                        if tail:
                            try:
                                log_f.write(tail + "\n")
                                log_f.flush()
                            except Exception:
                                pass
                            self._append(tail)
            finally:
                rc: int | None = None
                try:
                    if proc is not None:
                        rc = proc.wait(timeout=1)
                except Exception:
                    rc = None
                with self._lock:
                    self._exit_code = int(rc) if rc is not None else self._exit_code
                    self._ended_at = time.time() if self._ended_at is None else self._ended_at

                _upsert_run_history(
                    {
                        "run_id": run_id,
                        "ended_at": self._ended_at,
                        "exit_code": self._exit_code,
                        "start_error": self._start_error,
                    },
                    self._app_dir,
                )
                try:
                    if log_f is not None:
                        log_f.flush()
                        log_f.close()
                except Exception:
                    pass

        t = threading.Thread(target=_run, name=f"pipeline-run-{run_id}", daemon=True)
        with self._lock:
            self._thread = t
        t.start()

        return True, {"run_id": run_id, "cmd": cmd, "log_path": log_path}

    def stop(self) -> tuple[bool, dict[str, Any]]:
        with self._lock:
            proc = self._proc
            if self._starting and proc is None:
                self._cancel_requested = True
                self._append("[stop requested while starting]")
                return True, {"ok": True, "note": "cancel_requested"}

        if proc is None or proc.poll() is not None:
            return False, {"error": "not_running"}

        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        except Exception as e:
            return False, {"error": "stop_failed", "detail": str(e)}

        return True, {"ok": True}

    def status(self) -> dict[str, Any]:
        self._hydrate_from_history()
        with self._lock:
            running = self._proc is not None and self._proc.poll() is None
            oldest = self._lines[0][0] if self._lines else (self._seq + 1)
            newest = self._lines[-1][0] if self._lines else self._seq
            return {
                "starting": bool(self._starting),
                "running": running,
                "run_id": self._run_id,
                "started_at": self._started_at,
                "ended_at": self._ended_at,
                "exit_code": self._exit_code,
                "start_error": self._start_error,
                "cmd": self._cmd,
                "log_path": self._log_path,
                "oldest_seq": oldest,
                "newest_seq": newest,
                "dropped": self._dropped,
            }

    def logs(self, since: int = 0, limit: int = 250) -> dict[str, Any]:
        self._load_log_into_memory_if_needed()
        with self._lock:
            oldest = self._lines[0][0] if self._lines else (self._seq + 1)
            newest = self._lines[-1][0] if self._lines else self._seq
            truncated = bool(self._lines and since and since < oldest)
            entries: list[dict[str, Any]] = []
            next_since = since
            for seq, line in self._lines:
                if seq > since:
                    entries.append({"seq": seq, "line": line})
                    next_since = seq
                    if len(entries) >= limit:
                        break
            return {
                "entries": entries,
                "next_since": next_since,
                "truncated": truncated,
                "oldest_seq": oldest,
                "newest_seq": newest,
            }

    def history(self, limit: int = _RUN_HISTORY_MAX) -> list[dict[str, Any]]:
        with _RUN_HISTORY_LOCK:
            runs = _load_run_history(self._app_dir)
        return runs[: max(1, min(_RUN_HISTORY_MAX, int(limit or _RUN_HISTORY_MAX)))]


_PIPELINE = _PipelineRunner(APP_DIR)
_PIPELINES_LOCK = threading.Lock()
_PIPELINES: dict[str, _PipelineRunner] = {}

_MULTI_USER_MODE = False

_DASHBOARD_LOCK = threading.Lock()
_DASHBOARD_CACHE: dict[str, dict[str, Any]] = {}


def _workspace_for_user(user: dict[str, Any] | None) -> Path:
    if not _MULTI_USER_MODE or not user:
        return APP_DIR
    return ensure_workspace_for_user(str(user.get("username") or ""))


def _runner_for_user(user: dict[str, Any] | None) -> _PipelineRunner:
    if not _MULTI_USER_MODE or not user:
        return _PIPELINE
    username = str(user.get("username") or "").strip().lower()
    if not username:
        return _PIPELINE
    with _PIPELINES_LOCK:
        r = _PIPELINES.get(username)
        if r is None:
            r = _PipelineRunner(_workspace_for_user(user))
            _PIPELINES[username] = r
        return r


def _code_stamp() -> float:
    """Best-effort stamp for dashboard UI code (view.py)."""
    try:
        import applypilot.view as _view

        p = Path(getattr(_view, "__file__", "") or "")
        if p.exists():
            return float(p.stat().st_mtime)
    except Exception:
        return 0.0
    return 0.0


def _db_stamp(db_path: Path | None = None) -> float:
    """Best-effort last-write stamp for the SQLite DB (including WAL)."""
    try:
        dbp = Path(db_path) if db_path is not None else (APP_DIR / "applypilot.db")
    except Exception:
        return 0.0

    paths = [dbp, Path(str(dbp) + "-wal"), Path(str(dbp) + "-shm")]
    newest = 0.0
    for p in paths:
        try:
            if p.exists():
                newest = max(newest, float(p.stat().st_mtime))
        except Exception:
            continue
    return newest


def _ensure_dashboard(*, force: bool = False, app_dir: Path | None = None, db_path: Path | None = None) -> str:
    ad = Path(app_dir or APP_DIR)
    dbp = Path(db_path or (ad / "applypilot.db"))
    cache_key = str(ad)
    with _DASHBOARD_LOCK:
        entry = _DASHBOARD_CACHE.get(cache_key, {})

    stamp = _db_stamp(dbp)
    code_stamp = _code_stamp()

    if (
        not force
        and entry.get("path")
        and Path(str(entry.get("path"))).exists()
        and entry.get("db_stamp") is not None
        and entry.get("code_stamp") is not None
        and stamp <= float(entry.get("db_stamp") or 0.0)
        and code_stamp <= float(entry.get("code_stamp") or 0.0)
    ):
        return str(entry.get("path"))

    # Generate synchronously once (or force refresh).
    new_path = generate_dashboard(quiet=True, app_dir=ad, db_path=dbp)
    with _DASHBOARD_LOCK:
        _DASHBOARD_CACHE[cache_key] = {
            "path": new_path,
            "db_stamp": stamp,
            "code_stamp": code_stamp,
            "regen_in_flight": False,
        }
    return new_path


def _regen_dashboard_async(*, app_dir: Path | None = None, db_path: Path | None = None) -> None:
    ad = Path(app_dir or APP_DIR)
    dbp = Path(db_path or (ad / "applypilot.db"))
    cache_key = str(ad)

    with _DASHBOARD_LOCK:
        entry = _DASHBOARD_CACHE.get(cache_key, {})
        if entry.get("regen_in_flight"):
            return
        entry["regen_in_flight"] = True
        _DASHBOARD_CACHE[cache_key] = entry

    def _run() -> None:
        try:
            new_path = generate_dashboard(quiet=True, app_dir=ad, db_path=dbp)
            with _DASHBOARD_LOCK:
                _DASHBOARD_CACHE[cache_key] = {
                    "path": new_path,
                    "db_stamp": _db_stamp(dbp),
                    "code_stamp": _code_stamp(),
                    "regen_in_flight": False,
                }
        finally:
            with _DASHBOARD_LOCK:
                e2 = _DASHBOARD_CACHE.get(cache_key, {})
                e2["regen_in_flight"] = False
                _DASHBOARD_CACHE[cache_key] = e2

    threading.Thread(target=_run, name="dashboard-regen", daemon=True).start()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _login_keys(client_ip: str, login_ident: str) -> tuple[str, str]:
    ip = str(client_ip or "unknown").strip().lower()
    ident = str(login_ident or "").strip().lower()[:160]
    return (f"ip:{ip}", f"ip:{ip}|login:{ident}")


def _rate_limiter_prune(now_ts: float) -> None:
    cutoff = now_ts - _LOGIN_WINDOW_SECONDS
    stale: list[str] = []
    for key, dq in list(_LOGIN_FAILURES.items()):
        while dq and dq[0] < cutoff:
            dq.popleft()
        if not dq:
            stale.append(key)
    for key in stale:
        _LOGIN_FAILURES.pop(key, None)

    expired: list[str] = [k for k, until in _LOGIN_LOCKED_UNTIL.items() if until <= now_ts]
    for key in expired:
        _LOGIN_LOCKED_UNTIL.pop(key, None)


def _login_allowed(client_ip: str, login_ident: str) -> tuple[bool, int]:
    now_ts = time.time()
    k_ip, k_ident = _login_keys(client_ip, login_ident)
    with _LOGIN_RATE_LOCK:
        _rate_limiter_prune(now_ts)
        waits = [
            max(0, int(_LOGIN_LOCKED_UNTIL.get(k, 0) - now_ts))
            for k in (k_ip, k_ident)
            if _LOGIN_LOCKED_UNTIL.get(k, 0) > now_ts
        ]
        if waits:
            return False, max(waits)
        return True, 0


def _login_record_failure(client_ip: str, login_ident: str) -> None:
    now_ts = time.time()
    k_ip, k_ident = _login_keys(client_ip, login_ident)
    with _LOGIN_RATE_LOCK:
        _rate_limiter_prune(now_ts)
        for key in (k_ip, k_ident):
            dq = _LOGIN_FAILURES.setdefault(key, deque())
            dq.append(now_ts)
            while dq and dq[0] < (now_ts - _LOGIN_WINDOW_SECONDS):
                dq.popleft()
            if len(dq) >= _LOGIN_MAX_FAILURES_PER_WINDOW:
                _LOGIN_LOCKED_UNTIL[key] = now_ts + _LOGIN_LOCKOUT_SECONDS


def _login_record_success(client_ip: str, login_ident: str) -> None:
    k_ip, k_ident = _login_keys(client_ip, login_ident)
    with _LOGIN_RATE_LOCK:
        _LOGIN_FAILURES.pop(k_ident, None)
        _LOGIN_LOCKED_UNTIL.pop(k_ident, None)
        # Keep IP-level counters to continue broad brute-force protection.


def _cookie_value(raw_cookie: str, name: str) -> str | None:
    try:
        c = SimpleCookie()
        c.load(raw_cookie or "")
        m = c.get(name)
        if m is None:
            return None
        v = str(m.value or "").strip()
        return v or None
    except Exception:
        return None


def _session_cookie(token: str) -> str:
    # Deliberately no Secure flag for localhost http:// usage.
    return f"ap_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"


def _session_cookie_clear() -> str:
    return "ap_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


def _csrf_cookie(token: str) -> str:
    # Client JS reads this cookie and mirrors it in X-CSRF-Token.
    return f"ap_csrf={token}; Path=/; SameSite=Lax; Max-Age=2592000"


def _csrf_cookie_clear() -> str:
    return "ap_csrf=; Path=/; SameSite=Lax; Max-Age=0"


def _mark_job_by_id(db_path: Path, job_id: int, status: str, reason: str | None = None) -> int:
    conn = get_connection(db_path)
    now = _now_iso()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if status == "applied":
            cur = conn.execute(
                """
                UPDATE jobs
                   SET apply_status = 'applied',
                       applied_at = COALESCE(applied_at, ?),
                       apply_error = NULL,
                       agent_id = NULL
                 WHERE rowid = ?
                """,
                (now, int(job_id)),
            )
        else:
            cur = conn.execute(
                """
                UPDATE jobs
                   SET apply_status = 'failed',
                       apply_error = ?,
                       apply_attempts = 99,
                       agent_id = NULL
                 WHERE rowid = ?
                   AND applied_at IS NULL
                """,
                ((reason or "manual")[:400], int(job_id)),
            )
        conn.commit()
        return int(cur.rowcount or 0)
    except Exception:
        conn.rollback()
        raise


def _block_job_by_id(db_path: Path, job_id: int, reason: str = "user_deleted") -> int:
    conn = get_connection(db_path)
    row = conn.execute("SELECT url, application_url FROM jobs WHERE rowid = ?", (int(job_id),)).fetchone()
    if not row:
        return 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        add_blocked_url(str(row[0] or ""), reason, conn=conn)
        add_blocked_url(str(row[1] or ""), reason, conn=conn)
        cur = conn.execute(
            """
            UPDATE jobs
               SET apply_status = 'skipped',
                   apply_error = ?,
                   agent_id = NULL
             WHERE rowid = ?
            """,
            (reason, int(job_id)),
        )
        conn.commit()
        return int(cur.rowcount or 0)
    except Exception:
        conn.rollback()
        raise


def _cleanup_job_artifacts(rows: list[sqlite3.Row]) -> None:
    paths: set[Path] = set()
    for r in rows:
        trp = str(r["tailored_resume_path"] or "").strip() if "tailored_resume_path" in r.keys() else ""
        clp = str(r["cover_letter_path"] or "").strip() if "cover_letter_path" in r.keys() else ""

        def _add(p: str) -> None:
            if not p:
                return
            try:
                paths.add(Path(p))
            except Exception:
                return

        if trp:
            p = Path(trp)
            _add(str(p))
            _add(str(p.with_suffix(".pdf")))
            _add(str(p.with_suffix(".txt")))
            # Tailor helper artifacts
            stem = p.with_suffix("")
            _add(str(stem) + "_JOB.txt")
            _add(str(stem) + "_REPORT.json")

        if clp:
            p = Path(clp)
            _add(str(p))
            _add(str(p.with_suffix(".pdf")))
            _add(str(p.with_suffix(".txt")))

    for p in paths:
        try:
            if p.exists() and p.is_file():
                p.unlink()
        except Exception:
            pass


def _delete_jobs_by_ids(db_path: Path, ids: list[int]) -> int:
    uniq: list[int] = []
    seen: set[int] = set()
    for i in ids:
        try:
            v = int(i)
        except Exception:
            continue
        if v <= 0 or v in seen:
            continue
        seen.add(v)
        uniq.append(v)
    if not uniq:
        return 0

    conn = get_connection(db_path)
    qs = ",".join("?" for _ in uniq)
    rows = conn.execute(
        f"SELECT rowid, tailored_resume_path, cover_letter_path FROM jobs WHERE rowid IN ({qs})",
        tuple(uniq),
    ).fetchall()
    if not rows:
        return 0

    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(f"DELETE FROM jobs WHERE rowid IN ({qs})", tuple(uniq))
        conn.commit()
        deleted = int(cur.rowcount or 0)
    except Exception:
        conn.rollback()
        raise

    _cleanup_job_artifacts(list(rows))
    return deleted


def _login_page_html() -> str:
    return """<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>ApplyPilot Sign In</title>
  <style>
    :root { --bg:#f7f4ec; --card:#ffffff; --ink:#171a21; --line:#d8dce4; --muted:#5b6474; --accent:#0f766e; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; background: var(--bg); color: var(--ink); }
    .wrap { max-width: 980px; margin: 48px auto; padding: 0 18px; }
    h1 { margin: 0 0 8px; font-size: 32px; }
    .sub { color: var(--muted); margin: 0 0 18px; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px; }
    .card h2 { margin: 0 0 12px; font-size: 22px; }
    .row { display: grid; gap: 8px; margin-bottom: 10px; }
    input { width:100%; border:1px solid var(--line); border-radius: 10px; padding: 10px 12px; font-size:16px; }
    button { border:1px solid var(--line); border-radius: 10px; padding: 10px 14px; font-size: 16px; font-weight: 700; cursor: pointer; }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    .msg { margin: 0 0 12px; color: #b91c1c; min-height: 1.2em; }
    @media (max-width: 860px) { .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <div class=\"wrap\">
    <h1>ApplyPilot</h1>
    <p class=\"sub\">Sign in or create an account. Each account has its own workspace, jobs, profile, resume, and searches. The first account is the admin.</p>
    <p id=\"msg\" class=\"msg\"></p>
    <div class=\"grid\">
      <div class=\"card\">
        <h2>Sign In</h2>
        <div class=\"row\"><input id=\"login-ident\" placeholder=\"Username or email\" /></div>
        <div class=\"row\"><input id=\"login-pass\" type=\"password\" placeholder=\"Password\" /></div>
        <button class=\"primary\" onclick=\"doLogin(this)\">Sign in</button>
      </div>
      <div class=\"card\">
        <h2>Create Account</h2>
        <div class=\"row\"><input id=\"reg-full\" placeholder=\"Full name\" /></div>
        <div class=\"row\"><input id=\"reg-user\" placeholder=\"Username\" /></div>
        <div class=\"row\"><input id=\"reg-email\" placeholder=\"Email\" /></div>
        <div class=\"row\"><input id=\"reg-phone\" placeholder=\"Phone (optional)\" /></div>
        <div class=\"row\"><input id=\"reg-city\" placeholder=\"City (optional)\" /></div>
        <div class=\"row\"><input id=\"reg-country\" placeholder=\"Country (optional)\" /></div>
        <div class=\"row\"><input id=\"reg-pass\" type=\"password\" placeholder=\"Password (min 8 chars)\" /></div>
        <button class=\"primary\" onclick=\"doRegister(this)\">Create account</button>
      </div>
    </div>
  </div>
<script>
function setMsg(v) { var el = document.getElementById('msg'); if (el) el.textContent = v || ''; }
function getCookie(name) {
  try {
    const parts = (document.cookie || '').split(';');
    for (const p of parts) {
      const s = (p || '').trim();
      if (!s) continue;
      if (s.startsWith(name + '=')) return decodeURIComponent(s.slice(name.length + 1));
    }
  } catch (e) {}
  return '';
}
async function postJson(path, payload) {
  const headers = {'Content-Type':'application/json'};
  const csrf = getCookie('ap_csrf');
  if (csrf) headers['X-CSRF-Token'] = csrf;
  const r = await fetch(path, { method:'POST', headers:headers, body: JSON.stringify(payload || {}) });
  const t = await r.text();
  let j = {};
  try { j = JSON.parse(t || '{}'); } catch (e) { j = { detail: t }; }
  if (!r.ok || !j.ok) throw new Error(j.detail || j.error || ('HTTP ' + r.status));
  return j;
}
async function doLogin(btn) {
  setMsg('');
  const old = btn.textContent; btn.disabled = true; btn.textContent = 'Signing in...';
  try {
    await postJson('/api/auth/login', { login: (document.getElementById('login-ident') || {}).value || '', password: (document.getElementById('login-pass') || {}).value || '' });
    location.href = '/';
  } catch (e) { setMsg(e && e.message ? e.message : String(e)); }
  finally { btn.disabled = false; btn.textContent = old; }
}
async function doRegister(btn) {
  setMsg('');
  const old = btn.textContent; btn.disabled = true; btn.textContent = 'Creating...';
  try {
    await postJson('/api/auth/register', {
      full_name: (document.getElementById('reg-full') || {}).value || '',
      username: (document.getElementById('reg-user') || {}).value || '',
      email: (document.getElementById('reg-email') || {}).value || '',
      phone: (document.getElementById('reg-phone') || {}).value || '',
      city: (document.getElementById('reg-city') || {}).value || '',
      country: (document.getElementById('reg-country') || {}).value || '',
      password: (document.getElementById('reg-pass') || {}).value || ''
    });
    location.href = '/';
  } catch (e) { setMsg(e && e.message ? e.message : String(e)); }
  finally { btn.disabled = false; btn.textContent = old; }
}
</script>
</body>
</html>"""


def _inject_multi_user_ui(html: str, user: dict[str, Any]) -> str:
    me = {
        "id": int(user.get("id") or 0),
        "username": str(user.get("username") or ""),
        "is_admin": bool(user.get("is_admin")),
    }
    me_json = json.dumps(me, ensure_ascii=True).replace("</", "<\\/")
    script = (
        "<script>"
        "(function(){"
        f"const me={me_json};"
        "function getCookie(name){try{const parts=(document.cookie||'').split(';');for(const p of parts){const s=(p||'').trim();if(!s)continue;if(s.startsWith(name+'='))return decodeURIComponent(s.slice(name.length+1));}}catch(e){} return '';}"
        "function postJson(path,payload){"
        "const h={'Content-Type':'application/json'};const csrf=getCookie('ap_csrf');if(csrf)h['X-CSRF-Token']=csrf;"
        "return fetch(path,{method:'POST',headers:h,body:JSON.stringify(payload||{})})"
        ".then(async(r)=>{const t=await r.text();let j={};try{j=JSON.parse(t||'{}')}catch(e){j={detail:t}};if(!r.ok||!j.ok)throw new Error(j.detail||j.error||('HTTP '+r.status));return j;});"
        "}"
        "const bar=document.createElement('div');"
        "bar.style.cssText='position:fixed;right:12px;top:10px;z-index:9999;background:var(--card, rgba(255,255,255,0.92));color:var(--ink, #0b0f14);border:1px solid var(--line, #d8dce4);border-radius:999px;padding:5px 9px;display:flex;gap:7px;align-items:center;box-shadow:var(--shadow, 0 8px 20px rgba(0,0,0,0.08));font:600 12px ui-sans-serif,system-ui;line-height:1;backdrop-filter:blur(10px);';"
        "const role=me.is_admin?'admin':'user';"
        'bar.innerHTML=\'<span style="font-weight:700">\'+me.username+\'</span><span style="opacity:.7;font-size:11px">(\'+role+\')</span><button id="ap-logout-btn" style="border:1px solid var(--line, #d8dce4);border-radius:999px;padding:2px 7px;background:var(--surface, rgba(255,255,255,0.82));color:var(--ink, #0b0f14);cursor:pointer;font-weight:700;font-size:11px;line-height:1.1">Logout</button>\';'
        "document.body.appendChild(bar);"
        "const lb=document.getElementById('ap-logout-btn');"
        "if(lb){lb.onclick=async()=>{try{await postJson('/api/auth/logout',{});}catch(e){} location.href='/';};}"
        "if(!me.is_admin) return;"
        "const host=document.querySelector('#setup-panel .panel-body')||document.body;"
        "const panel=document.createElement('details');"
        "panel.className='panel';"
        "panel.style.margin='0.85rem 0';"
        'panel.innerHTML=\'<summary>User Admin</summary><div class="panel-body"><div style="display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin-bottom:.45rem"><button id="ap-admin-refresh" class="apply-link copy-btn">Refresh users</button><button id="ap-audit-refresh" class="apply-link copy-btn">Refresh audit</button><span class="meta-tag">Admin-only actions</span></div><div id="ap-admin-users" class="job-desc">Loading users...</div><div class="job-desc" style="margin-top:.6rem;opacity:.85">Recent admin audit</div><div id="ap-admin-audit" class="job-desc" style="max-height:180px;overflow:auto">Loading audit...</div></div>\';'
        "host.appendChild(panel);"
        "const target=document.getElementById('ap-admin-users');"
        "const targetAudit=document.getElementById('ap-admin-audit');"
        "function esc(s){const d=document.createElement('div');d.textContent=''+(s||'');return d.innerHTML;}"
        "async function loadAudit(){"
        "if(!targetAudit) return; targetAudit.textContent='Loading audit...';"
        "try{"
        "const r=await fetch('/api/admin/audit?limit=80'); const t=await r.text(); let j={}; try{j=JSON.parse(t||'{}')}catch(e){j={detail:t}};"
        "if(!r.ok||!j.ok) throw new Error(j.detail||j.error||('HTTP '+r.status));"
        "const items=Array.isArray(j.audit)?j.audit:[];"
        "if(!items.length){targetAudit.textContent='No audit events yet.'; return;}"
        "let h='<div style=\"display:grid;gap:.35rem\">';"
        "for(const a of items){"
        "const when=esc(a.created_at||''); const actor=esc(a.actor_username||('id:'+a.actor_user_id)); const action=esc(a.action||''); const target=esc(a.target_username||('id:'+a.target_user_id));"
        "h+='<div style=\"padding:6px 8px;border:1px solid #e4e7ee;border-radius:8px;background:#fff\"><strong>'+action+'</strong> <span style=\"opacity:.8\">'+actor+' -> '+target+'</span><div style=\"opacity:.65\">'+when+'</div></div>';"
        "}"
        "h+='</div>'; targetAudit.innerHTML=h;"
        "}catch(e){ targetAudit.textContent='Failed to load audit: '+(e&&e.message?e.message:String(e)); }"
        "}"
        "async function loadUsers(){"
        "if(!target) return; target.textContent='Loading users...';"
        "try{"
        "const r=await fetch('/api/admin/users'); const t=await r.text(); let j={}; try{j=JSON.parse(t||'{}')}catch(e){j={detail:t}};"
        "if(!r.ok||!j.ok) throw new Error(j.detail||j.error||('HTTP '+r.status));"
        "const users=Array.isArray(j.users)?j.users:[];"
        "if(!users.length){target.textContent='No users found.'; return;}"
        "let h='<div style=\"display:grid;gap:.45rem\">';"
        "for(const u of users){"
        "const canDelete=(u.id!==me.id);"
        "h+='<div style=\"border:1px solid #d8dce4;border-radius:10px;padding:8px\">';"
        "h+='<div><strong>'+esc(u.username)+'</strong> <span style=\"opacity:.7\">'+esc(u.email||'')+'</span></div>'"
        "+'<div style=\"display:flex;gap:.45rem;flex-wrap:wrap;align-items:center;margin-top:.35rem\">'"
        "+'<span class=\"meta-tag\">'+(u.is_admin?'admin':'user')+'</span>'"
        "+'<span class=\"meta-tag\">'+(u.is_active?'active':'disabled')+'</span>'"
        "+'<button class=\"apply-link copy-btn\" data-act=\"'+(u.is_admin?'demote':'promote')+'\" data-id=\"'+u.id+'\">'+(u.is_admin?'Demote admin':'Promote admin')+'</button>'"
        "+'<button class=\"apply-link copy-btn\" data-act=\"'+(u.is_active?'disable':'enable')+'\" data-id=\"'+u.id+'\">'+(u.is_active?'Disable':'Enable')+'</button>'"
        '+\'<button class="apply-link copy-btn" data-act="reset" data-id="\'+u.id+\'">Reset password</button>\''
        "+(canDelete?'<button class=\"apply-link danger\" data-act=\"delete\" data-id=\"'+u.id+'\">Delete</button>':'')"
        "+'</div></div>';"
        "}"
        "h+='</div>'; target.innerHTML=h;"
        "target.querySelectorAll('button[data-act]').forEach((b)=>{b.onclick=async()=>{"
        "const id=parseInt(b.getAttribute('data-id')||'0',10)||0; const act=(b.getAttribute('data-act')||'').toLowerCase(); if(!id||!act)return;"
        "try{"
        "if(act==='promote') await postJson('/api/admin/users/promote',{user_id:id});"
        "else if(act==='demote') await postJson('/api/admin/users/demote',{user_id:id});"
        "if(act==='disable') await postJson('/api/admin/users/disable',{user_id:id});"
        "else if(act==='enable') await postJson('/api/admin/users/enable',{user_id:id});"
        "else if(act==='reset'){ const np=prompt('New password (min 8 chars):',''); if(!np) return; await postJson('/api/admin/users/reset-password',{user_id:id,new_password:np}); }"
        "else if(act==='delete'){ if(!confirm('Delete this user and workspace data?')) return; await postJson('/api/admin/users/delete',{user_id:id,purge_workspace:true}); }"
        "await loadUsers();"
        "await loadAudit();"
        "}catch(e){ alert(e&&e.message?e.message:String(e)); }"
        "};});"
        "}catch(e){ target.textContent='Failed to load users: '+(e&&e.message?e.message:String(e)); }"
        "}"
        "const rb=document.getElementById('ap-admin-refresh'); if(rb) rb.onclick=loadUsers;"
        "const ab=document.getElementById('ap-audit-refresh'); if(ab) ab.onclick=loadAudit;"
        "loadUsers();"
        "loadAudit();"
        "})();"
        "</script>"
    )
    if "</body>" in html:
        return html.replace("</body>", script + "\n</body>", 1)
    return html + script


class _Handler(BaseHTTPRequestHandler):
    server_version = "ApplyPilotDashboard/1.0"

    def _send(self, status: int, body: bytes, content_type: str, headers: dict[str, Any] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            if isinstance(v, (list, tuple)):
                for item in v:
                    self.send_header(str(k), str(item))
            else:
                self.send_header(str(k), str(v))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Client disconnected mid-response (refresh/navigate). Ignore.
            try:
                self.close_connection = True
            except Exception:
                pass
            return

    def _send_json(self, status: int, obj: dict[str, Any], headers: dict[str, Any] | None = None) -> None:
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8", headers=headers)

    def _read_json(self) -> dict[str, Any]:
        try:
            n = int(self.headers.get("Content-Length", "0") or "0")
        except Exception:
            n = 0
        raw = self.rfile.read(n) if n > 0 else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # Match BaseHTTPRequestHandler signature.
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Keep CLI output clean; override default stdout logging.
        return

    def _current_user(self) -> dict[str, Any] | None:
        if not _MULTI_USER_MODE:
            return None
        token = _cookie_value(self.headers.get("Cookie", ""), "ap_session")
        if not token:
            return None
        return get_user_by_session(token)

    def _active_app_dir(self, user: dict[str, Any] | None) -> Path:
        return _workspace_for_user(user)

    def _origin_allowed(self) -> bool:
        """Best-effort same-origin check for POST requests."""
        origin = str(self.headers.get("Origin") or "").strip()
        if not origin:
            # Non-browser clients may omit Origin.
            return True
        host = str(self.headers.get("Host") or "").strip().lower()
        if not host:
            return False
        try:
            p = urlparse(origin)
        except Exception:
            return False
        if p.scheme not in ("http", "https"):
            return False
        return str(p.netloc or "").strip().lower() == host

    def _csrf_ok(self) -> bool:
        c_token = _cookie_value(self.headers.get("Cookie", ""), "ap_csrf")
        h_token = str(self.headers.get("X-CSRF-Token") or "").strip()
        if not c_token or not h_token:
            return False
        return secrets.compare_digest(c_token, h_token)

    def _is_admin(self, user: dict[str, Any] | None) -> bool:
        return bool(user and user.get("is_admin"))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path or "/")
        path = parsed.path or "/"

        if path == "/health":
            self._send_json(200, {"ok": True, "multi_user": bool(_MULTI_USER_MODE)})
            return

        if path == "/api/auth/me":
            user = self._current_user()
            self._send_json(
                200,
                {
                    "ok": True,
                    "multi_user": bool(_MULTI_USER_MODE),
                    "authenticated": bool(user) if _MULTI_USER_MODE else True,
                    "user": user,
                },
            )
            return

        if path == "/api/admin/users" and _MULTI_USER_MODE:
            user = self._current_user()
            if not self._is_admin(user):
                self._send_json(403, {"ok": False, "error": "admin_only"})
                return
            self._send_json(200, {"ok": True, "users": list_users()})
            return

        if path == "/api/admin/audit" and _MULTI_USER_MODE:
            user = self._current_user()
            if not self._is_admin(user):
                self._send_json(403, {"ok": False, "error": "admin_only"})
                return
            qs = parse_qs(parsed.query or "")
            try:
                limit = int((qs.get("limit") or ["200"])[0])
            except Exception:
                limit = 200
            self._send_json(200, {"ok": True, "audit": list_admin_audit_logs(limit=limit)})
            return

        if path in ("/", "/dashboard", "/dashboard.html"):
            user = self._current_user()
            if _MULTI_USER_MODE and not user:
                self._send(200, _login_page_html().encode("utf-8"), "text/html; charset=utf-8")
                return
            app_dir = self._active_app_dir(user)
            db_path = app_dir / "applypilot.db"
            # Serve a cached HTML file. Regeneration is triggered by DB writes.
            html_path = _ensure_dashboard(app_dir=app_dir, db_path=db_path)
            with open(html_path, "rb") as f:
                body = f.read()
            if _MULTI_USER_MODE and user:
                try:
                    text = body.decode("utf-8", errors="replace")
                    text = _inject_multi_user_ui(text, user)
                    body = text.encode("utf-8")
                except Exception:
                    pass
            self._send(200, body, "text/html; charset=utf-8")
            return

        user = self._current_user()
        if _MULTI_USER_MODE and path.startswith("/api/") and not path.startswith("/api/auth/") and not user:
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return

        app_dir = self._active_app_dir(user)
        db_path = app_dir / "applypilot.db"
        runner = _runner_for_user(user)

        if path == "/api/setup/status":
            try:
                from applypilot.setup_workspace import get_setup_status

                self._send_json(200, {"ok": True, "status": get_setup_status(app_dir)})
            except Exception as e:
                self._send_json(200, {"ok": True, "status": {"app_dir": str(app_dir)}, "error": str(e)})
            return

        if path == "/api/setup/read":
            try:
                from applypilot.setup_workspace import (
                    read_profile,
                    list_resume_variants,
                    read_searches_dict,
                    read_searches_yaml,
                    read_text,
                    resume_txt_path,
                )

                profile = read_profile(app_dir)
                searches_text, searches_trunc = read_searches_yaml(app_dir)
                searches = read_searches_dict(app_dir)
                resume_text, resume_trunc = read_text(resume_txt_path(app_dir))
                variants = list_resume_variants(app_dir)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "profile": profile,
                        "searches": searches,
                        "searches_text": searches_text,
                        "searches_truncated": bool(searches_trunc),
                        "resume_text": resume_text,
                        "resume_truncated": bool(resume_trunc),
                        "resume_variants": variants,
                    },
                )
            except Exception as e:
                self._send_json(400, {"ok": False, "error": "read_failed", "detail": str(e)})
            return

        if path == "/api/setup/resume-variant":
            qs = parse_qs(parsed.query or "")
            key = str((qs.get("key") or [""])[0] or "").strip().lower()
            if not key:
                self._send_json(400, {"ok": False, "error": "missing_key"})
                return
            try:
                from applypilot.setup_workspace import read_resume_variant

                text, trunc = read_resume_variant(app_dir, key)
                self._send_json(200, {"ok": True, "key": key, "text": text, "truncated": bool(trunc)})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": "read_failed", "detail": str(e)})
            return

        if path == "/api/job/full-description":
            qs = parse_qs(parsed.query or "")
            try:
                job_id = int((qs.get("job_id") or ["0"])[0])
            except Exception:
                job_id = 0
            if job_id <= 0:
                self._send_json(400, {"ok": False, "error": "bad_request", "detail": "missing job_id"})
                return
            try:
                conn = get_connection(db_path)
                row = conn.execute("SELECT full_description FROM jobs WHERE rowid = ?", (int(job_id),)).fetchone()
                if not row:
                    self._send_json(404, {"ok": False, "error": "not_found"})
                    return
                text = str(row[0] or "")
                # Keep payload bounded.
                if len(text) > 60000:
                    text = text[:60000]
                self._send_json(200, {"ok": True, "job_id": int(job_id), "text": text})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": "read_failed", "detail": str(e)})
            return

        if path == "/api/setup/resume-variants":
            try:
                from applypilot.setup_workspace import list_resume_variants

                self._send_json(200, {"ok": True, "variants": list_resume_variants(app_dir)})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": "read_failed", "detail": str(e)})
            return

        if path == "/api/pipeline/status":
            self._send_json(200, {"ok": True, "status": runner.status()})
            return

        if path == "/api/pipeline/history":
            qs = parse_qs(parsed.query or "")
            try:
                limit = int((qs.get("limit") or [str(_RUN_HISTORY_MAX)])[0])
            except Exception:
                limit = _RUN_HISTORY_MAX
            self._send_json(200, {"ok": True, "runs": runner.history(limit=limit)})
            return

        if path == "/api/pipeline/select":
            qs = parse_qs(parsed.query or "")
            rid = (qs.get("run_id") or [""])[0]
            ok, info = runner.select_run(rid)
            if not ok:
                if info.get("error") == "not_found":
                    self._send_json(404, {"ok": False, **info})
                elif info.get("error") == "busy":
                    self._send_json(409, {"ok": False, **info})
                else:
                    self._send_json(400, {"ok": False, **info})
                return
            self._send_json(200, {"ok": True, **info})
            return

        if path == "/api/pipeline/logs":
            qs = parse_qs(parsed.query or "")
            try:
                since = int((qs.get("since") or ["0"])[0])
            except Exception:
                since = 0
            try:
                limit = int((qs.get("limit") or ["250"])[0])
            except Exception:
                limit = 250
            limit = max(10, min(1000, limit))
            self._send_json(200, {"ok": True, "logs": runner.logs(since=since, limit=limit)})
            return

        self._send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = (self.path or "").split("?", 1)[0]
        data = self._read_json()

        if _MULTI_USER_MODE and path.startswith("/api/") and not self._origin_allowed():
            self._send_json(403, {"ok": False, "error": "invalid_origin"})
            return

        if path == "/api/auth/register" and _MULTI_USER_MODE:
            try:
                username = str(data.get("username") or "").strip()
                email = str(data.get("email") or "").strip()
                password = str(data.get("password") or "")
                full_name = str(data.get("full_name") or "").strip()
                phone = str(data.get("phone") or "").strip()
                city = str(data.get("city") or "").strip()
                country = str(data.get("country") or "").strip()
                user = create_user(
                    username=username,
                    email=email,
                    password=password,
                    full_name=full_name,
                    phone=phone,
                    city=city,
                    country=country,
                )
                app_dir = ensure_workspace_for_user(str(user.get("username") or ""))
                try:
                    from applypilot.setup_workspace import write_profile

                    write_profile(
                        app_dir,
                        {
                            "account": {
                                "username": username,
                            },
                            "personal": {
                                "full_name": full_name,
                                "email": email,
                                "phone": phone,
                                "city": city,
                                "country": country,
                            },
                        },
                    )
                except Exception:
                    pass
                token = create_session(int(user["id"]))
                csrf = secrets.token_urlsafe(24)
                self._send_json(
                    200,
                    {"ok": True, "user": user},
                    headers={"Set-Cookie": [_session_cookie(token), _csrf_cookie(csrf)]},
                )
            except ValueError as e:
                self._send_json(400, {"ok": False, "error": "invalid_request", "detail": str(e)})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": "register_failed", "detail": str(e)})
            return

        if path == "/api/auth/login" and _MULTI_USER_MODE:
            login = str(data.get("login") or "").strip()
            password = str(data.get("password") or "")
            client_ip = str((self.client_address or ("", 0))[0] or "")
            allowed, wait_s = _login_allowed(client_ip, login)
            if not allowed:
                self._send_json(
                    429,
                    {
                        "ok": False,
                        "error": "rate_limited",
                        "detail": f"Too many attempts. Try again in {max(1, wait_s)}s.",
                    },
                )
                return
            user = authenticate(login, password)
            if not user:
                _login_record_failure(client_ip, login)
                self._send_json(401, {"ok": False, "error": "invalid_credentials"})
                return
            _login_record_success(client_ip, login)
            ensure_workspace_for_user(str(user.get("username") or ""))
            token = create_session(int(user["id"]))
            csrf = secrets.token_urlsafe(24)
            self._send_json(
                200,
                {"ok": True, "user": user},
                headers={"Set-Cookie": [_session_cookie(token), _csrf_cookie(csrf)]},
            )
            return

        if path == "/api/auth/logout" and _MULTI_USER_MODE:
            token = _cookie_value(self.headers.get("Cookie", ""), "ap_session")
            if token:
                revoke_session(token)
            self._send_json(
                200,
                {"ok": True},
                headers={"Set-Cookie": [_session_cookie_clear(), _csrf_cookie_clear()]},
            )
            return

        if (
            path
            in (
                "/api/admin/users/disable",
                "/api/admin/users/enable",
                "/api/admin/users/delete",
                "/api/admin/users/reset-password",
                "/api/admin/users/promote",
                "/api/admin/users/demote",
            )
            and _MULTI_USER_MODE
        ):
            user = self._current_user()
            if not self._is_admin(user):
                self._send_json(403, {"ok": False, "error": "admin_only"})
                return
            try:
                target_id = int(data.get("user_id") or 0)
            except Exception:
                target_id = 0
            if target_id <= 0:
                self._send_json(400, {"ok": False, "error": "bad_request"})
                return
            current_id = int((user or {}).get("id") or 0)
            if (
                path in ("/api/admin/users/disable", "/api/admin/users/delete")
                and current_id
                and target_id == current_id
            ):
                self._send_json(
                    400, {"ok": False, "error": "invalid_request", "detail": "cannot modify your own admin account"}
                )
                return

            try:
                if path == "/api/admin/users/disable":
                    out = set_user_active(target_id, is_active=False, actor_user_id=current_id)
                    self._send_json(200, {"ok": True, "user": out})
                    return
                if path == "/api/admin/users/enable":
                    out = set_user_active(target_id, is_active=True, actor_user_id=current_id)
                    self._send_json(200, {"ok": True, "user": out})
                    return
                if path == "/api/admin/users/delete":
                    purge = bool(data.get("purge_workspace", True))
                    out = delete_user(target_id, purge_workspace=purge, actor_user_id=current_id)
                    self._send_json(200, {"ok": True, "deleted": out})
                    return
                if path == "/api/admin/users/promote":
                    out = set_user_admin(target_id, is_admin=True, actor_user_id=current_id)
                    self._send_json(200, {"ok": True, "user": out})
                    return
                if path == "/api/admin/users/demote":
                    out = set_user_admin(target_id, is_admin=False, actor_user_id=current_id)
                    self._send_json(200, {"ok": True, "user": out})
                    return

                # reset-password
                new_password = str(data.get("new_password") or "")
                out = reset_user_password(target_id, new_password, actor_user_id=current_id)
                self._send_json(200, {"ok": True, "user": out})
                return
            except ValueError as e:
                self._send_json(400, {"ok": False, "error": "invalid_request", "detail": str(e)})
                return
            except Exception as e:
                self._send_json(400, {"ok": False, "error": "admin_action_failed", "detail": str(e)})
                return

        user = self._current_user()
        if _MULTI_USER_MODE and path.startswith("/api/") and not path.startswith("/api/auth/") and not user:
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        if _MULTI_USER_MODE and path.startswith("/api/") and not path.startswith("/api/auth/"):
            if not self._csrf_ok():
                self._send_json(403, {"ok": False, "error": "csrf_failed"})
                return

        app_dir = self._active_app_dir(user)
        db_path = app_dir / "applypilot.db"
        runner = _runner_for_user(user)

        if path == "/api/setup/profile":
            try:
                from applypilot.setup_workspace import write_profile

                patch = data.get("profile") if isinstance(data, dict) else None
                if not isinstance(patch, dict):
                    self._send_json(400, {"ok": False, "error": "bad_request"})
                    return
                merged = write_profile(app_dir, patch)
                _regen_dashboard_async(app_dir=app_dir, db_path=db_path)
                self._send_json(200, {"ok": True, "profile": merged})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": "write_failed", "detail": str(e)})
            return

        if path == "/api/setup/resume-text":
            try:
                from applypilot.setup_workspace import write_resume_text

                text = data.get("text") if isinstance(data, dict) else None
                if not isinstance(text, str):
                    self._send_json(400, {"ok": False, "error": "bad_request"})
                    return
                write_resume_text(app_dir, text)
                _regen_dashboard_async(app_dir=app_dir, db_path=db_path)
                self._send_json(200, {"ok": True})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": "write_failed", "detail": str(e)})
            return

        if path == "/api/setup/resume-variant":
            try:
                from applypilot.setup_workspace import write_resume_variant

                key = str(data.get("key") or "").strip().lower() if isinstance(data, dict) else ""
                text = data.get("text") if isinstance(data, dict) else None
                if not key:
                    self._send_json(400, {"ok": False, "error": "missing_key"})
                    return
                if not isinstance(text, str):
                    self._send_json(400, {"ok": False, "error": "bad_request"})
                    return

                write_resume_variant(app_dir, key, text)
                _regen_dashboard_async(app_dir=app_dir, db_path=db_path)
                self._send_json(200, {"ok": True})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": "write_failed", "detail": str(e)})
            return

        if path == "/api/setup/resume-pdf":
            try:
                from applypilot.setup_workspace import write_resume_pdf

                b64 = data.get("b64") if isinstance(data, dict) else None
                if not isinstance(b64, str) or not b64.strip():
                    self._send_json(400, {"ok": False, "error": "bad_request"})
                    return
                raw = base64.b64decode(b64.encode("ascii"), validate=False)
                write_resume_pdf(app_dir, raw)
                _regen_dashboard_async(app_dir=app_dir, db_path=db_path)
                self._send_json(200, {"ok": True})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": "write_failed", "detail": str(e)})
            return

        if path == "/api/setup/searches":
            try:
                from applypilot.setup_workspace import write_searches_yaml

                text = data.get("text") if isinstance(data, dict) else None
                if not isinstance(text, str):
                    self._send_json(400, {"ok": False, "error": "bad_request"})
                    return
                write_searches_yaml(app_dir, text)
                _regen_dashboard_async(app_dir=app_dir, db_path=db_path)
                self._send_json(200, {"ok": True})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": "write_failed", "detail": str(e)})
            return

        if path == "/api/statement/generate":
            try:
                from applypilot.config import check_tier
                from applypilot.scoring.supporting_statement import generate_supporting_statement
                from applypilot.setup_workspace import read_profile

                check_tier(2, "supporting statement generation")

                resume_text = data.get("resume_text") if isinstance(data, dict) else None
                job_text = data.get("job_text") if isinstance(data, dict) else None
                title = str(data.get("title") or "").strip() if isinstance(data, dict) else ""
                org = str(data.get("org") or "").strip() if isinstance(data, dict) else ""
                max_words_raw = data.get("max_words") if isinstance(data, dict) else None
                try:
                    max_words = int(str(max_words_raw).strip()) if max_words_raw is not None else 1500
                except Exception:
                    max_words = 1500
                max_words = max(200, min(5000, int(max_words or 1500)))

                if not isinstance(resume_text, str) or not resume_text.strip():
                    self._send_json(400, {"ok": False, "error": "missing_resume"})
                    return
                if not isinstance(job_text, str) or not job_text.strip():
                    self._send_json(400, {"ok": False, "error": "missing_job_text"})
                    return

                profile = read_profile(app_dir)
                if not isinstance(profile, dict) or not profile:
                    profile = {}

                job = {
                    "title": title or "Supporting Statement",
                    "company": org or None,
                    "site": org or None,
                    "full_description": job_text,
                }

                statement = generate_supporting_statement(resume_text, job, profile)

                # If statement is over the requested word limit, do a deterministic tighten pass.
                def _word_count(t: str) -> int:
                    return len(re.findall(r"[A-Za-z0-9%]+", (t or "").strip()))

                wc = _word_count(statement)
                if wc > max_words:
                    from applypilot.llm import chat_json

                    sys = (
                        "You rewrite UK supporting statements. Keep the meaning, keep it truthful, "
                        "remove repetition, and ensure it fits the word limit. "
                        "Do not add new facts not present in the original statement or job text."
                    )
                    user_msg = (
                        f"WORD LIMIT: {max_words}\n\n"
                        f"JOB TEXT:\n{job_text[:8000]}\n\n"
                        f"ORIGINAL STATEMENT:\n{statement}\n\n"
                        'Return JSON only: {"statement":"..."}'
                    )
                    out = chat_json(
                        [{"role": "system", "content": sys}, {"role": "user", "content": user_msg}],
                        max_tokens=1800,
                        temperature=0.0,
                    )
                    try:
                        obj = json.loads((out or "").strip())
                        if isinstance(obj, dict) and str(obj.get("statement") or "").strip():
                            statement2 = str(obj.get("statement") or "").strip()
                            statement = statement2
                            wc = _word_count(statement)
                    except Exception:
                        pass

                self._send_json(
                    200, {"ok": True, "statement": statement, "word_count": int(wc), "max_words": int(max_words)}
                )
            except SystemExit as e:
                self._send_json(409, {"ok": False, "error": "tier_blocked", "detail": str(e)})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": "generate_failed", "detail": str(e)})
            return

        if path == "/api/jobs/mark":
            job_id = int(data.get("id") or 0)
            status = str(data.get("status") or "").strip().lower()
            reason = str(data.get("reason") or "").strip() or None

            if job_id <= 0 or status not in ("applied", "failed"):
                self._send_json(400, {"ok": False, "error": "bad_request"})
                return

            updated = _mark_job_by_id(db_path, job_id, status, reason=reason)
            if updated <= 0:
                self._send_json(404, {"ok": False, "error": "not_found"})
                return

            _regen_dashboard_async(app_dir=app_dir, db_path=db_path)

            self._send_json(200, {"ok": True, "updated": updated})
            return

        if path == "/api/jobs/select":
            job_id = int(data.get("id") or 0)
            sv = data.get("selected")
            if isinstance(sv, bool):
                selected = sv
            else:
                selected = str(sv or "").strip().lower() in ("1", "true", "yes", "on")
            xv = data.get("exclusive")
            if isinstance(xv, bool):
                exclusive = xv
            else:
                exclusive = str(xv or "").strip().lower() in ("1", "true", "yes", "on")
            if job_id <= 0:
                self._send_json(400, {"ok": False, "error": "bad_request"})
                return
            conn = get_connection(db_path)
            conn.execute("BEGIN IMMEDIATE")
            try:
                cleared = 0
                if selected:
                    if exclusive:
                        cleared_cur = conn.execute(
                            """
                            UPDATE jobs
                               SET apply_status = NULL,
                                   apply_error = NULL,
                                   agent_id = NULL
                             WHERE rowid != ?
                               AND apply_status = 'selected'
                            """,
                            (job_id,),
                        )
                        cleared = int(cleared_cur.rowcount or 0)
                    cur = conn.execute(
                        """
                        UPDATE jobs
                           SET apply_status = 'selected',
                               apply_error = NULL,
                               agent_id = NULL
                         WHERE rowid = ?
                           AND COALESCE(apply_status, '') NOT IN ('applied','in_progress','blocked')
                        """,
                        (job_id,),
                    )
                else:
                    cur = conn.execute(
                        """
                        UPDATE jobs
                           SET apply_status = NULL,
                               apply_error = NULL,
                               agent_id = NULL
                         WHERE rowid = ?
                           AND apply_status = 'selected'
                        """,
                        (job_id,),
                    )
                conn.commit()
                updated = int(cur.rowcount or 0)
            except Exception:
                conn.rollback()
                raise
            if updated <= 0:
                self._send_json(404, {"ok": False, "error": "not_found"})
                return
            _regen_dashboard_async(app_dir=app_dir, db_path=db_path)
            self._send_json(
                200,
                {"ok": True, "updated": updated, "selected": selected, "exclusive": exclusive, "cleared": cleared},
            )
            return

        if path == "/api/jobs/delete":
            job_id = int(data.get("id") or 0)
            if job_id <= 0:
                self._send_json(400, {"ok": False, "error": "bad_request"})
                return
            deleted = _delete_jobs_by_ids(db_path, [job_id])
            if deleted <= 0:
                self._send_json(404, {"ok": False, "error": "not_found"})
                return
            _regen_dashboard_async(app_dir=app_dir, db_path=db_path)
            self._send_json(200, {"ok": True, "deleted": deleted})
            return

        if path == "/api/jobs/delete-bulk":
            ids_raw = data.get("ids") if isinstance(data, dict) else None
            if not isinstance(ids_raw, list):
                self._send_json(400, {"ok": False, "error": "bad_request"})
                return
            ids: list[int] = []
            for x in ids_raw:
                try:
                    v = int(x)
                except Exception:
                    continue
                if v > 0:
                    ids.append(v)
            deleted = _delete_jobs_by_ids(db_path, ids)
            _regen_dashboard_async(app_dir=app_dir, db_path=db_path)
            self._send_json(200, {"ok": True, "deleted": int(deleted)})
            return

        if path == "/api/jobs/block":
            job_id = int(data.get("id") or 0)
            if job_id <= 0:
                self._send_json(400, {"ok": False, "error": "bad_request"})
                return

            updated = _block_job_by_id(db_path, job_id, reason="user_deleted")
            if updated <= 0:
                self._send_json(404, {"ok": False, "error": "not_found"})
                return

            _regen_dashboard_async(app_dir=app_dir, db_path=db_path)
            self._send_json(200, {"ok": True, "updated": updated})
            return

        if path == "/api/pipeline/run":
            payload = dict(data) if isinstance(data, dict) else {}
            ok, info = runner.start(payload)
            if not ok:
                err = info.get("error")
                if err == "already_running":
                    self._send_json(409, {"ok": False, **info})
                else:
                    self._send_json(400, {"ok": False, **info})
                return
            # IMPORTANT: send response before any expensive work.
            self._send_json(200, {"ok": True, **info})
            return

        if path == "/api/pipeline/stop":
            ok, info = runner.stop()
            if not ok:
                self._send_json(400, {"ok": False, **info})
                return
            self._send_json(200, {"ok": True, **info})
            return

        if path == "/api/score/repair":
            st = runner.status()
            if st.get("running") or st.get("starting"):
                self._send_json(409, {"ok": False, "error": "pipeline_running"})
                return
            try:
                from applypilot.scoring.scorer import run_score_repair

                result = run_score_repair()
                _regen_dashboard_async(app_dir=app_dir, db_path=db_path)
                self._send_json(200, {"ok": True, "result": result})
            except sqlite3.OperationalError as e:
                self._send_json(409, {"ok": False, "error": "db_locked", "detail": str(e)})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": "score_repair_failed", "detail": str(e)})
            return

        self._send_json(404, {"ok": False, "error": "not_found"})


def serve_dashboard(host: str = "127.0.0.1", port: int = 8765, *, multi_user: bool = False) -> str:
    """Start the dashboard server (blocking). Returns the URL."""
    global _MULTI_USER_MODE
    _MULTI_USER_MODE = bool(multi_user)

    if _MULTI_USER_MODE:
        init_auth_db()

    # Ensure we can replay console output after restart.
    _PIPELINE._hydrate_from_history()
    # Force-regenerate HTML at startup so UI changes are picked up.
    if not _MULTI_USER_MODE:
        _ensure_dashboard(force=True, app_dir=APP_DIR, db_path=APP_DIR / "applypilot.db")
    httpd = ThreadingHTTPServer((host, int(port)), _Handler)
    url = f"http://{host}:{int(port)}/"
    try:
        httpd.serve_forever()
    finally:
        try:
            httpd.server_close()
        except Exception:
            pass
    return url
