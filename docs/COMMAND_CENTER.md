# Using the application command center

## Open the new workspace

Start the dashboard server as usual and open:

`http://127.0.0.1:8765/workspace`

The root URL, `/dashboard`, and `/dashboard.html` redirect to this workspace. Classic workspace rendering and classic iframe fallbacks are disabled. Jobs, Applications, History & storage, CV Studio, Portfolio, Pipeline, Cover Studio, Statement Studio, Evidence inbox, Insights, and Settings use the native navigation. Old embedded Pipeline and Setup URLs redirect to the native Pipeline and Settings pages. Explicit standalone CLI dashboard exports remain available.

For a source installation, install the updated dependencies and restart the server:

```bash
pip install -e .
applypilot dashboard-serve
```

For the repository's multi-user Docker deployment:

```bash
docker compose up -d --build
```

The workspace uses the existing authenticated user/session and CSRF protection. Each user's history, document versions, rules, reminders, mailbox tokens, and assistant budget are isolated in their own workspace database.

Administrators can open **Settings → User Admin** to promote/demote accounts, enable/disable access, reset passwords, delete other accounts, and inspect the admin audit. Account management uses the same authenticated admin-only API endpoints.

## Update a qualification or professional role

1. Open **CV Studio → Qualifications**.
2. Add/edit/remove a certification. Save its name, issuer, dates, earned/in-progress/expired status, and role applicability. A comma is part of the name, not a separator.
3. Edit Education, Memberships, or Professional Development as needed, then choose **Save qualifications**.
4. For new employment, use **Experience & projects** and save the role, employer, dates, and verified achievement bullets.
5. Choose a job in **Document preview**, then **Regenerate this CV**. Existing jobs with an approved CV can be regenerated too.
6. Follow the run in **Pipeline**. After completion, select the job again to inspect the newest version and download text/PDF.

Professional experience is assembled under Professional Experience; qualifications, memberships, and CPD have their own PDF sections. Page placement depends on document length. Print rules keep headings with following content where possible and avoid splitting list items.

Shared facts are supplied as verified evidence to scoring and tailoring, including when a role-specific resume is routed. Existing source resumes remain editable separately. Removing a supplementary experience entry does not erase employment already written into a source resume; edit that source when correcting it.

CVs have input fingerprints and version history. Profile/base-resume/variant/routing changes mark older versions as needing regeneration. A failed regeneration leaves the previous approved path available. A manual or automated submission references a versioned CV; legacy CVs are copied into document history before use.


## Manage applications

- **Pick / Unpick:** manage the application queue. Set priority in an application record.
- **Open application:** opens the employer portal without marking a submission.
- **Start application:** select AI-assisted browser or Claude Code and choose preparation versus submission. Browser/AI dependencies for the selected engine still apply.
- **Applied:** record a manual submission and optional reference/confirmation evidence.
- **Failed:** record the technical failure. Uncertain submission results are held for reconciliation instead of being blindly retried.
- **Hiring status:** separately track acknowledgement, assessment, interview, offer, rejection, withdrawal, or no response.
- **Archive / Restore:** hide and recover a listing without deleting its history.
- **Block:** choose job, employer, or domain scope and provide a reason. **Insights → Block rules** supports unblocking, including legacy URL blocks.
- **Listing check:** distinguishes reachable pages, HTTP-closed listings, possible closed-listing text, and unavailable checks. A reachable page does not prove that applications are still accepted.
- **Possible duplicates:** application details show other listings with the same recorded employer/title for review.

Active application records show a persistent timeline. SSE streams updates, with polling fallback. Detailed form checkpoints and pause/resume/cancel controls are supported by the LLM browser engine. Claude Code records browser tool activity, navigation/fill/upload steps, and execution-state changes alongside its existing console. Checkpoint requests are displayed separately from actual worker acknowledgement.

Stopped dashboard-owned attempts are released into an uncertain state if no final outcome was recorded. Verify the employer portal or acknowledgement before recording a confirmed result. Legacy CLI stale-lock behavior remains backward compatible; `APPLYPILOT_RECONCILE_STALE=1` enables reconciliation for those runs too.

## Reminders and scheduling

Set a next-action date in the application record. Insights lists reminders, and the open workspace displays due-action notifications. **Export reminder to calendar** downloads an ICS event.

