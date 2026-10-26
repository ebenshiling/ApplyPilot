# New Zealand in your current ApplyPilot app

## Where to find it

1. Open **http://localhost:8765/workspace** and sign in with your existing account.
2. If the page was already open during the update, hard-refresh it with **Ctrl+Shift+R**.
3. Above the main page heading, choose **Country workspace → New Zealand**.
4. Switch back to **United Kingdom / legacy** to see your existing UK records.

NZ is a country view in the same application. It shares your candidate profile and
keeps each vacancy's own country, documents and application history. Existing
unknown-country records remain in the legacy view.

## Start here: four steps

**You do not enter an employer or upload a CV before searching.**

1. **Search:** enter a job title, select an NZ location and click **Find jobs**.
   Choose **Open SEEK results**, **Open Indeed results** or **Open LinkedIn results**
   to view vacancies on that board in a new tab. These links are external search
   pages, not scraped vacancies or an imported result count.
2. **Review jobs:** when you find a vacancy, click **Found a job? Save its link here**.
   Enter its URL and title, then save. Employer, salary and job text are optional
   details. A link-only save is a reference for tracking.
3. **Prepare:** open Step 3 after choosing a vacancy. Import/reconcile CV sources and
   review the actual documents. The existing source/factual requirements still apply
   to AI document preparation; they are not prerequisites for searching.
4. **Apply:** choose a saved vacancy in Step 4 and click **Review / apply**. Review its
   packet, complete the employer form and approve the final submission when ready.

The manual save form, employer-evidence panel and automatic-source configuration
are collapsed by default. The numbered links take you to each stage. Open sections
and search inputs are retained across in-page refreshes.

## Optional automatic import and later document setup

- Save the dedicated NZ campaign with your role searches and cities.
- Capture an original employer vacancy URL, or configure and validate the selected
  supported discovery sources. Empty source selection means no automated sources.
- Import your software, data/BI and support master CVs in the NZ source panel and
  review their factual assertions. Demo facts have not been imported into your account.
- Prepare documents, review their actual text/PDFs and record exact NZ question
  answers. Global UK work-rights answers are not copied into NZ.
- Use **Apply with agent** in Prepare & Track, or **Application record / schedule**
  for the full record. The browser shows the employer form, fills from your packet,
  lets you take over and requests approval for the final submit action.
- Record outcomes and reminders in the same NZ view.

**Advanced: automatic imports and saved search campaign** is for importing vacancies
from configured direct/Workday sources. Its **Import jobs from configured sources**
button shows real run status and counts, supports stopping the owned NZ run, and never
starts a new run from a status check. Automatic checking pauses after 30 checks or
three consecutive status failures; **Check import status** explicitly checks again.
Unknown, interrupted, failed and partial runs are distinguished from successful
zero-result runs. Search on the boards remains available with no configured imports.

The earlier "Run NZ discovery" button looked busy even when disabled: disabled
controls now have a normal unavailable cursor; only active requests show a progress
cursor. A disabled import button no longer occupies the main starting position.

Application controls are released; incomplete documents or missing source configuration
produce specific readiness messages. Supported workflow release is not a guarantee
that every employer ATS can be completed automatically. Login/security challenges and
ambiguous forms can require your input in the shared browser.

## Deployment and controls

The existing Compose service on port **8765** is rebuilt from the original checkout.
Its existing workspace and multi-user volume mappings are retained. Compose sets
`APPLYPILOT_NZ_ENABLED=1` so new and existing workspaces can see NZ without editing
each user's search file. An explicit boolean `nz_workflow_enabled: false` in a
workspace still disables that workspace's NZ view; `APPLYPILOT_NZ_DISABLED=1` is the
service-wide deny. Standalone CLI installations retain their opt-in default.

Live application execution is enabled by default. An operator can explicitly stop
it with `APPLYPILOT_LIVE_APPLICATIONS_DISABLED=1`. Normal packet, jurisdiction,
review and uncertain-result protections are retained. Merely updating the service
does not start a discovery run or submit applications.

## Verification and rollback

- Full agent verification: **805 passed, 62 skipped**, compile pass, strict AI **7/7**,
  formal evaluation **13/13**. Browser-only cases are checked separately.
- Dedicated real-browser/local-HTTP verification: **16 passed**, including UK and NZ
  execution, final approval, single attempt, uncertainty, document change and workspace ownership.
- No real employer application is submitted by release verification.
- Existing uncommitted source changes were compared to their authorised baseline;
  **135 integration paths, zero conflicts**.
- Restricted backups: `.release-backups/20261018-nz-live-integration/source.tar.gz`
  and `docker-data.tar.gz`. Both are excluded from Git and Docker build context.
- Previous Docker image: `applypilot-applypilot:nz-pre-release-20261018`.

For a rollback, stop the service before restoring matching source/data archives and
the retained image. Preserve any user data written since the backup before restoring.
Do not run an older image against a partially restored database or overwrite live data.
