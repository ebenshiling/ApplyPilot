# Reliable Pipeline workspace

The **Pipeline** tab at `/workspace#pipeline` is a native workspace page. Existing CLI commands and the classic dashboard remain available.

## Start with a focused workflow

The native page now starts with a **bounded, reviewed search scope**. See
[Discovery scope](DISCOVERY_SCOPE.md) for live configuration previews, matching-title
filtering, the shared 50-new-job default and new-run-only scoring.

- **Find & assess:** discovery, enrichment, and scoring.
- **Prepare selected applications:** CV, required statement/cover, and PDFs for picked jobs.
- **Custom stages:** choose individual stages. Preparation and submission are separate workflows.

Select a campaign or use workspace searches. Enter one role per line in **Role overrides**; leave it blank to use the saved role list. CSV overrides from the classic dashboard are also expanded into independent queries.

**Workspace searches** does not require a saved campaign. The dashboard and CLI use
the same absent-campaign identity when handing off captured settings/preferences to
the worker. Blank selections in older version-1 run snapshots are supported; named
campaigns, workspace ownership, country and the exact settings hash are still checked.

**Preview run** shows current eligible queues, board searches, smart targets, Workday role coverage, missing inputs, and sample resume decisions. Discovery can add jobs after the preview. Advanced controls include score threshold, workers, selected-only mode, streaming, provider skips, and source budgets. A budget of `0` means all configured targets. The native page starts with bounded source budgets when none are saved.

Board budgets are shared across boards; capped queries sample roles across locations and rotate on later runs. Smart targets and Workday targets rotate from recorded source work. Workday searches all configured priorities by default; an explicit maximum priority narrows it.

## Resume choice and configuration

Expand **Searches & resume rules** to edit roles, priorities, locations, exclusions, boards, mappings, and advanced routing JSON. **Preview changes** provides a saved/proposed diff. Unedited advanced keys are preserved, including board overrides and sector rules. Saving from a stale native editor is rejected instead of silently overwriting another tab's changes. The classic simple search builder also merges with the saved configuration rather than generating a replacement document.

If no routing section exists, ApplyPilot derives starter role rules from existing resume-variant filenames. Sector/general-purpose variants are not treated as role-specific filename rules. The preview explicitly labels these as inferred rules; save explicit mappings for more control. Unmatched jobs use the default resume. Tied families or an unavailable matching variant require a job-level override before automatic scoring/tailoring.

The job inspector shows the chosen variant, matched keywords, alternative candidates, input fingerprint, assessment explanation, missing requirements, and stored responsibility/evidence maps. Resume files are refreshed after edits. Changed scoring inputs are reassessed, and changed CV inputs queue replacement versions rather than clearing approved documents. Search-location or pricing edits do not themselves invalidate fit assessments.

Submitted document snapshots remain historical records. Regeneration produces new artifact filenames. Failed regeneration retains the prior approved path.

## Sources you own

Expand **Sources & extraction previews**:

1. Select registered sources and save the selection.
2. Add a public search template using `{query_encoded}` and optionally `{location_encoded}`, or a static listing URL.
3. Save a base URL for resolving relative links.
4. **Test extraction** performs a bounded, read-only structured/JSON-LD and candidate-link preview. It uses no AI and inserts no jobs. Sites without structured listings may need browser/AI extraction during discovery.
5. Edit a packaged source to create a workspace override. Disable it, or remove the override to restore the packaged default.

Catalogs are saved in workspace `sites.yaml`; package files and other workspaces are unaffected. Unknown names are not treated as new registrations. JobSpy boards still require implemented provider adapters.

**Workday employer portals** supports workspace-owned employer entries and overrides in `employers.yaml`: employer name, tenant, site ID, public Workday base URL, and disabled state. Portal tests use the CXS API without inference or insertion.

Source observations record target attempts, extracted/new/duplicate/rejected jobs, errors, and last outcome. Employer and source metadata remain separate. Search campaigns retain membership when the same listing is discovered by more than one campaign.

## Application packets and recovery

From a job record, review the inferred document requirements and explicitly choose whether a cover or supporting statement is needed. Add required screening questions and save their verified answers in **Settings → Application preferences & answers**.

Packet readiness checks required text files, PDF freshness, changed input hashes, missing verified answers, and active eligibility. A document path alone is not readiness. Archived, blocked, submitted, or active-worker jobs are excluded from ordinary preparation. Selected intent overrides the fit threshold for document preparation, but does not override archive/block rules.

Scoring outages remain retryable, with backoff and capped automatic attempts. Legacy provider-error zero scores can re-enter scoring. Each completed assessment/document is committed independently; rerunning a workflow resumes eligible work without replaying completed artifacts. **Problems & recovery** offers scoped retries. Interrupted runs retain completed work and can be resumed from the remaining queues.

Streaming waits for active upstream producers, including partial stage combinations. No-progress work has bounded waits and a stalled outcome. Stage results preserve item failures, and recent runs distinguish completion, partial success, failure, and interruption. Raw logs are available in the secondary console.

## Selective second opinions

The job inspector can evaluate one or two alternative resumes with the scoring model or configured reviewer. This is a user-triggered paid inference operation when the provider charges for it. Results are cached by inputs and route, with a maximum of ten evaluations per workspace per day; failed requests retain their budget reservations.

Comparative fit scores do not automatically change the chosen resume. Review the evidence and save an explicit override. Scores and model names do not establish a measured interview-outcome quality ranking.

## Usage, pricing, and outcomes

Pipeline model calls record provider-reported input/output tokens and actual response model names. Insights show requests and tokens by provider/model. Optional `ai_prices` supplies user-maintained USD-per-million rates:

```yaml
ai_prices:
  provider/model-name:
    input_per_million: 0.0
    output_per_million: 0.0
```

Replace placeholder rates with current provider prices. Missing rates or token data remain unknown. Estimates are not billing reconciliation and do not infer provider caching discounts or unreported retry costs.

Outcome cohorts group saved submissions by campaign, source, resume, document revision, writer model, and month. Small samples are reported as counts, not a best-model or best-CV recommendation. Historical records without model/version provenance are marked untracked.

## Verification

Regression coverage includes archived/selected scope, stale/existing PDFs, delayed streaming producers, bounded no-progress, thresholds, retryable scoring, route freshness/ambiguity, lossless editing, workspace source isolation, URL validation, extraction metadata, comparative budgets/cache, packet answers, campaign cohorts, HTTP flows, and a narrow-screen browser workflow.

Verification uses fixtures and mocked inference. Live source availability and actual model-output quality depend on provider/site behavior and should be assessed with a small real campaign.
