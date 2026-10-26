# New Zealand Job Search Integration Implementation Plan

- **Status:** Engineering plan only; no implementation authorised or performed.
- **Date:** 16 October 2026.
- **Normative architecture:** [NZ_JOB_SEARCH_INTEGRATION_DESIGN.md](NZ_JOB_SEARCH_INTEGRATION_DESIGN.md).
- **Discovery baseline:** [JOB_SEARCH_PLATFORM_DISCOVERY_AND_NZ_READINESS.md](JOB_SEARCH_PLATFORM_DISCOVERY_AND_NZ_READINESS.md).
- **Delivery approach:** Seven bounded phases; retain the shared platform and disable NZ until release gates pass.

## 1. Planning rules and evidence

**[E]** means existing inspected source; **[M]** missing capability; **[P]** proposed work; **[X]** external dependency; **[U]** unverified assumption. All tasks and schema changes are [P], even when the task references an existing file. Package paths in this plan are relative to `src/applypilot/`; test/document/script paths are repository-relative. No new application filename is asserted to exist. New service responsibilities may use small modules selected during implementation; their contracts and integration owners are specified below.

Instructions read: `AGENT_INSTRUCTIONS.md`, `docs/FMEA_POLICY.md`, `ops/repo_status.json`, active `ops/implementations/20261010-video-production/status.json`, `README.md` and the discovery report. No repository `AGENTS.md` was found. Existing user work is preserved. The two requested Markdown documents are the only deliverables of this design phase; no implementation records, source files, dependencies or data were changed.

Before any future engineering change, create the repository-required implementation plan/status/FMEA/pre-state record under the existing `ops/implementations/` process **after authorisation**. This plan is not a substitute for that operational record and does not execute its scripts.

### 1.1 Explicit compatibility exceptions

Product-owner decision E deliberately supersedes two older behaviours for GB and NZ:

1. Autonomous/scheduled/CLI submit intent cannot itself submit an external application; fresh human review is mandatory.
2. Unsupported factual stretches, inferred achievements and lenient factual publication cannot become final application documents.

Preserve UK searches, country-specific terminology, factual history, CV originals, submitted document bytes, routes/commands, config readability, integration configuration and statuses. Tests must prove those protected behaviours and separately prove the intentional safety changes. Do not claim “all UK behaviour identical” while changing the two policies. There is no production bypass flag restoring either policy.

### 1.2 External research dependency register

Research evidence and exact links are in design section 16. Findings affecting implementation:

| Dependency | Confirmed research result | Engineering disposition |
| --- | --- | --- |
| INZ employer tool | Public NZBN/name lookup, daily updates, some accredited employers unpublished | Implement manual official checks; missing result = unknown. |
| INZ automated access | Published terms prohibit automation/extraction unless otherwise agreed; no suitable authorised public accreditation API identified | No automated connector in V1. Written authority/access contract is a prerequisite for any later connector. No hidden-endpoint discovery. |
| Existing JobSpy 1.2.0 | Documentation lists NZ Indeed/Glassdoor, worldwide LinkedIn, US/Canada ZipRecruiter and unavailable Google Jobs | Test actual version-specific capability only after permitted access; geographic recognition is not launch approval. |
| Board rights | Indeed/LinkedIn restrictions; Glassdoor terms unavailable during review | New NZ automated routes disabled unless documented permission covers retrieval/storage/AI use; Indeed Apply automation not enabled. |
| Employer/Workday sources | Existing generic adapters; no specific NZ source proven here | Validate permitted NZ employer domains/portals before activation; otherwise manual reference/JD capture. |
| SEEK/Trade Me | No repository adapters or candidate-aggregation entitlement; official APIs have approval/purpose restrictions | Not required or presumed available in V1. |

No live job searches, official employer searches, provider tests or external forms were performed during this design. Lack of a public INZ API does not block the manual-verification release; lack of any validated automated job sources blocks claims of automated discovery coverage.

## 2. Task boundaries and target contracts

| Logical extension | Existing integration owners | Contract / non-goal |
| --- | --- | --- |
| CountryContext and policy resolver | `geography.py`, `pipeline_workspace.py`, `command_api.py`, `dashboard_server.py`, `application_profile.py` | Immutable per-operation country/revision snapshot; no global country switching or copied candidate workspace. |
| NZ employer/evidence service | `database.py`, `command_api.py`, record UI; UK precedent `uk_sponsorship.py` | Three proposed local tables and manual checks; no invented INZ API or reuse of UK licence flags as NZ accreditation. |
| Shared factual finaliser | `professional.py`, `setup_workspace.py`, `scoring/validator.py`, all writer callers | Canonical references and final-byte validation; no parallel CV generator or LLM-as-proof. |
| Common submission authority | `apply/assisted_browser.py`, `apply/application_packet.py`, `cli.py`, `apply/launcher.py`, `apply/llm_engine.py`, `scheduler.py` | One controlled executor and fresh actor-bound approval per attempt; no raw live autonomous tool bypass. |
| Country-aware request accounting | `llm.py`, `pipeline_store.py`, `writing_studio.py`, `ai_settings.py` | Existing caches/ledgers/reservations extended; no second cost platform or AI framework. |

**Proposed API contract additions**, implemented only within current authenticated route adapters:

- Explicit country/profile/campaign context on new workspace operations; persisted job country wins over a conflicting request.
- Versioned country-profile read/update operations with expected revision.
- Employer lookup/link and manual evidence record/history operations; read-only official-source links, not automatic verification fetches.
- Country-scoped answer/reconciliation operations and a thin permitted manual job-capture action.
- Review/approval responses use the existing browser command surface; fresh approval is not an engine submit boolean.
- Reuse existing job, Studio, packet, history, Insights, calendar, export and reconciliation routes with optional/additive metadata. Legacy no-country calls cannot sweep NZ rows into old global queues.

Exact new URL names should be defined alongside route tests during implementation. They are proposals, not documented existing endpoints. All new mutations reuse user ownership, CSRF and same-origin controls; country scoping is enforced on server services, not merely frontend filters.

## 3. Dependency order and complexity

