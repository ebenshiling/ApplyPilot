import json
import threading
import time
from http.cookiejar import CookieJar
from pathlib import Path

import pytest


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


def _opener_with_cookies():
    import urllib.request

    jar = CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    setattr(op, "_cookiejar", jar)
    return op


def _csrf_from_opener(opener) -> str:
    jar = getattr(opener, "_cookiejar", None)
    if jar is None:
        return ""
    try:
        for c in jar:
            if str(getattr(c, "name", "")) == "ap_csrf":
                return str(getattr(c, "value", "") or "")
    except Exception:
        return ""
    return ""


def _http_json(opener, method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if method.upper() == "POST":
        csrf = _csrf_from_opener(opener)
        if csrf:
            headers["X-CSRF-Token"] = csrf
    req = urllib.request.Request(url, data=body, method=method.upper(), headers=headers)
    try:
        with opener.open(req, timeout=3) as r:
            raw = r.read()
            return r.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            data = {"detail": raw.decode("utf-8", errors="replace")}
        return int(e.code), data


def _http_text(opener, method: str, url: str) -> tuple[int, str]:
    import urllib.request

    req = urllib.request.Request(url, method=method.upper())
    with opener.open(req, timeout=3) as r:
        raw = r.read()
        return int(r.status), raw.decode("utf-8", errors="replace")


def test_multi_user_registration_isolates_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path / "default"))
    monkeypatch.setenv("APPLYPILOT_MULTI_ROOT", str(tmp_path / "multi"))

    import importlib
    import applypilot.config as cfg
    from applypilot.database import close_connection, get_connection, init_db

    importlib.reload(cfg)

    import applypilot.view as view
    import applypilot.dashboard_server as ds

    importlib.reload(view)
    importlib.reload(ds)

    # Enable multi-user mode for this test server instance.
    ds._MULTI_USER_MODE = True
    ds.init_auth_db()

    # Start ephemeral server.
    httpd = ds.ThreadingHTTPServer(("127.0.0.1", 0), ds._Handler)
    port = int(httpd.server_address[1])
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    t.start()
    _wait_ok(f"http://127.0.0.1:{port}/health")

    base = f"http://127.0.0.1:{port}"
    op1 = _opener_with_cookies()
    op2 = _opener_with_cookies()

    try:
        # Unauthenticated root serves login/register UI.
        st, me0 = _http_json(op1, "GET", f"{base}/api/auth/me")
        assert st == 200
        assert me0.get("multi_user") is True
        assert me0.get("authenticated") is False

        # Register user alice and write her profile.
        st, reg1 = _http_json(
            op1,
            "POST",
            f"{base}/api/auth/register",
            {
                "username": "alice",
                "full_name": "Alice One",
                "email": "alice@example.com",
                "password": "password123",
                "city": "London",
                "country": "UK",
            },
        )
        assert st == 200
        assert reg1.get("ok") is True

        st, _ = _http_json(
            op1,
            "POST",
            f"{base}/api/setup/profile",
            {"profile": {"personal": {"full_name": "Alice One", "email": "alice@example.com"}}},
        )
        assert st == 200

        # Register user bob in separate cookie session and write his profile.
        st, reg2 = _http_json(
            op2,
            "POST",
            f"{base}/api/auth/register",
            {
                "username": "bob",
                "full_name": "Bob Two",
                "email": "bob@example.com",
                "password": "password123",
                "city": "Manchester",
                "country": "UK",
            },
        )
        assert st == 200
        assert reg2.get("ok") is True

        st, _ = _http_json(
            op2,
            "POST",
            f"{base}/api/setup/profile",
            {"profile": {"personal": {"full_name": "Bob Two", "email": "bob@example.com"}}},
        )
        assert st == 200

        # Each account writes into its own workspace.
        alice_profile = tmp_path / "multi" / "workspaces" / "alice" / "profile.json"
        bob_profile = tmp_path / "multi" / "workspaces" / "bob" / "profile.json"
        assert alice_profile.exists()
        assert bob_profile.exists()
        a = json.loads(alice_profile.read_text(encoding="utf-8"))
        b = json.loads(bob_profile.read_text(encoding="utf-8"))
        assert (a.get("personal") or {}).get("full_name") == "Alice One"
        assert (b.get("personal") or {}).get("full_name") == "Bob Two"

        # Auth-scoped status endpoint returns each user's own app_dir.
        st, s1 = _http_json(op1, "GET", f"{base}/api/setup/status")
        st2, s2 = _http_json(op2, "GET", f"{base}/api/setup/status")
        assert st == 200 and st2 == 200
        app1 = str(s1.get("status", {}).get("app_dir", "")).replace("\\", "/")
        app2 = str(s2.get("status", {}).get("app_dir", "")).replace("\\", "/")
        assert app1.endswith("workspaces/alice")
        assert app2.endswith("workspaces/bob")
    finally:
        for p in [
            tmp_path / "default" / "applypilot.db",
            tmp_path / "multi" / "workspaces" / "alice" / "applypilot.db",
            tmp_path / "multi" / "workspaces" / "bob" / "applypilot.db",
        ]:
            try:
                close_connection(p)
            except Exception:
                pass
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass


def test_admin_user_management_disable_delete_reset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path / "default"))
    monkeypatch.setenv("APPLYPILOT_MULTI_ROOT", str(tmp_path / "multi"))

    import importlib
    import applypilot.config as cfg
    from applypilot.database import close_connection, init_db

    importlib.reload(cfg)

    import applypilot.view as view
    import applypilot.dashboard_server as ds

    importlib.reload(view)
    importlib.reload(ds)

    ds._MULTI_USER_MODE = True
    ds.init_auth_db()

    httpd = ds.ThreadingHTTPServer(("127.0.0.1", 0), ds._Handler)
    port = int(httpd.server_address[1])
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    t.start()
    _wait_ok(f"http://127.0.0.1:{port}/health")

    base = f"http://127.0.0.1:{port}"
    admin = _opener_with_cookies()
    bob = _opener_with_cookies()

    try:
        st, reg_admin = _http_json(
            admin,
            "POST",
            f"{base}/api/auth/register",
            {
                "username": "admin1",
                "full_name": "Admin One",
                "email": "admin1@example.com",
                "password": "password123",
            },
        )
        assert st == 200
        assert reg_admin.get("ok") is True
        assert (reg_admin.get("user") or {}).get("is_admin") is True

        st, reg_bob = _http_json(
            bob,
            "POST",
            f"{base}/api/auth/register",
            {
                "username": "bob1",
                "full_name": "Bob One",
                "email": "bob1@example.com",
                "password": "password123",
            },
        )
        assert st == 200
        assert reg_bob.get("ok") is True
        assert (reg_bob.get("user") or {}).get("is_admin") is False
        bob_id = int((reg_bob.get("user") or {}).get("id") or 0)
        assert bob_id > 0

        # Non-admin cannot access admin endpoints.
        st, denied = _http_json(bob, "GET", f"{base}/api/admin/users")
        assert st == 403
        assert denied.get("error") == "admin_only"

        st, users = _http_json(admin, "GET", f"{base}/api/admin/users")
        assert st == 200
        assert users.get("ok") is True
        assert len(users.get("users") or []) >= 2

        # Bulk delete endpoint removes jobs permanently.
        admin_db = tmp_path / "multi" / "workspaces" / "admin1" / "applypilot.db"
        conn = init_db(admin_db)
        conn.execute(
            "INSERT INTO jobs (url, title, search_query, site, strategy, discovered_at, fit_score) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "https://example.com/job/a",
                "Data Analyst",
                "Data Analyst",
                "indeed",
                "jobspy",
                "2026-01-01T00:00:00Z",
                8,
            ),
        )
        conn.execute(
            "INSERT INTO jobs (url, title, search_query, site, strategy, discovered_at, fit_score) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "https://example.com/job/b",
                "IT Support Engineer",
                "IT Support Engineer",
                "linkedin",
                "jobspy",
                "2026-01-01T00:00:00Z",
                8,
            ),
        )
        conn.commit()
        ids = [
            int(r[0])
            for r in conn.execute("SELECT rowid FROM jobs WHERE url LIKE 'https://example.com/job/%'").fetchall()
        ]
        assert len(ids) == 2

        st, del_out = _http_json(admin, "POST", f"{base}/api/jobs/delete-bulk", {"ids": ids})
        assert st == 200
        assert int(del_out.get("deleted") or 0) == 2
        rem = int(
            conn.execute("SELECT COUNT(*) FROM jobs WHERE url LIKE 'https://example.com/job/%'").fetchone()[0] or 0
        )
        assert rem == 0

        # Promote bob to admin, verify access, then demote.
        st, out = _http_json(admin, "POST", f"{base}/api/admin/users/promote", {"user_id": bob_id})
        assert st == 200
        assert (out.get("user") or {}).get("is_admin") is True

        st, bob_admin_ok = _http_json(bob, "GET", f"{base}/api/admin/users")
        assert st == 200
        assert bob_admin_ok.get("ok") is True

        st, out = _http_json(admin, "POST", f"{base}/api/admin/users/demote", {"user_id": bob_id})
        assert st == 200
        assert (out.get("user") or {}).get("is_admin") is False

        st, bob_denied_again = _http_json(bob, "GET", f"{base}/api/admin/users")
        assert st == 403
        assert bob_denied_again.get("error") == "admin_only"

        # Cannot demote the last active admin.
        admin_id = int((reg_admin.get("user") or {}).get("id") or 0)
        st, last_admin_demote = _http_json(admin, "POST", f"{base}/api/admin/users/demote", {"user_id": admin_id})
        assert st == 400

        # Disable bob; his existing session should be revoked.
        st, out = _http_json(admin, "POST", f"{base}/api/admin/users/disable", {"user_id": bob_id})
        assert st == 200
        assert (out.get("user") or {}).get("is_active") is False

        st, unauth = _http_json(bob, "GET", f"{base}/api/setup/status")
        assert st == 401
        assert unauth.get("error") == "unauthorized"

        # Re-enable and reset password.
        st, out = _http_json(admin, "POST", f"{base}/api/admin/users/enable", {"user_id": bob_id})
        assert st == 200
        assert (out.get("user") or {}).get("is_active") is True

        st, out = _http_json(
            admin,
            "POST",
            f"{base}/api/admin/users/reset-password",
            {"user_id": bob_id, "new_password": "newpass456"},
        )
        assert st == 200

        # Old password fails, new password works.
        bob_login = _opener_with_cookies()
        st, bad = _http_json(
            bob_login,
            "POST",
            f"{base}/api/auth/login",
            {"login": "bob1", "password": "password123"},
        )
        assert st == 401
        assert bad.get("error") == "invalid_credentials"

        st, ok = _http_json(
            bob_login,
            "POST",
            f"{base}/api/auth/login",
            {"login": "bob1", "password": "newpass456"},
        )
        assert st == 200
        assert ok.get("ok") is True

        # Admin cannot disable or delete self.
        st, self_disable = _http_json(admin, "POST", f"{base}/api/admin/users/disable", {"user_id": admin_id})
        assert st == 400

        st, self_delete = _http_json(admin, "POST", f"{base}/api/admin/users/delete", {"user_id": admin_id})
        assert st == 400

        # Delete bob account.
        st, deleted = _http_json(admin, "POST", f"{base}/api/admin/users/delete", {"user_id": bob_id})
        assert st == 200
        assert (deleted.get("deleted") or {}).get("username") == "bob1"

        st, users2 = _http_json(admin, "GET", f"{base}/api/admin/users")
        assert st == 200
        usernames = [str(u.get("username") or "") for u in (users2.get("users") or [])]
        assert "admin1" in usernames
        assert "bob1" not in usernames

        st, audit = _http_json(admin, "GET", f"{base}/api/admin/audit?limit=200")
        assert st == 200
        actions = [str(a.get("action") or "") for a in (audit.get("audit") or [])]
        assert "user_promote_admin" in actions
        assert "user_demote_admin" in actions
        assert "user_reset_password" in actions
        assert "user_delete" in actions
    finally:
        for p in [
            tmp_path / "default" / "applypilot.db",
            tmp_path / "multi" / "workspaces" / "admin1" / "applypilot.db",
            tmp_path / "multi" / "workspaces" / "bob1" / "applypilot.db",
        ]:
            try:
                close_connection(p)
            except Exception:
                pass
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass


def test_dashboard_role_filter_ui_and_bulk_delete(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path / "default"))
    monkeypatch.setenv("APPLYPILOT_MULTI_ROOT", str(tmp_path / "multi"))

    import importlib
    import applypilot.config as cfg
    from applypilot.database import close_connection, init_db

    importlib.reload(cfg)

    import applypilot.view as view
    import applypilot.dashboard_server as ds

    importlib.reload(view)
    importlib.reload(ds)

    ds._MULTI_USER_MODE = True
    ds.init_auth_db()

    httpd = ds.ThreadingHTTPServer(("127.0.0.1", 0), ds._Handler)
    port = int(httpd.server_address[1])
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    t.start()
    _wait_ok(f"http://127.0.0.1:{port}/health")

    base = f"http://127.0.0.1:{port}"
    admin = _opener_with_cookies()

    try:
        st, login_html = _http_text(admin, "GET", f"{base}/")
        assert st == 200
        assert "Create account" in login_html
        assert "Sign in" in login_html

        st, reg = _http_json(
            admin,
            "POST",
            f"{base}/api/auth/register",
            {
                "username": "adminrole",
                "full_name": "Admin Role",
                "email": "adminrole@example.com",
                "password": "password123",
            },
        )
        assert st == 200
        assert reg.get("ok") is True
        assert (reg.get("user") or {}).get("is_admin") is True

        admin_db = tmp_path / "multi" / "workspaces" / "adminrole" / "applypilot.db"
        conn = init_db(admin_db)
        conn.execute(
            "INSERT INTO jobs (url, title, search_query, site, strategy, discovered_at, fit_score) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "https://example.com/job/role-1",
                "Data Analyst I",
                "Data Analyst",
                "indeed",
                "jobspy",
                "2026-01-01T00:00:00Z",
                8,
            ),
        )
        conn.execute(
            "INSERT INTO jobs (url, title, search_query, site, strategy, discovered_at, fit_score) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "https://example.com/job/role-2",
                "Data Analyst II",
                "Data Analyst",
                "linkedin",
                "jobspy",
                "2026-01-01T00:00:00Z",
                7,
            ),
        )
        conn.execute(
            "INSERT INTO jobs (url, title, search_query, site, strategy, discovered_at, fit_score) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "https://example.com/job/role-3",
                "IT Support Engineer",
                "IT Support",
                "glassdoor",
                "jobspy",
                "2026-01-01T00:00:00Z",
                8,
            ),
        )
        conn.commit()

        st, html = _http_text(admin, "GET", f"{base}/dashboard")
        assert st == 200
        assert "Role:</span>" in html
        assert "Delete shown" in html
        assert "Delete role" in html
        assert 'data-role="data analyst"' in html

        ids = [int(r[0]) for r in conn.execute("SELECT rowid FROM jobs WHERE search_query = 'Data Analyst'").fetchall()]
        assert len(ids) == 2

        st, out = _http_json(admin, "POST", f"{base}/api/jobs/delete-bulk", {"ids": ids})
        assert st == 200
        assert int(out.get("deleted") or 0) == 2

        rem = int(
            conn.execute("SELECT COUNT(*) FROM jobs WHERE url LIKE 'https://example.com/job/role-%'").fetchone()[0] or 0
        )
        assert rem == 1
    finally:
        for p in [
            tmp_path / "default" / "applypilot.db",
            tmp_path / "multi" / "workspaces" / "adminrole" / "applypilot.db",
        ]:
            try:
                close_connection(p)
            except Exception:
                pass
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass


def test_pipeline_run_keeps_smart_site_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path / "default"))
    monkeypatch.setenv("APPLYPILOT_MULTI_ROOT", str(tmp_path / "multi"))

    import importlib
    import applypilot.config as cfg
    from applypilot.database import close_connection

    importlib.reload(cfg)

    import applypilot.view as view
    import applypilot.dashboard_server as ds

    importlib.reload(view)
    importlib.reload(ds)

    ds._MULTI_USER_MODE = True
    ds.init_auth_db()

    httpd = ds.ThreadingHTTPServer(("127.0.0.1", 0), ds._Handler)
    port = int(httpd.server_address[1])
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    t.start()
    _wait_ok(f"http://127.0.0.1:{port}/health")

    base = f"http://127.0.0.1:{port}"
    admin = _opener_with_cookies()

    try:
        st, reg = _http_json(
            admin,
            "POST",
            f"{base}/api/auth/register",
            {
                "username": "adminsmart",
                "full_name": "Admin Smart",
                "email": "adminsmart@example.com",
                "password": "password123",
            },
        )
        assert st == 200
        assert reg.get("ok") is True

        searches_text = (
            "defaults:\n"
            '  location: "Remote"\n'
            "queries:\n"
            '  - query: "Data Analyst"\n'
            "    tier: 1\n"
            "smart_sites:\n"
            '  - "NHS Jobs"\n'
            '  - "Reed UK"\n'
        )
        st, out = _http_json(admin, "POST", f"{base}/api/setup/searches", {"text": searches_text})
        assert st == 200
        assert out.get("ok") is True

        st, read = _http_json(admin, "GET", f"{base}/api/setup/read")
        assert st == 200
        smart = (read.get("searches") or {}).get("smart_sites") or []
        assert "NHS Jobs" in smart
        assert "Reed UK" in smart

        st, run = _http_json(
            admin,
            "POST",
            f"{base}/api/pipeline/run",
            {
                "stages": ["discover"],
                "dry_run": True,
                "search_query": "Data Analyst",
                "jobspy_sites": "indeed",
                "smarte_sites": "NHS Jobs, Reed UK",
                "results_per_site": 20,
                "hours_old": 72,
                "discover_skip_jobspy": True,
                "discover_skip_workday": True,
                "discover_skip_smarte": True,
            },
        )
        assert st == 200
        assert run.get("ok") is True

        for _ in range(120):
            st, status = _http_json(admin, "GET", f"{base}/api/pipeline/status")
            assert st == 200
            running = bool((status.get("status") or {}).get("running"))
            if not running:
                break
            time.sleep(0.1)

        st, hist = _http_json(admin, "GET", f"{base}/api/pipeline/history?limit=1")
        assert st == 200
        assert hist.get("ok") is True
        runs = hist.get("runs") or []
        assert runs
        latest = runs[0]

        assert latest.get("search_query") == "Data Analyst"
        got_smart = latest.get("smarte_sites") or []
        assert "NHS Jobs" in got_smart
        assert "Reed UK" in got_smart

        p = Path(str(latest.get("searches_path") or ""))
        assert p.exists()
        txt = p.read_text(encoding="utf-8")
        assert "smart_sites:" in txt
        assert "NHS Jobs" in txt
        assert "Reed UK" in txt
    finally:
        for p in [
            tmp_path / "default" / "applypilot.db",
            tmp_path / "multi" / "workspaces" / "adminsmart" / "applypilot.db",
        ]:
            try:
                close_connection(p)
            except Exception:
                pass
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass


def test_auth_login_rate_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path / "default"))
    monkeypatch.setenv("APPLYPILOT_MULTI_ROOT", str(tmp_path / "multi"))

    import importlib
    import applypilot.config as cfg

    importlib.reload(cfg)

    import applypilot.view as view
    import applypilot.dashboard_server as ds

    importlib.reload(view)
    importlib.reload(ds)

    ds._MULTI_USER_MODE = True
    ds.init_auth_db()

    # Speed up limiter for test runtime.
    ds._LOGIN_WINDOW_SECONDS = 30
    ds._LOGIN_MAX_FAILURES_PER_WINDOW = 2
    ds._LOGIN_LOCKOUT_SECONDS = 1
    ds._LOGIN_FAILURES.clear()
    ds._LOGIN_LOCKED_UNTIL.clear()

    httpd = ds.ThreadingHTTPServer(("127.0.0.1", 0), ds._Handler)
    port = int(httpd.server_address[1])
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    t.start()
    _wait_ok(f"http://127.0.0.1:{port}/health")

    base = f"http://127.0.0.1:{port}"
    op = _opener_with_cookies()
    try:
        st, reg = _http_json(
            op,
            "POST",
            f"{base}/api/auth/register",
            {
                "username": "ratelimit",
                "full_name": "Rate Limit",
                "email": "ratelimit@example.com",
                "password": "password123",
            },
        )
        assert st == 200
        assert reg.get("ok") is True

        st, out = _http_json(op, "POST", f"{base}/api/auth/logout", {})
        assert st == 200
        assert out.get("ok") is True

        st, bad1 = _http_json(
            op,
            "POST",
            f"{base}/api/auth/login",
            {"login": "ratelimit", "password": "wrongpass"},
        )
        assert st == 401
        assert bad1.get("error") == "invalid_credentials"

        st, bad2 = _http_json(
            op,
            "POST",
            f"{base}/api/auth/login",
            {"login": "ratelimit", "password": "wrongpass"},
        )
        assert st == 401
        assert bad2.get("error") == "invalid_credentials"

        st, limited = _http_json(
            op,
            "POST",
            f"{base}/api/auth/login",
            {"login": "ratelimit", "password": "wrongpass"},
        )
        assert st == 429
        assert limited.get("error") == "rate_limited"

        time.sleep(1.1)
        st, ok = _http_json(
            op,
            "POST",
            f"{base}/api/auth/login",
            {"login": "ratelimit", "password": "password123"},
        )
        assert st == 200
        assert ok.get("ok") is True
    finally:
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass


