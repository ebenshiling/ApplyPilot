# ApplyPilot upgrade plan

Date: 2026-10-02<br>
Implementation ID: `20261002-application-command-center`<br>
Status: Core release implemented. See `docs/COMMAND_CENTER.md` and the implementation status record for delivered coverage, verification, and remaining roadmap refinements.

## Goal

Turn ApplyPilot into a clear, reliable job-application workspace: discover and assess jobs, maintain professional facts, generate current CVs, manage applications live, and track interviews and offers.

The first release combines frontend usability, reliable CV updates, and application tracking. AI assistance follows the tracking foundation; email integration follows the hiring tracker.

## Existing foundation and confirmed issues

- Dashboard: generated HTML/CSS/JavaScript in `src/applypilot/view.py`, served by `dashboard_server.py`.
- Existing actions: Listing, Apply, Pick/Unpick, Applied, Failed, Block, Delete, and CLI command-copy helpers.
- Pipeline status and logs already use polling.
- Shared AI layer supports OpenAI, Gemini, and local models. Claude Code powers an existing browser application path; direct Anthropic API support is separate work.
- Certifications are saved in `profile.json` under `resume_sections.certifications`, assembled into tailored text, and rendered as a dedicated PDF section.
- Certification editor merges incoming and saved values: deletions and corrections can retain unwanted entries. Commas also split entries.
- The regenerate button starts normal pending tailoring; jobs with existing tailored CVs are skipped.
- PDF conversion reads tailored text and checks text/report timestamps; profile edits alone do not refresh that text.
- The base resume and role-specific resume variants are separate inputs; profile edits do not rewrite them.
- PDF currently places Certifications before Education. Certification headings/lists lack the pagination protection applied to experience entries.
- Setup editors lack an explicit full-width textarea rule, and the navigation scrolls through one long page.

## Working rules

1. Preserve `applypilot init`, `run`, `apply`, and `dashboard-serve` workflows.
2. Use additive, backward-compatible migrations. Treat every workspace database and configuration as production data.
3. Use the existing `frontend-design` skill at `.agents/skills/frontend-design/SKILL.md` for frontend implementation. Target a refined, professional workbench with intentional typography, readable contrast, generous editor space, and controlled density.
4. Keep existing generated-dashboard architecture for the first release. Extract presentation modules where useful; any framework migration requires a separate decision.
5. Track real events from workers and persistent records. AI interprets evidence; it does not invent employer status or submission confirmation.
6. Capture professional facts once and render them deterministically into the correct document sections. AI may tailor emphasis, not fabricate qualifications or employment.
7. Distinguish the application execution state, employer hiring outcome, and visibility/block state.
8. Preserve the exact documents and evidence used for submitted applications, even after profile updates.
9. Follow `docs/FMEA_POLICY.md`, capture pre/post state for implementation increments, and run `python scripts/agent_verify.py` before marking implemented work complete.

## Phase 0 — Baseline and contracts

Dependencies: none.

- Record CLI/dashboard behavior, existing schema, per-user workspace isolation, and document-generation paths.
- Capture pre-state with `python scripts/capture_repo_state.py --out ops/implementations/20261002-application-command-center/pre_state.json` before runtime changes.
- Document mappings from legacy statuses to new execution, hiring, and visibility states.
- Define additive records for application attempts/events, professional-profile revisions, document versions, block rules, and later email matches.
- Define event fields: event ID, job/application ID, attempt ID, event type, previous/new state, source, timestamp, and evidence reference.
- Define authenticated API contracts and state-transition rules, including concurrent worker and manual-action behavior.
- Baseline screenshots at desktop, tablet, and mobile widths; establish keyboard and PDF verification scenarios.

Acceptance: migration/compatibility approach documented, representative workflows identified, and baseline checks recorded.

## Phase 1 — Frontend workbench

Dependencies: Phase 0 contracts; frontend work can proceed alongside Phase 2 after those contracts are agreed.

- Introduce primary views: Jobs, Applications, CV Studio, Pipeline, and Settings.
- Make setup navigation display the selected workspace section instead of requiring a long-page scroll.
- Full-width textareas; labels above controls; responsive grids; consistent spacing and action hierarchy.
- Separate ordinary profile editing from advanced JSON configuration.
- Show save state, validation errors beside the affected fields, and successful-save feedback.
- CV Studio layout: section editor and document preview, stacked on smaller screens.
- Pipeline layout: compact stage progress, clear run/pause/stop controls, expandable advanced settings, recent-runs table, and a readable console.
- State-aware job actions: View/Pick/Apply for new jobs, progress for active jobs, follow-up for submitted jobs, retry/manual continuation for failures, and unblock for blocked jobs.
- Put secondary actions and CLI copy helpers in a More menu.
- Support dark/light themes, visible keyboard focus, accessible labels, reduced motion, and usable touch targets.

Acceptance: editors fill their available width; no horizontal overflow at 375px, 768px, and 1440px; core actions work by keyboard; both themes are readable; existing live actions and served/static distinctions remain clear.