```text
P1 country foundation + safety containment
    -> P2 validated discovery / metadata
    -> P3 employer identity + manual accreditation + recruitment evidence
    -> P4 3 CVs + canonical claims + country documents
    -> P5 preparation / tracking / costs / portability
    -> P6 controlled assisted applications + universal fresh approval
    -> P7 end-to-end UK/NZ verification and gated release
```

Country isolation must exist before country-scoped imports/answers/generation. Accreditation can work manually before a connector. Full shared factual validation is the largest document change. Containment of unsafe legacy submit routes precedes exposing any new managed application path; complete approval hardening comes before release, not as a post-release task.

**Complexity scale:** small = bounded validation/config/metadata change; medium = several interacting modules plus persistence/UI tests; large = cross-cutting correctness/security contract with migration/compatibility and adversarial tests. These are relative complexity estimates, not elapsed-time promises. No speculative delivery dates are assigned. Implementation phases may be reviewed separately but are not automatic production deployments.

## 4. Phase 1 — Country foundation and compatibility/safety containment

**Scope:** Establish durable country identity, disabled NZ UX and immutable request/run context. Inventory and contain submission/factual bypasses so later features cannot accidentally activate them.

**Reuse [E]:** `multiuser.py`; `geography.py`; `pipeline_workspace.py`; `database.py::init_db(),ensure_columns()`; existing command/API/runner and revision patterns; current local workspace isolation.

### Tasks

| ID | Bounded engineering task | Existing targets / evidence |
| --- | --- | --- |
| P1.1 | Capture protected UK baseline from synthetic/approved backup fixtures: queries, source selection, CV bytes/paths, root/campaign config, documents, execution/hiring states, API/CLI utility outputs. Record the two intentional exceptions. | `AGENT_INSTRUCTIONS.md`; `docs/FMEA_POLICY.md`; `tests/test_dashboard_multi_user.py`, `test_pipeline_workspace.py`, `test_command_center.py`. |
| P1.2 | Add proposed `cc_country_profiles`; add nullable job country and document/session country metadata at the phases that need them. Use explicit column names before extending any full-row insert. Migration rerun must be a no-op. | `database.py`; schema helpers; `cv_library.py::import_files()`; `apply/assisted_browser.py::start()` currently have positional inserts. |
| P1.3 | Implement CountryContext resolver, code validation/aliases and versioned country policy references. Existing job/campaign conflict rejects; no NZ fallback to global work rights/settings. | `geography.py`; `pipeline_workspace.py::campaign_config(),save_campaign()`; `application_profile.py`. |
| P1.4 | Add request-local country view state and backend feature gate; NZ hidden/denied by default. New country endpoints use optimistic profile revisions and no credentials. | `web/workspace.html`, `web/workspace.js`, `command_api.py`, `dashboard_server.py`. |
| P1.5 | Snapshot explicit country/campaign/config into run metadata and subprocess scope. Ensure no-country CLI/legacy APIs exclude NZ queues, while direct NZ job operations resolve country and enforce its policies. | `_PipelineRunner.start(),_write_search_override_if_needed()` in `dashboard_server.py`; `cli.py`; `eligibility.py::candidates()`; `database.py::get_jobs_by_stage()`. |
| P1.6 | Add server-side mismatch validation to bulk/legacy setup/Studio/browser operations; prevent tab-switch responses mutating the wrong context. Preserve null-country legacy records without auto-classifying all as UK. | `command_api.py`; `_Handler` routes; `web/workspace.js`, `web/pipeline.js`, `web/studios.js`. |
| P1.7 | Inventory every live actuator and publication/export path. Add fail-closed containment for old autonomous/scheduled submit requests and new-country application-ready generation until their common gates are implemented. Preserve configs/intent; do not mutate completed records. | `cli.py::apply()`; `apply/launcher.py::main(),run_job()`; `apply/llm_engine.py`; `scheduler.py::tick()`; `_PipelineRunner.start()`; `pipeline_store.py::persist_document()`. |

**Dependencies:** future implementation authorisation; isolated test workspace; agreed compatibility exceptions. No INZ or job-provider credential required.

**Tests:** extend `test_global_search_and_visuals.py`, `test_pipeline_workspace.py`, `test_dashboard_multi_user.py`, `test_selected_only_filters.py`, `test_workspace_routes.py`, `test_dashboard_setup_endpoints.py`; add resolver, no-country legacy-sweep, wrong-country ID, stale revision, interleaved browser-tab/worker and migration-rerun cases. Assert direct helper invocation cannot bypass containment.

**Acceptance criteria:** GB data/config/files unchanged; NZ flag false at server and UI; explicit NZ context resolves consistently; old no-country jobs remain accessible; wrong-country mutations rejected; two candidate workspaces isolated; scopes cannot leak through environment/global state. No unattended submit is possible in the new execution build. Existing command syntax and utility modes remain accepted.

**Rollback:** keep NZ off; retain additive fields/country rows; invalidate queued contexts and approvals. Use a compatible safety build, not an old binary that ignores country/approval fields. No destructive down migration.

**Risks:** root config shallow merges, old helpers reading global profile, broad queue queries, nullable-country misclassification, positional inserts and changed schema defaults. **Complexity: Large**, because correctness spans UI/API/CLI/background paths; schema additions alone are small.

## 5. Phase 2 — NZ campaigns, permitted sources and vacancy metadata

**Scope:** Configure NZ discovery on existing engines with measured source readiness, country/provenance, correct URLs/freshness/salary and honest recovery.

**Reuse [E]:** current source registry, preview, query/location expansion, JobSpy and Workday/direct engines, source health, URL storage/dedupe and listing checks.

### Tasks

