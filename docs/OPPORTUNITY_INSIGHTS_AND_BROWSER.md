# Opportunity activity, analysis exports and question-aware browser

## Insights: a durable opportunity ledger

The **Insights** page includes an activity ledger for active, archived and deleted opportunities. Filter by opportunity, activity type or employer/text, and page through the timeline. Changed-value events show the previous and new values.

Tracking includes discovery, listing/enrichment changes, scoring and retries, resume routing, packet requirements, CV/cover/statement/PDF preparation, application execution and attempts, browser checkpoints and measured input latency, answer requests/drafts/insertions, reviewed answers and final approval, submission reconciliation, record edits, hiring outcomes, reminders, schedules, closure and deletion. Database triggers capture relevant direct worker/CLI writes as well as dashboard actions. Browser attempt summaries and compact stage facts survive full-record/generated-file deletion.

Use **Record activity** in Insights or an opportunity record for employer calls, follow-ups, assessments, interviews, offers, rejections, withdrawals and notes. Supply the actual event time when known; recording time is stored separately. A manual activity entry adds evidence to the timeline. Hiring status and submission reconciliation remain explicit controls; an interview note does not invent an applied date.

Earlier saved timeline events remain available. When detailed tracking is enabled on existing records, a **baseline** records what is known at activation. It does not reconstruct missing historical steps or claim that discovery happened at activation. Historical events with no separate event-time field use their saved recording time.

## Download for your own analysis

**Download complete analysis** / **Download analysis pack** exports a ZIP containing:

- `opportunities.csv`: stable URL identity, original record number, record state, source/query/campaign, stage results, errors, attempts, model/provider facts, hiring outcome, references, reminders and closure reason.
- `events.csv`: complete timeline, including deleted records, event type/source, before/after states, structured JSON payload, event time and recording time. It is not restricted to the 30-event UI page or 200-event record dialog.
- `browser_attempts.csv`: retained browser attempt states and timestamps.
- `reconciliations.csv`: candidate-checked outcomes and evidence.
- `schedules.csv`: application schedules and recorded states.
- `manifest.json`: schema version, dataset counts, units, identity key and coverage notes.

The JSON download carries the same datasets with structured payloads. Individual opportunity/activity CSV downloads are also available. **Download all matching activity** respects the activity filters but includes every matching event, irrespective of the current page offset.

Join tables using `job_url`; use attempt IDs in browser event payloads to join attempts. Times are ISO 8601 with time zones; durations are milliseconds. Empty values mean unknown/not recorded, not zero. Use the opportunity table for unique opportunity/outcome counts: companion database change events and narrative events can describe the same action. Baseline events represent observations, not completed workflow steps.

Downloads are authenticated and workspace-owned. Credentials/password inputs are not collected as browser keystroke values; credential-like strings are redacted in exports, and CSV formula-leading cells are escaped. No binary screenshots or document contents are bundled in analysis exports; the workspace backup remains available separately.

## Faster manual browser control

The live view uses Chromium compositor frames via CDP screencast, with a screenshot fallback. Manual typing/clicks are FIFO batched, full-page inspection is removed from the manual polling loop, and question drafting runs separately from browser input. The sidebar shows queue depth, processed input latency and the active image transport. Static pages need not produce a continuous frame rate.

See [shared-browser interaction latency](BROWSER_INTERACTION_LATENCY.md) for immediate decoded-frame delivery, native press/drag/double-click controls, event-driven focus, schema-free authenticated hot routes and grouped input audit timings.

The model may still take time to draft an answer, but the live browser remains interactive during drafting. An action is considered inserted only after the browser has processed it; failed insertion keeps the text available for editing/retry.

## Answer the selected question

1. **Take over**, then click the answer field in the live browser. The sidebar reads its question and nearby guidance. Alternatively choose **Read page fields** and select the exact field.
2. Select **Draft an answer from my evidence**. Optional guidance can request a shorter answer or emphasize relevant supplied evidence.
3. Review/edit the answer and its quoted sources from the reviewed CV, statement/cover, saved professional facts or verified answers. Missing evidence is shown rather than guessed. Sensitive/personal declarations need your explicit answer or saved value.
4. Choose **Insert into focused field**. The target is tied to a specific document/element and a fingerprint of its contents. Changed pages/fields reject stale drafts. Replacing an existing answer requires the replacement checkbox; field character limits and select choices are checked.
5. Optionally check **I verified this answer; save it for reuse**. Verified answers can be matched locally on later forms.

Unnamed textareas, accessible labels, iframe fields, open shadow-root fields and contenteditable answer editors are supported. Browser password/consent controls remain direct manual input. Drafting and insertion never submit; final review, single-use approval and uncertain-submission locks remain in place.

## Calendar reminder

**Export reminder to calendar** focuses the reminder/date input with inline guidance when no date is set. With a date, it saves the reminder and downloads an `.ics` file without navigating away to an API error page.
