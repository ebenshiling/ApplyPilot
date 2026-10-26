# Pipeline audit and improvement roadmap

**Implementation update:** the audit findings and improvement phases are implemented in the [native Pipeline workspace](PIPELINE_WORKSPACE.md). The sections below retain the original audited behavior and configuration advice as the historical evidence record; use the new guide for current workflows.

Audited: **4 October 2026**. Scope: orchestration, dashboard run controls, discovery configuration, resume selection, scoring/writing, and pipeline usability. This document records current behavior and proposed improvements. Runtime fixes are a follow-up phase.

## Executive assessment

The architecture already supports configurable role searches, multiple resume files, task-specific AI models, sequential/streaming stages, document validation, and application tracking. The highest-value next work is making those features agree on **scope, eligibility, configuration, and completion**.

The most important user-specific finding: the inspected workspace has ten resume variants, but its saved `searches.yaml` has **no `resume_routing` section**. Automatic scoring and tailoring therefore select the default resume unless a different effective per-run configuration supplies routing. Creating variants in CV Studio does not configure automatic routing.

### Inspected workspace configuration

| Setting | Observed |
|---|---|
| Role queries | 25 |
| Locations | 9 |
| JobSpy boards | 4 |
| Selected smart-source names | 6 |
| Resume variants on disk | 10 |
| Automatic resume routing configured | No |
| JobSpy search combinations across boards | 900 before per-run overrides |
| Smart targets | 900 after the configured site allow-list and deduplication, before environment filters/caps |
| Data Analyst query tier | 3; excluded from default Workday tier-1/2 searches when Workday runs |
| Graduate title exclusion | Enabled |

These are planning counts, not requests made during the audit. Runtime overrides, source skips, caps, and provider behavior can reduce the actual workload. `results_per_site: 35` is a per-search result limit, not a total-run job budget.

## Current execution and AI behavior

```text
Discover -> Enrich -> Score -> Tailor -> Statement -> Cover -> PDF
                                                        Submission: separate apply workflow
```

- **Discovery:** JobSpy adapters, packaged Workday employers, and smart extraction from a packaged direct-site registry. Smart extraction combines structured page/API/JSON-LD/card extraction with AI-assisted extraction where needed.
- **Enrichment:** fetches fuller descriptions and application URLs.
- **Resume selection:** deterministic keyword matching against job title, company, source, and description. Title matches add 3 points per keyword; other matches add 1. Equal scores retain the earlier family. The selector does not ask AI to compare all ten CVs.
- **Scoring:** AI evaluates the selected resume, augmented with shared professional facts. The description passed to the scorer is truncated to 6,000 characters. Changing resume routes does not automatically invalidate existing scores.
- **Tailoring:** selects the base resume again, generates a job-specific CV, and applies validation/review. Scoring and tailoring do not persist a common routing decision as a durable job-level contract.
- **Statements/covers:** normally use the job's tailored text. Supporting statements are currently requested for all eligible jobs in that stage, rather than only postings explicitly requiring one.
- **AI providers:** task routing supports different extraction, scoring, writing, and reviewer models. This audit used mocked inference; it does not establish real-output quality or a model quality ranking.

## Prioritized findings

Priority **P1**: fix before expanding automated runs. **P2**: next usability/data-quality release. **P3**: refinements. Reproductions are in `ops/implementations/20261004-pipeline-audit/audit_checks.py`; recorded output is `audit_results.json` alongside it. Source references are to the audited checkout.

### F01 — P1: streaming PDF completion cannot converge

**Evidence:** `pipeline.py:294-319,419-438`; `scoring/pdf.py:664-747`.

Streaming counts any `tailored_resume_path` ending in `.txt` as pending PDF work. PDF conversion creates a sibling PDF and retains the text path. A fixture with an existing up-to-date PDF produces **pending=1, conversions=0**. The worker repeats immediately whenever pending is nonzero, without a no-progress delay or bound. This can leave a run spinning after useful work is complete.

**Fix/acceptance:** count actual missing/stale render artifacts; persist or compute render readiness consistently; apply bounded retry/backoff and a stalled state. A fully rendered run must terminate; one failed conversion must not cause an unlimited loop.

### F02 — P1: pending counts disagree with actual tailoring eligibility

**Evidence:** `pipeline.py:294-376`; `database.py:617-722`; `scoring/tailor.py:2484-2535`.