| ID | Bounded engineering task | Existing targets / evidence |
| --- | --- | --- |
| P2.1 | Add one new NZ campaign, primary Auckland/Wellington/Christchurch and optional NZ places/remote. Encode the three role groups and explicit selected-location filtering; keep root/UK campaigns byte-equivalent except intended additive campaign list entry. | `pipeline_workspace.py::save_campaign(),preview()`; `geography.py`; `web/geography-editor.js`, `web/pipeline.js`. |
| P2.2 | Extend source metadata validation/serialization for countries, permitted methods, terms reference, storage/AI/application modes, reviewed date and identity query keys. Enforce at source preview and execution. Treat empty NZ source/board selection as none. | `source_registry.py::validate_source(),catalog(),preview()`; `config.py::load_sites_config()`; discovery entry points. |
| P2.3 | Build reviewed source fixture/acceptance matrix. Prefer approved direct employer domains/Workday tenants. Existing NZ-capable boards stay disabled until permissions and live read-only validation pass; no Google/ZipRecruiter/SEEK/Trade Me assumptions. | `discovery/jobspy.py::_run_one_search(),run_discovery()`; `discovery/workday.py`; `source_registry.py`. |
| P2.4 | Add normalized observation envelope at all three ingestion paths: provider/requisition ID, original listing/direct apply URL, advert country evidence, source dates, arrangement, salary, content hash and timestamps. Promote descriptions without skipping country/evidence work. | `discovery/jobspy.py::store_jobspy_results()`; `discovery/workday.py::_fetch_one_detail(),store_results()`; `discovery/smartextract.py::_store_jobs_filtered()`; `enrichment/detail.py`; `pipeline_store.py::tag_job()`. |
| P2.5 | Preserve meaningful query IDs/navigation URLs for new NZ records; use exact source IDs/canonical aliases for dedupe and warnings for fuzzy matches. Never bulk-rekey existing jobs or strip actual apply tokens. Update last-seen/provenance/campaign membership on duplicates without resetting state. | `database.py::normalize_url(),find_existing_job_url(),store_jobs()`; source-specific store functions; `command_center.py::details()`. |
| P2.6 | Extend availability checks to safe actual destinations, source expiry and provenance; retain reachable/unknown/closed distinctions and first-seen vs posted vs checked. Block automatic queries to prohibited evidence/board services. | `command_center.py::check_listing()`; `enrichment/detail.py`; `scheduler.py` availability use; `web/workspace.js`. |
| P2.7 | Parse/render NZ salary metadata, including k-ranges, intervals, currency evidence, hours/package/KiwiSaver qualifiers. Read NZ compensation; unknowns remain discoverable; no implicit NZD/GBP conversion or immigration threshold. | `discovery/salary_filter.py`; discovery adapters; `application_profile.py`; current salary display. |
| P2.8 | Add thin manual employer-URL/JD capture when no permitted adapter is available; reuse storage/block/dedupe checks. Reference-only restricted-board records cannot silently fetch or send prohibited text to AI. | `command_api.py`; `database.py::store_jobs()`; source URL-validation helpers. |

**Dependencies:** P1. For automated activation, [X/U] source permission covering collection, retention and AI use plus actual reliability evidence. These are not dependencies for manual capture. No new provider installation is required by the design.

**Tests:** extend `test_jobspy.py`, `test_smartextract_targets.py`, `test_direct_source_recovery.py`, `test_database_dedupe.py`, `test_salary_filter.py`, `test_enrichment_url_resolution.py`, `test_pipeline_reliability.py`. Use recorded/permitted fixtures: wrong-country returns, unknown advert location, hybrid ≠ remote-only, NZ provider payload, partial provider failure vs valid empty results, query-ID collisions, tokenized direct link, expired/403/timeout, rediscovery and mixed salary bases.

**Acceptance criteria:** no UK filters/source selection changed; effective preview lists sources and restrictions truthfully; original links survive round-trip; dates have distinct meanings; unknown salary/rights do not delete jobs; no NZ automatic source enabled without evidence. At least the selected release source set must pass a permitted read-only acceptance test before advertising automatic search. Zero valid automatic sources yields explicit manual mode and a blocked automated-coverage gate, not a false success.

**Rollback:** disable individual NZ sources/campaign execution, preserve observations/jobs/old URLs, stop queued source operations; no UK registry mutation or data deletion.

**Risks:** terms/availability drift, source-template metadata loss, partial failures reported as zero, legacy canonicalization, query×city×source explosion and global compensation. **Complexity: Medium–Large**, depending on actual permitted sources; no estimate assumes a new board adapter.

## 6. Phase 3 — Employer identity, manual accreditation and overseas evidence

**Scope:** Deliver complete first-release manual verification and transparent recruitment classifications before any NZ “ready to apply” claim.

**Reuse [E]:** record panels, upload/path/type protections, `cc_events`, URL/source validation patterns; UK sponsorship logic as a precedent only; current extraction and job metadata.

### Tasks

| ID | Bounded engineering task | Existing targets / evidence |
| --- | --- | --- |
| P3.1 | Add proposed `cc_employers`/`cc_employer_evidence` tables/indexes; implement bounded identity/matching/observation/reduction service. Distinguish recruiter, brand, subsidiary and legal hiring employer. | `database.py` initialization; `command_api.py` service entry points; no existing NZ service. |
| P3.2 | Implement manual official-check UI and API: exact NZBN/name query, official source, actual check/recorded times, result, quote/artifact/hash, actor and source type. No automated INZ URL fetch/iframe/search. | Existing job detail/portfolio upload security patterns in `command_api.py`; `web/workspace.js`; new bounded evidence panel responsibility. |
| P3.3 | Implement reduction for identity ambiguity, fresh/stale, source-expired, unavailable/conflicting and last-successful check. `not_listed` remains unknown; refresh failure preserves old success/date. Validate source dates and explicit supersession/resolution. | Proposed evidence service; existing event/lifecycle conventions. |
| P3.4 | Extract literal vacancy work-rights/overseas/AEWV candidates using deterministic sentence/section rules; reuse existing extraction only for suggestions if available. Store content hash, quote, scope, conditions, dates and rule version. | `enrichment/detail.py`; `discovery/*` promoted-description paths; `jobs.immigration_assessment` proposal. |
| P3.5 | Compute the four required recruitment labels; keep technical fit independent, silence unknown, conflicts reviewed. Accreditation may support potential-review only. Show employer vs advert vs candidate evidence separately. | `command_center.py::overview(),details()`; record/Jobs UI; `scoring/scorer.py` caller separation. |
| P3.6 | Guard UK sponsor annotation to the resolved UK/legacy-compatible path; never write new NZ evidence into `sponsor_licensed`/`sponsorship_explicit`. Preserve historical UK values. | `enrichment/detail.py` currently invokes `uk_sponsorship.annotate_sponsorship()` without country guard; `uk_sponsorship.py` remains UK. |
| P3.7 | Add local stale-evidence reminders only. Feature remains functional when official site unavailable; manual/employer-provided evidence has explicit provenance and cannot masquerade as an official API result. | `background.py`, `command_center.py` reminders/events; no new polling service. |

