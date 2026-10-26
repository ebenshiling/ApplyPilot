import httpx
import pytest

from test_command_center import workspace, http_workspace  # noqa: F401
from test_workspace_startup import browser_context


@pytest.mark.parametrize("path,destination", [
    ("/", "/workspace"),
    ("/dashboard", "/workspace"),
    ("/dashboard.html", "/workspace"),
    ("/dashboard?embedded=pipeline", "/workspace#pipeline"),
    ("/dashboard?embedded=setup", "/workspace#settings"),
])
def test_legacy_entry_points_redirect_without_classic_rendering(http_workspace, monkeypatch, path, destination):
    base, root, _ = http_workspace
    from applypilot import dashboard_server as ds

    def forbidden(*args, **kwargs):
        pytest.fail("Serving the native workspace must not render classic HTML")

    monkeypatch.setattr(ds, "generate_dashboard", forbidden)
    monkeypatch.setattr(ds, "_ensure_dashboard", forbidden)
    with httpx.Client(base_url=base) as client:
        response = client.get(path)
        assert response.status_code == 302
        assert response.headers["location"] == destination
        native = client.get(destination)
        assert native.status_code == 200
        assert 'data-view="pipeline"' in native.text
        assert 'href="/dashboard"' not in native.text
        assert not (root / "dashboard.html").exists()


@pytest.mark.parametrize("path", ["/", "/dashboard", "/dashboard.html", "/workspace"])
def test_workspace_entry_points_keep_multi_user_login_gate(http_workspace, monkeypatch, path):
    base, _, _ = http_workspace
    from applypilot import dashboard_server as ds
    monkeypatch.setattr(ds, "_MULTI_USER_MODE", True)
    monkeypatch.setattr(ds._Handler, "_current_user", lambda self: None)
    with httpx.Client(base_url=base) as client:
        response = client.get(path)
        assert response.status_code == 200
        assert "Sign in or create an account" in response.text
        assert 'data-view="jobs"' not in response.text
        assert client.get("/api/center").status_code == 401


def test_legacy_write_hook_no_longer_starts_classic_renderer(http_workspace, monkeypatch):
    _, root, _ = http_workspace
    from applypilot import dashboard_server as ds

    def forbidden(*args, **kwargs):
        pytest.fail("Legacy write hooks must not launch classic rendering")

    monkeypatch.setattr(ds.threading, "Thread", forbidden)
    monkeypatch.setattr(ds, "generate_dashboard", forbidden)
    ds._regen_dashboard_async(app_dir=root, db_path=root / "applypilot.db")


@pytest.mark.parametrize("view", ["classic", "toString"])
def test_unknown_view_stays_in_native_jobs(http_workspace, view):
    base, _, _ = http_workspace
    with browser_context() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/workspace#" + view)
            page.get_by_role("heading", name="Find your next move.", exact=True).wait_for()
            page.wait_for_function("state.data && state.view === 'jobs'")
            assert page.locator('a[href^="/dashboard"], #classic-frame').count() == 0
            assert not errors
        finally:
            browser.close()
