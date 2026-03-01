import importlib
import json
import os
import threading
import time
from http.cookiejar import CookieJar
from pathlib import Path

import pytest


def _wait_ok(url: str, *, timeout_s: float = 8.0) -> None:
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
        with opener.open(req, timeout=6) as r:
            raw = r.read()
            return int(r.status), json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            data = {"detail": raw.decode("utf-8", errors="replace")}
        return int(e.code), data


def test_dashboard_playwright_clickthrough(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    if str(os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") or "").strip() != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1 to run Playwright integration")

    pw = pytest.importorskip("playwright.sync_api")
    sync_playwright = pw.sync_playwright

    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path / "default"))
    monkeypatch.setenv("APPLYPILOT_MULTI_ROOT", str(tmp_path / "multi"))

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
    base = f"http://127.0.0.1:{port}"
    _wait_ok(f"{base}/health")

    admin_db = tmp_path / "multi" / "workspaces" / "adminui" / "applypilot.db"
    bob_db = tmp_path / "multi" / "workspaces" / "bobui" / "applypilot.db"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            page.goto(f"{base}/", wait_until="domcontentloaded")
            page.fill("#reg-full", "Admin UI")
            page.fill("#reg-user", "adminui")
            page.fill("#reg-email", "adminui@example.com")
            page.fill("#reg-pass", "password123")
            page.click('button:has-text("Create account")')
            page.wait_for_selector("#setup-panel", timeout=15000)
            page.wait_for_selector("#ap-logout-btn", timeout=10000)

            bob = _opener_with_cookies()
            st, reg_bob = _http_json(
                bob,
                "POST",
                f"{base}/api/auth/register",
                {
                    "username": "bobui",
                    "full_name": "Bob UI",
                    "email": "bobui@example.com",
                    "password": "password123",
                },
            )
            assert st == 200
            assert reg_bob.get("ok") is True
            bob_id = int((reg_bob.get("user") or {}).get("id") or 0)
            assert bob_id > 0

            conn = init_db(admin_db)
            conn.execute(
                "INSERT INTO jobs (url, title, search_query, site, strategy, discovered_at, fit_score) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "https://example.com/ui-job-1",
                    "Data Analyst One",
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
                    "https://example.com/ui-job-2",
                    "Data Analyst Two",
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
                    "https://example.com/ui-job-3",
                    "IT Support One",
                    "IT Support",
                    "glassdoor",
                    "jobspy",
                    "2026-01-01T00:00:00Z",
                    8,
                ),
            )
            conn.commit()

            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector('select:has(option[value="data analyst"])', timeout=10000)
            page.select_option('select:has(option[value="data analyst"])', "data analyst")
            page.once("dialog", lambda d: d.accept())
            page.click('button:has-text("Delete role")')
            page.wait_for_timeout(1200)
            assert page.locator(".job-card[data-id]").count() == 1

            summary = page.locator("summary", has_text="User Admin").first
            promote_btn = page.locator(f'button[data-id="{bob_id}"][data-act="promote"]:visible').first
            if promote_btn.count() == 0:
                summary.click()
                page.wait_for_timeout(200)

            promote_btn = page.locator(f'button[data-id="{bob_id}"][data-act="promote"]:visible').first
            promote_btn.click()
            page.wait_for_selector(f'button[data-id="{bob_id}"][data-act="demote"]:visible', timeout=10000)
            page.locator(f'button[data-id="{bob_id}"][data-act="demote"]:visible').first.click()
            page.wait_for_selector(f'button[data-id="{bob_id}"][data-act="promote"]:visible', timeout=10000)
            page.locator(f'button[data-id="{bob_id}"][data-act="disable"]:visible').first.click()
            page.wait_for_selector(f'button[data-id="{bob_id}"][data-act="enable"]:visible', timeout=10000)
            page.locator(f'button[data-id="{bob_id}"][data-act="enable"]:visible').first.click()
            page.wait_for_selector(f'button[data-id="{bob_id}"][data-act="disable"]:visible', timeout=10000)

            page.fill("#search-smart-sites", "NHS Jobs, Reed")
            page.click('button:has-text("Generate YAML")')
            page.wait_for_selector("#setup-searches", timeout=10000)
            yaml_text = page.input_value("#setup-searches")
            assert "smart_sites:" in yaml_text
            assert "NHS Jobs" in yaml_text
            assert "Reed" in yaml_text

            page.click('button:has-text("Save searches.yaml")')
            page.wait_for_timeout(600)
            setup_read = context.request.get(f"{base}/api/setup/read")
            assert setup_read.ok
            smart_sites = (setup_read.json().get("searches") or {}).get("smart_sites") or []
            assert "NHS Jobs" in smart_sites
            assert "Reed" in smart_sites

            page.check("#pipe-dry-run")
            page.check("#pipe-skip-jobspy")
            page.check("#pipe-skip-workday")
            page.check("#pipe-skip-smarte")
            page.fill("#pipe-search-query", "Data Analyst")
            page.fill("#pipe-jobspy-sites", "indeed")
            page.fill("#pipe-smarte-sites", "NHS Jobs, Reed")
            page.click("button[onclick*=\"pipelineRun(['discover'])\"]")

            latest = None
            for _ in range(100):
                hist = context.request.get(f"{base}/api/pipeline/history?limit=1")
                if hist.ok:
                    runs = hist.json().get("runs") or []
                    if runs:
                        latest = runs[0]
                        break
                time.sleep(0.1)

            assert latest is not None
            assert latest.get("search_query") == "Data Analyst"
            got_smart = latest.get("smarte_sites") or []
            assert "NHS Jobs" in got_smart
            assert "Reed" in got_smart

            browser.close()
    finally:
        for p in [
            tmp_path / "default" / "applypilot.db",
            admin_db,
            bob_db,
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
