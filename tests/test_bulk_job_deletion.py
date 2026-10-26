"""Bulk UI uses the actual owned lifecycle endpoints against disposable workspaces."""
import os
import re

import pytest

from test_command_center import workspace, http_workspace  # noqa: F401
from test_job_lifecycle import seed


@pytest.fixture
def browser_jobs(http_workspace):
    if os.environ.get('APPLYPILOT_RUN_COUNTRY_UI') != '1':
        pytest.skip('Enable local-only UI checks with APPLYPILOT_RUN_COUNTRY_UI=1')
    from playwright.sync_api import sync_playwright
    base, root, conn = http_workspace
    shared = seed(root, conn)
    conn.execute("UPDATE jobs SET title='Target one' WHERE rowid=1")
    conn.execute("INSERT INTO jobs(url,title,company,tailored_resume_path,discovered_at) VALUES('https://example.org/two','Target two','Fixture',?,'2026-10-26')", (str(shared),))
    kept = root/'tailored_resumes/kept.txt';kept.write_text('Other job document')
    conn.execute("INSERT INTO jobs(url,title,company,tailored_resume_path,discovered_at) VALUES('https://example.org/keep','Keep this job','Fixture',?,'2026-10-26')", (str(kept),))
    conn.commit()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width':1500,'height':1100})
            errors = []
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/workspace');page.locator('#job-search').wait_for()
            yield page, root, conn, shared, kept
            assert not errors
        finally:
            browser.close()


def select_targets(page):
    page.locator('#job-search').fill('Target')
    page.get_by_role('checkbox',name='Select all shown jobs',exact=True).check()
    assert page.locator('[data-selection-count]').inner_text() == '2 selected'


def confirm_bulk(page):
    page.get_by_role('button',name='Delete selected',exact=True).click()
    page.locator('#bulk-delete-confirm').click()


def test_filtered_select_all_deletes_selected_jobs_and_shared_generated_files_only(browser_jobs):
    page, root, conn, shared, kept = browser_jobs
    select_targets(page)
    assert page.locator('input[data-select]:checked').count() == 2
    confirm_bulk(page)
    page.wait_for_function("document.querySelector('[data-bulk-delete-feedback]')?.textContent.includes('2 jobs deleted')")
    assert page.evaluate('state.view') == 'jobs' and page.evaluate('location.hash') == '#jobs'
    assert [r[0] for r in conn.execute('SELECT title FROM jobs')] == ['Keep this job']
    assert not shared.exists() and not shared.with_suffix('.pdf').exists()
    assert kept.exists() and (root/'resume.txt').read_text() == 'Original source CV'
    assert conn.execute('SELECT COUNT(*) FROM cc_retired_jobs').fetchone()[0] == 2
    assert page.locator('#job-search').input_value() == 'Target'
    page.locator('#job-search').fill('')
    assert page.locator('.cards article').count() == 1


def test_partial_bulk_deletion_keeps_uncertain_record_selected_and_never_posts_its_delete(browser_jobs):
    page, root, conn, shared, kept = browser_jobs
    conn.execute("UPDATE jobs SET apply_status='submission_uncertain' WHERE rowid=2");conn.commit()
    requests = []
    page.on('request',lambda r:requests.append(r.post_data_json['id']) if r.url.endswith('/api/center/lifecycle/delete') else None)
    page.locator('#refresh').click();page.locator('#job-search').wait_for()
    select_targets(page);confirm_bulk(page)
    page.wait_for_function("document.querySelector('[data-bulk-delete-feedback]')?.textContent.includes('1 job deleted')")
    assert requests == [1]
    assert conn.execute("SELECT apply_status FROM jobs WHERE rowid=2").fetchone()[0] == 'submission_uncertain'
    assert shared.exists() and kept.exists()
    assert page.locator('input[data-select="2"]').is_checked()
    assert page.locator('[data-selection-count]').inner_text() == '1 selected'
    assert '1 not deleted' in page.locator('[data-bulk-delete-feedback]').inner_text()
    assert page.evaluate('state.view') == 'jobs'


def test_clear_and_cancel_bulk_confirmation_do_not_delete_records(browser_jobs):
    page, root, conn, shared, kept = browser_jobs
    select_targets(page)
    page.get_by_role('button',name='Clear selection',exact=True).click()
    assert page.locator('input[data-select]:checked').count() == 0
    assert page.get_by_role('button',name='Delete selected',exact=True).is_disabled()
    page.get_by_role('checkbox',name='Select all shown jobs',exact=True).check()
    page.get_by_role('button',name='Delete selected',exact=True).click()
    page.locator('.lifecycle-dialog [data-lifecycle-close]').click()
    assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 3
    assert shared.exists() and kept.exists()
    assert page.locator('[data-selection-count]').inner_text() == '2 selected'


