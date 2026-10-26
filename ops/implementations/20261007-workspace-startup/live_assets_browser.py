"""Exercise deployed JavaScript with delayed Studios and fixture-only APIs.

The HTML shell is the unchanged source shell; scripts/styles come from the live
Docker HTTP server. Every API request is intercepted with isolated fixture data.
No login, production data access, provider request or application action occurs.
"""
import json
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from applypilot import ai_settings, command_center as cc
from applypilot.database import close_connection, init_db

base = "http://127.0.0.1:8765"
repo = Path(__file__).absolute().parents[3]
shell = (repo/"src/applypilot/web/workspace.html").read_text(encoding="utf-8")
with tempfile.TemporaryDirectory(dir=r"C:\Users\swazy\AppData\Local\Temp\opencode") as temporary:
    root = Path(temporary)
    conn = init_db(root/"applypilot.db")
    overview = {"ok": True, **cc.overview(conn, root)}
    settings = {"ok": True, **ai_settings.public(root)}
    setup = {"ok": True, "profile": {}, "resume_text": "", "resume_variants": [], "searches": {}}
    results = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            held, errors, initial_requests = [], [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/workspace", lambda route: route.fulfill(body=shell, content_type="text/html"))
            page.route("**/assets/studios.js", lambda route: held.append(route))
            def fixture_api(route):
                assert route.request.method == "GET", "Startup must not issue writes"
                path = route.request.url.removeprefix(base).split("?", 1)[0]
                if path == "/api/center":
                    initial_requests.append(path)
                    route.fulfill(json=overview)
                elif path == "/api/center/ai/settings":
                    route.fulfill(json=settings)
                elif path == "/api/setup/read":
                    route.fulfill(json=setup)
                elif path == "/api/center/stream":
                    route.fulfill(body="", content_type="text/event-stream")
                else:
                    route.fulfill(json={"ok": True, "multi_user": False, "events": []})
            page.route("**/api/**", fixture_api)
            for iteration in range(3):
                if iteration:
                    page.reload(wait_until="commit")
                else:
                    page.goto(base+"/workspace#settings", wait_until="commit")
                page.wait_for_function("typeof api==='function' && document.readyState==='interactive'")
                page.wait_for_timeout(250)
                assert len(initial_requests) == iteration
                held[-1].continue_()
                page.get_by_role("heading", name="AI preferences.", exact=True).wait_for()
                assert page.locator(".ai-provider-card").count() == 3
                assert page.locator("input[type=password]").count() == 0
                assert not errors
                results.append({"cycle": iteration+1, "settings_rendered": True, "secret_fields": 0, "page_errors": 0})
            browser.close()
    finally:
        close_connection(root/"applypilot.db")
print(json.dumps({"deployed_asset_browser_smoke": "passed", "api_data": "isolated fixtures only", "cycles": results}, indent=2))