## Phase 2 — Reliable CV updates and professional facts

Dependencies: Phase 0. Integrate with Phase 1 CV Studio.

### 2A. Fix current update behavior

- Make certification edits replace the edited list rather than silently unioning it with saved entries. Provide explicit add/edit/delete controls.
- Treat a certification as one entry; preserve punctuation such as commas.
- Retain backward compatibility with existing string-list certification data.
- Add genuine targeted regeneration: one job or selected jobs, including jobs that already have tailored documents.
- Keep prior document versions and point to a new version only after successful generation and validation.
- Rebuild tailored text before PDF output when professional facts change.
- Detect outdated documents using relevant profile/resume/variant revisions or content fingerprints, not only file timestamps.
- Improve heading-with-content pagination and list-item splitting for Certifications and Education.

### 2B. Structured CV Studio

- Editors for professional experience, education, certifications, skills, projects, memberships, and continuing professional development (CPD).
- Certification fields: name, issuer, award date, expiry date, status (earned/in progress/expired), credential ID/link, role-family applicability, and inclusion preference.
- Experience fields: employer, role, dates, responsibilities, achievements, and supporting metrics.
- Establish canonical shared facts with explicit role-specific overrides. Preview the effect on variants before applying changes; preserve imported user text.
- Show which base resume/variant and profile revision each generated CV used.
- Add supported Memberships and Professional Development sections to assembly, validation, and PDF rendering.
- Preview the proposed section and inclusion of each update before generation.
- Add document history, version comparison, and downloads for previous versions.
- Provide section-order/page-length preferences with readable defaults; do not guarantee a fixed page number or shrink text excessively to meet a target.

Acceptance scenarios:

1. Add an earned certificate, save, regenerate an existing job's CV: it appears exactly once under Certifications in text and PDF.
2. Edit/delete it: corrected text appears and the old/deleted entry disappears from future output.
3. A comma in the name remains part of one certification.
4. In-progress qualifications are labeled correctly and are not represented as earned.
5. Add employment: it appears under Professional Experience, not Certifications.
6. Update shared facts: affected variants/documents are identified and can be regenerated without silently replacing custom content.
7. Updating a profile preserves the document snapshot used for an already-submitted application.
8. Multi-page PDFs retain readable typography and keep section headings with following content where feasible.
9. Existing string-list profiles and CLI tailoring continue to work.

## Phase 3 — Application records and upgraded actions

Dependencies: Phase 0; integrates with Phases 1 and 2.

- Job detail panel: full description, source links, salary, location, deadline if known, fit explanation, listing-check timestamp, and documents.
- Keep opening Apply separate from recording a submission.
- Apply menu: Open application, Prepare documents, and Start auto-apply through the existing engine.
- Pick creates a queue entry with priority, notes, and optional planned execution time.
- Applied records submission date, method, reference number, document version, and manual versus verified evidence.
- Failed records the attempt, failed step, error category, explanation, and appropriate recovery action.
- Block supports job, employer, agency, and domain scope, reason, optional expiry, and explicit unblock. Show scope before applying a rule.
- Archive/restore for recoverable removal; retain permanent deletion as an explicit secondary action with well-defined history behavior.
- Validate status transitions on the server and return useful conflicts when an action is invalid.
- Add application detail/history, notes, bulk actions, and cross-source duplicate detection with review for ambiguous matches.

Acceptance: manual actions and worker updates produce consistent persisted history; conflicts do not overwrite active attempts; bulk actions report per-item outcomes; block scope is reversible; opening a link never means an application was submitted.

## Phase 4 — Live execution and dependable recovery

Dependencies: Phase 3 application/attempt records.

- Persist worker events for preparation, opening form, filling details, uploading, submitting, and confirmation.
- Add Server-Sent Events (SSE) for live updates, with reconnect/replay support and polling fallback.
- Show current step, elapsed time, last activity, heartbeat, waiting-for-input state, and evidence references.
- Implement cooperative pause/resume/cancel at defined safe checkpoints; represent stop requests accurately.
- Distinguish execution states: queued, preparing, running, waiting for input, paused, submitted, failed, canceled, and submission uncertain.
- Structured failures: authentication, missing profile data, upload rejection, timeout, closed listing, and uncertain submission.
- Retry only appropriate failures; reconcile uncertain submissions before another submission attempt. Add bounded retries and per-job attempt locking/idempotency.
- Support manual continuation and store its resulting evidence.
- Notifications for completion, recoverable failure, and required user input.
- Optional AI diagnosis grounded in actual logs/events, with secrets and unnecessary personal data excluded.

Acceptance: dashboard updates without page reload; reconnect does not lose or duplicate events; workspace feeds are isolated; interrupted workers recover accurately; uncertain submissions are not blindly retried; pause/stop labels reflect worker state.

## First release gate

Ship Phases 1–4 after Phase 0, with Phase 2A prioritized early. The release must demonstrate:

