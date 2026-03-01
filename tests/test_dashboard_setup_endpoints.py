import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest


def _start_server(host: str) -> tuple[threading.Thread, int, Any]:
    """Start dashboard server on an ephemeral port.

    Returns (thread, port, httpd).
    """
    import applypilot.dashboard_server as ds

    # Mirror serve_dashboard() but allow port=0 and return the handle.
    ds._PIPELINE._hydrate_from_history()
    ds._ensure_dashboard(force=True)
    httpd = ds.ThreadingHTTPServer((host, 0), ds._Handler)
    port = int(httpd.server_address[1])
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    t.start()
    return t, port, httpd


def _wait_ok(url: str, *, timeout_s: float = 5.0) -> None:
    import urllib.request

    deadline = time.time() + timeout_s
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return
        except Exception as e:
            last_err = e
            time.sleep(0.05)
    raise RuntimeError(f"server not ready: {last_err}")


def _http_json(method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            raw = r.read()
            return r.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            data = {"detail": raw.decode("utf-8", errors="replace")}
        return int(e.code), data


def test_setup_endpoints_write_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Force server to use our temp app dir.
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))

    # applypilot.config is imported very early in this test suite; reload so
    # APP_DIR/DB_PATH reflect the env override.
    import importlib
    import applypilot.config as cfg

    importlib.reload(cfg)

    # Reload modules that capture APP_DIR at import time.
    import applypilot.view as view
    import applypilot.dashboard_server as ds

    importlib.reload(view)
    importlib.reload(ds)

    # Ensure DB exists so dashboard generation doesn't crash.
    from applypilot.database import close_connection, init_db

    db_path = cfg.DB_PATH
    init_db(db_path)

    host = "127.0.0.1"
    t, port, httpd = _start_server(host)
    _wait_ok(f"http://{host}:{port}/health")

    st, data = _http_json("GET", f"http://{host}:{port}/api/setup/status")
    assert st == 200
    assert data.get("ok") is True
    s = data.get("status") or {}
    assert s.get("app_dir") == str(tmp_path)

    # Read endpoint works
    st, data = _http_json("GET", f"http://{host}:{port}/api/setup/read")
    assert st == 200
    assert data.get("ok") is True
    assert isinstance(data.get("profile"), dict)
    assert isinstance(data.get("searches_text"), str)
    assert isinstance(data.get("resume_text"), str)

    # Write profile
    st, data = _http_json(
        "POST",
        f"http://{host}:{port}/api/setup/profile",
        {
            "profile": {
                "personal": {
                    "full_name": "A B",
                    "email": "a@example.com",
                    "city": "X",
                    "country": "Y",
                    "password": "should-not-persist",
                }
            }
        },
    )
    assert st == 200
    assert (tmp_path / "profile.json").exists()
    profile_saved = json.loads((tmp_path / "profile.json").read_text(encoding="utf-8"))
    assert "password" not in (profile_saved.get("personal") or {})

    # Write resume text
    st, data = _http_json(
        "POST",
        f"http://{host}:{port}/api/setup/resume-text",
        {"text": "Hello resume\nLine 2\n"},
    )
    assert st == 200
    assert (tmp_path / "resume.txt").exists()

    # Write searches
    st, data = _http_json(
        "POST",
        f"http://{host}:{port}/api/setup/searches",
        {"text": 'defaults:\n  location: "Remote"\nqueries:\n  - query: "X"\n    tier: 1\n'},
    )
    assert st == 200
    assert (tmp_path / "searches.yaml").exists()

    # Status reflects setup
    st, data = _http_json("GET", f"http://{host}:{port}/api/setup/status")
    assert st == 200
    s = data.get("status") or {}
    assert s.get("has_profile") is True
    assert s.get("has_resume_txt") is True
    assert s.get("has_searches") is True

    # Invalid profile patch should fail schema validation.
    st, data = _http_json(
        "POST",
        f"http://{host}:{port}/api/setup/profile",
        {"profile": {"not_a_real_field": {"x": 1}}},
    )
    assert st == 400
    assert data.get("error") == "write_failed"

    # Invalid searches.yaml should fail strict validation.
    st, data = _http_json(
        "POST",
        f"http://{host}:{port}/api/setup/searches",
        {"text": 'queries:\n  - query: "X"\n    tier: 1\nboards:\n  - monster\n'},
    )
    assert st == 400
    assert data.get("error") == "write_failed"

    # Cleanup thread-local sqlite handle
    close_connection(db_path)
    try:
        httpd.shutdown()
        httpd.server_close()
    except Exception:
        pass
