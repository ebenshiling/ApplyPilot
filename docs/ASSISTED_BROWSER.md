# Apply with agent

The application record in `/workspace` now offers **Apply with agent**. It opens a live, authenticated view of the same Chromium session used by the agent. This works in Docker without a desktop display, VNC service or public browser-control port.

## Use the workflow

1. Prepare the job's application packet. Review the CV and any required cover letter or supporting statement in their Studios. Render the PDFs and save verified screening answers in Settings.
2. Open the job record and choose **Apply with agent**. Each document shows whether its PDF is ready, missing or out of date. If needed, choose **Prepare current PDFs**; this renders the saved text without rewriting it or calling a model. Preview the text or PDF for each included document, confirm that you reviewed them, then choose **Open application with agent**. Each session keeps separate copies of those documents; later edits do not change that session's packet. If documents change after you load this screen, refresh readiness and review the current packet before starting.
3. Watch the live view. The agent uses the configured **forms** route (including your existing Luna selection), saved contact details, verified answers and reviewed statement/cover text. It uploads documents to identified file fields, preserves existing answers and advances through unambiguous Next/Continue/Review controls.
4. Use **Take over** for login, security checks, consent or new information. Click a field in the live view and type, use Tab/Shift+Tab, or paste a longer answer into **Text to insert**. Scroll with the provided controls or your wheel. **Upload to next empty file field** lets you choose a reviewed document when the field's purpose is unclear. Then choose **Resume agent**.

   Kallidus sites such as Barhale can show a cookie-preferences iframe over the advert. The agent pauses and offers the site's **Save my settings** and **Accept all cookies** actions in the sidebar. Review the cookie panel, choose your preference, then resume. The agent recognizes the equivalent top/bottom ASP.NET Apply buttons on verified Kallidus vacancy pages as application-entry controls, separate from final submission.
5. At the final page, inspect the employer form and the displayed answers. Choose **Approve & submit once** to authorize one final attempt. Taking over or changing the page invalidates the previous approval; request final review again.
6. A detected employer confirmation saves a screenshot, confirmation text, timestamp and application reference where present. The job is marked applied and its hiring status becomes submitted. **View saved confirmation** remains available when you reopen the record, including after a server restart.

If confirmation is missing or the browser fails after the attempt, the record becomes **submission uncertain**. Check the employer portal or confirmation email, then use **Reconcile submission** in the job record or blocked packet screen to record confirmed submitted, confirmed not submitted or still unsure. See the [reconciliation guide](SUBMISSION_RECONCILIATION.md). The agent does not automatically submit again.

## Session behaviour

- One active browser per workspace; four across a dashboard process.
- Login and CAPTCHA are manual. Authentication lives only in this isolated, temporary browser; it is not transferred from your own Chrome profile.
- Closing the live view requests takeover/pause. Reopen **Apply with agent** to reconnect. Sessions expire after 15 minutes without browser-view activity; stop a session explicitly when finished.
- A server restart closes the browser. The next record load recovers pre-submit attempts as prepared and post-approval attempts as uncertain. Reviewed packets and confirmation evidence remain in the workspace.
- Transient page navigation, iframe or image-refresh errors pause automation while keeping a live browser open for takeover. The last good image is retained until it refreshes. A categorized diagnostic appears in the sidebar and is saved without passwords, form values or raw provider error text. Only genuine browser-launch failures advise installing Chromium; a closed tab/browser has its own restart message. Repeated identical errors are deduplicated rather than filling the timeline.
- Navigation is bounded to 20 agent steps. Complex custom widgets, ambiguous controls and unanswered required fields need takeover. This initial release uses DOM planning with a screenshot-based remote view; it is not a universal native computer-use agent.
- The legacy CLI engines remain accessible under **CLI application engines** and retain their separate execution behaviour.

## Setup

The existing Docker image already installs Chromium. Rebuild and restart it to load this feature:

```bash
docker compose up -d --build
```

For a local checkout, install the browser in the same Python environment used by the dashboard:

```bash
python -m playwright install chromium
applypilot dashboard-serve
```

No model/provider change is required. A forms-provider failure is displayed as a manual handoff; check **Settings → AI preferences → Models by task → Forms** before resuming.

See [Application profile and browser responsiveness](APPLICATION_PROFILE_AND_BROWSER.md) for complete personal/address/equality fields, local saved-value filling, continuous viewing and batched takeover input.

The optional [Decisions observer trial](DECISIONS_OBSERVER.md) is available in Settings → AI preferences. It evaluates page classifications in a separate worker and reports suggestions without controlling browser actions. The [OpenAI Decisions/Agents assessment](OPENAI_APPLICATION_AGENT_APIS.md) describes the architecture and later managed-orchestration evaluation.

## Verification

Synthetic local application portals cover multi-page and iframe navigation, CV uploads, reviewed statements, manual login/consent, blocked unapproved submission, stale approval, existing confirmation text, uncertain results, workspace ownership and the complete workspace UI. They do not submit real applications or call a paid model.

```powershell
$env:APPLYPILOT_RUN_PLAYWRIGHT='1'
python -m pytest -q tests/test_assisted_browser.py
```