The generic pending SQL does not include archive visibility, command-center blocks/preferences, or all later title filters. A fixture with one archived eligible-looking job gives **pending=1, actual tailoring candidates=0**. The same no-progress loop can occur. Dashboard aggregate counts also use independent, partly hardcoded predicates (`database.py:461-550`).

**Fix/acceptance:** one eligibility service shared by counters, workers, and the UI. Report exclusions with reasons. Archiving/blocking the final candidate must drain the effective queue, not leave a phantom pending item.

### F03 — P1: supporting-statement scope and threshold are inconsistent

**Evidence:** `scoring/supporting_statement.py:731-754`; `pipeline.py:463-469`; `scoring/cover_letter.py:1111-1134`.

Supporting statements use their own SQL and do not consult the selected-only flag or archive/block/preference filters. With selected-only enabled, a mocked batch processed an **unselected** high-score job and an **archived** high-score job, while ignoring a selected score-4 job. Sequential orchestration also omits `min_score` when invoking the statement runner, so a requested threshold of 9 falls back to 7. Cover letters respect selection but likewise query directly without the shared downstream visibility/block/preference filtering.

**Fix/acceptance:** all document stages use the same scoped candidate selector; selected intent consistently overrides score where advertised; statement thresholds are forwarded; archived/blocked jobs receive no new automatic documents. Generate statements only when required or explicitly requested.

### F04 — P1: streaming can finish downstream stages too early

**Evidence:** `pipeline.py:84-92,399-433,521-539`.

Only the immediate predecessor is checked, while omitted stages are marked done. In a `tailor + pdf` run, skipped `cover` permits PDF to finish when its queue is momentarily empty, even while tailoring can still produce files. The bounded fixture reproduced PDF done while tailor was not done.

**Fix/acceptance:** resolve active producer dependencies for the selected stage graph, not only fixed adjacent stages. Test partial combinations such as `tailor + pdf`, `score + tailor`, and `tailor + cover + pdf` with delayed production.

### F05 — P1: transient scoring errors become permanent low-fit records

**Evidence:** `scoring/scorer.py:176-181,231-251,297-304`; `database.py:643`.

Inference errors return score 0, which is saved as a score. Normal scoring fetches only NULL scores, so an outage is no longer automatically retryable. The existing repair path recovers embedded scores from truncated responses; it does not retry ordinary provider errors. Successful scores are also held in memory until the batch completes, making interruption expensive on large runs.

**Fix/acceptance:** distinguish scoring state/error from actual fit; retain failed work as retryable with bounded backoff; commit successes incrementally; show “could not score” separately from “poor fit.” Verify outage, malformed output, interrupted batch, and resume-change invalidation cases.

### F06 — P1: success reporting loses failures and useful counts

**Evidence:** `pipeline.py:100-228,419-438,472-480`; `discovery/workday.py:491-570,625-653`.

Stage wrappers discard batch return values and report `ok` unless an exception escapes. JobSpy can return an error count, and scoring/writing can return item errors, yet the stage still appears successful. Streaming also ignores returned stage errors and ends with `status: ok`; a fixture reproduced that exact result. Workday's locally counted employer errors are omitted from its return payload.

**Fix/acceptance:** structured stage results with attempted/succeeded/failed/skipped/retryable counts, source outcomes, and terminal state (`completed`, `partial`, `failed`, `cancelled`, `stalled`). Display item failures even when the subprocess exits normally. Measure actual stage end times rather than total-run completion time for every streaming stage.

### F07 — P1: simple search generation can erase routing and advanced settings

**Evidence:** `view.py:3693-3801,5591`; `setup_workspace.py:603-620`.

The simple builder generates a fresh YAML document containing queries, locations, country, boards, smart-site selection, defaults, and exclusions. It does not carry through `resume_routing`, board overrides, Workday tier settings, or other advanced keys. Saving replaces the whole document. The inspected file has this generated shape; the audit cannot establish whether routing was previously present and removed.

The builder also assigns tiers by list position: first two roles tier 1, next three tier 2, remainder tier 3. This unintentionally affects default Workday coverage for a long role list.

**Fix/acceptance:** round-trip parsed configuration and preserve unedited keys, provide explicit per-role priority controls, show a before/after diff, and validate routes/files. Editing one location through the builder must preserve every routing and advanced setting.

### F08 — P2: the Pipeline override field accepts one query, not multiple roles

