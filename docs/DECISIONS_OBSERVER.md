# Decisions observer trial

ApplyPilot now has an optional, **observation-only** OpenAI Decisions integration. It uses `gpt-6-luna` through `POST /v1/decisions` to classify page state, estimate whether a new submission receipt is present, and suggest a supplied non-final navigation control.

## Enable and evaluate

1. Open **Settings → AI preferences** and configure your OpenAI connection if needed.
2. Expand **Decisions observer**. Enable checks for new browser sessions, set a daily request budget and choose **Save AI preferences**.
3. Choose **Run synthetic benchmark**. This sends up to nine requests using labelled synthetic page metadata: listing, login, valid next page, missing information, security check, final review, new receipt, conditional receipt and an older receipt.
4. Read the results in the same panel. It reports labelled-case correctness, false confirmations, median provider-request latency, reported input tokens and a standard-price estimate. Failed/refused requests are visible. A benchmark stops on a provider failure, refusal, disabled setting or exhausted budget.
5. Start **Apply with agent** for a reviewed job. Expand **Decisions observer · experimental** in its live browser sidebar to see the latest observed classification alongside the existing DOM baseline. Refresh the Settings results to see aggregate comparisons.

The trial starts **off**. The setting applies to new browser sessions. Disabling it prevents subsequent provider requests from existing observers; a request already sent may still finish and count toward usage.

## What the observer does

- Runs in a separate worker; browser navigation and takeover do not wait for the classifier.
- Observes at most 20 unique snapshots per browser session, with one pending request at a time. Repeated page polling and typing into an already-filled field do not cause repeated observations. Snapshots encountered while the worker is busy can be skipped.
- Sends bounded field/control labels, required/valid/filled flags, a submission-attempt flag and redacted page excerpts. Entered field values, page URLs, DOM identifiers, screenshots and applicant documents are not included. Authentication fields suppress body excerpts.
- Saves only the input hash, baseline/expected labels, typed predictions, request status, latency and reported usage. Provider error bodies and credentials are excluded from reports.
- Records suggestions only. It has no browser-action interface; the current engine still fills/navigates, checks actual employer evidence and enforces the candidate's page-bound final submission approval.

## Interpreting the measurements

**Synthetic correctness** compares completed predictions with known fixture labels. **Browser baseline agreement** compares them with the existing heuristics and is not a measure of real-world accuracy. Refusals/failures and incomplete benchmark runs must be considered alongside correctness; they are not counted as successful predictions.

The confirmation probability uses an experimental **0.8** threshold for reporting. Confidence estimates are not calibrated approval or proof of submission. A model's false confirmation cannot mark an application applied.

Reports cover the most recent 1,000 observations and list the latest 40. Latency measures the provider request/response processing, not the duration of an application or a speed comparison with Responses. No claim of a measured 10× ApplyPilot speed-up is made.

The standard estimate uses **$0.10 per million provider-reported input tokens**, with no output charge. Regional/long-context premiums are excluded. Missing token usage stays unknown; a partial known-usage estimate is not a complete bill. Requests reserve against both the Decisions daily allowance (default **20**) and the existing workspace AI request budget, including failed attempts.

## Operation and recovery

Requests make one transport attempt, with a 12-second HTTP phase timeout and at most 5 seconds for connection. Authentication, access, billing, quota/rate-limit, timeout and invalid-response failures have separate report categories. There is no automatic retry/replay.

After a service interruption, a running benchmark becomes interrupted when results load. Already reserved requests retain their recorded pending/unknown state if their outcome was not saved; do not infer success or replay them automatically.

The existing encrypted workspace OpenAI connection and service-inheritance behaviour are reused. No new SDK dependency or credential field is needed. Environment equivalents are:

```bash
APPLYPILOT_DECISIONS_OBSERVER=1
APPLYPILOT_DECISIONS_DAILY_BUDGET=20
```

The UI-managed workspace settings take precedence over environment defaults. This first trial uses text metadata; image inputs and managed Agents orchestration remain later evaluation phases.

See [OpenAI API architecture assessment](OPENAI_APPLICATION_AGENT_APIS.md) and the [official Decisions guide](https://developers.openai.com/api/docs/guides/decisions/).

For an isolated evaluation from a checkout, `python scripts/decisions_benchmark.py` checks only the synthetic baseline without provider calls. Adding `--live` explicitly sends up to nine real requests with the service credential in a disposable evaluation workspace, separate from candidate records and preferences. `--out` can save the resulting measurements to an existing directory.

## Initial live measurement — 7 October 2026

The first nine-case run classified 5/9 page states correctly. More explicit category boundaries improved the same calibration set to **8/9**, with **9/9 next-control suggestions**, **9/9 confirmation checks** and no false confirmations. Median request latency on the refined run was **280.98 ms**, using **6,569 reported input tokens** (standard estimate **$0.0006569**). The remaining mismatch was an email-only Next page classified as unknown with 0.29 confidence. Three page predictions had confidence below 0.8.

Both runs together made 18 requests, used 11,716 reported input tokens and had a standard estimate of $0.0011716. These are small synthetic calibration results, not independent ATS validation or evidence that the model should control submission. The trial remains observation-only. Records are in `ops/implementations/20261007-decisions-observer/live_trial_v1.json` and `live_trial_v2.json`.
