# Job Search Platform Discovery and New Zealand Readiness

- **System:** ApplyPilot 0.2.0
- **Review date:** 16 October 2026
- **Discovery status:** COMPLETE — repository-level discovery; runtime and third-party availability are unverified
- **Overall NZ readiness:** MEDIUM
**Recommended direction:** A small hybrid: existing shared engine and country/campaign configuration, with explicit NZ-specific policy and evidence handling.

## Review scope and evidence conventions

This is an investigation of the **current working tree**, including existing modified and untracked application files. The checkout's HEAD is `36839ea49c4d6b6c3ec5a72c2b6f0a7861bf3621`; HEAD alone does not represent the reviewed application. Git status showed substantial pre-existing development work. This report is the sole deliverable created by this review.

Repository instructions reviewed: `AGENT_INSTRUCTIONS.md`, `ops/repo_status.json`, the active `ops/implementations/20261010-video-production/status.json`, `docs/FMEA_POLICY.md`, `README.md`, and relevant contributor/workflow guides. No `AGENTS.md` was found within the repository. Operational implementation records describe completed work; they are historical evidence, not a substitute for code inspection.

**Evidence labels used below:**

- **Verified:** present in inspected source, schema declarations, routes, or configuration. This means verified by static inspection, not demonstrated against a live employer/provider.
- **Inferred:** an architectural implication of those implementation details.
- **Not found:** no corresponding implementation was located in the inspected source/configuration. This does not claim anything about private external services.
- **Unverified:** requires runtime, external access, operational information, or product-owner input.

The review traced Python modules, web assets, persistence declarations, packaged configuration, documentation, and relevant test sources. It did not open personal workspace databases, CVs, messages, credentials, or `.env` contents. No application startup, provider request, browser application, migration, dependency installation, deployment, or application test/evaluation run was performed. Startup helpers such as `init_db()` automatically make additive schema changes; avoiding application execution preserved the investigation-only boundary. Existing tests are cited as coverage evidence, not reported as newly passing. Documentation-only verification and final Git inspection are described at the end.

**Evidence path convention:** package module/asset paths such as `geography.py`, `apply/prompt.py`, `web/workspace.js` and `config/sites.yaml` are relative to **`src/applypilot/`**. Paths beginning `docs/`, `tests/`, `scripts/`, `ops/` or `.github/`, and named root project files such as `pyproject.toml` and `docker-compose.yml`, are relative to the repository root. Named functions and classes are the primary reference anchors; selected line numbers locate especially important findings in this reviewed working tree.

## Contents