def test_csrf_and_origin_guard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path / "default"))
    monkeypatch.setenv("APPLYPILOT_MULTI_ROOT", str(tmp_path / "multi"))

    import importlib
    import urllib.error
    import urllib.request
    import applypilot.config as cfg

    importlib.reload(cfg)

    import applypilot.view as view
    import applypilot.dashboard_server as ds

    importlib.reload(view)
    importlib.reload(ds)

    ds._MULTI_USER_MODE = True
    ds.init_auth_db()

    httpd = ds.ThreadingHTTPServer(("127.0.0.1", 0), ds._Handler)
    port = int(httpd.server_address[1])
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    t.start()
    _wait_ok(f"http://127.0.0.1:{port}/health")

    base = f"http://127.0.0.1:{port}"
    op = _opener_with_cookies()
    try:
        st, reg = _http_json(
            op,
            "POST",
            f"{base}/api/auth/register",
            {
                "username": "csrfuser",
                "full_name": "Csrf User",
                "email": "csrf@example.com",
                "password": "password123",
            },
        )
        assert st == 200
        assert reg.get("ok") is True

        # Missing X-CSRF-Token should be rejected for authenticated API writes.
        body = json.dumps({"profile": {"personal": {"full_name": "Csrf User", "email": "csrf@example.com"}}}).encode(
            "utf-8"
        )
        req = urllib.request.Request(
            f"{base}/api/setup/profile",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with op.open(req, timeout=3):
                raise AssertionError("expected csrf failure")
        except urllib.error.HTTPError as e:
            assert e.code == 403
            payload = json.loads((e.read() or b"{}").decode("utf-8") or "{}")
            assert payload.get("error") == "csrf_failed"

        # Wrong Origin must be rejected even if CSRF token is present.
        csrf = _csrf_from_opener(op)
        req2 = urllib.request.Request(
            f"{base}/api/setup/profile",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": csrf,
                "Origin": "http://evil.example",
            },
        )
        try:
            with op.open(req2, timeout=3):
                raise AssertionError("expected origin failure")
        except urllib.error.HTTPError as e:
            assert e.code == 403
            payload = json.loads((e.read() or b"{}").decode("utf-8") or "{}")
            assert payload.get("error") == "invalid_origin"

        # Valid call with CSRF should pass.
        st, ok = _http_json(
            op,
            "POST",
            f"{base}/api/setup/profile",
            {"profile": {"personal": {"full_name": "Csrf User", "email": "csrf@example.com"}}},
        )
        assert st == 200
        assert ok.get("ok") is True
    finally:
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass
