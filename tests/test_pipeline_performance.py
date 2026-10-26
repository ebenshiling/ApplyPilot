"""Read work is bounded without stale authority or changed queue semantics."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json
import os
import threading

import pytest

from applypilot import country_context as country
from applypilot import pipeline_workspace as pw
from applypilot import setup_workspace as setup
from applypilot.ai_settings import environment, workspace_context
from applypilot.read_model import read_scope, reuse_read
from test_pipeline_workspace import workspace  # noqa: F401
from test_command_center import http_workspace  # noqa: F401


def test_read_reuse_copies_values_and_expires_between_operations(tmp_path):
    file = tmp_path / 'profile.json'
    file.write_text('{"personal":{"full_name":"Original"}}')
    with read_scope(tmp_path):
        first = setup.read_profile(tmp_path)
        first['personal']['full_name'] = 'Caller edit'
        assert setup.read_profile(tmp_path)['personal']['full_name'] == 'Original'
        file.write_text('{"personal":{"full_name":"Updated"}}')
        assert setup.read_profile(tmp_path)['personal']['full_name'] == 'Original'
    assert setup.read_profile(tmp_path)['personal']['full_name'] == 'Updated'
    with read_scope(tmp_path):
        assert setup.read_profile(tmp_path)['personal']['full_name'] == 'Updated'


def test_owner_country_nested_and_thread_scopes_remain_independent(tmp_path):
    first, second = tmp_path/'one', tmp_path/'two'
    first.mkdir(); second.mkdir()
    for root, name in ((first, 'First'), (second, 'Second')):
        (root/'profile.json').write_text(json.dumps({'name': name}))
    @reuse_read
    def value(app_dir):
        active = country.current()
        return (setup.read_profile(app_dir)['name'], active.country_code if active else None)
    with read_scope(first):
        assert value(first) == ('First', None)
        with read_scope(second):
            assert value(second) == ('Second', None)
        assert value(first) == ('First', None)
        with country.operation(country.resolve(first, country_code='NZ', config={'nz_workflow_enabled':True})):
            assert value(first) == ('First', 'NZ')
        assert value(first) == ('First', None)
    barrier = threading.Barrier(2)
    def worker(root):
        with read_scope(root):
            barrier.wait(5)
            return value(root)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(worker, (first, second))) == [('First', None), ('Second', None)]


def test_environment_read_is_reused_only_for_current_display(tmp_path, monkeypatch):
    import dotenv
    file = tmp_path/'.env'
    file.write_text('LLM_MODEL=first-model\n')
    original = dotenv.dotenv_values
    calls = Mock(wraps=original)
    monkeypatch.setattr(dotenv, 'dotenv_values', calls)
    with workspace_context(tmp_path), read_scope(tmp_path):
        assert environment()['LLM_MODEL'] == 'first-model'
        assert environment()['LLM_MODEL'] == 'first-model'
        assert calls.call_count == 1
    file.write_text('LLM_MODEL=second-model\n')
    with workspace_context(tmp_path):
        assert environment()['LLM_MODEL'] == 'second-model'
    assert calls.call_count == 2


def test_populated_preview_parses_search_file_once_and_keeps_fresh_next_request(workspace, monkeypatch):
    root, conn = workspace
    for i in range(12):
        conn.execute("INSERT INTO jobs(url,title,company,location,full_description,fit_score,score_fingerprint) VALUES(?,?,?,?,?,?,?)",
                     (f'https://example.org/perf/{i}', 'Data Analyst', 'Fixture', 'London', 'SQL reporting', 8, 'score-v2:old'))
    conn.commit()
    original = Path.read_text
    calls = []
    def read(path, *args, **kwargs):
        if path == root/'searches.yaml':
            calls.append(1)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    first = pw.preview(conn, root, {})
    assert len(calls) == 1
    cfg = setup.read_searches_dict(root)
    cfg['queries'] = [{'query':'New search role','tier':1}]
    (root/'searches.yaml').write_text(__import__('yaml').safe_dump(cfg))
    second = pw.preview(conn, root, {})
    assert second['config']['queries'] != first['config']['queries']


def test_progress_matches_snapshot_and_does_not_build_source_or_packet_previews(workspace, monkeypatch):
    root, conn = workspace
    conn.execute("UPDATE jobs SET score_status='retryable',score_error='Fixture scoring failure'")
    conn.commit()
    full = pw.state(conn, root)
    monkeypatch.setattr(pw, 'preview', Mock(side_effect=AssertionError('No full preview during polling')))
    monkeypatch.setattr(pw, 'packet', Mock(side_effect=AssertionError('No document packet during polling')))
    from applypilot.discovery import smartextract
    monkeypatch.setattr(smartextract, 'build_scrape_targets', Mock(side_effect=AssertionError('No discovery plan during polling')))
    progress = pw.progress(conn, root)
    assert progress == {k: full[k] for k in ('queue', 'runs', 'problems')}
    conn.execute("UPDATE jobs SET score_error=NULL,score_status=NULL")
    conn.commit()
    assert not pw.progress(conn, root)['problems']


def test_preview_only_validates_packets_that_it_returns(workspace, monkeypatch):
    root, conn = workspace
    for i in range(35):
        conn.execute("INSERT INTO jobs(url,title,company) VALUES(?,?,?)", (f'https://example.org/sample/{i}', 'Fixture role', 'Fixture'))
    conn.commit()
    original = pw.packet
    calls = Mock(wraps=original)
    monkeypatch.setattr(pw, 'packet', calls)
    out = pw.preview(conn, root, {})
    assert len(out['routes']) == 30 and calls.call_count == 30


def test_progress_endpoint_returns_live_counts_without_static_catalog(workspace):
    from applypilot import command_api
    root, conn = workspace
    handler = SimpleNamespace(_send_json=Mock())
    assert command_api.get(handler, '/api/center/pipeline/progress', 'country_code=GB', root)
    code, value = handler._send_json.call_args.args
    assert code == 200 and value['ok']
    assert set(value) == {'ok','queue','runs','problems'}


def test_ready_workspace_reads_do_not_repeat_migrations_or_cache_rows(workspace):
    from applypilot import database as db
    root, conn = workspace
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        assert db.workspace_connection(root/'applypilot.db') is conn
        assert not any(s.lstrip().upper().startswith(('CREATE','ALTER','UPDATE','INSERT','DELETE')) for s in statements)
        conn.execute("UPDATE jobs SET title='Changed row'")
        conn.commit()
        assert db.workspace_connection(root/'applypilot.db').execute('SELECT title FROM jobs').fetchone()[0] == 'Changed row'
    finally:
        conn.set_trace_callback(None)


def test_external_schema_change_triggers_initialization_again(workspace, monkeypatch):
    from applypilot import database as db
    root, conn = workspace
    conn.execute('CREATE TABLE external_fixture(value TEXT)')
    conn.commit()
    original = db.init_db
    initialization = Mock(wraps=original)
    monkeypatch.setattr(db, 'init_db', initialization)
    assert db.workspace_connection(root/'applypilot.db') is conn
    assert initialization.call_count == 1
    assert db.workspace_connection(root/'applypilot.db') is conn
    assert initialization.call_count == 1


@pytest.mark.parametrize('cold_start', range(5))
def test_replaced_workspace_and_concurrent_first_reads_are_initialized(tmp_path, cold_start):
    from applypilot import database as db
    target = tmp_path/'nested/applypilot.db'
    def first_read(_):
        try:
            conn = db.workspace_connection(target)
            return conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        finally:
            db.close_connection(target)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(first_read, range(4))) == [0,0,0,0]
    replacement = tmp_path/'replacement.db'
    conn = db.init_db(replacement)
    conn.execute("INSERT INTO jobs(url,title) VALUES('https://example.org/new','Replacement row')")
    conn.commit()
    db.close_connection(replacement)
    replacement.replace(target)
    try:
        assert db.workspace_connection(target).execute('SELECT title FROM jobs').fetchone()[0] == 'Replacement row'
    finally:
        db.close_connection(target)


def test_browser_polls_progress_once_per_cycle_without_refetching_catalog_or_erasing_edits(http_workspace):
    if os.environ.get('APPLYPILOT_RUN_COUNTRY_UI') != '1':
        pytest.skip('Enable local-only browser verification with APPLYPILOT_RUN_COUNTRY_UI=1')
    from playwright.sync_api import sync_playwright
    base, root, conn = http_workspace
    (root/'profile.json').write_text('{"personal":{"full_name":"Performance Fixture"}}')
    (root/'resume.txt').write_text('Performance Fixture\nSUMMARY\nVerified fixture evidence')
    (root/'searches.yaml').write_text('queries: [{query: Analyst}]\nlocations: [{location: "London, UK"}]\n')
    with sync_playwright() as pw_browser:
        browser = pw_browser.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(base+'/') else r.abort())
            requests, errors, responses = [], [], []
            page.on('request', lambda r: requests.append(r.url))
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('response', lambda r: responses.append(r.status) if '/api/center/pipeline/preview' in r.url else None)
            page.goto(base+'/workspace#pipeline')
            page.locator('#native-start').wait_for()
            page.wait_for_function("document.querySelector('#native-preview-content')?.getAttribute('aria-busy') !== 'true'")
            page.locator('#native-queries').fill('User control value')
            page.wait_for_timeout(6200)
            assert page.locator('#native-queries').input_value() == 'User control value'
            full = [u for u in requests if u.split('?')[0].endswith('/api/center/pipeline')]
            progress = [u for u in requests if '/api/center/pipeline/progress?' in u]
            assert len(full) == 1 and len(progress) >= 2
            assert responses and all(s == 200 for s in responses)
            assert not errors
        finally:
            browser.close()
