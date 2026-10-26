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
            page.locator('.rail [data-view="settings"]').click()
            page.wait_for_selector("#native-profile-save")
            page.wait_for_selector("#logout")
            assert page.url.startswith(base + "/workspace")
            assert page.locator('a[href^="/dashboard"], #classic-frame').count() == 0

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
            page.locator('.rail [data-view="jobs"]').click()
            page.wait_for_selector('#job-search')
            page.fill('#job-search', 'Data Analyst')
            pw.expect(page.locator('.cards > .card')).to_have_count(2)
            page.locator('.card button[data-action="pick"]').first.click()
            pw.expect(page.locator('.card button[data-action="unpick"]')).to_have_count(1)

            page.locator('.rail [data-view="settings"]').click()
            summary = page.locator("summary", has_text="User Admin").first
            summary.click()
            promote_btn = page.locator(f'button[data-admin-id="{bob_id}"][data-admin-action="promote"]').first
            promote_btn.click()
            page.locator(f'button[data-admin-id="{bob_id}"][data-admin-action="demote"]').click()
            page.locator(f'button[data-admin-id="{bob_id}"][data-admin-action="promote"]').wait_for()
            page.locator(f'button[data-admin-id="{bob_id}"][data-admin-action="disable"]').click()
            page.locator(f'button[data-admin-id="{bob_id}"][data-admin-action="enable"]').click()
            page.locator(f'button[data-admin-id="{bob_id}"][data-admin-action="disable"]').wait_for()
            assert page.locator('#admin-audit article').count() >= 4

            page.fill('[data-profile-group="personal"][data-profile-key="city"]', 'Manchester')
            with page.expect_response(lambda r: r.url.endswith('/api/setup/profile') and r.request.method == 'POST') as saved:
                page.click('#native-profile-save')
            assert saved.value.ok
            pw.expect(page.locator('#save-state')).to_have_text('Saved')

            page.locator('.rail [data-view="pipeline"]').click()
            page.get_by_text('Searches & resume rules', exact=True).click()
            page.click('#native-add-query')
            page.locator('[data-query-row] input').last.fill('Data Analyst')
            with page.expect_response(lambda r: r.url.endswith('/pipeline/config/save') and r.request.method == 'POST') as saved_config:
                page.click('#native-config-save')
            assert saved_config.value.ok, saved_config.value.text()
            pw.expect(page.locator('#native-config-save')).to_be_enabled()
            page.get_by_text('Sources & extraction previews', exact=True).click()
            page.locator('[data-source-choice]').evaluate_all('(inputs) => inputs.forEach(input => input.checked = ["NHS Jobs", "Reed"].includes(input.dataset.sourceChoice))')
            with page.expect_response(lambda r: r.url.endswith('/pipeline/config/save') and r.request.method == 'POST') as saved_sources:
                page.click('#native-source-selection')
            assert saved_sources.value.ok, saved_sources.value.text()
            setup_read = context.request.get(f"{base}/api/setup/read")
            assert setup_read.ok
            smart_sites = (setup_read.json().get("searches") or {}).get("smart_sites") or []
            assert "NHS Jobs" in smart_sites
            assert "Reed" in smart_sites

            page.fill('#native-queries', 'Data Analyst')
            with page.expect_response(lambda r: r.url.endswith('/pipeline/preview') and r.request.method == 'POST') as preview:
                page.click('#native-preview')
            assert preview.value.ok
            assert json.loads(preview.value.request.post_data)['search_queries'] == ['Data Analyst']
            assert page.locator('#native-preview-content').inner_text()

            page.click('#logout')
            page.wait_for_selector('#login-ident')
            assert context.request.get(f'{base}/api/center').status == 401

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
