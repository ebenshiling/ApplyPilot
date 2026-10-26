import os
from pathlib import Path

import pytest

from applypilot import ai_settings
from test_command_center import workspace, http_workspace  # noqa: F401


def browser_context():
    if os.environ.get("APPLYPILOT_RUN_PLAYWRIGHT") != "1":
        pytest.skip("Set APPLYPILOT_RUN_PLAYWRIGHT=1 for browser verification")
    from playwright.sync_api import sync_playwright
    return sync_playwright()


def configure(root, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-service-secret")
    (root/".env").write_text("GEMINI_API_KEY=fixture-existing-private-secret\n")
    ai_settings.save(root, {"routes": [{"task": "score", "provider": "gemini", "model": "gemini-2.5-flash", "thinking_budget": 0}]})


def test_connections_are_status_only_and_modal_secrets_are_discarded(http_workspace, monkeypatch):
    base, root, _ = http_workspace
    configure(root, monkeypatch)
    with browser_context() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base+"/workspace#settings")
        page.get_by_role("heading", name="AI preferences.", exact=True).wait_for()
        assert page.locator(".ai-provider-card").count() == 3
        assert page.locator("input[type=password]").count() == 0
        assert page.locator("#ai-connection-dialog").count() == 0
        assert "fixture-existing-private-secret" not in page.content()
        assert "workspace .env" not in page.locator("#native-ai-settings").inner_text()
        page.get_by_role("button", name="Manage Google Gemini connection", exact=True).click()
        page.get_by_role("dialog", name="Google Gemini connection.", exact=True).wait_for()
        assert page.locator("input[type=password]").count() == 0
        page.get_by_role("radio", name="Set a workspace connection").check()
        assert page.locator("#ai-connection-secret").input_value() == ""
        assert page.locator("#ai-connection-secret").get_attribute("type") == "password"
        page.locator("#ai-connection-secret").fill("fixture-discarded-secret")
        page.keyboard.press("Escape")
        page.locator("#ai-connection-dialog").wait_for(state="detached")
        assert page.locator("input[type=password]").count() == 0
        page.get_by_role("button", name="Manage Google Gemini connection", exact=True).click()
        page.get_by_role("radio", name="Set a workspace connection").check()
        assert page.locator("#ai-connection-secret").input_value() == ""
        page.locator("#ai-connection-secret").fill("fixture-second-discarded-secret")
        page.get_by_role("button", name="Close connection settings", exact=True).click()
        page.locator("#ai-connection-dialog").wait_for(state="detached")
        assert "fixture-discarded-secret" not in (root/".ai-settings.json").read_text()
        assert ai_settings.environment(root)["GEMINI_API_KEY"] == "fixture-existing-private-secret"
        assert not errors
        browser.close()


def test_connection_save_preserves_pending_preferences_and_never_resubmits_secret(http_workspace, monkeypatch):
    base, root, _ = http_workspace
    configure(root, monkeypatch)
    original_env = (root/".env").read_text()
    with browser_context() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        saves = []
        page.on("request", lambda request: saves.append(request.post_data_json) if request.method == "POST" and request.url.endswith("/api/center/ai/settings") else None)
        page.goto(base+"/workspace#settings")
        page.locator("#ai-studio-budget").fill("31")
        page.locator("#ai-model-settings > summary").click()
        model = page.locator('[data-ai-task="score"] [data-ai-field="model"]')
        model.fill("fixture-pending-model")
        page.get_by_role("button", name="Manage Google Gemini connection", exact=True).click()
        page.get_by_role("radio", name="Set a workspace connection").check()
        page.locator("#ai-connection-secret").fill("fixture-new-private-secret")
        page.get_by_role("button", name="Save connection", exact=True).click()
        page.locator("#ai-connection-dialog").wait_for(state="detached")
        assert saves[0] == {"credentials": {"GEMINI_API_KEY": "fixture-new-private-secret"}}
        assert ai_settings.environment(root)["GEMINI_API_KEY"] == "fixture-new-private-secret"
        assert "fixture-new-private-secret" not in (root/".ai-settings.json").read_text()
        assert "fixture-new-private-secret" not in page.content()
        assert page.locator("#ai-studio-budget").input_value() == "31"
        assert model.input_value() == "fixture-pending-model"
        page.get_by_role("button", name="Save AI preferences", exact=True).click()
        page.locator("#toast").get_by_text("AI preferences saved.", exact=True).wait_for()
        assert "credentials" not in saves[1]
        assert saves[1]["studio_daily_budget"] == 31
        assert next(route for route in saves[1]["routes"] if route["task"] == "score")["model"] == "fixture-pending-model"
        assert (root/".env").read_text() == original_env
        browser.close()