> Add a qualification → save → see affected CVs → regenerate one existing CV → preview correct PDF section → pick a job → run application → observe live progress → inspect submission evidence or a useful failure explanation.

AI failure diagnosis may ship with this release if the event foundation is ready. Other AI features are not blockers for reliable tracking and CV editing.

## Phase 5 — AI assistance and provider integration

Dependencies: professional facts, document versions, and application/event records.

- Extend the existing provider abstraction; start with existing OpenAI support and add direct Anthropic/Claude API support. Retain Gemini/local compatibility.
- Configure model choice by task, request timeout, retries/backoff, structured-output validation, and provider health feedback.
- Smaller models for classification/summaries; stronger models for difficult reasoning and document preparation.
- Cache against model/prompt/input revisions; invalidate changed inputs. Track token usage, estimated cost, and configurable budgets/concurrency.
- Job explanations with references to listing/profile evidence and explicit missing information.
- Preparation panel with resume/cover-letter previews, original-versus-tailored comparison, requirement coverage, editable screening answers, and reusable verified answers.
- Copilot queries real application data and links to records: priority suggestions, failures, and follow-ups.
- Interview preparation based on the submitted document version, job description, and user-provided experience.
- Copilot action proposals use the same validated APIs; do not let untrusted job/email content issue tool instructions.
- Provider errors never erase application state or prevent manual workflows.

Acceptance: explanations reference real evidence; invalid model output is handled; fabricated claims are rejected; cache invalidation follows updates; task costs are visible; provider failure leaves records usable.

## Phase 6 — Hiring tracker and communications

Dependencies: Phase 3 records; Phase 5 for AI email extraction.

- Separate employer outcomes: submitted, acknowledged, assessment, interview, offer, rejected, withdrawn, and no response.
- Track recruiter details, interview dates/time zones/links, assessment deadlines, notes, next actions, and offer details.
- Follow-up reminders and editable message drafts; distinguish draft creation from sending.
- Optional Gmail and Outlook connectors with connect/disconnect, background synchronization, and clear connection health.
- Match messages by reference number, thread, employer, role, and application date. Deduplicate imported messages/events.
- AI extraction produces structured proposed updates with source messages and confidence.
- Auto-update only strong matches; send ambiguous matches to a review inbox. Allow corrections and preserve provenance.
- Calendar links/export and notification preferences for deadlines, interviews, assessments, and follow-ups.
- No-response is a configurable elapsed-time condition, not inferred employer rejection.

Acceptance: explicit rejection differs from a technical application failure; ambiguous email cannot overwrite the wrong application; repeated sync creates no duplicate updates; disconnect stops synchronization; dates display in the intended time zone.

## Phase 7 — Scheduling, preferences, and analytics

Dependencies: live execution and hiring outcome records.

- Schedule queue processing with priorities, configurable daily limits, concurrency, and durable restart behavior.
- Recheck listing availability/deadlines before execution; distinguish a confirmed closed listing from a network/check failure.
- Bulk prepare/apply/retry operations with per-item progress.
- Snooze and preference rules for role type, location, salary, sponsorship, and employer exclusions.
- Suggest preference changes from repeated block reasons; show the proposed change before saving.
- Analytics: submitted versus confirmed applications, response/interview rates, source performance, failure categories, follow-up workload, and AI usage/cost.
- Show sample size, date window, and missing data. Resume-version associations describe observed outcomes, not proven causal improvements.
- Export application history and useful reports.

Acceptance: scheduled work survives restart without duplicate submissions; exclusions apply consistently; unknown salary/sponsorship is distinct from a negative answer; metrics have documented denominators and reconcile with stored records.

## Cross-cutting implementation and verification

- Additive migrations, transaction boundaries, per-workspace isolation, and explicit legacy mappings.
- Keep credentials server-side; exclude tokens from logs, browser payloads, exports, and AI prompts.
- Persist minimum necessary evidence; support retention/removal without corrupting application history.
- Browser checks for navigation, actions, save feedback, responsive editors, keyboard access, and SSE reconnect.
- Meaningful tests for certificate edits/deletion, regeneration of existing CVs, document staleness, PDF section rendering/pagination, state transitions, event replay, uncertain submission recovery, and email matching.
- Use mocked providers/portals for automated submission tests; validate browser flows with dry-run before live operation.
- Run relevant focused checks during increments and `python scripts/agent_verify.py` at implementation completion. Capture post-state and update implementation status honestly.
- Record follow-up risks in `ops/implementations/20261002-application-command-center/fmea.json`.

## Decisions to settle when their phase starts

- Frontend visual direction within the professional-workbench design and desired default theme.
- Credential availability and default models for OpenAI/Anthropic; budget preferences.
- Gmail, Outlook, or both as the first email connector.
- Default section order, document length preference, and per-role inclusion rules.
- Notification channels and whether outbound email sending is required beyond drafts/reminders.
- Deployment topology for persistent background scheduling and email synchronization.

These decisions do not block baseline work, certification fixes, or responsive editor improvements.