**Dependencies:** P1; P2 envelope or permitted manual job capture. [X] public INZ tool for human checks; [U] private authorised integration remains optional and out of V1 scope.

**Tests:** extend `test_command_center.py`, `test_pipeline_workspace.py`, `test_provider_recovery.py`, `test_global_search_and_visuals.py`; add fixture-driven NZ evidence tests in the existing test suite. Cases: 13-digit format vs real ownership, unpublished employer, explicit expired status vs stale observation, contradictory current sources, agency/client ambiguity, unrelated group company, failed recheck, future timestamps, edited artifacts, source provenance and UK annotation no-op for NZ. Text tests cover negation, conditional/boilerplate, mixed countries and partial JD.

**Acceptance criteria:** auditable manual official accreditation verification works; no API invented or automated INZ access; no-result unknown; stale and expired meanings distinguishable; all four labels visible/filterable; unknown jobs remain in default discovery; changing accreditation does not change technical fit. Evidence cannot declare visa/job-check approval or present accreditation as sponsorship confirmation.

**Rollback:** disable NZ evidence editing if faulty, retain observations and render safe unknown state; block new conclusions rather than rewriting history. Other country evidence remains untouched. No destructive table removal.

**Risks:** wrong legal entity, misleading absence, daily source changes, stale-cache success inflation, missing quote applicability and misleading badge wording. **Complexity: Medium** for manual V1; any authorised feed is a separately scoped future task.

## 7. Phase 4 — Three NZ master CVs and evidence-only country documents

**Scope:** Reuse master library/routing and current generation/Studio architecture while making shared canonical facts authoritative. Apply common factual finalisation to all countries; keep country-specific UK/NZ wording separate.

**Reuse [E]:** `cv_library.py`, `role_routing.py`, `professional.py`, profile/source revisions, existing role packs/writer/reviewer/validators/renderer and writing versions/publication.

### Tasks

| ID | Bounded engineering task | Existing targets / evidence |
| --- | --- | --- |
| P4.1 | Extend source metadata with country/category/reconciliation manifest; import three supplied masters through existing extraction/original retention. Use returned collision-safe keys. Prevent existing import role assignment from changing root UK routing. | `cv_library.py::import_files(),assign_role(),action()`; `setup_workspace.py` variant writers. |
| P4.2 | Map all owner-listed exact titles and reviewed family keywords to the three NZ variants; preserve longest-title/tie handling. Unknown/ambiguous/unavailable NZ routes require selection, never silent UK default. Rename/archive updates nested campaign references. | `role_routing.py::route_resume_for_job()`; `pipeline_workspace.py`; `web/pipeline.js`; `cv_library.py::action()`. |
| P4.3 | Add canonical stable fact/entity IDs, typed period/credential metadata and verification manifest references/hashes. Extend validators that currently strip/reject new metadata. UI reconciliation shows source CV claim vs canonical fact/evidence. No automatic source-fact verification. | `setup_workspace.py::_validate_profile_patch(),_validate_project_entry_list()`; `professional.py`; CV/Profile/Portfolio editors; `cc_profile_revisions`. |
| P4.4 | Implement conflict actions: correct presentation, verified shared-profile correction, exclude claim, or unresolved. Profile update invalidates only dependent future claims/documents in both countries and retains all original/submitted bytes. | `professional.py`; `cv_library.py`; `command_center.py` revision/document registry; `role_routing.py::score_fingerprint()`. |
| P4.5 | Add versioned GB/NZ document policy composition. NZ summaries/CVs/covers/statements/questions use appropriate terminology/limits and approved country disclosure; GB NHS planning remains available. | `_build_tailor_prompt(),assemble_resume_text()` in `scoring/tailor.py`; `scoring/cover_letter.py`; `scoring/supporting_statement.py`; `nhs_writing.py`; `writing_studio.py::context()`. |
| P4.6 | Remove common unsupported-fact tolerances and fallbacks: reviewer minor stretches/learnable skills, lenient factual bypass, guessed project years/bullets, fixed personal-employer examples and unverified closer claims. Do not remove actual verified UK/NHS history. | `scoring/tailor.py::_strict_evidence_enabled(),_build_judge_prompt(),assemble_resume_text()`; `scoring/cover_letter.py::_ROLE_LETTER_PACKS` and polishing functions; `apply/prompt.py` close-equivalent claims/relocation wording. |
| P4.7 | Implement structured final claim manifest and hard final-byte gate. Literal/approved meaning-preserving fact wording or verified computations only; new prose remains draft until evidence-linked wording review. Validate all factual sections after polishing, including summaries/numbers/durations/credential status. Reviewer cannot override. | `professional.py`; `scoring/tailor_strategy.py`; `scoring/validator.py`; writer callers; existing reports. |
| P4.8 | Enforce same finaliser at batch persistence, Studio publish, custom/manual documents, legacy match/statement APIs, application-ready export/PDF and browser upload. Track document country/policy/fact/claim hashes; edits invalidate approval. Historical artifacts remain truthful/unverified until reviewed. | `pipeline_store.py::persist_document()`; `writing_studio.py::save(),validate(),publish()`; `dashboard_server.py::_run_manual_job_match()` and `/api/statement/generate`; `apply/application_packet.py`; `scoring/pdf.py`; `command_api.py`. |
| P4.9 | Extend country rights/disclosures/relocation/answers; no sample default rights. Exact wording approved by candidate; permit expiry and non-NZ jurisdiction/negation/condition handling. Keep global legacy answers unchanged but unavailable for NZ sensitive auto-fill. | `application_profile.py::application_values(),local_fills()`; `apply/question_assistant.py`; `writing_studio.py`; `command_api.py` answer handling. |
| P4.10 | Preserve TXT/PDF/HTML and uploaded original DOCX; test document length/text parsing. Explain employer DOCX requirements and manual alternative without fake format support. | `scoring/pdf.py`; `cv_library.py::extract(),export_pdf()`; `web/studios.js`. |

**Dependencies:** P1 country profile/policy contracts; candidate-supplied actual master CVs and canonical evidence (not accessed in this design); P3 for any employer evidence/disclosure included in documents. No new AI provider/model/framework.

**Tests:** extend `test_native_studios.py`, `test_resume_assembly.py`, `test_tailor_strategy.py`, `test_tailor_lenient_mode.py`, `test_validator_required_sections.py`, `test_cover_letter_prompting.py`, `test_supporting_statement.py`, `test_nhs_writing_guidance.py`, `test_question_assistant.py`, `test_pdf_letter_html.py`, `test_pdf_highlighting.py`, strict regression/dataset evals.

Adversarial examples: unseen tool inserted with valid fact ID; real number copied to wrong employer; inflated duration/overlapping dates; project made into employment; incomplete certificate made into a qualification; guessed dates; invented direct NHS/NZ experience; claims injected by deterministic closer; custom CV/supplemental facts marked verified by an AI; manual edit after approval; wrong-country visa answer; exact repeated question with different GB/NZ answers. Positive cases preserve truthful reordering, omission, target headline, verified paraphrase, real prior NHS employer in NZ output and UK criteria examples.

**Acceptance criteria:** all three masters imported/revisioned without loss or UK mapping changes; all target title routes pass; unresolved claims cannot become final; no learnable-skill/stretch bypass; shared professional history remains one authority; country-disclosure text cannot be inferred from job location. All publish/export/upload entry points apply the same gate. TXT/PDF round-trip keeps required content and no unsafe truncation.

**Rollback:** turn off NZ generation; keep imports/originals/revisions; retain invalid drafts and diagnostics separately from current approved pointers. Continue safe deterministic verified-text/manual preparation; do not restore stretch allowances. A faulty factual gate blocks finalisation until corrected; existing submitted artifacts are not rewritten.

**Risks:** semantic-entailment overclaim, free-text history ambiguity, broad watchlist false positives, noisy duration computations, fixed filler, global fingerprint invalidation, candidate reconciliation burden and multiple writer call cost. **Complexity: Large; highest document correctness work.** The plan deliberately does not treat an LLM reviewer or citation existence as a proof system.

## 8. Phase 5 — NZ preparation, tracking, usage and portability

**Scope:** Complete country-scoped end-to-end preparation/tracking and remove repeat-generation/cost/backup gaps without a new pipeline.

**Reuse [E]:** `pipeline.py`, `eligibility.py`, application packet, command center, inbox, lifecycle, Insights, existing task client/caches/ledgers, scheduler preparation and workspace transfer.

### Tasks

| ID | Bounded engineering task | Existing targets / evidence |
| --- | --- | --- |
| P5.1 | Implement NZ technical-only score context and separate practical/immigration panels. Accreditation updates do not rescore technical fit; missing sponsorship never removes a technical match. Preserve existing 1–10 scale/model routing. | `scoring/scorer.py`; `scoring/tailor_strategy.py` UK hard constraints; `command_center.py`; `pipeline_workspace.py::inspect_job()`; `web/workspace.js`. |
| P5.2 | Thread persisted country into packet requirements/answers/readiness and scopes. Same vacancy in multiple campaigns points to one application; ambiguous/multi-country vacancy requires chosen destination before preparation. Existing status values unchanged. | `eligibility.py::document_requirements(),packet(),candidates()`; `apply/application_packet.py`; `database.py` queues; `pipeline_store.py`. |
| P5.3 | Add explicit final document approval manifest, separate from validated draft and later submit approval. Persist hashes of the actual reviewed TXT/PDF and rights/fact/policy revisions; update/invalidation is atomic. | `writing_studio.py::publish()`; `cc_documents.context_json` proposal; `apply/application_packet.py::status(),prepare()`; record/Studio UI. |
| P5.4 | Extend timelines, reminder/calendar, hiring updates, inbox matching/review, history/retired snapshots and Insights with derived immutable country. Do not trust selected UI country for a message's matched application. | `command_center.py`; `evidence_inbox.py`; `inbox_identity.py`; `job_lifecycle.py`; `opportunity_insights.py`; `insights_visuals.py`; `command_api.py`. |
| P5.5 | Add operation/request keys, leases, dedupe and country metadata to existing reservations/ledgers; scope all caches to country/policy/fact dependencies and avoid charging identical in-flight retries. Apply request/token caps before model calls, including forms and legacy endpoints. | `writing_studio.py`; `pipeline_store.py::record_usage()`; `llm.py`; `ai_settings.py`; `command_center.py::assistant()`; `apply/question_assistant.py`. |
| P5.6 | Correlate pipeline/interactive usage to avoid double counting; unknown usage/prices remain unknown, workspace limits sum GB+NZ. Bound transport/repair/draft/reviewer retries under one operation. | `cc_ai_usage`, `cc_pipeline_usage`, `cc_writing_requests` proposal; `llm.py`; Insights. |
| P5.7 | Make new-input fingerprints dependency-scoped. Appending NZ YAML or verification metadata must not mark all UK outputs stale or rescore every UK job. Keep historical fingerprints and a proven pre-activation compatibility baseline rather than relabelling unknown documents current. | `command_center.py::fingerprint()` currently hashes all searches/variants; `role_routing.py::score_fingerprint()` hashes full profile; `pipeline_workspace.py::invalidate_scores()`; `writing_studio.py` profile hashes. |
| P5.8 | Export/import country profiles/employer evidence/claim manifests and governed browser receipt artifacts; rebase owned paths, invalidate imported approvals, preserve legacy archive readability. Do not include decrypted private proofs in credential-free backups by accident. | `workspace_transfer.py` allowlists/backup/path rebasing; `job_lifecycle.py` references/cleanup; `command_api.py` export. |

**Dependencies:** P1–P4; decisions on retention/budget defaults. Existing current AI routing settings remain; no hardcoded future price required.

**Tests:** extend `test_pipeline_reliability.py`, `test_pipeline_workspace.py`, `test_application_packet.py`, `test_command_center.py`, `test_opportunity_insights.py`, `test_evidence_inbox.py`, `test_inbox_identity.py`, `test_ai_routing.py`, `test_provider_recovery.py`, `test_workspace_transfer.py`, `test_job_lifecycle.py`. Include permission-restricted text not sent to AI, identical concurrent generation, failed reservation/restart, budget exhaustion, missing price/token counts, relevant-vs-unrelated revision invalidation and country history after retirement/import.

**Acceptance criteria:** complete NZ prepare/review/track flow without submission; GB/NZ data scopes remain separate, shared history intact; identical request returns/reuses same result; no duplicate provider accounting; evidence-only update causes no needless CV generation; history/backup restore contains country/evidence references and never revives approval tokens.

**Rollback:** disable affected NZ preparation/Insights write actions, retain authoritative DB/files/usage. Invalidate incomplete operations and packet approvals, not submitted history. No restore over post-backup applications.

**Risks:** broad legacy hash invalidation, duplicate ledgers, expired leases replaying costs, legacy inbox wrong-country review, deleted shared evidence, incomplete exported screenshots and vault-key recovery. **Complexity: Medium–Large.**

## 9. Phase 6 — Controlled assisted applications and fresh universal approval

**Scope:** Complete decision E across every live submission path. Reuse the integrated browser, not a new application agent framework. Unsupported sites use manual external-browser handoff.

**Reuse [E]:** `apply/assisted_browser.py` document approval/page signature/session state/receipt screenshots; `apply/application_packet.py`; locks/events/uncertain statuses and `submission_reconciliation.py`.

### Tasks

| ID | Bounded engineering task | Existing targets / evidence |
| --- | --- | --- |
| P6.1 | Extend final review to exact country/employer/destination/packet/rights/policy/fact/complete answer+consent hash and authenticated candidate actor. Persist review epoch/revision. Mandatory current packet token; changes invalidate it. | `Session.final_review(),signature(),start()` and current optional packet token in `apply/assisted_browser.py`; `apply/application_packet.py`. |
| P6.2 | Implement five-minute configurable, random single-use approval capability; persist only hash/actor/review/expiry metadata. CAS consume and durable submitting intent before action. Restart/logout/actor mismatch/revision change invalidates; no token in prompt/command/queue/log. | `Session.submit(),transition()`; `cc_browser_sessions.approval_json/approval_revision` proposal; current authentication/CSRF. |
| P6.3 | Make owned controlled executor sole live actuator. Agents receive only narrow action/value references; remove unmediated live MCP/CDP/arbitrary JS/shell/network/credential access. Unknown live adapters fail closed. | `apply/launcher.py::_make_mcp_config(),run_job()`; `apply/llm_engine.py`; browser session commands; `cli.py`. |
| P6.4 | Harden tested/permitted ATS paths against pointer/keyboard Enter, implicit submits, frame/popups, JavaScript requests and uncontrolled network channels. DOM event guard is defence in depth, not sole enforcement. Classify/allow draft-save/upload/navigation separately; deny unknown state changes. | `assisted_browser.py::GUARD,Session.advance(),command(),click_control(),submit()`; `web/assisted-browser.js`, `web/browser-frame-view.js`. |
| P6.5 | Route existing CLI/legacy API submit intent through controlled review/handoff. Keep arguments/utility commands; `--submit`/default Claude no longer grants approval. Noninteractive invocation returns documented `approval_required` status/nonzero outcome without external submission. Direct helper calls enforce same boundary. | `cli.py::apply()`; `apply/launcher.py::main(),worker_loop(),run_job()`; `apply/llm_engine.py::_worker_loop(),_maybe_submit_application()`; `command_api.py` apply route; `_PipelineRunner.start()`. |
| P6.6 | Scheduled submit rows retain original mode/intent but prepare/hold for current review. Continuous/parallel workers queue independent reviews; never batch-approve or replay stale locks. No auto-consent or declaration synthesis. | `scheduler.py::schedule(),tick()`; `background.py`; apply acquisition/checkpoint helpers. |
| P6.7 | Unify post-attempt outcome: missing receipt or crash after consumption -> submission_uncertain, not prepared/safely retryable. One verified not-submitted reconciliation enables new review, not automatic retry. | `apply/llm_engine.py` currently returns prepared when confirmation missing; `apply/launcher.py::mark_result()`; `submission_reconciliation.py`; `command_center.py::release_attempt()`. |
| P6.8 | Enforce destination/source restrictions, current approved documents, question-scoped NZ disclosures and duplicate employer/requisition checks before preparing/uploading/submitting. Model cannot infer visa/consent or approve finalisation. | `source_registry.py`; `application_profile.py`; `question_assistant.py`; packet/session start; current block/preference checks. |
| P6.9 | Provide truthful manual handoff for unsupported/disallowed sites. Keep reviewed documents/answers and manual outcome evidence; do not treat opening a page as submitted or certify independent external-browser clicks. | Existing external application links and manual mark/reconcile routes; `web/assisted-browser.js`. |

**Dependencies:** P1 containment, P4 common factual gate and rights, P5 approved packet/provenance. [X/U] approved employer/ATS automation access and actual adapter semantics. No universal arbitrary-site guarantee.

**Tests:** extend `test_assisted_browser.py`, `test_application_packet.py`, `test_question_assistant.py`, `test_apply_lock_recovery.py`, `test_submission_reconciliation.py`, `test_profile_browser_speed.py`, `test_browser_recovery.py`, `test_browser_interaction_latency.py`, `test_dashboard_multi_user.py`, `test_command_center.py`. Build local synthetic ATS fixtures with submission counters and network logs; no real employer submissions.

Required negative tests: forged/wrong-country/wrong-user/replayed/expired token, expired candidate session, altered files/PDF/disclosure/answers/target, stale final-control index, two simultaneous approvers, no-profile work-rights answer, current form mismatch, programmatic `requestSubmit`, hidden request/fetch, auto-submit on last field, Enter key, popup/cross-origin frame, raw CDP/MCP/tool attempt, allowlisted navigation with unexpected mutation, schedule/CLI/direct-helper bypass, retry after connection drop and crash before/after request dispatch. Assert **zero external submit transactions before approval** and no second authorized attempt after consumption.

**Acceptance criteria:** no supported platform path submits without a fresh candidate-bound current review; all legacy entry points respect common authority; server-side enforcement proven at actuator/network boundary; unsupported semantics cause manual handoff; receipt ambiguity becomes uncertain; no automatic replay. Existing UK statuses, records and factual packet flow remain compatible except the specified approval change.

**Rollback:** disable managed submission capability and retain manual application/preparation/tracking. Revoke outstanding approvals; hold interrupted work uncertain for reconciliation. Never roll back by restoring autonomous paths, older binaries or blanket skip flags. Keep historical receipt/evidence files intact.

**Risks:** false universal-safety claims from label heuristics, JS/network bypass, overbroad credentials, TOCTOU between review/click, irreversible external side effects and duplicate submissions. **Complexity: Large**, high-impact FMEA required; adapter coverage is intentionally bounded.

## 10. Phase 7 — End-to-end validation, migration rehearsal and release

**Scope:** Verify the full approved design with NZ initially off, then enable only a validated source/feature cohort. This is the first production-release decision, not an automatic consequence of finishing earlier tasks.

**Reuse [E]:** current pytest/Playwright fixtures and `scripts/agent_verify.py`, `scripts/ai_regression_eval.py`, `scripts/ai_eval_dataset.py`; workspace transfer and operational FMEA/status records.

### Tasks

| ID | Bounded engineering task | Verification evidence required |
| --- | --- | --- |
| P7.1 | Run migration twice on representative old synthetic/approved backup DBs; compare protected rows/config/artifact hashes. Rehearse feature rollback and compatible-binary handling. | No lost statuses/URLs/files, no inferred country/fact verification, no positional-insert errors; old imports readable. |
| P7.2 | Execute interleaved GB/NZ UX scenario with all three CV routes, unknown/restricted/supported/conflicting recruitment examples and manually recorded official evidence. | Every design journey step mapped and demonstrated; unknown stays visible; no UK state changed by NZ edits. |
| P7.3 | Re-run UK search/routing/NHS/Studio/inbox/lifecycle/API/CLI contracts, with explicit new factual/approval exceptions. | Approved golden fixtures; historical documents byte-identical; side effects bounded; no arbitrary test deletions to hide failures. |
| P7.4 | Run adversarial claim and submission fixtures through every entry path, including legacy/scheduled/direct Python callers. | All unsupported factual claims blocked at finalisation; zero unapproved synthetic submissions, uncertain recovery truthful. |
| P7.5 | Run permitted read-only NZ source acceptance tests at bounded rates; record version, terms/access basis, date, target cases and failure distinctions. | Actual tested source capability, not just NZ enum. A valid empty result is distinct from failed/blocked. |
| P7.6 | Exercise quotas/caches/duplicate operations, long contexts, truncation, missing usage, failed providers, stale evidence and key-free backup/private-proof boundaries. | Measured request counts/token accounting, no repeated unrequested generation, no confidential leakage or revived approvals. |
| P7.7 | Review FMEA mitigation owners, external access records, retention/budgets, operator release notes and product-owner acceptance. Enable only named NZ capabilities for approved candidates. | Final approval before enabling NZ; manual accreditation remains available without API; managed ATS scope explicitly listed. |

**Dependencies:** P1–P6 acceptance, source permission/reliability evidence, supplied/reconciled candidate facts, owner confirmation of operational defaults/retention. No dependency on a hypothetical INZ API, new AI framework or generated DOCX.

**Required verification commands**, only during an authorised implementation in an isolated workspace where evaluations cannot write production data:

```text
python scripts/agent_verify.py
python scripts/ai_regression_eval.py --strict
python scripts/ai_eval_dataset.py --strict
```

`agent_verify.py` already invokes both strict evals; run the standalone commands for targeted development or changed/failing suites, not redundant paid/live testing. Use the existing environment-gated Playwright suites with isolated local employer fixtures. Capture test environment, checked dependency versions, skipped tests and reasons. Do not install/upgrade dependencies or call live providers implicitly as part of a test. Offline fixture tests are not reported as live NZ reliability tests.

**Acceptance criteria:** all invariant IDs I1–I8 proven; no critical unresolved FMEA safety failure; UK protected golden cases pass; country scoping/rights/claims/approval verified across all entry paths; selected NZ sources have documented permitted use; manual accreditation complete; backup/rollback rehearsed; no misleading sponsorship/visa/ATS claims. Optional unavailable source/managed-site features remain disabled with clear UI rather than weakening gates.

**Rollback:** turn NZ off at server, stop new NZ jobs, invalidate approvals and keep safe GB/manual workflows. Preserve submitted data/evidence and usage; reconcile active attempts. Roll back to a compatible safety build, not an unsafe pre-policy build or destructive schema restore. A full restore never silently overwrites post-backup applications.

**Risks:** environmental/version drift, tests skipped in CI, old executable accidentally restarted, source permissions changing, undocumented partial rollout and missing operational ownership. **Complexity: Medium–Large**, dominated by safety/migration/real-source validation rather than new UI.

## 11. Schema and state checklist

Refer to design section 11 for field contracts. This is the complete intended first-release storage scope, not an executed migration.