def test_server_connection_choice_and_private_inline_save_error(http_workspace, monkeypatch):
    base, root, _ = http_workspace
    configure(root, monkeypatch)
    original_env = (root/".env").read_text()
    with browser_context() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(base+"/workspace#settings")
        page.get_by_role("button", name="Manage Google Gemini connection", exact=True).click()
        page.get_by_role("radio", name="Use server connection").check()
        assert page.locator("input[type=password]").count() == 0
        page.route("**/api/center/ai/settings", lambda route: route.fulfill(status=503, json={"ok": False, "error": "Connection settings unavailable. Retry."}) if route.request.method == "POST" else route.continue_())
        page.get_by_role("button", name="Save connection", exact=True).click()
        page.locator(".ai-connection-error").get_by_text("Connection settings unavailable. Retry.", exact=True).wait_for()
        assert page.get_by_role("button", name="Save connection", exact=True).is_enabled()
        page.unroute("**/api/center/ai/settings")
        page.get_by_role("button", name="Save connection", exact=True).click()
        page.locator("#ai-connection-dialog").wait_for(state="detached")
        assert ai_settings.environment(root)["GEMINI_API_KEY"] == "fixture-service-secret"
        assert (root/".env").read_text() == original_env
        assert "Managed by your server" in page.locator(".ai-provider-card").first.inner_text()
        browser.close()


def test_ai_preferences_responsive_themes_and_optional_price_layout(http_workspace, monkeypatch):
    base, root, _ = http_workspace
    configure(root, monkeypatch)
    with browser_context() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(base+"/workspace#settings")
        page.get_by_role("heading", name="AI preferences.", exact=True).wait_for()
        assert not page.locator("#ai-model-settings").evaluate("el=>el.open")
        assert not page.locator("#ai-pricing-settings").evaluate("el=>el.open")
        for theme in ("light", "dark"):
            page.evaluate("theme=>document.documentElement.dataset.theme=theme", theme)
            for width in (375, 768, 1440):
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
                screenshots = os.environ.get("APPLYPILOT_AI_SETTINGS_SCREENSHOTS")
                if screenshots and width in (375, 1440):
                    page.locator("#ai-pricing-settings").evaluate("el=>el.open=false")
                    page.evaluate("document.activeElement?.blur()")
                    page.locator(".ai-preferences").screenshot(path=str(Path(screenshots)/f"ai-preferences-{theme}-{width}.png"), style=".skip{visibility:hidden}")
                page.locator("#ai-pricing-settings").evaluate("el=>el.open=true")
                inputs = page.locator(".ai-price-row").first.locator("input")
                input_box, output_box = inputs.nth(0).bounding_box(), inputs.nth(1).bounding_box()
                assert abs(input_box["y"]-output_box["y"]) < 2
                assert input_box["width"] >= 80
                page.get_by_role("button", name="Manage Google Gemini connection", exact=True).click()
                dialog = page.locator("#ai-connection-dialog")
                box = dialog.bounding_box()
                assert box["x"] >= 0 and box["x"]+box["width"] <= width
                page.get_by_role("radio", name="Set a workspace connection").check()
                assert page.locator("#ai-connection-secret").bounding_box()["width"] <= box["width"]
                if screenshots and width == 1440:
                    dialog.screenshot(path=str(Path(screenshots)/f"ai-connection-{theme}.png"))
                page.keyboard.press("Escape")
                dialog.wait_for(state="detached")
        browser.close()