**Evidence:** `dashboard_server.py:906,975-977`; `view.py:1829,6505`.

`Application Support Analyst, Data Analyst` becomes one tier-1 query containing a comma. It does not become two searches. The Settings builder's Roles CSV field behaves differently, producing separate entries. Per-run overrides do preserve existing routing keys on their normal YAML path.

**Fix/acceptance:** explicit multi-query controls with chips/list entries and an effective-search preview. Until then, leave the Pipeline override blank to use saved multi-role searches, or enter exactly one role.

### F09 — P2: arbitrary smart-site names do not register new sources

**Evidence:** `config.py:217-224`; `discovery/smartextract.py:637-644,1915-1997,2314-2317`; `dashboard_server.py:946-986`; `view.py:3723-3734`.

`smart_sites` is an allow-list over targets built from packaged `config/sites.yaml`. Unknown names accepted by the per-run backend do not create a URL template; they may match zero targets. The Settings builder ignores unknown catalog names, and the saved-YAML validator rejects unknown names (`setup_workspace.py:287-305`). A supplied custom source dictionary can build targets, but normal CLI discovery does not load such dictionaries from a workspace registry. Workday similarly loads the packaged employer registry.

**Fix/acceptance:** a workspace-owned source registry merged by stable ID with packaged defaults; URL/template validation; enable/disable and edit controls; test extraction preview; clear unsupported-source feedback. A new source saved in one workspace must work without editing package files or another user's configuration.

### F10 — P2: routing needs provenance, freshness, and clearer context rules

**Evidence:** `role_routing.py:65-74,88-112,172-257`; `scoring/scorer.py:268-280`; `scoring/tailor.py:2632-2639`.

- Missing configuration falls back to `resume.txt`, even with ten variants on disk.
- Equal keyword totals resolve by configuration order, without an ambiguity signal.
- Generic description keywords can outweigh the intended job family; the returned “score” is a keyword tally, not calibrated confidence.
- Text is cached by resolved path without modification/content invalidation; a fixture returned old text after editing the file in the same process. New CLI processes reload it, but persistent consumers have no automatic freshness guarantee.
- Blended routing runs before context enable/threshold logic. A fixture selected a blended route with context disabled and only one context hit. This requires an explicit policy decision and consistent semantics.
- Existing score rows lack a persisted common route/fingerprint tying them to the resume subsequently tailored.

**Fix/acceptance:** preview matched terms, candidate routes, selected variant, reason/fallback, content hash, and ambiguity. Persist the decision/version used for scoring and writing; invalidate stale scores/documents deliberately. Require an explicit override for ambiguous cases. AI can help classify a shortlist, with a confidence threshold, while deterministic routing remains a visible baseline.

### F11 — P2: employer metadata is lost on discovery

**Evidence:** `discovery/smartextract.py:711-724`; `discovery/workday.py:380-400`; `scoring/scorer.py:164-168`.

Smart storage does not save `company`, even if the supplied job contains it; the fixture confirmed NULL after insertion. Workday stores employer name in `site` rather than the dedicated company field. Consequently, downstream features may substitute a job board for the employer. This weakens company blocking, email matching, sector routing, and employer analytics.

**Fix/acceptance:** preserve employer/source as distinct fields and retain extraction provenance. Verify structured employer data survives discovery/enrichment and distinguish unknown employer from source name.

### F12 — P2/P3: controls and “ready” status need a coherent user model

**Evidence:** `view.py:1792-1845`; `web/workspace-classic.css`; `database.py:542-550`; `pipeline.py:642-648`.

The embedded classic pipeline retains one wrapping control strip with presets, stages, thresholds, switches, search overrides, source lists, and stop/clear actions. “Apply-only” actually generates documents and does not submit. “Ready to apply” checks paths and a link, not PDF freshness, application-specific requirements, or shared eligibility. Dry run lists stages, rather than showing effective jobs, routes, source coverage, and budget. README stage counts also lag the current seven-stage orchestration.

**Fix/acceptance:** use the layout and run preview below; derive readiness from required artifacts plus active eligibility; align CLI, API, UI labels, and documentation.

## Configuration guide: supported today

### 1. Search several roles correctly

1. Open **Settings → Job Search** in the served workspace.
2. The simple builder's Roles field supports comma-separated roles. Once advanced routing is configured, use **Advanced YAML** so generation does not discard those extra keys.
3. Preserve existing locations, exclusions, and other settings while editing separate `queries` entries:

