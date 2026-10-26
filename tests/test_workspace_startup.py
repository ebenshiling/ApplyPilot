import os

import pytest

from applypilot import command_center as cc
from test_command_center import workspace, http_workspace  # noqa: F401


def browser_context():
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1 for browser verification")
    from playwright.sync_api import sync_playwright
    return sync_playwright()


@pytest.mark.parametrize("asset,view,selector", [
    ("studios.js", "settings", ".ai-provider-card"),
    ("pipeline.js", "settings", ".ai-provider-card"),
    ("studios.js", "portfolio", "#portfolio-projects"),
    ("nhs-planner.js", "cover", "#nhs-planner"),
    ("nhs-planner.js", "statement", "#nhs-planner"),
])
def test_direct_entry_and_refresh_wait_for_all_deferred_modules(http_workspace, asset, view, selector):
    base, root, conn = http_workspace
    snapshot = {"ok": True, **cc.overview(conn, root)}
    with browser_context() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            held, overview_requests, errors = [], [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/assets/"+asset, lambda route: held.append(route))
            def overview(route):
                overview_requests.append(route.request)
                route.fulfill(json=snapshot)
            page.route("**/api/center", overview)
            for iteration in range(2):
                if iteration:
                    page.reload(wait_until="commit")
                else:
                    page.goto(base+"/workspace#"+view, wait_until="commit")
                page.wait_for_function("typeof api === 'function' && document.readyState === 'interactive'")
                page.wait_for_timeout(250)  # Give an eager bootstrap response time to render.
                assert len(overview_requests) == iteration, "Workspace fetched/drew before its deferred modules were ready"
                assert "Loading your workspace" in page.locator("#content").inner_text()
                assert len(held) == iteration+1
                held[-1].continue_()
                page.wait_for_load_state("domcontentloaded")
                page.locator(selector).first.wait_for()
                page.wait_for_function("document.querySelector('#connection').textContent !== 'Connecting…'")
                assert page.evaluate("state.view") == view
                assert "is not defined" not in page.locator("#content").inner_text()
                assert not errors
                assert len(overview_requests) == iteration+1
            page.evaluate("initializeWorkspace(); initializeWorkspace()")
            page.wait_for_timeout(100)
            assert len(overview_requests) == 2
        finally:
            browser.close()


@pytest.mark.parametrize("asset", ["studios.js", "pipeline.js", "nhs-planner.js"])
def test_failed_dependency_has_reload_recovery_not_an_undefined_renderer(http_workspace, asset):
    base, _, _ = http_workspace
    with browser_context() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/assets/"+asset, lambda route: route.abort())
            page.goto(base+"/workspace#settings")
            page.get_by_role("heading", name="Workspace tools could not load.", exact=True).wait_for()
            assert page.get_by_role("button", name="Reload workspace", exact=True).is_visible()
            assert page.locator('a[href^="/dashboard"], #classic-frame').count() == 0
            assert page.locator("#connection").inner_text() == "Workspace tools unavailable"
            assert "is not defined" not in page.locator("#content").inner_text()
            assert not errors
            page.unroute("**/assets/"+asset)
            page.get_by_role("button", name="Reload workspace", exact=True).click()
            page.get_by_role("heading", name="AI preferences.", exact=True).wait_for()
            assert page.locator(".ai-provider-card").count() == 3
            assert not errors
        finally:
            browser.close()