def test_stop_remaining_finishes_current_job_without_deleting_the_rest(browser_jobs):
    page, root, conn, shared, kept = browser_jobs
    posts = []
    def deleting(route):
        posts.append(route.request.post_data_json['id'])
        response = route.fetch()
        page.evaluate("document.querySelector('#bulk-delete-stop').click()")
        route.fulfill(response=response)
    page.route(re.compile(r'.*/api/center/lifecycle/delete$'),deleting)
    select_targets(page);confirm_bulk(page)
    page.wait_for_function("document.querySelector('[data-bulk-delete-feedback]')?.textContent.includes('1 job deleted')")
    assert len(posts) == 1
    assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 2
    assert shared.exists() and kept.exists()
    assert page.locator('[data-selection-count]').inner_text() == '1 selected'
    assert 'Not processed' in page.locator('[data-bulk-delete-feedback]').text_content()


def test_server_failure_is_reported_and_other_selected_jobs_can_complete(browser_jobs):
    page, root, conn, shared, kept = browser_jobs
    posted = []
    def deleting(route):
        ident = route.request.post_data_json['id'];posted.append(ident)
        if ident == 1:route.fulfill(status=503,json={'ok':False,'error':'Synthetic temporary failure'})
        else:route.fulfill(response=route.fetch())
    page.route(re.compile(r'.*/api/center/lifecycle/delete$'),deleting)
    select_targets(page);confirm_bulk(page)
    page.wait_for_function("document.querySelector('[data-bulk-delete-feedback]')?.textContent.includes('1 job deleted')")
    assert sorted(posted) == [1,2]
    assert conn.execute('SELECT title FROM jobs WHERE rowid=1').fetchone()[0] == 'Target one'
    assert conn.execute('SELECT COUNT(*) FROM jobs WHERE rowid=2').fetchone()[0] == 0
    assert shared.exists() and kept.exists()
    assert page.locator('input[data-select="1"]').is_checked()
    assert 'Synthetic temporary failure' in page.locator('[data-bulk-delete-feedback]').text_content()


def test_select_all_only_selects_visible_filtered_jobs_and_individual_checkboxes_update_count(browser_jobs):
    page, _, conn, _, _ = browser_jobs
    select_targets(page)
    page.locator('input[data-select="1"]').uncheck()
    assert page.locator('[data-selection-count]').inner_text() == '1 selected'
    assert page.get_by_role('checkbox',name='Select all shown jobs',exact=True).evaluate('e=>e.indeterminate')
    page.locator('#job-search').fill('Keep')
    assert '1 outside this filter' in page.locator('[data-selection-count]').inner_text()
    page.get_by_role('checkbox',name='Select all shown jobs',exact=True).check()
    assert page.locator('[data-selection-count]').inner_text() == '2 selected · 1 outside this filter'
    assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 3


def test_country_change_before_dispatch_stops_batch_without_wrong_country_deletion(browser_jobs):
    page, _, conn, _, _ = browser_jobs
    posts = []
    page.on('request',lambda r:posts.append(r.url) if r.url.endswith('/api/center/lifecycle/delete') else None)
    def preview(route):
        response = route.fetch()
        page.evaluate("state.country='NZ'")
        route.fulfill(response=response)
    page.route(re.compile(r'.*/api/center/lifecycle/preview\?.*'),preview)
    select_targets(page);confirm_bulk(page)
    page.wait_for_function("!document.querySelector('#bulk-delete-confirm')")
    assert not posts and conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 3


def test_changed_job_identity_is_not_deleted_using_an_old_selection(browser_jobs):
    page, _, conn, shared, _ = browser_jobs
    select_targets(page)
    page.get_by_role('button',name='Delete selected',exact=True).click()
    conn.execute("UPDATE jobs SET url='https://example.org/replaced-two' WHERE rowid=2");conn.commit()
    page.locator('#bulk-delete-confirm').click()
    page.wait_for_function("document.querySelector('[data-bulk-delete-feedback]')?.textContent.includes('1 job deleted')")
    assert conn.execute('SELECT url FROM jobs WHERE rowid=2').fetchone()[0] == 'https://example.org/replaced-two'
    assert shared.exists()
    assert 'identity changed' in page.locator('[data-bulk-delete-feedback]').text_content()


def test_nz_saved_jobs_support_bulk_delete_without_touching_legacy_jobs(browser_jobs):
    page, root, conn, shared, kept = browser_jobs
    (root/'searches.yaml').write_text('nz_workflow_enabled: true\ncountry: UK\nqueries: [{query: Analyst}]\nlocations: [{location: "London, UK"}]\n')
    conn.execute("INSERT INTO jobs(url,title,country_code) VALUES('https://example.nz/a','NZ one','NZ'),('https://example.nz/b','NZ two','NZ')");conn.commit()
    page.locator('#refresh').click();page.wait_for_function("!document.querySelector('#workspace-country option[value=NZ]').disabled")
    page.select_option('#workspace-country','NZ');page.locator('#nz-saved-jobs [data-job-selection]').wait_for()
    page.get_by_role('checkbox',name='Select all shown jobs',exact=True).check();confirm_bulk(page)
    page.wait_for_function("document.querySelector('#nz-saved-jobs [data-bulk-delete-feedback]')?.textContent.includes('2 jobs deleted')")
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE country_code='NZ'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE country_code IS NULL").fetchone()[0] == 3
    assert shared.exists() and kept.exists()
    assert page.evaluate('state.view') == 'jobs'