Schedule preparation or submission from the application record. Schedules persist across server restarts, run while the dashboard server is running, and share the existing per-workspace runner. Listing availability and preferences are checked before scheduled execution. Interrupted/ambiguous attempts become **needs review**, not automatically replayed submissions.

`APPLYPILOT_SCHEDULE_DAILY_LIMIT` sets the workspace's scheduled execution limit (default 20). Queue priority applies to browser job selection. Settings supports excluded title terms, excluded locations, and remote-only application preferences; an unknown location is not automatically classified as on-site.

Settings also holds reusable verified screening answers. The assisted browser may map matching questions to these saved values; it does not invent an answer or accept a model-generated value outside the saved set.

## Configure AI assistance

The command-center assistant explains job fit, diagnoses failures, prepares interview questions, drafts follow-ups, and answers questions about saved applications. Answers are grounded in workspace evidence; drafts are not sent messages.

OpenAI/Gemini/local support remains available. Direct Anthropic support is separate from Claude Code CLI authentication:

```dotenv
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your-key
LLM_MODEL=claude-sonnet-4-6
APPLYPILOT_AI_DAILY_REQUESTS=50
```

For OpenAI, use `LLM_PROVIDER=openai`, `OPENAI_API_KEY`, and the desired `LLM_MODEL`. The init wizard now offers Anthropic as well.

### GPT-6 Luna / GPT-5.6 Luna trial