| Storage work | Phase | Compatibility requirement |
| --- | --- | --- |
| New `cc_country_profiles` aggregate | P1 | No identity/history duplication; revisions; no implicit NZ rights seeded from UK. |
| New `cc_employers` and immutable `cc_employer_evidence` | P3 | Explicit matching/provenance, no status inferred from absent public result. |
| Jobs country/employer/metadata/assessment columns | P1–P3 | Nullable legacy values; URL/status identity unchanged. |
| CV source country/category/reconciliation manifest | P4 | Existing originals/revisions intact; explicit-column inserts; collision keys respected. |
| Document country/context; writing source manifest | P4–P5 | Old fingerprint/history preserved; new validation/approval bound to content. |
| Browser country/review/approval revision metadata | P6 | No usable imported/restarted capability; consumption atomic; receipt/history intact. |
| Generation-request and usage operation/country metadata | P5 | Same ledgers; exact request correlation; unknown usage not zero. |
| Profile verification/typed fields | P4 | Validated additive metadata; one professional source; no automatic true/verified migration. |
| Country policy/source/campaign configuration | P1–P4 | Explicit NZ settings, root UK settings preserved, default NZ feature off. |

Do not add another application table, countries database, CV database, generic rules engine, queue broker, AI memory store or visa-calculation subsystem. Country-scoped reusable answers stay in the small country-profile aggregate in V1; legacy `cc_answers` data/uniqueness remain intact. Evidence/public and immigration/private fields have different access/retention needs even when both use the same workspace database.

## 12. Cross-phase failure-mode register

This design-stage register supports, but does not create, the repository-required future `fmea.json` implementation record.

| Failure mode | Cause/effect | Detection | Mitigation / owner phase |
| --- | --- | --- | --- |
| NZ settings overwrite UK | Root merge/global state; wrong source/CV/rights | Golden config/hash tests, interleaved contexts | Immutable resolver + scoped writes, P1/P2. |
| Public no-result marked ineligible | Unpublished employer mistaken for negative | Explicit no-result fixture and UI assertion | Unknown accreditation with official explanation, P3. |
| Wrong employer verified | Brand/agency/group name fuzzy match | NZBN/identity mismatch fixtures | Stable legal identity, manual ambiguity review, P3. |
| Unsupported claim finalised | Soft citation checks, deterministic filler, manual/legacy bypass | Full-entry adversarial claim fixtures | Final-byte closed claim set and shared hard gate, P4. |
| Current NZ rights fabricated | Global UK yes/no or old saved answer | Same-question cross-country/expiry/negation tests | Explicit scoped disclosures, manual ambiguity, P1/P4. |
| Unapproved application sent | Broad Claude tools, schedule flag, JS endpoint bypass | Zero-before-approval synthetic transaction counters | Containment then sole controlled executor, P1/P6. |
| Double submission after crash | Replay/old lock/receipt not detected | Crash-window and concurrent-worker tests | Atomic capability consumption + uncertain/reconciliation, P6. |
| UK docs become stale for NZ-only edit | Existing whole-file fingerprint | No-regeneration/no-score-call assertions | Dependency-scoped fingerprints, preserve legacy baseline, P4/P5. |
| Excessive AI charges | Repeated UI retry, repairs/drafts/transport retry stacks | Per-operation request/token fixture counts | Atomic leases/idempotency/caps and shared ledger correlation, P5. |
| Migration breaks existing writers | Positional full-row inserts after new columns | Old/new fixture write + migration-rerun tests | Explicit column lists, additive compatible rollout, P1/P7. |
| Evidence/rights leak in exports/logs | Whole context/screenshot/private artifact export | Sensitive marker fixtures, ownership/backup tests | Minimum payload, protected artifacts, explicit retention, P3/P5/P6. |
| Unsafe downgrade | Old code ignores country/approval or restores stale DB | Rollback rehearsal and version guard tests | Compatible safety build; no destructive restore/replay, P7. |

## 13. Release checklist and remaining decisions

### Mandatory release evidence

- [ ] NZ disabled by default and server denial tested.
- [ ] UK protected configuration/history/artifact contracts pass; intentional E changes documented.
- [ ] Concurrent country scopes verified across UI/API/CLI/jobs/subprocesses/answers/cache/inbox/exports.
- [ ] At least the advertised NZ discovery source set has actual permitted read-only acceptance evidence.
- [ ] Manual official accreditation recording, identity, timestamps/freshness/conflicts/unpublished handling demonstrated.
- [ ] All four recruitment classifications and independent technical/practical panels tested; unknown visible.
- [ ] Three actual master CVs imported/reconciled; originals/revisions intact; all listed target routes tested.
- [ ] Unsupported facts blocked from final application docs across every publication/export/upload path.
- [ ] Exact NZ rights/disclosures approved by user; no inference from UK status or desired destination.
- [ ] Fresh actor/page/country/packet-bound approval required by every managed submission path; raw alternatives contained.
- [ ] Background and scheduled work cannot grant/replay approval; uncertain outcomes cannot silently retry.
- [ ] Usage limits/retry idempotency, partial cost coverage and backup/key/retention rules verified.
- [ ] Migration/rerun/rollback and scoped feature disable rehearsed without losing subsequent applications.
- [ ] No unimplemented provider/API/format or visa eligibility guarantee advertised.

### Open operational details, not reopened baseline decisions

1. Identify initial permitted NZ employer/Workday/board source set and any access agreements.
2. Confirm 24-hour evidence review defaults, five-minute approval TTL, source budgets and two-page NZ CV target.
3. Supply actual master CVs and verified canonical/history/rights facts for later import/reconciliation.
4. Decide evidence/immigration-proof/screenshot retention and optional private-proof uploads.
5. Confirm configured daily/request/token ceilings and model prices if a monetary ceiling is required.
6. Choose first tested/permitted ATS scope; unsupported sites stay manual.
7. Pursue a written INZ/MBIE integration agreement only if automated verification is later required. Manual verification remains valid V1 behaviour.

## 14. Design-phase verification and stop condition

The plan is cross-referenced to existing source functions/schema helpers/test files and the companion design. Proposed schemas/services/routes are labelled as proposals; no application files are claimed to exist at invented locations. No tests, migrations, job-provider searches, model calls or browser applications were executed as implementation. Read-only repository inspection and public-document research informed the design. Markdown whitespace checks passed for both requested reports; final Git status showed them as the only additional paths relative to this phase's initial listing.

**Stop after these two reports. Implementation, migrations, configuration changes, real candidate CV import, provider enablement and applications require explicit product-owner authorisation.**