```yaml
queries:
  - query: "Application Support Analyst"
    tier: 1
  - query: "Data Analyst"
    tier: 1
  - query: "IT Support Analyst"
    tier: 1

# Include tier-3 roles if you deliberately keep those priorities and use Workday.
workday_max_tier: 3
```

4. Save the YAML. Leave Pipeline's **Role/query override** blank to use the list.
5. Start with a small role/location/source selection. The current saved 25 × 9 × 4 configuration is much broader than three roles across two locations and two boards.
6. Review exclusions intentionally: `graduate` currently rules out graduate-titled opportunities, including potential entry-level data roles. Change this only if those roles are wanted.

### 2. Connect existing resume variants to automatic routing

CV Studio can maintain the variant text. Add this **top-level section** to the existing Advanced YAML; these filenames already exist in the inspected workspace:

```yaml
resume_routing:
  variants:
    data_analyst: "resume_variants/data_analyst.txt"
    application_support_analyst: "resume_variants/application_support_analyst.txt"
    it_support_analyst: "resume_variants/it_support_analyst.txt"
  families:
    - name: "data"
      variant: "data_analyst"
      keywords: ["data analyst", "business intelligence", "power bi"]
    - name: "application_support"
      variant: "application_support_analyst"
      keywords: ["application support", "production support", "software support"]
    - name: "it_support"
      variant: "it_support_analyst"
      keywords: ["it support", "service desk", "helpdesk", "desktop support"]
  context_routing:
    enabled: false
  blended_routes:
    enabled: false
```

This chooses one base resume per posting; it does not score every resume and select the highest AI score. Unmatched roles use the default resume. Keywords match the entire posting, so review overlapping families. Use role-specific phrases rather than shared terms such as “communication” or “Excel.” These are starter rules, not an automatically measured best mapping.

More precise application-engineer/technical-systems variants can be added as additional entries and families. Sector-specific variants (`public_*`, `commercial_*`) can be configured using `context_families` and `blended_routes`; see `role_routing.py` for their schema. Because blending currently bypasses context enable/threshold rules, establish correct role-only selection before enabling it.

Newly scored and tailored jobs use effective routing. Existing scores are not refreshed by saving YAML; use the explicit rescore workflow for chosen evaluation jobs rather than assuming existing ratings changed. Existing approved or submitted documents should remain versioned when regenerated.

### 3. Select an existing discovery source

Set `smart_sites` to exact registered names, for example:

```yaml
smart_sites:
  - "NHS Jobs"
  - "GOV.UK Find a job"
boards:
  - indeed
  - linkedin
```

These names select known sources. The Pipeline smart-sites CSV is a per-run allow-list. JobSpy's board list is limited to its implemented providers; entering an arbitrary website in `boards` does not add an adapter.

### 4. Add a genuinely new direct site

The current normal discovery path requires a **registry change**, not merely a new allow-list name:

1. Identify the actual public search/listing URL and test how query/location parameters work.
2. Add an entry to `src/applypilot/config/sites.yaml` under `sites`, preserving existing entries:

```yaml
  - name: "Example Careers"
    url: "https://careers.example.org/jobs?q={query_encoded}&location={location_encoded}"
    type: search
```

Use `type: static` with a literal listing URL when no query template exists. The example domain is a placeholder, not a working source.

3. Add a matching `base_urls` entry if enrichment must resolve relative links for that source.
4. Rebuild/recreate the Docker service so the modified packaged registry is available; then add the exact name to the workspace's `smart_sites` list.
5. Run only that source with one role/location; inspect extracted title, employer, URL, location, description, duplicates, and rejection reasons before broadening it. Different sites may require adapter-specific extraction work.

This changes the installed source catalog for that deployment. A workspace-local `sites.yaml` is not currently loaded by this path. A user-owned Sources editor is the proposed improvement in F09. For Workday, the analogous current registry is `src/applypilot/config/employers.yaml`, with an employer's actual endpoint/site details following existing entries.

## Proposed Pipeline workspace layout

Keep the visual language of the new workspace, but give Pipeline its own native page:

1. **Run header:** current state, one primary Run/Resume action, Stop while active, last outcome.
2. **Campaign:** selected role families, resume mappings, locations and enabled sources; summary of overrides.
3. **Workflow cards:** “Find & assess,” “Prepare selected applications,” and “Custom stages.” Use “Prepare documents” for the existing Apply-only preset.
4. **Stage timeline:** Discover / Enrich / Score / CV / Statement / Cover / PDF, with queued, succeeded, failed, excluded, and retryable counts.
5. **Run preview:** effective source/query count, eligible job count, sample resume routes, missing inputs, document requirements, and estimated work. Approximate dollar costs only after provider pricing and measured token usage are available.
6. **Advanced drawer:** workers, stream mode, thresholds, source budgets, query overrides, and validation policy. Preserve the full effective configuration with each run.
7. **Problems queue:** structured failures, explanation, retry one/retry eligible failures, and no-progress diagnosis. Raw logs and recent runs in a secondary console.

Use stacked cards and a drawer on narrow screens, rather than a horizontal strip of CSV inputs. Accessibility acceptance: labelled inputs, keyboard navigation, non-colour status cues, and a usable layout at phone/tablet/desktop sizes.

## Recommended implementation order

| Phase | Deliverables | Completion criteria |
|---|---|---|
| **1. Reliability** | Shared scoped eligibility; PDF convergence; streaming dependency resolution/backoff; statement scope/threshold; retryable scoring; truthful results | Fixtures for all P1 orchestration/scope failures become correctness tests; sequential and streaming produce the same eligible artifacts and terminate |
| **2. Configuration integrity** | Lossless search editing; explicit role priority; resume-route preview/provenance; stale-input invalidation | One-location edits preserve advanced keys; Data Analyst coverage and resume choice are visible before a run; scoring/writing share the chosen resume fingerprint |
| **3. User-owned sources** | Workspace registry, source CRUD/test, employer preservation, provider capabilities, coverage feedback | A custom source works without editing package files; source and employer stay distinct; errors and rejected rows are explainable |
| **4. Native Pipeline UX** | Campaign picker, run preview, stage timeline, advanced drawer, problem queue | Main flow works without CSV knowledge; partial/failing runs have actionable outcomes; responsive and keyboard checks pass |
| **5. Evidence-driven intelligence** | Multi-variant evaluation where useful, cost/quality measurements, application-specific document requirements, campaign outcome analytics | Actual measurements drive model/resume recommendations; generated claims remain tied to professional evidence |

### Feature recommendations worth adding

- **Search campaigns/personas:** save “Application Support,” “Data & BI,” and “Public-sector IT” with separate queries, sources, resume rules, requirements, and budgets. Run individually or together with overlap/deduplication tracking.
- **Why this job / why not:** show discovery filters, fit evidence, missing essentials, sponsorship/salary signals, chosen CV, and precise next action in one inspector.
- **Source health and coverage:** last success, parse failures, blocked/rate-limited targets, new relevant yield, duplicates, and role coverage. Rotate capped search windows without starving lower-priority roles.
- **Requirement-to-evidence matrix:** map job criteria to verified CV/professional facts, flag genuine gaps, and collect user evidence once for reuse. Build on existing tailoring maps rather than duplicate them.
- **Selective second opinions:** evaluate alternate resume families or a stronger reviewer only for borderline/ambiguous jobs, with visible evidence and bounded cost.
- **Durable run checkpoints:** resume interrupted work by job/stage/input fingerprint, with explicit retry policy and cancellation state.
- **Application packets:** generate only the CV, statement, cover, and answers required by that posting; readiness derives from the packet, not generic path presence.
- **Outcome learning:** connect campaign/source/resume/model versions to interviews and offers, with sample size and date cohorts. Keep recommendations cautious until enough outcomes exist.

## Verification and remaining limits

- Offline diagnostic run reproduced **14 behavioral findings** and captured one configuration snapshot (15 report entries). Database changes and generated fixture files were confined to isolated memory/temporary storage.
- Search-builder loss of advanced keys, cover visibility filtering, Workday error propagation, and the UI layout were traced directly through source. They are not presented as live production incidents.
- No discovery run or paid inference was used. No live employer/source reachability, model-output quality ranking, or dollar-cost claim was established.
- Production searches, resume content, and database records were inspected only where needed and were not rewritten during this audit.
- Repository verification outcome is recorded in the implementation `status.json`.
- Full gate passed: **349 tests with Playwright enabled**, compilation, **7 AI regression checks**, and **13 formal evaluation cases**. Passing existing tests does not negate the newly reproduced gaps; Phase 1 should add correctness regression coverage for them.