Both Luna IDs have explicit Chat Completions support in the client. For a low-cost first trial, set these values in the `.env` beside `docker-compose.yml`:

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=your-existing-key
LLM_MODEL=gpt-6-luna
OPENAI_REASONING_EFFORT=none
APPLYPILOT_AI_DAILY_REQUESTS=20
```

`gpt-5.6-luna` can be substituted as the model. Luna requests use `max_completion_tokens`, developer messages, and explicit reasoning effort. With `none`, there is no reasoning-token budget competing with the generated text. For non-none effort the client omits temperature, and the output limit includes reasoning as well as visible text. The accepted efforts are `none`, `low`, `medium`, `high`, `xhigh`, and `max`. Truncated or empty Luna replies are treated as failures rather than usable documents.

After updating `.env`, recreate the service with `docker compose up -d --build --force-recreate`. A simple `docker restart` does not reload Compose environment values. Test **Explain fit** on one job, then regenerate one selected CV and inspect it before trying a larger batch. Keep **Submit application** disabled during document-quality trials. The assistant request cap does not limit all pipeline calls.

The compatibility checks use mocked HTTP responses; live model access, billing, and CV quality are established by that user-managed trial. Changing reasoning effort changes the assistant cache identity, so the new setting is not hidden by an answer cached under the previous effort.

### Reasoning headroom for document writing

You can trial `low` reasoning for tailoring, supporting statements and cover letters:

```dotenv
LLM_TAILOR_REASONING_EFFORT=low
LLM_STATEMENT_REASONING_EFFORT=low
LLM_COVER_REASONING_EFFORT=low
```

For OpenAI Luna writing tasks only, the client adds bounded headroom to the existing caller's completion limit: low adds 2048 tokens, medium 4096, high 8192, and xhigh/max 16384. None adds zero. Examples with low are CV drafts 2300 → 4348 total tokens and cover letters 1200 → 3248. Supporting-statement totals depend on the word limit (a 1400-word request uses 2100 → 4148).

These are combined reasoning-plus-answer ceilings, not separately reserved amounts or guarantees of document quality. The model may allocate them differently. Actual billed usage depends on tokens generated, and truncated replies still fail rather than being silently accepted. Total Luna limits cannot exceed 128000 tokens; the client never automatically expands a failed request beyond the configured allowance.

Optional task overrides are `LLM_TAILOR_REASONING_TOKEN_ALLOWANCE`, `LLM_STATEMENT_REASONING_TOKEN_ALLOWANCE`, and `LLM_COVER_REASONING_TOKEN_ALLOWANCE`, each an integer from 0 to 16384. For example, use 4096 for a writing task if a measured trial shows the low default is too tight. None reasoning always uses the original caller limit. Small non-writing requests and other providers receive no automatic extra headroom.

The active route description includes the allowance, and request logs show requested/total tokens. Changes alter client/statement cache identities. After updating `.env`, recreate the service to apply it, then regenerate one representative document and compare quality and usage before increasing batch size.

### Task-specific routing

The system can now use different providers and models for different tasks. Unconfigured tasks inherit the global `LLM_PROVIDER` / `LLM_MODEL`; an explicit task provider uses its own provider's model default if no task model is supplied.

The deployed split is:

| Task | Provider / model |
| --- | --- |
| Scoring and Explain fit | Gemini / `gemini-2.5-flash` |
| Summaries, failure explanations, copilot | Gemini / `gemini-2.5-flash` |
| Discovery / description extraction | Gemini / `gemini-2.5-flash` |
| CV tailoring | OpenAI / `gpt-6-luna` |
| Supporting statements | OpenAI / `gpt-6-luna` |
| Cover letters / follow-up drafts | OpenAI / `gpt-6-luna` |
| Factual reviewer / judge | OpenAI / `gpt-4.1-mini` (configurable baseline) |
| Interview preparation / form mapping | OpenAI / `gpt-6-luna` |
| Email classification / date extraction | OpenAI / `gpt-6-luna` (reasoning `none`) |

Example configuration in the `.env` beside Compose:

```dotenv
LLM_SCORE_PROVIDER=gemini
LLM_SCORE_MODEL=gemini-2.5-flash
LLM_SCORE_THINKING_BUDGET=0
LLM_SUMMARY_PROVIDER=gemini
LLM_SUMMARY_MODEL=gemini-2.5-flash
LLM_SUMMARY_THINKING_BUDGET=0
LLM_TAILOR_PROVIDER=openai
LLM_TAILOR_MODEL=gpt-6-luna
LLM_TAILOR_REASONING_EFFORT=none
LLM_STATEMENT_PROVIDER=openai
LLM_STATEMENT_MODEL=gpt-6-luna
LLM_REVIEW_PROVIDER=openai
LLM_REVIEW_MODEL=gpt-4.1-mini
```

The same `PROVIDER`, `MODEL`, `REASONING_EFFORT`, and `THINKING_BUDGET` suffixes work for `SCORE`, `SUMMARY`, `TAILOR`, `STATEMENT`, `COVER`, `REVIEW`, `ASSISTANT`, `FORMS`, `EXTRACT`, and `EMAIL`. Reasoning settings currently apply to OpenAI Luna models; thinking budgets apply to Gemini. Both API keys remain configured normally. After changes, recreate the service with `docker compose up -d --build --force-recreate`.

View the effective route table in **Settings** or **Insights**. Assistant replies display their actual provider/model, and pipeline logs include task/provider/model on requests. CV reports include writer/reviewer configuration and judge model metadata. Credentials and endpoint URLs are excluded from route descriptions.

Routing scopes are isolated across threads and nested reviews. A temporary tailoring-model override cannot change the review model or another worker's client. Statement and assistant caches reflect route/model/reasoning changes. Missing task credentials fail with a configuration error rather than silently switching providers.

The reviewer is an explicit baseline, not an automatic winner chosen from a live comparison. To evaluate another reviewer, change `LLM_REVIEW_PROVIDER` / `LLM_REVIEW_MODEL` and compare outputs on the same jobs. Programmatic validators remain deterministic and do not require an AI provider.

Identical assistant inputs/model/task reuse cached answers. Changed professional evidence or application history invalidates that cache key. Daily request slots are reserved transactionally, including unsuccessful attempts. Insights shows request counts and provider-reported token usage. It does not invent dollar costs when model pricing is not configured.

Restart the server/container after changing its provider environment. The Docker Compose configuration forwards the new provider, budget, and mailbox settings.

## Gmail / Outlook evidence inbox

You can paste employer messages immediately, without connecting an account. The inbox identifies proposed hiring states and possible application matches. Every imported update requires review before changing a hiring record; repeated imports are deduplicated.

To connect a mailbox, register an OAuth application with the provider and configure:

```dotenv
APPLYPILOT_OAUTH_REDIRECT_BASE=http://127.0.0.1:8765
GMAIL_CLIENT_ID=...
GMAIL_CLIENT_SECRET=...
# or:
OUTLOOK_CLIENT_ID=...
OUTLOOK_CLIENT_SECRET=...
```

Register the exact callback:

`http://127.0.0.1:8765/api/center/mail/callback`

Use the same hostname for the workspace and redirect base so the authenticated session reaches the callback. In **Evidence inbox**, choose **Connect gmail** or **Connect outlook** and authorize the account. Tokens are encrypted using a private key stored in that user's workspace; they are never returned to the browser.