1. [Executive summary](#1-executive-summary)
2. [Existing system architecture](#2-existing-system-architecture)
3. [Current feature inventory](#3-current-feature-inventory)
4. [Job discovery workflow](#4-job-discovery-workflow)
5. [CV management and tailoring workflow](#5-cv-management-and-tailoring-workflow)
6. [Supporting statement and cover-letter workflow](#6-supporting-statement-and-cover-letter-workflow)
7. [Application management lifecycle](#7-application-management-lifecycle)
8. [User profile and employment-data architecture](#8-user-profile-and-employment-data-architecture)
9. [AI features and orchestration](#9-ai-features-and-orchestration)
10. [Country and internationalisation support](#10-country-and-internationalisation-support)
11. [Existing UK-specific assumptions](#11-existing-uk-specific-assumptions)
12. [New Zealand integration readiness](#12-new-zealand-integration-readiness)
13. [Reusable components and gap-analysis table](#13-reusable-components-and-gap-analysis-table)
14. [Missing capabilities](#14-missing-capabilities)
15. [Architectural options](#15-architectural-options)
16. [Risks and constraints](#16-risks-and-constraints)
17. [Recommended integration direction](#17-recommended-integration-direction)
18. [Questions requiring product-owner decisions](#18-questions-requiring-product-owner-decisions)

## 1. Executive summary

ApplyPilot is a Python job-search, document-preparation, and application-management application. It combines a Typer CLI with a native browser workspace and a separate browser-driven submission subsystem. It is substantially more capable than a simple job-board scraper: it has multi-user workspace isolation, multiple source CVs, professional-fact editing, deterministic CV routing, AI fit scoring, validated tailoring, versioned writing studios, application timelines, email evidence review, reminders, analytics, and a visible assisted application browser.

**Five principal conclusions:**

1. **NZ search geography is already represented.** `geography.py` recognizes `NZ`, `New Zealand`, and the JobSpy market `new zealand`. Structured country/state/city/remote searches and campaign-specific routing already exist. `tests/test_global_search_and_visuals.py::test_country_aliases_use_runtime_identifiers` includes NZ. This is routing capability, not evidence that every configured board currently returns NZ vacancies.
2. **The three requested NZ master-CV categories can reuse the existing library and routing engine.** Variants are stored in `resume_variants/*.txt`; `role_routing.py::route_resume_for_job()` supports explicit title mappings, weighted keyword families, manual overrides, ambiguity handling, and sector blends. Shared professional facts are supplied by `professional.py::enrich_evidence()`.
3. **Country-specific work rights and employer evidence are the largest functional gaps.** The profile has one global work-authorisation object. Enrichment calls the **UK** sponsor-register annotator without a country guard (`enrichment/detail.py:815–821`). There is no NZ accreditation, AEWV, overseas-applicant, or unrestricted-NZ-rights policy implementation. UK licence evidence must not be interpreted as NZ accreditation or a vacancy-level sponsorship offer.
4. **Existing truthfulness safeguards reduce risk but do not establish a fabrication-free guarantee.** Default citation/numeric checks can warn instead of block; the reviewer permits “minor stretches”; deterministic project fallbacks can supply unsupported achievement wording. These conflict with the README's absolute “never fabricates” claim. See section 5.5.
5. **Application safety depends on the execution path.** The integrated assisted browser requires document approval and page-bound final approval for one submission attempt, and preserves uncertain outcomes. The older Claude CLI engine can submit autonomously by default; scheduled submit mode uses the older LLM engine. The new browser's approval guarantees do not apply globally.

**Recommendation:** retain the shared engine and canonical professional profile. Use country/campaign configuration for locations, sources, and role-to-CV mapping; add a bounded NZ policy/evidence layer and a country-scoped user experience only where actual NZ requirements exceed configuration. A separate application/backend, duplicated employment history, or additional AI orchestration framework is not justified by the inspected implementation.

**Readiness interpretation:** high reuse for general search, documents, and tracking; low readiness for verified NZ sponsorship/accreditation decisions; medium overall. The platform can form the foundation of the workflow, but is not currently an end-to-end NZ immigration-aware application system.

## 2. Existing system architecture

### 2.1 Technologies and component boundaries

| Layer | Verified implementation | Evidence |
| --- | --- | --- |
| Runtime/package | Python >=3.11, package version 0.2.0, Hatchling build, AGPL-3.0-only | `pyproject.toml` |
| CLI | Typer/Rich; `applypilot` entry point; init, preparation pipeline, apply, status, workspace transfer, sponsor update | `cli.py`, `wizard/init.py` |
| Frontend | Static HTML/CSS and vanilla JavaScript; hash-based views; no React/Vue/Next build pipeline found | `web/workspace.html`, `web/workspace.js`, `web/pipeline.js`, `web/studios.js` |
| HTTP server | Python `ThreadingHTTPServer` / `BaseHTTPRequestHandler`; handwritten JSON routes and static asset handling | `dashboard_server.py::_Handler`, `serve_dashboard()`; `command_api.py` |
| Domain services | Python modules for command center, pipeline, studios, profile, discovery, lifecycle, browser and mail | `command_center.py`, `pipeline_workspace.py`, `writing_studio.py`, `apply/` |
| Database | SQLite via standard-library `sqlite3`; thread-local connections, WAL, busy timeout; handwritten SQL; **no ORM** | `database.py::get_connection()`, `init_db()` |
| User documents/configuration | Local workspace files, JSON/YAML, originals and generated artifacts; SQLite stores metadata, revisions and references | `config.py`, `setup_workspace.py`, `cv_library.py` |
| Browser automation/rendering | Playwright Chromium; older Claude Code + Playwright MCP path; Chrome worker isolation | `apply/assisted_browser.py`, `apply/llm_engine.py`, `apply/launcher.py`, `apply/chrome.py`, `scoring/pdf.py` |
| AI access | Application-owned HTTP client/provider adapters and context-local task routing | `llm.py`, `ai_routing.py`, `ai_settings.py` |

### 2.2 Communication diagram

```text
Candidate browser
  /workspace + web/*.js
      | JSON fetch; CSRF header in multi-user mode
      | SSE application events; streamed browser frames/input
      v
dashboard_server._Handler ---- multiuser.py ---- auth.db
      |
      +-- command_api.py --> command_center / studios / profile / lifecycle
      |                         |
      |                         +--> user's applypilot.db + workspace files
      |
      +-- _PipelineRunner --> subprocess: python -m applypilot run/apply
      |                           |
      |                           +--> pipeline.py, eligibility.py
      |                                discover -> enrich -> score -> tailor
      |                                -> statement -> cover -> pdf
      |                                      |
      |                              same user's DB/files
      |
      +-- assisted_browser.Session --> owned Chromium --> employer portal
      |        | reviewed values / forms AI          | approval / receipt
      |
      +-- background thread --> scheduler + optional mailbox sync

CLI ------------------------------------------------> same domain services

External edges:
  JobSpy boards / Workday CXS / direct career pages
  Gemini / OpenAI / Anthropic / local compatible LLM endpoint
  UK Home Office sponsor CSV
  Gmail / Microsoft Graph OAuth read connectors
  optional OpenAI Decisions observer
  legacy Claude Code + npx MCP servers / optional CapSolver
```

The browser does not invoke AI providers directly. The server resolves the user's workspace and calls domain services or launches a scoped CLI subprocess. `_PipelineRunner.start()` sets `APPLYPILOT_DIR`, run IDs, campaign and job scopes, optional per-run search YAML, and subprocess logs. SQLite and files provide communication between stages; there is no message broker. Event streams query `cc_events` and emit heartbeats. The assisted browser has its own owned session registry, command handling and durable records. Evidence: `dashboard_server.py:1147–1337,2334–2378`, `command_api.py`, `pipeline.py`, `apply/assisted_browser.py`.

### 2.3 Persistence and data model

**Two database levels:** `multiuser.py` maintains a separate `auth.db`; each candidate workspace has its own `applypilot.db`. Candidate rows do not carry a tenant ID because isolation is by workspace path/database. CLI/single-user mode uses `APPLYPILOT_DIR` or the selected home workspace (`config.py::_select_app_dir()`).

| Store / table group | Data and relationships |
| --- | --- |
| Auth: `users`, `sessions`, `admin_audit_log` | Account contact details, active/admin flags, password hashes, hashed expiring sessions and administrative actions. |
| `jobs` | URL primary key; employer/title/search query/location/salary/source; description/application URL; sponsor signals; fit assessment; selected route; document paths and hashes; preparation and execution status/attempt metadata. |
| `blocked_urls`, `cc_blocks`, `cc_preferences` | Legacy prefix exclusions; reversible job/domain/employer blocks; workspace application exclusions and remote-only preference. |
| `cc_job_meta`, `cc_events`, `cc_opportunity_registry`, `cc_activity_schema` | Hiring state, visibility, priority, notes, reminder/deadline, recruiter/reference/evidence, submitted CV document ID; append-style activity timeline, execution-change trigger, retained opportunity identity snapshots and activity-schema version. |
| `cc_documents`, `cc_profile_revisions` | Generated CV registry, input fingerprints and variant identity; saved profile JSON revisions. |
| `cc_cv_sources`, `cc_cv_revisions` | Source CV metadata/original-file references and content revisions. |
| `cc_portfolio_assets` | Project/profile evidence-file metadata and archive status. |
| `cc_writing_documents`, `cc_writing_requests` | Draft families/parent versions, source context, validation, provider/model and publication state; generation-budget reservations. |
| `cc_pipeline_runs`, `cc_pipeline_stages`, `cc_pipeline_items` | Run/stage/item states, configuration snapshots, results and input hashes. |
| `cc_source_health`, `cc_campaign_jobs`, `cc_variant_evaluations`, `cc_score_history` | Discovery counters, campaign membership, optional CV comparisons, archived assessments. Campaign definitions themselves live in YAML. |
| `cc_ai_cache`, `cc_ai_usage`, `cc_pipeline_usage` | Context-dependent assistant cache and separate interactive/pipeline token ledgers. |
| `cc_answers`, `cc_controls`, `cc_schedules`, `cc_listing_checks` | Reusable candidate-approved answers, worker checkpoint requests, durable per-job application schedules and availability observations. |
| `cc_messages`, `cc_inbox_rules`, `cc_message_identities` | Imported mail, deterministic/AI classification, proposed outcomes/dates, review status, duplicate provider/message identities and sender exclusions. |
| `cc_mail`, `cc_oauth` | Encrypted connector tokens and OAuth/PKCE state; initialized by connector code. |
| `cc_browser_sessions`, `cc_browser_attempt_history` | Durable assisted-browser attempts and associated navigation/activity observations; initialized when browser/history features are used. |
| `cc_submission_reconciliations` | Candidate-checked resolution of an uncertain submission, source/evidence/reference and recorded date. |
| `cc_job_closures`, `cc_retired_jobs`, `cc_cleanup_files` | Reasoned closure, retained deleted-opportunity identity/snapshot, guarded generated-file cleanup and retry bookkeeping. |
| `cc_decision_runs`, `cc_decision_observations` | Optional observer benchmark and page-classification measurements; not application decisions. |

Evidence: `database.py::init_db(),_ALL_COLUMNS`; `command_center.py::init_schema()`; `pipeline_store.py::init_schema()`; `pipeline_workspace.py::invalidate_scores()`; `cv_library.py::init_schema()`; `writing_studio.py::init_schema()`; `evidence_inbox.py::init_schema()`; `inbox_identity.py`; `mail_connectors.py::init()`; `apply/assisted_browser.py::_schema()`; `opportunity_insights.py::init_browser_history()`; `job_lifecycle.py::init_schema()`; `submission_reconciliation.py::init_schema()`; `decision_observer.py::schema()`.

There are no normalized employer, country, occupation, employment-history, or education tables. Most domain relationships use `job_url` and application code rather than an ORM-enforced graph. Jobs have **one current set of document pointers and one route override**, with historical versions held separately. Forward migrations are `CREATE TABLE IF NOT EXISTS`, conditional `ALTER TABLE`, indexes/triggers and additive helpers, automatically invoked through `init_db()`. There is no versioned migration framework in the inspected dependency list.

### 2.4 Authentication, authorisation and roles

- **Single-user mode:** no dashboard login requirement; the workspace is trusted locally. CLI operations use filesystem access.
- **Multi-user mode:** local registration/login, salted PBKDF2-HMAC-SHA256 password hashes (120,000 rounds), random session tokens stored as SHA-256 hashes, default 30-day expiry, active-account checks. First registration becomes admin. Evidence: `multiuser.py::_password_hash(),create_user(),authenticate(),create_session(),get_user_by_session()`.
- **Roles:** regular candidate and administrator (`is_admin`); no recruiter, coach, reviewer, organisation, or delegated approval role found. Admins manage users and inspect admin audit logs. Mutations include enable/disable, password reset, promote/demote and deletion, with safeguards around the current/last admin (`multiuser.py`, `dashboard_server.py` admin routes).
- **Request protection:** authenticated workspace routing, admin checks, same-origin checks and double-submit CSRF validation on protected POSTs; login throttling. Browser sessions are additionally workspace-owned. Evidence: `_Handler._current_user(),_origin_allowed(),_csrf_ok(),do_GET(),do_POST()` and `apply/assisted_browser.py::_owned()`.
- **Security boundary:** workspace isolation is logical filesystem isolation inside one service process/container, not a separate operating-system sandbox per user. Session cookies are HttpOnly/SameSite=Lax but intentionally lack `Secure` for local HTTP (`dashboard_server.py::_session_cookie()`). TLS/reverse-proxy deployment is not provided by Compose. See section 16 for implications of wider hosting.

### 2.5 Background work, deployment and configuration

**Execution:** dashboard preparation runs use subprocesses, with one active `_PipelineRunner` per workspace. Pipeline stages can run sequentially or as stage threads in streaming mode. Discovery/enrichment use bounded worker concurrency; LLM writing/scoring is predominantly per-job sequential. Streaming stops after five no-progress passes and records stalled/cancelled states. Jobs and stage metadata act as the durable queue. There is no Celery/Redis/SQS/cron service found. Evidence: `pipeline.py::_run_sequential(),_run_streaming(),_run_stage_streaming()`; `dashboard_server.py::_PipelineRunner`.

**Scheduling:** `background.py` runs per-workspace daemon threads while the dashboard server is alive, checks schedules about every 10 seconds, and polls connected mailboxes about every five minutes. `scheduler.py` schedules an existing job's application preparation/submission, **not recurring vacancy searches**. Queued schedules survive restarts; interrupted running attempts become `needs_review`, not automatically replayed. Daily scheduled-attempt default: 20.

**Deployment:** local pip/source workflows plus a Python 3.11-slim Docker image. Docker installs the package, pinned `python-jobspy==1.2.0`, its import smoke check, Playwright Chromium/system dependencies. Compose runs `dashboard-serve --host 0.0.0.0 --port 8765 --multi-user`, maps port 8765, persists workspace/multi-user/transfer directories, uses UID/GID 1000 and `restart: unless-stopped`. The image does not install Node.js/npx or Claude Code; therefore the advertised older Claude/MCP path is not fully provisioned by this Dockerfile. The integrated assisted browser uses installed Chromium. Evidence: `Dockerfile`, `docker-compose.yml`, `cli.py::dashboard_serve()`, `apply/launcher.py::_make_mcp_config()`.

No production/staging environment definitions, infrastructure-as-code cloud deployment, or populated CI workflow under `.github` were found. The deployed version, live configuration, provider credentials, backups and uptime remain unverified. The source configuration is not proof of a running service.

**Configuration hierarchy:**

- `config.py`: workspace selection, path constants, `.env` loading, package defaults and dependency-based tiers.
- `profile.json`: shared candidate facts, work rights/availability/compensation, tailoring and validation preferences.
- `searches.yaml`: role queries/tiers, structured places, boards, budgets, exclusions, routing, campaigns and optional prices.
- Packaged `config/sites.yaml` / `config/employers.yaml`, merged with workspace-owned catalogs by `source_registry.py`.
- Per-run search overrides and environment flags for source skips, job/campaign selection, selected-only processing and regeneration.
- `ai_settings.py`: context-local settings plus `.ai-settings.json`, encrypted workspace keys, explicit service-key inheritance; public APIs expose presence/origin, not key values.
- Features are gated by configuration and runtime checks rather than a central flag service: geography selected/legacy mode; strict/lenient tailoring; source budgets/skips; provider task routing; optional Decisions observer. `config.py::get_tier()` is a dependency heuristic, not a role or subscription system.

**Documentation/decisions:** current architecture is primarily documented in `docs/COMMAND_CENTER.md`, `PIPELINE_WORKSPACE.md`, `STUDIOS.md`, `GLOBAL_LOCATIONS_AND_VISUAL_INSIGHTS.md`, `ASSISTED_BROWSER.md`, `SUBMISSION_RECONCILIATION.md`, `JOB_LIFECYCLE.md`, `PIPELINE_AUDIT.md`, `UPGRADE_PLAN.md`, plus per-implementation plans/status/FMEA under `ops/implementations/`. No standalone ADR collection was found. `CONTRIBUTING.md` contains older module/command examples; README's autonomous CLI marketing also predates parts of the served workflow. Source takes precedence.

## 3. Current feature inventory

| Capability | Current implementation and important boundary |
| --- | --- |
| Vacancy discovery | JobSpy, registered Workday employers, smart direct-source extraction; editable campaigns, budgets and source previews. No packaged NZ-specific board adapter found. |
| Search management | Saved YAML configurations/campaigns, query tiers, board overrides, effective previews, run/source history. No recurring-search scheduler found. |
| Geographic search | Country/state/city/remote place objects, provider-market routing and selected-location post-filtering; raw advert locations remain text. |
| Relevance | AI 1–10 fit scores, confidence/reasoning, threshold queues, deterministic route candidates, requirement/coverage reports and optional CV comparisons. |
| Profile/portfolio | Editable shared professional facts, qualifications, skills, projects and evidence attachments; user-asserted facts are not independently certified. |
| CV library | Multi-file upload, extraction, originals, variants, revisions, editing, preview, duplicate/rename/archive/default selection. |
| CV generation | Profile-to-CV builder and per-vacancy AI tailoring with deterministic assembly, multiple drafts, validators and optional reviewer. |
| Writing | Versioned cover and supporting-statement studios, manual drafts, criteria/example planner, tone/word/character controls, export and explicit publication. |
| Application assistance | Manual links/downloads, visible approval-based browser, older assisted LLM and autonomous Claude CLI engines. |
| Application management | Pick/unpick, priorities, notes, visibility, separate execution/hiring states, timelines, documents, reminders, schedules, closures and retained deleted history. |
| Inbox | Manual import, Gmail/Outlook read OAuth connectors, deterministic classification, opt-in AI proposals and candidate-reviewed updates. |
| AI assistance | Explain fit, error diagnosis, copilot summary/next actions, interview preparation, follow-up drafts and evidence-cited form-answer drafts. |
| Analytics | Unique-opportunity cohorts, source/outcome/fit/location activity charts, submission timing, token/cost coverage and CSV/JSON/ZIP exports. |
| Portability | UI backup and CLI export/import, SQLite backup, variant/artifact path rebasing. Browser-session evidence directory is not in the generated-directory export list. |

Evidence: `command_api.py` route inventory; `dashboard_server.py` setup/pipeline/legacy routes; `web/workspace.js`, `web/pipeline.js`, `web/studios.js`; named domain modules in sections 2–9. Export boundary: `workspace_transfer.py::GENERATED_DIRS,export_workspace()`.

## 4. Job discovery workflow

### 4.1 Query-to-display journey

```text
Pipeline search editor / selected campaign / CLI searches.yaml
    -> validation + effective preview
    -> _PipelineRunner creates scoped CLI run and optional search override
    -> pipeline._run_discover()
       + JobSpy: roles x places x boards
       + Workday: roles x registered employer portals
       + Smart extract: template/static targets x selected roles/places
    -> engine-specific title / location / salary filtering
    -> normalized URL deduplication + blocks / retired identity checks
    -> jobs + campaign/source provenance + source-health counters
    -> enrich missing descriptions / application URLs
    -> route source CV + shared evidence -> AI fit scoring
    -> command_center.overview() -> /api/center
    -> native Jobs/Applications views ordered by priority, fit, discovery date
       + detail, packet, source/route/gap inspection and live event updates
```

Evidence: `web/pipeline.js`; `pipeline_workspace.py::merge_config(),preview(),campaign_config()`; `_PipelineRunner.start()`; `pipeline.py::_run_discover()`; `command_center.py::overview()`; `web/workspace.js::renderJobs(),filteredJobs(),openDetail()`.

### 4.2 Providers and geographic limits

**JobSpy.** `discovery/jobspy.py::run_discovery()` normalizes historical YAML formats, applies board overrides, and expands configured queries across places. `_run_one_search()` calls third-party `scrape_jobs()` with board, term, location, result count, `hours_old`, Markdown description format, per-place `country_indeed`, optional proxy/remote flag, and LinkedIn description fetching. Glassdoor is queried separately with a simplified location. This is third-party scraping through JobSpy, not a set of first-party job-board API contracts.

Configured/validated boards are Indeed, LinkedIn, Glassdoor, ZipRecruiter and Google. `geography.py::board_scope()` suppresses Indeed/Glassdoor for unknown markets and ZipRecruiter outside US/Canada. NZ is a recognized market; country coverage is still provider/runtime-dependent. No live result-volume, access or terms validation was performed. JobSpy is installed separately by Docker/bootstrap; it is not a required package entry in `pyproject.toml`.

**Workday.** `discovery/workday.py::workday_search()` POSTs to employer CXS endpoints, with empty facets and keyword text, then paginates (20 per page, maximum 25 pages). `workday_detail()` retrieves descriptions/direct URLs; location filtering operates on returned `locationsText`, not country request facets. Employer catalogs are editable through `source_registry.py::save_employer()`. This registry identifies portals, not accredited employers. `posted`, requisition ID and remote metadata are read into transient dictionaries, but `store_results()` does not persist them as structured freshness/remote/requisition fields. Salary is stored as null on this path.

**Direct sites.** `discovery/smartextract.py::build_scrape_targets(),_run_one_site()` and `source_registry.py` support static listing pages and role/location URL templates. Playwright collects JSON-LD, network/API responses, candidate cards and DOM metadata. Known selectors are tried first; AI can judge relevant API responses, select an extraction strategy, and produce CSS selectors. Application code executes known strategy types rather than arbitrary model-generated Python. Configured sources include UK public-sector/health/job sites, Canadian sites, and global remote/tech boards. Adzuna is a configured web source, not an implemented licensed Adzuna API integration.

`geography.py::source_country()` infers UK/Canada from selected domains or accepts a source country field. It does **not** infer `.nz`; the source-saving validator's clean output also does not expose a new country field. Templates may use `{country_encoded}`, `{state_encoded}`, `{city_encoded}`, `{location_encoded}` and role placeholders. Country-specific domains on generic top-level domains (for example, HealthJobsUK) are not reliably scoped merely by the present suffix heuristic. These are source-catalog limitations, not a general prohibition on NZ sources.

### 4.3 Search/filter capability audit

| Requested concern | Verified behaviour / limitation |
| --- | --- |
| Keywords | Free-text role queries, priority tiers and board-specific query subsets. Direct-site title matching has additional role-family compatibility heuristics. No semantic global search index. |
| Countries/locations | Broad country catalog including NZ; free-text structured places; per-place provider country; selected-location or legacy substring filters. Unknown locations are retained. No geocoding/radius engine or authoritative city hierarchy; metadata such as distance may be preserved without being used in provider requests. |
| Categories/occupations | Query lists, role packs and heuristic families. No SOC/ANZSCO/NZ occupation-classification model or code lookup found. |
| Remote/hybrid/onsite | Remote request flags, textual/returned remote markers and workspace remote-only exclusions. No canonical three-state work-arrangement column or consistent hybrid-only/onsite-only request filter. Selected-location matching treats “hybrid” as remote-like in its text heuristic (`geography.py::SelectedLocations.matches()`). |
| Salary | JobSpy/direct filtering via `salary_filter.py`; annualised range-overlap comparison, retaining unknown/different-currency structured values. Uses global profile compensation. Direct text parsing has GBP-specific currency bypasses and does not robustly parse `k` shorthand or distinguish every dollar currency. No FX service. Workday storage lacks salary filtering data. |
| Employers | Workday employer selection; board keywords; manual employer/domain blocks. No common provider-level employer inclusion filter or normalized employer directory. |
| Visa sponsorship | UK text signals and Home Office matches during detail enrichment. Legacy summary API/static dashboard support sponsorship filter values; native `cc.overview()`/Jobs toolbar does not expose an equivalent NZ or general sponsorship facet. No sponsorship hard gate in `eligibility.py::exclusions()`. |
| Deduplication | Canonical URL primary identity; `find_existing_job_url()` checks listing/application URL variants. Indeed identity query keys retained; most other query strings stripped. Not semantic cross-board merging. Same-title/employer duplicates are shown as warnings in record details. |
| Freshness | JobSpy sends `hours_old`; `discovered_at` is a local first-insert timestamp. Source URLs may contain age restrictions. No general persisted posting date, expiry date, last-seen date or `validThrough` pipeline found. Duplicate discovery generally does not refresh description/freshness. |
| Expiry | Manual `check_listing()` observes 404/410, selected closed-page phrases, reachable or unknown states; scheduler checks before launch. HTTP 200 is explicitly not proof applications remain open. No universal periodic expiry sweep. |
| Search history | Saved searches/campaigns in YAML; run snapshots/logs, stage results, source-health counters and job search-query/provenance fields. Not a separately normalized personal search-query history/favourites model. |
| Automated searches | User-triggered pipeline and streaming runs; no recurring vacancy-search schedule found. `apply --continuous` polls the application queue, not boards. |
| Recommendations/ranking | AI fit score against routed CV/shared facts, then priority/fit/date sort and threshold preparation. Copilot can suggest next actions. No collaborative recommender, learned preferences model or external recommendation feed. |
| Incomplete descriptions | JobSpy/Workday descriptions over 200 chars can be promoted directly. Others enter Playwright enrichment. Clean page-text fallback survives AI failure. “Full description” is a field/state, not a completeness certification; short/partial context can reach scoring. |
| URLs/attribution | Separate listing/application URLs; source site, strategy, search query, source ID/provenance and campaign membership. Application link may fall back to listing; direct-employer ownership is not verified. |

Evidence: `discovery/jobspy.py::_normalize_search_cfg(),_full_crawl(),_run_one_search(),store_jobspy_results()`; `discovery/workday.py::search_employer(),_fetch_one_detail(),store_results()`; `discovery/smartextract.py::_store_jobs_filtered(),build_scrape_targets()`; `database.py::normalize_url(),find_existing_job_url(),store_jobs()`; `pipeline_store.py::tag_job(),record_discovery()`; `command_center.py::details(),check_listing(),preference_exclusions()`; `dashboard_server.py::_job_summaries()`; `web/workspace.js::renderJobs()`.

### 4.4 Enrichment and scoring details

`enrichment/detail.py::scrape_detail_page()` uses a three-tier cascade: JSON-LD, deterministic CSS/content/apply-link extraction, then structured AI extraction. It records partial/error states and retains cleaned page text if inference fails. It handles relative-source URL resolution and some site-specific apply-link traps. Source-specific delays, transient HTTP handling and browser timeouts exist. It does not establish that a link belongs to the employer or that all person-specification attachments were retrieved.

`scoring/scorer.py::score_job()` sends the CV and a job excerpt (description truncated to 6,000 characters) to `SCORE_PROMPT`. Output is score 1–10, keywords, reasoning and confidence. `run_scoring()` routes the CV, adds professional evidence, rejects ambiguous routes, stores assessment metadata and fingerprints, and schedules bounded retries. Provider account/configuration failures defer the remaining batch without repeatedly charging calls. A score is a model's fit judgement, **not** an ATS percentage or sponsorship assessment.

Geographic restrictions therefore exist at several independent points: saved legacy filters, source domains/templates, provider coverage, role/title filtering, profile/application preferences, and older application prompts. Changing the search country alone does not remove all of them.

## 5. CV management and tailoring workflow

### 5.1 Upload, parsing, storage and editing

`POST /api/center/cv/import` accepts up to 20 base64-encoded files per request; each file has a 12 MB limit. The HTTP request itself is capped at 24 MB, so many large files cannot all fit in one request. `cv_library.py::extract()` supports:

- TXT/MD: UTF-8 text.
- PDF: file signature, unlocked/text-based PDF, at most 50 pages, text extraction via pypdf; image-only PDFs fail explicitly. No OCR service found.
- DOCX: ZIP/XML paragraph extraction with decompression/member/document-size bounds. No rich Word layout preservation in extracted text.

Successful originals are saved under `cv_uploads/`; collision-safe named text variants under `resume_variants/`; source metadata and text revisions in `cc_cv_sources` / `cc_cv_revisions`. Per-file errors do not discard successful imports. The default `resume.txt` remains separately selectable. Naming a target role can update title routing. Users can edit sources, restore revisions, duplicate/rename/archive, select a default, download originals/text/PDF, and build editable CV text from the profile. Evidence: `cv_library.py::import_files(),action(),build_profile_cv(),suggestions(),export_pdf()`; `setup_workspace.py::write_resume_text(),write_resume_variant()`; `command_api.py` CV routes.

Parsing is mainly **text extraction and literal section heuristics**, not AI reconstruction of a fully normalized employment record. `suggestions()` proposes selected contact/summary/skill/project values for review. It does not automatically replace verified history or infer qualifications. Structured history is separately edited in profile/CV Studio.

### 5.2 Master CV routing

`role_routing.py::route_resume_for_job()` chooses a source using configured variants and role families:

1. Explicit per-job override when routing is active.
2. Longest matching saved title phrase.
3. Keyword-family matching (title hits weighted three, other job-text hits one).
4. Optional sector/context override or role/context blended route.
5. Default CV, with explicit ambiguity/unavailable-route metadata where applicable.

Equal top family matches require user attention rather than silently choosing one. Inferred filename rules are available when no routing config exists, but should be reviewed. Routing is deterministic, not an AI agent. Optional one/two-variant AI comparisons are user-triggered, cached, budgeted, and do not automatically change the chosen route (`pipeline_workspace.py::evaluate_variants()`).

Three variants for software/business applications, data/BI and support can be configured without changing this mechanism. Country scoping would currently be by campaign and chosen variant names, not a first-class CV-country attribute.

### 5.3 End-to-end tailoring

```text
Eligible enriched vacancy + current fit assessment
    -> deterministic master-CV route / explicit override
    -> professional.enrich_evidence(shared profile, role family)
    -> role pack + adaptive bullet budget
    -> evidence fact library + responsibilities + requirement gaps + keywords
    -> 2–3 JSON draft candidates per attempt
    -> sanitize / deterministic assembly / citation & numeric checks
    -> rank drafts by coverage, readability and validation penalties
    -> requirement gates + full validation + reviewer (unless lenient)
    -> unique versioned TXT + diagnostic JSON; approved current pointer only
    -> PDF rendering, document registry, stale-input/packet checks
    -> candidate preview / download / source correction / regeneration
```

Evidence: `scoring/tailor.py::tailor_resume(),assemble_resume_text(),run_tailoring()`; `scoring/tailor_strategy.py`; `scoring/validator.py`; `pipeline_store.py::persist_document()`; `command_center.py::register_document()`; `apply/application_packet.py`.

The model produces JSON sections; code injects identity/contact details, assembles layout, and draws education/certification/environment/project facts from profile sections. Shared professional entries are prepended and deduplicated by exact casefolded header, so duplicate or differently worded roles can still coexist. The top CV headline may match the target role. Default title mode is `factual`; `balanced` and `aggressive` modes permit differing degrees of current-role title rewriting.

### 5.4 Matching, missing skills, “ATS” and review

- **Fit:** `scorer.py::SCORE_PROMPT` is subjective 1–10 role fit.
- **Requirements/gaps:** `tailor_strategy.py::extract_jd_requirements(),evaluate_requirement_gaps()` uses heuristics, skill vocabulary, credential/constraint patterns and synonyms to list matched/missing must-haves, desirable skills, credentials and domains. It is not exhaustive semantic proof of eligibility. Its hard-constraint set explicitly includes UK work authorisation.
- **Coverage:** `score_jd_coverage()` calculates covered target terms / selected target terms. Targets are partly seeded from known skills and keyword overlap, not necessarily all requirements in the advert.
- **Draft rank:** `rank_candidate()` adds `60 × coverage + 20 × readability`, then subtracts capped penalties for validator errors/warnings, citation errors and numeric inconsistencies. This is an internal draft-selection score, not a measured ATS pass probability.
- **Limits:** the job excerpt for tailoring is 6,000 characters; fact-library prompt display is limited to 40 facts, with a larger internal fact set. Requirements beyond those excerpts can be missed.
- **Review/edit:** native users can edit source variants and canonical facts, preview/download generated job-CV versions, and regenerate. No native direct edit-and-publish endpoint for an already generated job-specific CV was found; that should not be confused with source editing or editable writing-studio drafts. Older manual job-match APIs return generated text/history and allow promotion to the default source, with different history semantics.
- **Versioning:** normal job tailoring uses unique versioned artifact names and `cc_documents`; original uploads/source revisions and submitted snapshots are preserved. The older manual matching route writes deterministic artifact names and maintains JSON history, so its version guarantees differ.
- **Exports:** TXT/PDF plus downloadable original uploads; HTML previews. DOCX is an input/original-download format, not a generated DOCX export implementation.
- **ATS-friendliness:** renderer uses real text, normal headings, bullets and a straightforward document flow; A4 output via Chromium. These are sensible parsing foundations, but no external ATS compatibility certification or recruiter/ATS outcome test was found. “Must fit 1 page” is in the generation prompt; PDF rendering does not prove a universal one-page or NZ-specific layout policy.

Evidence: `scoring/tailor_strategy.py:634–738,844–985,1032–1194`; `scoring/tailor.py:480–657,1452–1578,2070–2426,2634–2722`; `scoring/pdf.py::build_html(),render_pdf()`; `web/workspace.js::renderCv(),loadPreview()`; `dashboard_server.py::_run_manual_job_match()`.

### 5.5 Truthfulness controls and material gaps

**Existing controls:** skills boundary and preserved entities; factual title mode by default; generated-field and full-document validators; required/current employer and education checks; known fabrication-tool watchlists; prompt instructions prohibiting invented facts; source fact citations; numeric consistency; requirement gates; reviewer; approval-only pointer publication; candidate preview and review. `professional.py::credential_line()` preserves earned/in-progress/expired labels from structured entries.

**These do not guarantee no fabrication:**

1. `tailor.py::_strict_evidence_enabled()` returns false by default. Citation and numeric errors become `evidence_warnings` unless strict evidence is enabled (`tailor.py:2315–2323`). “Strict” overall mode does not automatically mean strict evidence.
2. `_build_judge_prompt()` explicitly allows up to three minor stretches, related/learnable tools or skills (`tailor.py:771–781`). Lenient mode skips this reviewer entirely and relaxes other gates.
3. `validator.py::validate_json_fields()` warns on unrecognized employer headers rather than rejecting every one. Skills checks rely substantially on a finite watchlist and specific chronic-invention guards, not a full positive semantic allowlist for every claim.
4. `tailor.py::assemble_resume_text()` has fallback project bullets. For example, an empty project bullet list can produce dashboard/SQL/Python achievements from a project name/skills, and project year can be guessed from education (`tailor.py:1173–1266`). This is deterministic content generation, not literal verified history.
5. Cover-letter role packs reference fixed example employers, and closer-polishing functions inject specific capabilities/context; their validators do not comprehensively verify every employer, achievement and number. See section 6.
6. Citation validation checks ID existence/presence, not whether each cited fact actually entails the whole generated claim. Numeric checking compares tokens and allows “derived” wording; it does not validate calculations or link every number to the correct employer/date.
7. “Verified” in the UI means explicitly supplied/reviewed by the candidate. There is no independent employment, degree, licence or skills-verification service.

Consequently, future NZ generation should reuse this foundation only with an explicit truthfulness policy agreed by the product owner. The README's absolute “never fabricates” statement is stronger than current enforcement. No safeguard or prompt was changed by this review.

## 6. Supporting statement and cover-letter workflow

### 6.1 Existing document types and templates

| Requested content | Current capability |
| --- | --- |
| Cover letters | Dedicated generator, role/sector paragraph packs, diagnostics, Cover Studio and PDF/text export. |
| Supporting statements | Dedicated NHS/UK public-sector-oriented generator, person-spec criteria extraction, structured JSON output, quality report and Statement Studio. |
| Personal statements | Detected as a statement requirement; can be drafted through Statement Studio. No separate country-neutral personal-statement engine found. |
| Application/screening answers | Saved `cc_answers`; conservative form mapping; selected-question AI drafting from cited evidence in the assisted browser. |
| Competency-based answers | Generic question drafting and reviewed criteria/example planning can be used. No distinct competency framework, scoring engine or universal STAR template workflow found. |
| Professional/skills summaries | Editable profile summary/skills; CV summary/core-skills generation; assistant summaries. No separate country-profile summary service. |
| Relocation/work-rights statements | Candidate facts can be supplied manually to writing/questions. No dedicated NZ relocation/disclosure template or eligibility validation found. |

Evidence: `scoring/cover_letter.py`, `scoring/supporting_statement.py`, `writing_studio.py`, `nhs_writing.py`, `apply/question_assistant.py`, `command_center.py::assistant()`, `eligibility.py::document_requirements()`.

### 6.2 Generation, editing and publication

```text
Saved vacancy OR pasted role/employer/description
    + auto/named/tailored/custom CV
    + shared professional facts
    + candidate-confirmed supplemental facts / criteria examples
    + tone, structure, word/character limits and employer AI policy
      -> generation reservation + input/model hash cache
      -> configured cover/statement generator
      -> validate -> save draft version (including failed-validation drafts)
      -> candidate edit / preview / save another version
      -> explicit "Use for application"
      -> revalidate, compare profile/CV hashes, exclude unsuitable job states
      -> current job document pointer -> PDF/packet -> application review
```

`writing_studio.py::context()` limits descriptions to 50,000 characters and reads selected CV evidence; shared facts are appended. `generate()` reserves a daily allowance before inference (default 20 generations, configurable 1–200), caches identical requests, and blocks generation when the candidate marked employer AI content as not allowed. That employer-policy field is user-supplied, not automatically researched.

`save()` keeps parent/family versions, source context, validation and provider/model. Invalid drafts can remain editable; draft generation alone does not publish. `publish()` checks active/submitted/archive/block conditions, rejects changed profile/CV sources, validates again and only then sets the current job pointer. TXT/PDF downloads and HTML preview are supported. Older approved files remain present. Evidence: `writing_studio.py::generate(),save(),validate(),publish(),pdf()` and `command_api.py` studio routes.

### 6.3 Prompts and validation

**Cover:** `_build_cover_letter_prompt()` requests five short paragraphs, UK-style tone, evidence-specific skills, real metrics and a greeting/sign-off. Role packs cover IT support, application support, technical systems, public/commercial support and reporting. `generate_cover_letter()` uses job excerpts, evidence selection, recent-opening similarity checks, sanitization, deterministic closer rewriting, up to four outer attempts, and `validate_cover_letter()` / consistency checks. Studio options adjust word limits. Core generators can return their last attempt even when invalid; the calling studio/batch validators determine publication.

**Important personalization leakage:** `_ROLE_LETTER_PACKS` in `scoring/cover_letter.py:33–128` contains fixed employer/example references rather than deriving every example from the current user. `_polish_reporting_closer()` can insert Healthcare Analytics/EPR context, and `_polish_support_closer()` can insert capabilities. These are not a country-neutral verified template library. Employer examples are intentionally not reproduced here as candidate facts.

**Statement:** `_build_prompt()` explicitly says “UK supporting statements,” “NHS / UK public sector” and “UK spelling” (`supporting_statement.py:540–578`). Structures include criteria-led, story-then-criteria and values-first, with NHS-specific values wording even in the available general variants. `_is_nhs_supporting_information_job()` has broad detection terms such as “person specification” and “supporting information,” so non-NHS adverts can trigger NHS-style handling.

`evaluate_statement_quality()` checks length, email/UK mobile leakage, copied form instructions, speculative joining plans, AI meta text and a finite unsupported-claim rule set (clinical/NHS, training, formal process/project work, Welsh). Criteria coverage uses lexical heuristics and reports warnings. Missing criteria are not necessarily publication blockers. Validation is useful but not a general semantic verification of all countries' qualifications, work rights or cultural claims.

`nhs_writing.py` provides candidate-reviewed essential/desirable criteria, confirmed examples, trust values, employer instructions/AI policy and character caps. It is a reusable evidence-planning pattern with NHS naming and assumptions. Supporting-statement generation caches inputs, makes up to five outer attempts and one structured-output repair per attempt, then returns the last text. Batch/studio paths perform further validation.

**Country conclusion:** writing context and source selection are reusable. Existing prompts/templates are **not** already NZ profiles; simply choosing an NZ location leaves UK/NHS language in place. Country-specific template selection requires a small extension, while retaining the writing services and validation plumbing.

## 7. Application management lifecycle

### 7.1 State model and normal journey

The system distinguishes three concepts:

- **Execution:** `jobs.apply_status`, attempts, lock/agent, errors and `applied_at`.
- **Hiring:** `cc_job_meta.hiring_status` — submitted, acknowledged, assessment, interview, offer, rejected, withdrawn, no_response.
- **Visibility/closure:** active, archived, blocked, snoozed; explicit closure and retained deleted-history records.

```text
Discovered / saved job
   -> enrich and assess -> candidate Pick (selected)
   -> prepare validated CV / required cover / statement / answers / current PDFs
   -> manually apply OR launch an application engine
        -> in_progress -> prepared (no confirmed submission)
                       -> submitting -> applied + applied_at + evidence
                                     -> submission_uncertain -> reconcile
                       -> failed / manual / stopped
   -> submitted hiring record
   -> acknowledged -> assessment/interview -> offer or rejected
      [withdrawn / no_response also recordable]
   -> follow-up / reminder / notes / archive / reasoned closure
   -> optional user-requested delete: compact history + guarded artifact cleanup
```

Hiring values are validated choices, not a rigid country-specific transition graph. Rejection is a hiring outcome, not a technical apply failure. Evidence: `command_center.py::HIRING_STATES,update_job(),classify_failure()`; `job_lifecycle.py`; `submission_reconciliation.py`.

### 7.2 Tracking, deadlines, interviews, reminders and attachments

Discovered vacancies persist automatically. Pick/unpick is the saved shortlist rather than a separate saved-vacancy entity. Records support priority, notes, recruiter, application reference, submission method, evidence and a submitted CV version. Activity is persisted in `cc_events`, including database-triggered execution transitions.

There is one general `due_at` field used for reminder, deadline or snooze, not separate normalized closing-date, interview, follow-up and multi-event tables. Users enter a timezone-aware date; UI uses local datetime controls and ISO UTC conversion. `.ics` export creates a calendar event. Email analysis can propose interview/assessment/deadline dates and meeting details; candidate review may write a reminder/notes. There is no interview-booking integration, automatic calendar write, outbound reminder-email service, or guaranteed notification delivery found. Follow-up assistant output is a draft, not a sent message.

Generated CV/cover/statement documents, submission screenshots and portfolio evidence exist. A general per-application arbitrary attachment library was not found; portfolio uploads belong to project/profile evidence. Employer identity is largely a job's company string plus recruiter data, not a verified employer entity.

Analytics aggregate unique opportunities, preparation milestones, activity, responses/interviews/offers, sources, resume/model/campaign cohorts, fit bands, reminder/history coverage and estimated token cost. CSV/JSON/ZIP exports exist. Evidence: `command_center.py::overview(),details(),update_job()`; `command_api.py` calendar/export routes; `evidence_inbox.py::analyze(),review()`; `opportunity_insights.py`; `insights_visuals.py`; `pipeline_workspace.py::insights()`.

### 7.3 Automation paths and their different safeguards

| Path | User confirmation / safety | Important limits |
| --- | --- | --- |
| Manual employer application | Candidate opens link/downloads documents and submits in their browser; manual applied status can retain reference/evidence. | Opening a URL is not recorded as submission. Ordinary manual applied marking is not independently verified. |
| **Integrated Apply with agent** | `start()` requires `documents_approved`; packet readiness/ownership and job exclusions checked; reviewed files copied into session; candidate can take over login/CAPTCHA; final review token bound to page signature; one submit attempt; receipt detection and evidence retained. | DOM/control/receipt heuristics are not an employer API guarantee. Unsupported pages need manual help. Snapshot approval does not independently verify facts or work rights. |
| Older `--engine llm` | Common fields/PDF upload, saved answers; prepare is default, explicit `--submit` enables completion/submission; lock and checkpoint controls. | Separate browser/handoff; Docker has no desktop for the older headful handoff. Submit mode can auto-advance/accept consent through legacy ATS-specific logic; no equivalent page-bound candidate approval token. Missing confirmation can be returned as prepared. |
| Older Claude Code engine | CLI default; uses a generated profile/resume prompt and MCP browser tools; dry-run prompt; blocked/manual ATS and SSO rules, worker locks/attempt limits, result parsing. | Can submit autonomously without `--submit`; `--dry-run` is prompt-based rather than the integrated browser's DOM gate. Runs `bypassPermissions`; no per-submission human approval equivalent. |
| Scheduled applications | Candidate explicitly schedules prepare or submit; due-time current-state/preference/listing checks; durable intent and daily cap; interrupted schedules held for review. | Scheduler launches the older LLM engine. Scheduling “submit” authorizes that engine; it does not later require the integrated browser's approval step. Server must be running. |

Evidence: `apply/assisted_browser.py::start(),Session.final_review(),Session.submit(),Session.confirm(),Session.advance()`; `apply/application_packet.py`; `apply/llm_engine.py::_assist_one_job(),_maybe_submit_application(),_worker_loop()` and ATS consent helpers; `apply/launcher.py::acquire_job(),run_job(),worker_loop()`; `apply/prompt.py::build_prompt()`; `cli.py::apply()`; `scheduler.py::tick()`; `docs/ASSISTANT_AND_ASSISTED_APPLY.md`.

**Integrated safety details:** blocks final submission interactions until approved, distinguishes application-entry navigation, does not invent missing answers or demographics, avoids overwriting populated fields, caps automated advancement at 20 steps, invalidates approval on answer/page changes, and records `submission_uncertain` if a receipt is not detected. One active integrated browser per workspace and four globally are enforced. Confirmation is based on affirmative page text and optional reference, with screenshots/JSON saved. Review tokens and workspace session ownership protect against stale/cross-user commands.

**Duplicate submission prevention:** URL dedupe, `applied_at`/active locks, uncertain-state guards, submitted snapshots, duplicate title/company warnings, retired identity blocking and reconciliation. `submission_reconciliation.py::reconcile()` requires checked evidence for clearing uncertainty, rejects “missing email” as proof of non-submission, and never starts a retry itself. Duplicate employer/requisition identity across different job URLs is not structurally enforced. Older direct CLI stale-lock recovery can reacquire `in_progress` work unless `APPLYPILOT_RECONCILE_STALE=1`; the dashboard runner explicitly sets this flag.

There is no country-specific application process object. Generic packet requirements and ATS handling can accommodate different documents, but country-specific legal eligibility/consent/disclosure is not centrally enforced. Packaged automation restrictions are engineering restrictions, not evidence of job-board permission to automate. Optional CapSolver and legacy Gmail MCP tooling differ from the read-only native mailbox connectors.

## 8. User profile and employment-data architecture

### 8.1 Canonical candidate model

| Information | Current representation |
| --- | --- |
| Identity/contact/address | `profile.json.personal`: separate/full/preferred names, email, phone/mobile, address lines, city/county/province/state/country/code/postcode, professional links, optional birth/nationality data. |
| Account identity | Separate auth user row plus profile account/contact information initially populated on registration; not a universal automatic sync between both stores. |
| Employment summary | `experience`: total years, education level, current title/company, target role. |
| Employment history | `resume_sections.professional_experience`: header (“role at employer”), subtitle (dates/location), bullet list and optional ID/family/include fields. Also remains in each source CV's text. Dates/employer are not separately typed relational fields. |
| Education | `resume_sections.education` string lines, `experience.education_level`, `resume_facts.preserved_school`; not a normalized qualification/institution model. |
| Certifications | Legacy certification lines and structured `credentials` with name, issuer, awarded/expires/link, earned/in_progress/expired, family/include. |
| Skills | `skills_boundary` categories of string lists; role-specific technical-environment fields and source CV text. |
| Projects/portfolio | General/data/support/application-support project lists with header/subtitle/bullets/ID/URLs/technologies/family/include; separate portfolio attachment metadata/files. |
| Preserved evidence | `resume_facts`: company/project/school allowlists and real metrics. “Verified” is candidate-supplied, not third-party verification. |
| Preferred titles/places | Search queries/location objects, exclusions and per-campaign overrides in `searches.yaml`; target role in profile; workspace application preferences in SQLite. |
| Work authorisation | One global `work_authorization` object: legally authorized, sponsorship required, permit type/expiry, driving/transport fields. No country key selected by job destination. |
| Relocation/availability | Availability fields including willing_to_relocate/travel, earliest start, notice and job-type/shift preferences. Global rather than destination-scoped. |
| Salary preferences | One global compensation object with currency/range/expectation; not campaign-specific in current filtering. |
| Screening memory | `cc_answers` exact reusable questions/answers, not country/employer-scoped. |

Evidence: `profile.example.json`; `setup_workspace.py::_validate_profile_patch(),_validate_project_entry_list(),write_profile()`; `application_profile.py::application_values(),contact_values()`; `professional.py`; `web/studios.js`; `cv_library.py`; `pipeline_workspace.py::campaign_config()`.

### 8.2 Sharing verified history across countries

**Already possible:** one workspace profile is read across source CVs, scoring, tailoring and writing. `professional.py::enrich_evidence()` selects included role-family facts, credentials and projects and appends them to source evidence. `cv_library.py::build_profile_cv()` can render source text from shared facts. Shared updates do not automatically overwrite source variants or submitted documents; revisions/fingerprints signal regeneration needs.

**Not yet a strict single-source-of-truth history graph:** variants contain independent full text; there are no foreign-key references from each CV claim to a canonical employment entry. A header-based merge can duplicate differently worded history; conflict detection is not systematic. Candidate edits to a variant can diverge from the profile. Stored fact citations are per-generation IDs, not permanent verified-fact IDs across all documents.

**Country preferences:** campaigns can retain separate country/location/query/board/source/CV-routing settings while sharing the profile. They do not presently carry a complete country document policy, work-rights record, relocation disclosure or compensation profile. Simultaneous UK and NZ work rights cannot safely be represented by changing the same global yes/no field between runs. `application_profile.py::local_fills()` reads that global value even when the form asks about a different country; its negation guard does not resolve jurisdiction.

**Assessment:** multiple country/role CV presentations without duplicating history are architecturally feasible using existing shared facts plus presentation variants, but guaranteed history consistency and simultaneous country-specific rights require an explicit additive model/policy extension. Duplicating the entire user/workspace/profile for NZ would work against the user's canonical-history requirement.

## 9. AI features and orchestration

### 9.1 Provider/model routing and common controls

`ai_routing.py::TASKS` defines `score`, `summary`, `tailor`, `statement`, `cover`, `review`, `assistant`, `forms`, `extract`, and `email`. Per-task `LLM_<TASK>_PROVIDER/MODEL` overrides inherit global defaults otherwise. ContextVars scope task/model/workspace settings; clients are pooled by immutable route/credential configuration, with thread-local usage. Provider keys may be service environment, workspace `.env`, or encrypted UI-managed overrides.

| Provider | Implemented transport / code defaults |
| --- | --- |
| Google Gemini | Native Generative Language `generateContent`; default `gemini-2.5-flash`; configurable thinking; model-name compatibility/fallback handling. |
| OpenAI | Chat Completions; default `gpt-4o-mini`; special handling for configured `gpt-6-luna` / `gpt-5.6-luna` IDs and bounded reasoning headroom. |
| Anthropic | Messages API; default `claude-sonnet-4-6`. |
| Local compatible endpoint | OpenAI-compatible chat transport; `LLM_URL`, optional key and configured model. |
| Decisions observer | Separate direct OpenAI `/v1/decisions` request with fixed `gpt-6-luna`, optional observation-only feature. |
| Older autonomous agent | Claude Code CLI selected by CLI model option (CLI default `haiku`, launcher defaults can differ); independent from writing task routing. |

These are **source defaults/accepted identifiers**, not an inventory of live configured/available models. No private settings were read. DeepSeek and managed provider-agent APIs discussed in documentation are not first-class implemented providers/harnesses in this code. Generic compatible transport does not verify provider-specific integration.

`llm.py` implements optional cross-thread throttling, timeout/retry/backoff, Retry-After support, account-level errors with a 15-minute client/thread circuit breaker, JSON response modes, tolerant parsing and bounded shape-repair. `structured_json()` checks object/top-level required keys, not a full typed schema. Model/task identity participates in caches. Writing tasks can reserve up to 16,384 additional reasoning tokens within an overall completion cap. No uniform global monetary spend ceiling exists.

### 9.2 Feature-by-feature implementation register

Each entry identifies purpose/location, inputs/outputs, model/prompt, persistence, validation, failure handling and cost controls.

#### A. Discovery extraction and API-response judgement

- **Purpose/location:** classify captured responses, choose JSON-LD/API/CSS strategy and generate selectors; `discovery/smartextract.py::judge_api_responses(),_run_one_site(),execute_css_selectors(),ask_llm()`.
- **Inputs → outputs:** bounded browser intelligence, network samples/cards/HTML → JSON verdict/strategy/extraction paths/selectors → application-extracted vacancy dictionaries.
- **Model/prompt:** `extract` route; `STRATEGY_PROMPT`, selector and response-judgement prompts. No external AI scraping framework.
- **Database:** resulting jobs, provenance, source-health and pipeline usage; errors/reports retained in run outputs.
- **Validation:** known strategy switch, selector execution, job-field/title/location/salary filters, URL dedupe; deterministic known-site selectors can bypass inference.
- **Failure/cost:** malformed JSON repair, per-site LLM/execution/CAPTCHA errors, bounded source windows and browser retries; extraction calls commonly have 4,096 output-token caps. Not every provider failure can be solved by retrying.

#### B. Full-description/application-link extraction

- **Purpose/location:** last-tier enrichment; `enrichment/detail.py::extract_with_llm(),scrape_detail_page()`.
- **Inputs → outputs:** page URL/title and up to 30,000 content characters → `full_description`, `application_url` (optional company from other extractors).
- **Model/prompt:** `extract` route, `DETAIL_EXTRACT_PROMPT`, structured JSON requiring description/link keys.
- **Database:** updates job descriptions, application links, enrichment timestamp/errors; then best-effort UK sponsor annotation.
- **Validation:** output value types, cleaned descriptions, deterministic URL fallback; no verification that every model-extracted link is a direct employer endpoint.
- **Failure/cost:** deterministic JSON-LD/CSS first; preserved cleaned page text on AI failure; one shape repair, 4,096 output tokens, optional cooldown and pipeline usage ledger.

#### C. Job-to-CV fit scoring and optional variant comparison

- **Purpose/location:** rank opportunities; `scoring/scorer.py::score_job(),run_scoring()`; `pipeline_workspace.py::evaluate_variants()`.
- **Inputs → outputs:** routed CV/shared professional evidence, title/company/location and up to 6,000 JD characters → score 1–10, keywords, reasoning/confidence, provider/model.
- **Model/prompt:** `score` route and `SCORE_PROMPT`; optional comparison may deliberately use `review` route with the same evaluator.
- **Database:** jobs score/status/route/fingerprint/model fields, `cc_score_history`, `cc_variant_evaluations`, usage.
- **Validation:** JSON/legacy/partial-response score parsing and range clamps; route ambiguity blocks processing. A recovered partial score is accepted without a full semantic completeness check.
- **Failure/cost:** null score plus retryable/blocked/failed metadata, exponential retry timing and five-attempt boundary; batch deferral for account failures. Score request cap 768 output tokens. Comparisons limited to two variants, ten evaluations/day, reservations and cache. No learned ATS calibration.

#### D. CV tailoring, summaries and improvement diagnostics

- **Purpose/location:** job-specific CV, summary, core skills and requirement gaps; `scoring/tailor.py::tailor_resume(),assemble_resume_text()` and `scoring/tailor_strategy.py`.
- **Inputs → outputs:** routed source, shared profile, JD, role pack, keywords, facts/responsibilities → JSON drafts, assembled text, coverage/gap/citation/quantity reports, approved/failed status.
- **Model/prompt:** `tailor` route; `_build_tailor_prompt()`, role packs, adaptive budget, gap guidance; configurable factual/balanced/aggressive mode.
- **Database:** current approved pointer/input hashes/model, document/version registry, reports, pipeline usage. Previous approved paths survive failed generation.
- **Validation:** section/entity/tool checks, requirement gates, selected-target coverage, citation/numeric checks, full text and reviewer. Section 5.5 details enforcement gaps.
- **Failure/cost:** fresh context with prior error notes; default three drafts per attempt (configurable two/three), default three retries plus first attempt, so up to 12 writer calls before transport retries, plus reviewer calls; 2,300 output tokens per draft plus applicable reasoning headroom. Cross-run attempt cap normally five. Optional smart-model fallback code/config exists, but is not assumed to guarantee recovery. No universal per-day tailoring spend cap.

#### E. Factual CV reviewer

- **Purpose/location:** secondary quality judgement; `scoring/tailor.py::judge_tailored_resume(),_build_judge_prompt()`.
- **Inputs → outputs:** source/generated CV, target title, allowed evidence → PASS/FAIL and issues.
- **Model/prompt:** `review` route; text verdict prompt with the explicit minor-stretch tolerance.
- **Database:** judgement in artifact report; affects publication, usage recorded in pipeline context.
- **Validation/failure:** verdict parsing; failure feeds regeneration; lenient mode skips reviewer. Not a formal verification service and not necessarily an independent model/provider.
- **Cost:** 512 output-token cap, invoked after candidate selection/other gates; retries can repeat it.

#### F. Cover letters and follow-up drafting

- **Purpose/location:** cover generator `scoring/cover_letter.py::generate_cover_letter()` plus versioned `writing_studio.py`; follow-up assistant through `command_center.py::assistant()`.
- **Inputs → outputs:** CV/facts, job excerpt, role/paragraph pack, optional style/evidence/limits → plain-text letter; follow-up → unsent draft.
- **Model/prompt:** `cover` route; `_build_cover_letter_prompt()` and assistant system instructions. Fixed role-pack examples/closer injection are limitations.
- **Database:** batch job pointer/report, studio versions/requests, assistant cache/usage as appropriate.
- **Validation/failure:** length, paragraphs, greeting/sign-off, banned/meta text, supported-tool heuristics, duplicate openings; studio publication rechecks. Generator can return failed last draft; caller must validate.
- **Cost:** up to four generation attempts per invocation; output cap `max(1200, max_words*3)` and applicable reasoning headroom; studio default 20 generations/day/cache; follow-up shares assistant allowance.

#### G. Supporting/personal statements and legacy tightening

- **Purpose/location:** `scoring/supporting_statement.py::generate_supporting_statement(),evaluate_statement_quality()`; studios and `/api/statement/generate` legacy route in `dashboard_server.py`.
- **Inputs → outputs:** CV, profile, person specification/JD, supplemental confirmed facts, structure/length → JSON `statement`, text and quality/coverage diagnostics; legacy route may tighten overlength text.
- **Model/prompt:** `statement` route, UK/NHS `_build_prompt()`, NHS planner context and legacy UK rewrite prompt.
- **Database:** job statement fields/status, file cache/report, studio versions/reservations; legacy response does not inherently publish into a job packet.
- **Validation/failure:** shape repair, word/character constraints, finite unsupported-claim rules, instruction/contact leakage and criteria warnings; batch publication validates again. Exhaustion returns last text, not guaranteed approved output.
- **Cost:** deterministic source/model cache, five outer attempts and one JSON repair each, 1,800–5,000 output-token budget based on length, studio allowance; legacy route does not implement the same studio reservation flow.

#### H. Fit explanation, failure diagnosis, copilot and interview preparation

- **Purpose/location:** `command_center.py::assistant()`; native UI request/retry/cancel controls in `web/workspace.js`.
- **Inputs → outputs:** current job/events/meta, professional-fact whitelist and CV sections, or up to 150 prioritized jobs plus workspace metrics → textual evidence-grounded explanation, interview preparation, diagnostic or next-action suggestions.
- **Model/prompt:** explain → `score`; diagnose/copilot → `summary`; interview → `assistant`; followup → `cover`. System says records are untrusted, no tools/actions, distinguish unknowns, cite IDs and never invent history/outcomes.
- **Database:** content/model keyed `cc_ai_cache`; `cc_ai_usage` reservations with pending/completed/failed status.
- **Validation/failure:** bounded evidence, contact/credential minimisation, empty-output rejection, sanitized failure categories. Citation requirements are prompt-based rather than mechanically validated for these free-text responses.
- **Cost:** cache, default shared 50 requests/day, 1,800 output tokens; one interactive transport attempt/45-second HTTP phase budget. UI stops waiting after 60 seconds; that does not cancel a provider charge already in progress.

#### I. Form-field mapping

- **Purpose/location:** `apply/llm_engine.py::_llm_plan_fills()` reused by integrated browser `Session.fill()`.
- **Inputs → outputs:** known nonempty contact values, reviewed cover/statement, verified answers and up to 40 empty fields → at most 12 references to supplied value keys/field identifiers. Literal values come from application code.
- **Model/prompt:** `forms` route; assisted-mapping prompt prohibits guessing/passwords/submission, treats labels as untrusted.
- **Database:** reads saved answers/profile; browser records progress; no uniform per-call mapping usage reservation/day ceiling was found.
- **Validation/failure:** value-key and field membership allowlists, sensitive/local-only filtering, no filled-field overwrite, option matching, manual handoff on failure. Country work-rights values are handled primarily by deterministic profile fills, not legal inference.
- **Cost:** 800 output tokens, session plan cache, bounded fields/fills; integrated path uses one-attempt/45-second request budget and navigation bound. These are not a monetary cap.

#### J. Selected-question screening/competency answer drafts

- **Purpose/location:** `apply/question_assistant.py::draft_answer()`; `Session.request_draft(),finish_draft(),insert_answer()` in `apply/assisted_browser.py`.
- **Inputs → outputs:** selected question/help/maxlength, reviewed documents, professional profile and up to eight relevant verified answers → answer, literal source quotes and missing-evidence list; direct saved-value matches can avoid AI.
- **Model/prompt:** `forms` route; first-person evidence-only prompt prohibits invented eligibility/history and tool/browser actions.
- **Database:** usage/event records with answer/source metadata; reusable answer saved only at candidate insertion request. Changes invalidate final approval.
- **Validation/failure:** sensitive medical/demographic/consent/password questions blocked; quoted text must exist in allowed source; empty/citationless/overlength drafts require candidate input; stale field/value hashes prevent wrong insertion. A supporting quote is not full logical entailment of every sentence.
- **Cost:** 1,600 output tokens, one attempt/30-second request budget, one in-flight answer per session. No pre-request daily-budget enforcement found on this path, despite usage recording.

#### K. Email evidence classification, matching and date extraction

- **Purpose/location:** `evidence_inbox.py::analyze(),validate_analysis(),verified_datetime(),review()`; deterministic prefilter and matchers first.
- **Inputs → outputs:** imported message/source date and up to 50 submitted application candidates → category, proposed hiring outcome, match/evidence/confidence, summary, up to three interview/assessment/deadline events, exact meeting/recruiter data.
- **Model/prompt:** `email` route, explicit untrusted-message/evidence-only JSON instructions.
- **Database:** message analysis lease/cache/diagnostics and proposed fields; shared usage ledger. Hiring/reminder updates require candidate review.
- **Validation/failure:** literal quotes, valid categories, confidence >=0.7 for proposed outcomes, constrained matching, source-supported clock/date/zone, DST ambiguity rejection; errors retain no applied hiring update. Deterministic sender/category rules avoid analysing obvious alerts/promotions.
- **Cost:** 2,400 output tokens, cached analysis, shared default 50/day reservation; transport controls from common client. Mail synchronization itself is not autonomous AI outcome approval.

#### L. Browser Decisions observer

- **Purpose/location:** `decision_observer.py::evaluate(),Observer,start_benchmark()` observes page classifications and compares with a deterministic baseline.
- **Inputs → outputs:** redacted bounded field/control labels/validity and limited excerpts → finite page-state/confirmation/next-control predictions with confidence and measured latency; nine synthetic benchmark cases.
- **Model/prompt:** fixed `gpt-6-luna` direct Decisions API; `questions()` instructions and `PROMPT_REVISION`.
- **Database:** decision run/observation tables and usage reservation.
- **Validation/failure:** answer count/type/choices/probability validation, sanitized failures/refusals; labels only for private authentication views. **Observation only: it does not authorize or execute browser actions.**
- **Cost:** disabled by default; default own 20/day plus shared assistant budget; one bounded HTTP request, queue/cache controls. Code includes an estimate based on a fixed input rate; this review did not validate current commercial pricing.

#### M. Older autonomous Claude application agent

- **Purpose/location:** `apply/launcher.py::run_job()` and `apply/prompt.py::build_prompt()` orchestrate real browser submission.
- **Inputs → outputs:** candidate profile, selected CV/cover, job, generated instructions and browser/Gmail MCP tools → tool actions, result markers, execution logs/stats and DB outcome.
- **Model/prompt:** Claude Code CLI model option; large dynamically generated autonomous-application prompt. It is not routed through `llm.py`.
- **Database:** acquire locks, attempts/status/results, submitted snapshots, progress events; worker logs and cost stats.
- **Validation/failure:** domain/manual-site blocks, tool denylist, result parsing, timeouts and uncertain-error classification; broader autonomy than the integrated browser. Dry-run relies on agent instructions.
- **Cost:** CLI limit/worker/timeout/attempt bounds, reported Claude usage/cost; no common assistant daily token budget. npx MCP dependencies are resolved separately.

### 9.3 Orchestration, memory and framework assessment

The seven-stage preparation pipeline is application code, not a multi-agent framework. Writer/reviewer tasks are sequential functions with structured outputs, not a self-directed collaborative agent team. Parallel application workers are separate job executors; the integrated browser uses deterministic state/navigation logic with bounded AI mapping/drafting. Claude Code provides the older external agent loop/tools.

There is no vector database, embedding retrieval, RAG framework, trained job classifier, or learned long-term agent memory found. Context is assembled from local files/SQLite; evidence ranking and CV routing are lexical. Caches, verified answers, profile revisions and timelines are contextual memory but not autonomous memory formation. The unified LLM client does not expose a general provider tool-calling loop; MCP tool execution belongs to the legacy Claude path.

Most general scoring, writing, explanation, interview and answer capabilities are reusable for NZ with the right country context and validation. No demonstrated requirement calls for another AI framework, an additional model provider, fine-tuning, or a new agent service.

## 10. Country and internationalisation support

| Area | Existing support | NZ implication / effort classification |
| --- | --- | --- |
| Country search model | Structured country/state/city/kind/remote rows and aliases, including NZ | **Already supported/configuration** for places such as Auckland, Wellington and Christchurch. |
| Provider routing | Per-place JobSpy market and board suppression; direct-source templates; Workday returned-location filtering | **Configuration** for existing markets; **small extension/adapters** for sources requiring special extraction/access. Actual NZ coverage unverified. |
| Country-specific preferences | Campaigns retain geography/query/source/route overrides | **Configuration** for searches/CV choices; not complete country policy. |
| Persisted vacancy geography | Free-text `jobs.location`, campaign membership/provenance; no authoritative country ID | **Structural additive extension** for reliable destination-based rights/evidence policy independent of text heuristics. |
| Employer registry | Registered Workday portals and source URLs; UK sponsor CSV lookup | **New NZ-specific capability** for accreditation and legal-entity evidence; existing UK fields are insufficient. |
| Currency/salary | Currency strings and conservative structured comparison; no FX; GBP-specific direct parsing | NZD can be a string, but **small extension** for NZ salary interpretation and **country-scoped preference model** for simultaneous markets. No verified NZ tax/KiwiSaver/package conventions. |
| Dates/time zones | ISO UTC persistence, browser locale display, explicit reminder offsets, IANA date verification | Basic international timestamps **already supported**. NZ abbreviation handling/display/time-zone policy needs review; no NZST/NZDT alias mapping found. `Pacific/Auckland` can use generic IANA support if supplied verbatim. |
| Phone/address | Generic strings, country code, province/state/postcode; local field mapping | Basic NZ values **already supported**; no E.164 validation/formatting or NZ-specific address/phone normalization. UK-only phone leak detection misses other formats. |
| UI localisation | English strings; `toLocaleString()` follows browser locale | No translation catalogs, chosen UI locale, or country-bound formatting system found. **Small extension** only if product requires explicit regional presentation. |
| CV layout | Shared fixed headings/A4 renderer and profile validation knobs; one-page writer instruction | Renderer reusable; **small extension** for selectable country document policy. A4 alone is not NZ CV support. |
| Spelling/tone | UK-style cover and explicit UK/NHS statement instructions | **Small prompt-selection extension**, not merely changing a country field. No country template registry found. |
| Immigration/work rights | One global authorization/sponsorship object, UK licence/text signals | **Structural country-scoping + new policy/evidence** needed for concurrent UK/NZ. |
| Occupation classifications | Role-family and requirement keyword patterns | No NZ codes/classification versioning; add only if needed for an agreed assessment, not assumed necessary for keyword discovery. |
| Application requirements | Per-job cover/statement/answers requirements; default NHS detection | Generic packet reusable; **small extension** for explicit country-specific document rules. |

Evidence: `geography.py`; `pipeline_workspace.py::campaign_config(),save_campaign()`; `database.py::_ALL_COLUMNS`; `discovery/salary_filter.py`; `application_profile.py`; `evidence_inbox.py::verified_datetime()`; `web/workspace.js:7,173`; `scoring/pdf.py`; `scoring/cover_letter.py`; `scoring/supporting_statement.py`; `eligibility.py::document_requirements()`.

**Overall character:** this is a globally configurable search foundation with substantial UK-focused workflow specialization and older US/Canadian defaults. Describing it as exclusively UK would be inaccurate; describing it as fully internationalised would also be inaccurate.

## 11. Existing UK-specific assumptions

| Evidence | Assumption and effect |
| --- | --- |
| `uk_sponsorship.py::GOVUK_REGISTER_PAGE,detect_sponsorship_from_text(),match_licensed_sponsor()` | Only the UK Home Office register; UK visa/CoS/Skilled Worker/NHS band expressions. Exact/suffix-name match, no NZ accreditation authority. A nonmatch becomes “No,” which is not equivalent to verified ineligibility. |
| `enrichment/detail.py:815–821` | UK annotator invoked without destination guard. Non-UK vacancies can acquire irrelevant UK licence labels. Jobs promoted as already enriched by JobSpy/Workday may bypass this annotator, so annotation coverage is also inconsistent. |
| `config/searches.uk.*.yaml` | UK places, accept/reject lists, seniority exclusions, support/data title mixes and source selection. Legacy filters remain unless selected-location mode is explicitly used. |
| `discovery/smartextract.py::_looks_like_uk_location(),_location_ok()` | UK postcode/location heuristics in legacy mode; built-in selectors and source pruning optimized for several UK sites. |
| `config/sites.yaml`, `source_registry.py::INDEED_TEMPLATE` | Packaged UK direct-source URLs and UK Indeed example; generic `.com` UK sites can evade suffix scoping. No packaged SEEK/Trade Me NZ integration found. |
| `scoring/supporting_statement.py::_build_prompt(),_STRUCTURE_VARIANTS` | All statement generation remains explicitly UK/NHS; values-first wording and NHS-specific unsupported-claim rules. |
| `scoring/cover_letter.py::_build_cover_letter_prompt(),_ROLE_LETTER_PACKS` | UK-style tone, fixed example-employer references and public-sector/healthcare closer wording; not universally derived from user/job facts. |
| `scoring/tailor_strategy.py::_HARD_CONSTRAINT_PATTERNS,_CREDENTIAL_PATTERNS` | UK work rights, SC/DV and UK clinical registration patterns; not NZ immigration qualification logic. |
| `scoring/supporting_statement.py::_validate_statement(),evaluate_statement_quality()` | UK mobile regex for personal-data leakage; other phone formats are not equivalently covered. |
| `discovery/salary_filter.py::salary_text_ok()` | GBP-specific cross-currency exceptions, no general multi-dollar-currency resolution. |
| `dashboard_server.py` legacy `/api/statement/generate` | UK rewrite instructions survive outside the native studios. |
| `view.py` / older dashboard APIs | UK smart-site/sponsorship presets remain in legacy code even though served root/classic routes redirect to native workspace. Their presence is not proof they appear in the current native UI. |

**Additional non-country-specific but NZ-relevant assumptions:** `apply/prompt.py::_build_screening_section()` hardcodes “cannot relocate” regardless of the saved availability preference and permits “YES” for close-equivalent tools. `_build_location_check()` assumes local-city eligibility, treats remote broadly, and calls overseas onsite roles ineligible; it reads legacy location patterns rather than the complete structured geography model. These conflict with an overseas relocation workflow even though the profile now stores willingness to relocate. `_currency_symbol()` only maps a small set of currencies. Such legacy behaviour must be included in future compatibility analysis, not silently inherited by NZ automation.

## 12. New Zealand integration readiness

### 12.1 Requirement-level assessment

| Potential NZ requirement | Current readiness | Repository basis / remaining issue |
| --- | --- | --- |
| Auckland/Wellington/Christchurch and other places | High for configuration | `geography.py` supports NZ/structured places; routing alias test exists. No live search success claimed. |
| Software/business applications, data/BI, IT/application support | High for role presentation/search | Free-text queries; engineering/data_bi/support/application_support packs; configurable title/family routing. Overlapping terms need reviewed mapping. |
| Broad NZ discovery | Medium | JobSpy market + global/direct/Workday engines reusable; no validated NZ source collection/coverage benchmark or dedicated local board adapter. |
| Overseas-applicant acceptance | Low | No explicit overseas/relocation acceptance classification or exclusion policy found; unknown text cannot establish willingness. |
| Potential AEWV-supporting employer | Low | No AEWV-specific classification, authoritative NZ evidence source, or link between employer status and vacancy offer. |
| Employer accreditation verification | Low | UK register machinery is useful precedent, but wrong authority/schema. No NZ legal identity/accreditation record or refresh policy. |
| Exclude unrestricted-existing-NZ-rights-only jobs | Low | Global rights values and generic regex signals do not implement this rule. |
| Direct employer links | Medium | Separate application URL extraction/fallback exists; employer-domain legitimacy and final-link ownership not verified. |
| Job freshness/expiry | Medium-low | JobSpy recency filter and manual listing checks; no normalized posted/expiry/last-seen model or universal pre-submit freshness policy. |
| Three NZ master-CV categories | High for storage/routing | Multi-CV library, role title/family mapping, explicit override and ambiguity handling. Country-specific metadata/templates absent. |
| One verified employment history | Medium | Shared canonical facts exist; variants remain full text without enforced claim/history references. |
| NZ CV tailoring | Medium | Same writer/assembly/validation pipeline reusable; no NZ document policy and current truthfulness caveats apply. |
| NZ cover/supporting documents | Medium-low | Studios/context/versioning/publication are reusable; underlying UK/NHS prompts and fixed examples need country selection. |
| Screening/relocation/work-rights disclosures | Medium-low | Cited answers, saved facts and manual review reusable; rights/answers not jurisdiction-scoped, relocation legacy conflict persists. |
| Application tracking and reviewed browser | High for generic process | Existing states, evidence, packet snapshots, approval/reconciliation and timelines are country-neutral enough to reuse. |
| NZ sponsorship suitability assessment | Low | No calibrated rule set, evidence confidence/freshness or distinction between accreditation, job eligibility, offer and candidate eligibility. Fit scoring is not this capability. |

### 12.2 Three NZ CV categories and one history

The requested categories map naturally onto current configurable variants and role packs:

1. **Software Development and Business Applications:** engineering, with business-analysis/application context as appropriate.
2. **Data Analytics and Business Intelligence:** data_bi.
3. **IT Support and Application Support:** a single reviewed master category can route by multiple title phrases, while job-specific tailoring selects support or application_support emphasis.

This is a reuse assessment, not proposed variant creation. Shared profile facts should remain the source for identity, employers, dates, education, qualifications and achievements. Country/role presentations can vary in ordering and emphasis. Current history duplication in text means that “supported by shared facts” must not be mistaken for automatic canonical consistency.

### 12.3 Accreditation, sponsorship and immigration evidence boundaries

Future NZ requirements involve distinct questions: employer legal identity, current accreditation evidence, whether a particular vacancy accepts overseas applicants or may support a work-visa process, candidate work rights, and current immigration rules. The existing UK pair `sponsor_licensed` / `sponsorship_explicit` does not capture all of these, and its lookup evidence must not be reused with changed labels.

No authoritative NZ data feed, supported verification API, accreditation terms/access entitlement or current immigration policy was validated in this review. In particular, configuring an employer portal is not accreditation verification; an accreditation result would not itself prove a job offer or candidate visa eligibility. An implementation should retain unknown/conditional outcomes and evidence/source/check date rather than manufacture confidence from absent data. Exact NZ legal/policy requirements and data availability belong to the separate design phase and authoritative-source validation.

## 13. Reusable components and gap-analysis table

| Capability | Existing implementation | Reusable? | Gap | Evidence |
| --- | --- | --- | --- | --- |
| Job searching | JobSpy board matrix, Workday CXS and smart direct sources; campaigns/budgets/previews | Yes, strongly | Validated NZ source coverage/access; local-board adapters if required; not every existing source is global | `discovery/jobspy.py::run_discovery()`; `discovery/workday.py::run_workday_discovery()`; `discovery/smartextract.py::build_scrape_targets()` |
| Location filtering | Country/state/city/remote objects; per-place provider market; selected/legacy filters | Yes | Persistent canonical vacancy country, `.nz`/source country handling, remote/hybrid precision and unknown-location decisions | `geography.py::normalize_location(),SelectedLocations,board_scope(),source_country()` |
| CV management | Multiple originals/variants, source edits/revisions, shared professional facts, generated history | Yes | Country profile metadata; enforced canonical-history consistency; generated DOCX if required | `cv_library.py`; `setup_workspace.py`; `professional.py`; `command_center.py::register_document()` |
| CV tailoring | Routed CV, role pack, draft selection, validators/judge, coverage/gap reports and PDFs | Yes, with policy changes | NZ format/context; strict evidence policy; deterministic fallback claims and title/skills tolerances | `scoring/tailor.py`; `scoring/tailor_strategy.py`; `scoring/validator.py` |
| Cover letters | Role/sector writer, versioned Studio, validation, export and publication | Yes | UK tone/fixed examples/closer facts; country policy selection and broader claim checking | `scoring/cover_letter.py::_ROLE_LETTER_PACKS,_build_cover_letter_prompt()`; `writing_studio.py` |
| Supporting statements | UK/NHS writer, criteria/examples planner, review and Studio | Yes, service pattern | Country-neutral/NZ templates and terminology; non-UK privacy/claim coverage | `scoring/supporting_statement.py::_build_prompt()`; `nhs_writing.py`; `writing_studio.py` |
| AI orchestration | Context-local task routing, provider adapters, caching, JSON repair, ledgers, existing pipeline | Yes | Country context and cost/validation consistency across legacy/interactive paths; no new framework required | `ai_routing.py`; `llm.py`; `pipeline.py`; `pipeline_store.py` |
| Application tracking | Separate execution/hiring/visibility states, timelines, notes/reminders, mail review, reconciliation/history | Yes | Country/visa evidence association; multiple typed deadline/interview/follow-up events if required | `command_center.py`; `submission_reconciliation.py`; `opportunity_insights.py`; `evidence_inbox.py` |
| Employer verification | Workday portal registry and UK sponsor-name lookup | Pattern only | NZ accreditation authority/access, legal-entity matching, dated evidence, refresh/unknown handling | `source_registry.py::save_employer()`; `uk_sponsorship.py` |
| Work-authorisation filtering | Global profile values, UK text signals, limited UK hard requirements, manual preference rules | Partial | Country-scoped rights/disclosure and explicit NZ unrestricted-rights/overseas/visa suitability rules; hard gate absent | `application_profile.py`; `uk_sponsorship.py`; `eligibility.py::exclusions()`; `scoring/tailor_strategy.py` |
| Country-specific configuration | Structured locations, country aliases, campaign overrides and arbitrary source templates | Partial, useful base | Country document/rights/accreditation/salary policy model; durable vacancy country and versioned policy/evidence | `geography.py`; `pipeline_workspace.py::campaign_config(),save_campaign()`; `database.py::_ALL_COLUMNS` |
| Application assistance | Approved packet + page-bound one-attempt assisted browser | Yes, preferred foundation | NZ disclosure/eligibility checks; consistent restrictions across older CLI/scheduled paths | `apply/assisted_browser.py`; `apply/application_packet.py`; `scheduler.py` |
| Freshness checks | Provider recency window and explicit availability observations | Partial | Structured posted/expiry/last-seen, recheck interval, direct-link validation | `discovery/jobspy.py::_run_one_search()`; `command_center.py::check_listing()`; `database.py` |

## 14. Missing capabilities

The following were not found as complete capabilities, rather than merely missing UI labels:

1. **NZ employer-accreditation evidence:** authoritative source integration, legal employer identity, accreditation state/date/evidence history and freshness policy.
2. **NZ visa/overseas suitability:** separate advert policy, candidate-country rights and possible sponsorship/work-visa process; exclusion of existing-unrestricted-NZ-rights-only adverts.
3. **Simultaneous country-safe profile preferences:** work rights, disclosure, relocation and compensation scoped to destination without duplicating employment history.
4. **Country-specific document profiles:** selected NZ prompts/templates/layout/terminology with preserved UK defaults; current Studios have no complete country policy selector.
5. **Enforced canonical history:** stable shared employment/qualification/claim identities across full-text master CVs and generated claims; conflict reconciliation is incomplete.
6. **Complete vacancy freshness and identity:** reliable posting/expiry/last-seen/requisition/employer identity; cross-board duplicates and employer-link verification.
7. **Validated NZ job-provider access/coverage:** no dedicated SEEK/Trade Me implementation found; generic custom URLs do not establish lawful/working access or completeness.
8. **NZ-specific regression/evaluation cases:** no accreditation, NZ work-rights disclosure, relocation, local terminology or end-to-end NZ fixture suite found. The current global test includes NZ alias handling, not the whole workflow.
9. **Uniform application safeguards:** old CLI/scheduled paths do not all require the integrated browser's human final approval and uncertainty semantics.
10. **Uniform cost/privacy handling:** different caps, usage ledgers and data scopes by path; not one global enforceable spend budget or country-specific personal-data policy.

Optional rather than prerequisite: generated DOCX exports, OCR, translation catalogs, normalized interview scheduling, recurring searches and formal occupation-code integration. Whether these enter scope is a product decision, not an inference that a new NZ workflow necessarily needs them. Evidence for the gaps is the contrasted schemas/function paths in sections 4–13 and the source search for NZ/accreditation/AEWV/occupation integrations.

## 15. Architectural options

These are architectural comparisons for a later decision, not implementation authorisation or a migration plan.

| Criterion | A. Dedicated NZ module | B. Country-specific configuration | C. Small hybrid |
| --- | --- | --- | --- |
| Reuse | Can reuse services if disciplined; highest temptation to copy workflows | Highest direct reuse for existing search/routing | High reuse, shared preparation/tracking, explicit NZ policy boundary |
| Complexity | Moderate/high if separate routes/state/studios are built | Lowest for locations/templates already configurable; insufficient alone for new accreditation/rights semantics | Moderate, limited to demonstrated policy/evidence gaps |
| Maintenance | Parallel UX/validation/automation can drift | Simple until business logic becomes scattered conditionals | Shared engine maintained once; country rules/evidence owned explicitly |
| User experience | Clear NZ destination but possible duplicated setup/history | Familiar campaign/settings UI; may hide important country context | Clear NZ context using existing workspace/campaigns; specialized evidence only where needed |
| Database | Risk of parallel tables/profile/doc pointers | YAML-first changes small; still needs additive storage for new facts/evidence | Additive country/evidence associations; one job/profile/history foundation |
| Security | More route/authorization paths and possible duplicated sensitive data | Existing workspace boundary reusable; global rights/answer reuse still unsafe | Same auth/workspace boundary, explicit destination-aware disclosures and evidence checks |
| Testing | Must test separate NZ paths plus shared services | Country combinations need tests even if stored as config; misconfiguration can leak into UK | Shared regression suite plus bounded country-policy and integration cases |
| International expansion | Another module per country risks repetition | Good for simple country differences | Good if policy boundary remains small; no need for a generic rules platform now |
| UK regression risk | Lower isolation only if genuinely separate; shared edits still affect UK | Changes to defaults/global settings can break UK | Preserve existing UK defaults; explicit new destination opt-in contains effects |
| AI tokens/operating cost | Potential duplicate prompts/context/reviews | Lowest added overhead where deterministic configuration suffices | Similar to B; accreditation/filtering can be deterministic and avoid unnecessary inference |
| Fit to actual requirements | Useful only if NZ truly demands a different end-to-end experience | Enough for a search/CV-preset pilot, **not** authoritative accreditation + multi-country rights | Satisfies the stated direction without a separate engine |

**Recommended: C, deliberately small.** The discovery/profile/CV/packet/tracking engine remains shared. Existing campaign configuration supplies most NZ search and CV selection. Country-specific accreditation/evidence, rights/disclosure and writing policy are explicit, bounded additions. A focused NZ workspace view can be a filtered experience rather than a separate frontend/backend.

**Why not configuration alone?** Adding an NZ city, title mapping and CV variant is configuration. Correctly distinguishing UK licence evidence from NZ accreditation, choosing the right country's authorization values and retaining checked evidence requires new semantics and code. Putting those facts into ad hoc text fields would hide rather than remove the complexity.

**Why not a full dedicated module?** Most requested workflows already exist. A duplicate NZ generator, application state machine or profile would introduce avoidable maintenance, history divergence and token use. No requirement demonstrated a need for a new queue service, database product, AI framework or microservice.

If the product owner reduces the first release to manually reviewed NZ discovery with existing document tools, configuration may be sufficient for that narrower milestone. It would still not constitute the proposed full accreditation-aware workflow.

## 16. Risks and constraints

| Risk | Existing mitigation / evidence | Remaining gap or constraint |
| --- | --- | --- |
| Break UK search/document/application workflows | Additive schema conventions; legacy location mode; campaign overrides; current tests and FMEA policy | Shared templates, global profile settings and source scoping remain coupled. New NZ semantics must be opt-in and covered by UK regressions. |
| Duplicate/conflicting profile/history | One workspace profile, revisions, shared evidence enrichment and source revisions | Full-text variants and fixed prompt examples can diverge; no canonical claim graph. Separate NZ profile copies would worsen this. |
| Duplicate CV-generation logic | Reusable role packs, route service, Studio publication and renderer | Separate NZ writers/studios would duplicate validation and cost; country selection should reuse existing functions. |
| Provider access, blocking and job-board terms | Source previews, error categories, retries/backoff, source skips, manual ATS/SSO catalogs | No licensing/robots/terms enforcement engine found. Public URLs, CXS calls, proxies, CAPTCHA helpers and a successful scrape do not establish permission. NZ source availability/entitlements unverified. |
| Incorrect sponsorship classification | Yes/No/Conditional/Unknown text signals, evidence snippets, register-name confidence/source/check time | UK-only logic unguarded by country; negative-before-conditional pattern ordering can over-classify conditional boilerplate. Accreditation and vacancy willingness are distinct. No NZ suitability calibration. |
| Outdated immigration/register evidence | UK CSV default seven-day freshness, source metadata, Unknown when no register | Stale disk cache can be returned after refresh failure; process-wide in-memory index is reused without expiry recheck. A recent job `sponsor_checked_at` is not proof of a newly fetched register. No NZ refresh/version policy. |
| Wrong legal employer/accreditation claim | UK exact/suffix normalized name matching avoids broad fuzzy guarantees | Names/brands/agencies/legal entities may differ; nonmatch is not a definitive accreditation failure. No authoritative NZ match ID/evidence review. |
| Fabricated experience/skills/qualifications | Evidence prompts, factual mode, watchlists, fact IDs, numeric checks, reviewer and candidate preview | Default soft evidence gates, reviewer tolerance, fallback project claims, fixed letter examples and narrow semantic checks remain. User-supplied facts are self-attested. |
| AI hallucinations or prompt injection | Untrusted-data instructions in statements, assistant, questions and email; field/value allowlists; literal-quote validation | Not uniformly enforced across all older prompts; quote existence does not establish every claim. Scoring/tailoring accept substantial untrusted job text without an equivalent general instruction boundary. |
| Wrong work-rights/relocation answers | Explicit saved values, sensitive local-only fields, no inference of blocked questions, candidate final review | Global authorization values can answer the wrong jurisdiction; exact saved answers are not country-scoped; legacy prompt says cannot relocate and permits close-equivalent tool claims. |
| Sensitive personal data | Isolated workspace routing, hashed auth, CSRF, encrypted AI/mail credentials, credential redaction, bounded context, UI backups omit `.env`/vault key | Profile/CV/message/answer/history and screenshots are plaintext files/SQLite. AI writing/scoring can receive full CV/contact data; native forms may send needed contact values. No retention/consent/data-residency enforcement model found. Local vault key is stored alongside workspace, not an external KMS. |
| Network/browser isolation | Source-preview/listing checks reject private addresses and check redirect targets; workspace-owned files/sessions | Those checks are not a uniform browser/discovery egress policy; integrated browser start checks HTTP(S)/host but not the full preview DNS policy. One shared host/container serves multiple workspaces. |
| Public hosting/auth bootstrap | Local multi-user sessions, admin guards/audit, login throttle | Open registration/first-user-admin, local HTTP cookies without Secure and externally mapped Compose port need deliberate operational deployment choices. No production TLS/multi-instance design established. |
| Unapproved/unintended submissions | Integrated reviewed packet, page signature/token, one attempt, human takeover, durable uncertain state and reconciliation | Legacy Claude default and scheduled LLM submit bypass equivalent final human approval. Legacy auto-consent/navigation and prompt-based dry-run are less restrictive. No platform-wide submission policy gate. |
| Duplicate applications | URL canonicalisation, locks, `applied_at`, uncertain guards, retired identities and duplicate warnings | Distinct URLs can represent one requisition; non-Indeed query-key stripping may also collapse distinct query-identified jobs. Older stale-lock replay semantics differ. |
| Unnecessary API costs | Task routing/caches, deterministic routing, source/run/attempt budgets, assistant/Studio allowances and usage estimates | Tailoring creates multiple drafts/reviews; outer repairs plus transport retries multiply calls. Form-answer/mapping and legacy endpoints lack uniform reservation caps. Cost estimates require complete usage and configured prices; historical pricing docs are not live billing. |
| Database changes/concurrency | WAL/busy timeout, short transactions, additive columns, workspace DB separation, safe SQLite backup | Schema changes happen on normal initialization; migrations on large/user DBs require backup/compatibility validation later. No distributed lock/broker. Single document pointer per job constrains simultaneous alternative workflow packets. |
| Background reliability | Durable schedule state/intents; interrupted runs held for review; bounded streaming; source health/logs | Server-bound daemon threads, in-memory runners/session capacity, broad rollback/error swallowing and no external supervisor/queue guarantee. Stalled/failed work still requires user action. |
| Backup/evidence completeness | UI/CLI export, SQLite backup includes WAL commits, safe import allowlists/path rebasing | `browser_sessions/` is absent from exported generated directories; DB references do not themselves preserve screenshots. Credential-free restore cannot decrypt old connector tokens without the original key. |
| Misleading readiness/analytics | Packet file/hash checks, separate execution/hiring outcomes, unique-opportunity metrics, unknown coverage labels | Fit/coverage is not visa/ATS/shortlisting probability; reachable is not open; prompt approval is not truth verification. Late advert requirements may be absent from truncated context. |

Evidence: `docs/FMEA_POLICY.md`; `database.py`; `geography.py`; `uk_sponsorship.py`; `source_registry.py`; `command_center.py`; `application_profile.py`; `scoring/tailor.py`; `scoring/validator.py`; `scoring/cover_letter.py`; `llm.py`; `ai_settings.py`; `mail_connectors.py`; `apply/assisted_browser.py`; `apply/launcher.py`; `apply/prompt.py`; `scheduler.py`; `background.py`; `workspace_transfer.py`. These risks are tied to inspected code paths; external legal/immigration facts were not inferred.

### 16.1 Verification coverage and future constraints

Relevant existing test sources include:

- Search/geography/dedupe/salary: `tests/test_global_search_and_visuals.py`, `test_jobspy.py`, `test_smartextract_targets.py`, `test_direct_source_recovery.py`, `test_database_dedupe.py`, `test_salary_filter.py`.
- Profile/studios/CVs: `test_native_studios.py`, `test_resume_assembly.py`, `test_tailor_strategy.py`, `test_tailor_lenient_mode.py`, `test_cover_letter_prompting.py`, `test_supporting_statement.py`, `test_nhs_writing_guidance.py`.
- Safety/lifecycle: `test_assisted_browser.py`, `test_application_packet.py`, `test_submission_reconciliation.py`, `test_apply_lock_recovery.py`, `test_job_lifecycle.py`, `test_question_assistant.py`.
- Operations/isolation/AI: `test_dashboard_multi_user.py`, `test_pipeline_reliability.py`, `test_pipeline_workspace.py`, `test_ai_routing.py`, `test_provider_recovery.py`, `test_evidence_inbox.py`, `test_workspace_transfer.py`.
- Offline evaluation: `scripts/ai_regression_eval.py`, `scripts/ai_eval_dataset.py`, `scoring/eval_harness.py`, `scoring/eval_dataset.py`; `scripts/agent_verify.py` runs compilation, pytest and both strict evals.

Test existence is not proof of live provider success or of NZ edge-case coverage. Playwright integration tests can be environment-gated. No tests/evals were executed during this report-only task. A future authorized implementation should use isolated fixtures for UK/NZ country switching, simultaneous work rights, unknown/stale accreditation, exact legal employer matches, non-fabricated document claims, page-bound approval, uncertain submissions, time zones and provider failure/cost controls. Live employer submissions are not an appropriate regression test.

## 17. Recommended integration direction

**Adopt a small hybrid, using existing configuration wherever it is sufficient.**

- Keep one candidate workspace/profile and professional-history foundation. Multiple NZ master CVs should be reviewed presentations of the same evidence, with current library/routing/version services reused.
- Retain the existing preparation stages, document renderer/Studios, application state/history/analytics, provider routing and approved assisted-browser flow.
- Use existing campaigns for NZ locations, titles, sources and CV-selection rules. Explicit country context must survive into work-rights, writing and employer-evidence decisions; search text alone is insufficient.
- Treat NZ employer accreditation and vacancy/candidate suitability as new, evidence-bearing country-specific capabilities. The UK sponsor registry remains UK evidence. Unknown/unverified results must remain distinguishable from rejection or confirmation.
- Make country document policy and accurate disclosures selectable without changing established UK defaults. Resolve existing truthfulness exceptions before claiming all generated NZ content is verified.
- Prefer the integrated candidate-reviewed browser foundation for future NZ assistance. Product-owner decisions are required before carrying legacy autonomous or scheduled-submit behaviour into that experience.

This is an architectural direction, not a detailed NZ module design, provider selection, schema migration or implementation plan. A normalized employment database, generic international rules platform or new AI framework is not automatically necessary. Additive country/evidence structures should be limited to facts that the actual approved workflow must store and enforce.

**Decision summary:**

| Question | Finding |
| --- | --- |
| Can existing search infrastructure target NZ? | Yes, geography/provider routing is already present; real coverage still needs validation. |
| Can the three requested master-CV categories be stored/routed? | Yes, existing variants/title-family mapping can represent them. |
| Can they share employment history? | Yes through shared profile facts; strict cross-document consistency is not currently guaranteed. |
| Can current UK sponsorship machinery establish NZ suitability? | No. |
| Can existing AI/document/tracking services be reused? | Yes, with explicit country context and stronger evidence/policy boundaries. |
| Is a separate job-search engine or AI framework needed? | No demonstrated requirement. |
| Is configuration alone sufficient for the full proposed workflow? | No; accreditation, country-scoped rights and country writing policy need extensions. |
| Has any NZ functionality been implemented by this review? | No. |

## 18. Questions requiring product-owner decisions

These are choices the repository cannot resolve, rather than technical questions answerable from the code.

1. **First-release boundary:** should the initial NZ experience cover reviewed discovery/documents/tracking only, or must accreditation and visa-suitability evidence be included before it is useful?
2. **Parallel markets:** should UK and NZ campaigns run concurrently for one candidate? If so, what destination-specific work rights, sponsorship need, relocation availability/start dates and compensation preferences should the candidate explicitly attest to?
3. **Unknown eligibility:** should adverts/employers with unknown overseas acceptance/accreditation remain in a review queue, be excluded from automatic preparation, or be hidden by default? How should conditional adverts be treated?
4. **Accreditation evidence standard:** what approved authoritative data access/source, freshness threshold, matching confidence and manual-review responsibility are acceptable? Is manually checked evidence sufficient when no suitable supported feed is available?
5. **Source priorities/access:** which NZ boards/employer sites matter most, and are licensed APIs/paid data access acceptable if generic discovery cannot provide adequate coverage?
6. **Document standards:** what NZ CV length/sections, language/tone and target examples should define acceptance? Are the three categories supplied as reviewed master documents or built from the saved professional profile? Is generated DOCX required?
7. **Truthfulness standard:** should all NZ claims require strict evidence, exact official employment titles and zero unsupported/“learnable skill” stretches? Who resolves conflicts between master CV text and shared profile facts?
8. **Submission policy:** should NZ be manual/approval-only, or may users explicitly authorize older autonomous and scheduled submission paths? Is fresh final approval required for every application?
9. **Data and spending policy:** which cloud AI providers/local models are acceptable for candidate/relocation/work-rights data, what retention should apply to evidence/screenshots/messages, and what per-user request/spend limits are desired?
10. **User experience:** is a dedicated NZ navigation/view required, or is a clearly labelled NZ campaign within the current workspace sufficient for the first approved scope?

### Report verification and conclusion

The report was cross-checked against the named implementation paths, route declarations, schema helpers and representative test sources. All 18 requested sections are present. Findings explicitly distinguish code capability, inference and unverified external behaviour. No live data or credentials are reproduced. `git diff --no-index --check /dev/null JOB_SEARCH_PLATFORM_DISCOVERY_AND_NZ_READINESS.md` passed. Final Git status showed the requested report as the only additional path relative to the initial status listing; existing application changes belong to the pre-review working tree. No application tests or live integrations were executed.

**SYSTEM DISCOVERY: COMPLETE. NZ INTEGRATION READINESS: MEDIUM. Recommended approach: small hybrid with maximal reuse. The separate NZ design and implementation phase requires explicit authorisation.**