Synchronization runs every five minutes while the server is running, and **Sync now** is available. Each sync is bounded to recent messages (up to 50); Gmail filters recent application-related messages. **Disconnect** removes the stored authorization and stops subsequent synchronization. Historical evidence remains available.

OAuth authorization, live provider behavior, and real employer submissions require your credentials/accounts. Automated verification uses mocked providers and temporary workspaces, not live submissions. Reconnect mailboxes when moving workspaces to another machine; the private connector key is not part of the existing default transfer export.

### Filter and analyze employer correspondence

The inbox opens on **Application mail**. The filter also contains **Unclassified / other**, **Job alerts**, **Promotions**, **Recorded**, **Dismissed**, **Ignored**, and **All messages**, with counts and pagination. Existing unreviewed messages are classified on migration; previously accepted hiring updates and source messages are preserved.

Native provider message IDs and RFC message IDs identify repeated imports. Legacy/native copies are reconciled only when a unique complete-text match can be verified, allowing for link URLs added by the newer HTML parser. A recorded/dismissed/ignored original keeps its decision and analysis, and receives missing receipt metadata. Source copies are retained under **Duplicate copies (audit)** and cannot be reviewed twice. Distinct native deliveries, changed outcomes, different known receipt times and ambiguous legacy matches remain separate. A receipt and a later rejection are separate messages even when they concern the same application or thread.

Unchanged polling responses keep the current DOM, expanded source messages, focus and page position. Pagination returns a valid page after messages are removed and displays an explicit page count. With five messages and a page size of 25, **Page 1 of 1** correctly disables both Previous and Next.

Import/sync performs local rule classification only. It does not call a paid model. An uncertain message has no selected hiring outcome. Job alerts and promotions cannot be recorded as application updates unless you explicitly move them to application review.

- **Dismiss** hides one message from active review; **Restore message** brings it back.
- **Ignore sender** puts current unrecorded and future imported messages from that exact address into Ignored. It does not block a whole domain or change the mailbox itself.
- **Ignored senders → Stop ignoring sender** removes that future-import rule. Restore earlier messages separately.
- **Move to application review** lets you correct a classification; it clears earlier automatic status/match proposals.

For an application or unclassified message, choose **Analyze with AI**. The configured route is:

```dotenv
LLM_EMAIL_PROVIDER=openai
LLM_EMAIL_MODEL=gpt-6-luna
LLM_EMAIL_REASONING_EFFORT=none
```

Luna proposes a category, hiring outcome, submitted-application match, source quotes, up to three dates/deadlines, meeting link, and recruiter contact. The backend checks literal evidence, submitted candidate IDs, reference/employer/role matching, date/clock/time-zone consistency and source URLs. Model confidence is an estimate, not a calibrated guarantee. Unsupported suggestions remain unselected or show an issue for manual review.

Explicit source dates and clocks with a stated time zone can become reminder suggestions. Ambiguous numeric dates, missing years/time zones, unsupported dates and DST ambiguity require manual entry. Today/tomorrow are resolved only from the email's true receipt time and an explicit event time zone. Legacy imports with unknown receipt time are not anchored to their synchronization timestamp. New syncs preserve native message/thread IDs and true receipt timestamps, including when the same message is fetched again.

Analysis is user-triggered, uses one bounded logical request and shares `APPLYPILOT_AI_DAILY_REQUESTS` with assistant actions. Identical message/candidate/model inputs reuse the cached analysis. Provider/model and measured tokens appear in usage reporting. Background synchronization does not automatically analyze mail. A dismissal or manual recategorization during inference is preserved.

To record an update, choose the correct **Application** and **Hiring update**, review the source, and click **Accept reviewed update**. Dates, meeting links and recruiter details are saved only when their corresponding checkboxes are selected. You can edit a verified reminder time before accepting. The hiring state, optional reminder/details, message evidence and accepted flag commit together. Source messages and application notes remain available in history.

## Coverage and follow-up refinements

The implemented release delivers the roadmap's core workspace, CV correctness, application/hiring records, live LLM checkpoints, AI assistance, read-only mailbox connectors, reminders, scheduling, preferences, and basic analytics.

The broader roadmap still includes refinements tracked in the implementation record: configurable dollar pricing and live reviewer comparisons, opt-in high-confidence automatic updates and historical email backfills, richer document diff/layout controls, additional notification channels, advanced salary/sponsorship rules, analytics date/cohort filters, and command-center document-history portability in workspace transfers. These are not represented as already implemented.
