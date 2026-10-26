# New Zealand Job Search Integration Design

- **Status:** Design complete; proposed behaviour only; not implemented.
- **Date:** 16 October 2026.
- **Baseline:** `JOB_SEARCH_PLATFORM_DISCOVERY_AND_NZ_READINESS.md` and product-owner decisions A–E in the design request.
- **Architecture:** Configuration-driven hybrid, one shared platform, country-scoped UK/NZ experiences.
- **Companion:** [Bounded implementation plan](NZ_JOB_SEARCH_IMPLEMENTATION_PLAN.md).

## 1. Scope, evidence and decisions

This document describes the target system. It does not change the current application. Only this document and the companion plan are deliverables of this phase. No application code, prompts, configuration, database, credentials or existing UK campaign was changed; no migrations, installations, deployments, job searches or applications were run.

`AGENT_INSTRUCTIONS.md`, `docs/FMEA_POLICY.md`, `ops/repo_status.json`, the active implementation status, `README.md`, the discovery report and relevant source were reviewed. No repository `AGENTS.md` was found. The working tree contains pre-existing modified/untracked implementation; those files, rather than only committed HEAD, informed the design.

### 1.1 Evidence labels and paths

- **[E] Existing:** confirmed in inspected repository code.
- **[M] Missing:** required behaviour absent/incomplete in the inspected implementation.
- **[P] Proposed:** design to implement only after authorisation.
- **[X] External:** published information/dependency outside the repository.
- **[U] Unverified:** operational reliability, permission or product detail not established.

Package paths such as `geography.py`, `apply/assisted_browser.py` and `web/workspace.js` are relative to **`src/applypilot/`**. `docs/`, `tests/`, `scripts/`, `ops/` and root filenames are repository-relative. Proposed identifiers, JSON keys, schemas, contracts and routes below do **not** exist unless explicitly marked [E]. New logical services are named by responsibility; new implementation filenames are deliberately not asserted.

### 1.2 Binding product invariants

| ID | Target invariant |
| --- | --- |
| I1 | Shared identity, employment, education, qualifications and verified professional facts exist once per candidate workspace. |
| I2 | GB and NZ contexts coexist. A browser-tab switch never changes an existing job, running task, document, answer or submission's country. |
| I3 | Employer accreditation, vacancy overseas recruitment evidence and candidate work rights are separate assessments. None proves the others. |
| I4 | Unknown/conflicting overseas suitability remains discoverable; absence of sponsorship text is not a negative finding. |
| I5 | No application-ready generated factual claim may bypass evidence reconciliation/validation. Style may change; facts may not be embellished. |
| I6 | Every platform-executed external application submission needs fresh, explicit approval for that exact attempt. Schedules, CLI flags, agents and background workers cannot grant approval. |
| I7 | Existing UK records/configuration and historical document bytes/statuses are preserved; intentional policy changes are enumerated. |
| I8 | NZ remains disabled until country isolation, manual accreditation verification, factual controls and submission safety are validated. |

**Compatibility conflict resolved:** exact preservation of autonomous CLI/scheduled-submit behaviour and reviewer-permitted factual stretches is incompatible with decision E. This design preserves their commands/configuration/history, but intentionally changes their ability to finalise unsupported documents or submit unattended, for **all countries**. UK tone, searches, role rules and NHS-specific writing remain country-selected. These are explicit policy exceptions, not claims of zero behavioural change.

## 2. Architecture and country boundary

### 2.1 One candidate workspace; two product workspaces

[E] `multiuser.py` isolates candidates by filesystem workspace and SQLite database. The term **New Zealand workspace** below means a country-scoped experience within that same candidate workspace; it is not another account, `APPLYPILOT_DIR`, database or copied `profile.json`.

```text
Candidate account / existing isolated filesystem workspace
  |
  +-- SHARED: profile + verified history + CV originals/revisions
  |           provider/task routing + caches + usage + job/application engine
  |           rendering + timelines + inbox + submission/reconciliation
  |
  +-- United Kingdom view [GB] -- existing UK campaigns, NHS/UK policies
  |
  +-- New Zealand view [NZ] ---- NZ campaign + 3 CV presentations
                                 NZ rights/disclosures + employer evidence

Every request/run -> immutable CountryContext -> existing domain service
  discovery -> enrichment -> technical fit -> CV route -> grounded documents
       |          |                                      |
       +-- existing sources                  existing Studios/PDF/versioning
                  |
          bounded NZ evidence service
          manual official check first; authorised feed only if agreed

Reviewed packet -> owned assisted browser -> current-page review
                -> candidate approval -> single controlled submit attempt
                -> evidence / uncertain -> existing reconciliation
```

### 2.2 Reuse versus bounded extensions

| Responsibility | [E] Foundation | [P] Extension |
| --- | --- | --- |
| Country configuration | `geography.py`; `pipeline_workspace.py::campaign_config(),save_campaign()` | CountryContext resolver; explicit country profile; isolated campaign inheritance and validation. |
| Discovery | `discovery/jobspy.py`, `discovery/workday.py`, `discovery/smartextract.py`, `source_registry.py` | NZ source enablement evidence, original URL/freshness/salary metadata and safe country provenance. |
| Matching/routing | `scoring/scorer.py`, `role_routing.py` | NZ technical rubric, independent practical/immigration panels, country-specific source eligibility. |
| Canonical facts | `setup_workspace.py`, `professional.py`, `cc_profile_revisions` | Stable fact references, candidate verification metadata, claim reconciliation and immutable manifests. |
| CVs/writing | `cv_library.py`, `scoring/tailor.py`, `writing_studio.py`, `scoring/pdf.py` | Country policies and evidence-only finalisation; no second library or generator. |
| Employer evidence | UK pattern in `uk_sponsorship.py`; portfolio/upload security patterns | Small NZ legal-employer/evidence service, manual verification and deterministic suitability reducer. UK registry code stays UK. |
| Applications | `command_center.py`, `apply/application_packet.py`, `apply/assisted_browser.py`, `submission_reconciliation.py` | Universal approval boundary, country-bound packet/answer snapshots, legacy compatibility adapters. |
| AI/usage | `ai_routing.py`, `llm.py`, `pipeline_store.py`, `cc_writing_requests`, `cc_ai_usage` | Country/version-aware request identities, atomic reservations and retry ceilings. |

No new AI framework, vector store, CV database, job-search engine, cloud service or distributed queue is required for the first release. The existing one-runner/one-live-browser-per-candidate constraints can remain: **concurrent workflows** means both contexts remain available and correctly scoped, not unlimited simultaneous preparation/browser sessions.

### 2.3 CountryContext contract and precedence

[P] Resolve once, pass explicitly; do not set a process-global “current country.” Context contains:

```text
country_code: GB | NZ | legacy-unassigned
workspace_identity, job_url?, campaign_id?, operation_id
country_profile_revision, policy_id, policy_version
effective_search_snapshot, routing_snapshot
rights_revision, disclosure_revision, verified_facts_revision
```

Country codes in storage use ISO-style `GB`/`NZ`; existing provider alias `uk` and search string `new zealand` remain transport mappings, not storage identities.

Resolution rules:

1. A persisted job's country is authoritative for every job mutation/generation/application; an explicitly different request country is rejected, not used to reinterpret the job.
2. A new run/draft requires explicit campaign/country in the new UX. Saved campaign country and request country must agree. An unsaved Studio draft carries explicit country even without a job URL.
3. A run captures effective configuration/revisions before execution. Country profiles replace country-sensitive sections; NZ does not inherit UK title exclusions, salary preferences, NHS requirements, role blends or smart sources through a shallow global merge.
4. Country-neutral identity/history is inherited. Country-specific rights/disclosures/answers never fall back from GB to NZ or vice versa. Missing NZ rights means unknown and manual answer required.
5. Unscoped legacy reads/runs retain the old cohort plus explicitly adopted GB records; **they exclude NZ records** unless an explicit read-only all-countries view is requested. Jobs explicitly identified by URL/ID still resolve their own country and cannot bypass NZ gates.
6. Legacy records are not bulk-assigned GB based on location/campaign strings. They remain accessible in the legacy/UK experience as “country not confirmed.” New rights-dependent preparation/submission requires explicit destination confirmation. Historical states/files are unchanged.
7. Tab country/campaign state is request-local. SSE updates include country metadata; clients discard wrong-context responses. Bulk operations validate each job before changing anything. Shared AI credentials/models are not duplicated per country.

Integration points: `command_api.py::get(),post()`; `_Handler` setup/legacy routes and `_PipelineRunner.start()` in `dashboard_server.py`; `eligibility.py::candidates()`; `role_routing.py::route_resume_for_job(),score_fingerprint()`; `writing_studio.py::context()`; `application_profile.py::application_values()`; browser/question/inbox services. Existing campaign override allowlists must be extended; merely adding YAML keys today would not activate this design.

### 2.4 Vacancy identity and application ownership

[E] `jobs.url` is the primary key and one row owns the active route, document pointers, execution/hiring record. [P] Retain that model. A job can belong to several discovery campaigns (`cc_campaign_jobs`) but has **one chosen application destination**. Campaign membership alone never changes its country.

An advert spanning GB/NZ or worldwide remote locations is shown with ambiguous destination evidence; the user selects a destination before generation. Once preparation exists, country reassignment requires an explicit inactive/unsubmitted recontextualisation operation: retain old versions, invalidate current packet/approvals, and require fresh generation/review. Submitted or uncertain records cannot be reassigned. The same employer/requisition is not duplicated to evade duplicate-submission protection.

V1 does not maintain two independent concurrent country packets against the same vacancy row. That would require a separate application-context entity and is unnecessary for ordinary concurrent GB and NZ vacancies. Cross-country views show a reference to the existing owned application rather than creating a second application history.

## 3. External research and source readiness

### 3.1 Authoritative NZ findings

[X] Public documentation reviewed on 16 October 2026 establishes:

- INZ's accredited employer tool accepts a **13-digit NZBN**, company name or trading name (minimum three characters for name search). The list is updated daily. Some employers elect not to appear; **no result is not proof of non-accreditation** [X1].
- INZ's terms prohibit scraping/automation/data gathering, require standard web-browser access, and allow another method only by agreement [X2]. A visible website request or internal endpoint is not an authorised integration API.
- INZ's AEWV guidance separates an accredited employer, a current offer, an approved job check for the role and candidate requirements. A CV alone is not sufficient immigration evidence of employment history [X3]. The job-search platform will not make visa eligibility determinations or initiate visa applications.
- INZ copyright permits some Crown-content reuse with attribution and exceptions; that licence does not remove website-access restrictions [X4].

**API conclusion [U/X]:** no documented, authorised public employer-accreditation API suitable for ApplyPilot was identified in the reviewed accredited-employer, online-tool/terms, employer and MBIE portal material. This is not an assertion that no private/partner mechanism exists. VisaView/visa verification is a distinct service and must not be repurposed as employer accreditation. A future automated connector requires written INZ/MBIE confirmation of purpose, access, fields, rate limits, retention and redistribution terms. No endpoint, credentials or entitlement is invented here.

**V1 strategy [P]:** support recorded manual verification using the official tool in the candidate's ordinary browser. This satisfies first-release accreditation verification without depending on a hypothetical API. No automatic INZ source preview, search, browser bot, background refresh, hidden endpoint or bulk harvesting is enabled. Where an employer is unpublished, request evidence through an ordinary candidate/employer interaction; record it as employer-supplied, not independently official verification. Retain unknown until an adequate authorised/official check exists.

### 3.2 Provider readiness matrix

“NZ geographic support,” “operational reliability,” “permission to collect/store/use with AI” and “permission to automate applications” are independent checks.

| Provider/path | [E/X] NZ capability evidence | [U] Limits and [P] V1 disposition |
| --- | --- | --- |
| Direct employer careers pages | Existing static/template/JSON-LD/CSS extraction; no board country constraint | **Preferred foundation per approved employer/source.** Register employer domain, NZ scope, allowed methods and terms; validate retrieval and link attribution before enabling. A public page alone is not permission. |
| Employer Workday CXS portals | Existing keyword API/detail extractor; returned locations filtered; direct employer registry | **Conditional foundation.** NZ employer/tenant/site must be verified and permitted; empty CXS country facets mean post-filtering is required. No employer accreditation implied. |
| JobSpy Indeed | Version-specific 1.2.0 documentation lists New Zealand; `country_indeed='new zealand'` plus structured city [X5] | Technically supported, **not established reliable/permitted for this deployment**. Indeed terms restrict automated access without permission and prohibit non-official Indeed Apply automation [X6]. Disabled for new NZ automated collection until access and downstream AI/storage rights are validated. |
| JobSpy LinkedIn | 1.2.0 documentation says global `location`; company IDs exist upstream but ApplyPilot does not pass them | Global geography is not access permission. LinkedIn agreement prohibits scraping/unauthorised automation [X7]. Do not enable as a default NZ scraper without explicit permitted integration. Human-open/direct-employer discovery can remain separate. |
| JobSpy Glassdoor | 1.2.0 country list marks NZ supported; existing simplified location path | Conditional only. Terms page returned 403 during review; permissions/reliability unverified. Disabled pending validation, not treated as zero-results success. |
| JobSpy Google Jobs | Existing code offers `google`, but 1.2.0 package documentation says currently unavailable [X5] | Not a default source. Also adapter currently omits the documented `google_search_term`. Any future enablement needs a verified supported path, not location-parser inference. |
| ZipRecruiter | US/Canada only in upstream docs and `geography.py::board_scope()` | Excluded from NZ, explicit reason shown. |
| SEEK | No repository adapter. Official developer site requires approval; documented uses include employer job posting/application export/profile-prefill [X8] | Do not assume this is a candidate-wide search API. Optional future integration only with relevant approved access; no dependency for V1. |
| Trade Me Jobs | No repository adapter. Official developer site has a Jobs contact; API approval/purpose/data-combination restrictions apply [X9] | No generic permission to aggregate with other boards. Exclude automated V1 integration unless Jobs-specific written approval covers this use. |
| Other packaged global/UK/Canadian sites | Existing registry mixes regions, including fixed remote-programming pages | Do not import all into NZ. Enable only tested/permitted NZ-relevant sources; keep UK catalogs/settings untouched. |

**Reliability conclusion:** no live NZ discovery/provider tests were performed, so no provider is claimed universally reliable. V1 should launch only with an explicitly validated direct-employer/Workday source set and/or a permitted existing board route. If no automated sources pass, offer honest manual URL/JD capture and keep the automated-discovery release gate closed. Do not advertise multi-board coverage based on configuration alone.

**Employer-domain search:** direct source registries already pin a domain; Workday pins an employer portal. Current JobSpy adapter has no general employer-domain search argument. Company-name terms are not a domain constraint; upstream LinkedIn IDs are not website domains. Do not use `site:` through the existing Google Jobs path as an invented reliable integration. An optional user-owned employer-domain allowlist filters validated returned destinations; agency ATS domains require a verified employer-to-ATS relationship.

### 3.3 Source acceptance record

[P] Extend workspace source metadata, not a new provider marketplace: `countries`, access state (`unreviewed`, `permitted`, `restricted`, `disabled`), terms/evidence reference, reviewed date, allowed retrieval/storage/AI-use/application modes, identity query keys, rate/backoff/cache bounds, last successful retrieval and known failure category. Store only nonsecret references. Enforce it on source preview and run execution, not just UI selection. Honor withdrawals/403/429 without proxy/CAPTCHA evasion. New NZ source settings cannot override a server restriction.

## 4. NZ campaign, ingestion and vacancy metadata

### 4.1 Campaign configuration

[P] Add one named NZ campaign with three editable career groups, using existing query/tier/routing structures. No existing campaign is rewritten. Defaults are product proposals, not assertions about a candidate's skills.

| Group | Initial search vocabulary |
| --- | --- |
| Software | Software Developer, Python Developer, Django Developer, Full Stack Developer, React Developer, TypeScript Developer, Application Developer, Junior Software Engineer, Backend Developer. |
| Data/BI | Data Analyst, Business Intelligence Analyst, Reporting Analyst, Insights Analyst, Junior Data Engineer, Analytics Engineer, Data Quality Analyst, BI Developer. |
| Support | IT Support Analyst, IT Technician, Application Support Engineer, Technical Support Engineer, Systems Support Analyst, Service Desk Analyst. |

Primary places: **Auckland, Wellington, Christchurch**, each with explicit `country: New Zealand`, city, kind city and `remote: false`. Offer Hamilton, Tauranga, Dunedin and other user-added NZ places as optional—not automatic extra search multiplication. Offer a separate New Zealand whole-country/remote scope; remote is not worldwide eligibility. Preserve distinct remote/hybrid/onsite evidence rather than `hybrid` being counted as remote-only.

Existing-compatible campaign fields: `id`, `name`, `country`, `geography_enabled: true`, `location_filter_mode: selected`, queries/tiers, places, boards, selected smart sites, defaults and `resume_routing`. Proposed additional fields: `country_code: NZ`, country-policy reference, bounded source budgets and approved employer selection. Extend `save_campaign()`/override validation to retain them. Set `defaults.country_indeed` consistently to `new zealand` to avoid inherited UK values. Do not inherit legacy UK location reject lists or exclude graduate/senior titles without the user's NZ preference.

Suggested tunable preview defaults: 168-hour board window, 50 results/source/target, 12 board searches/run, 12 direct targets/run and 6 Workday employer/query targets/run. These are application workload bounds, **not provider allowances**; use the lower permitted source limit. No recurring search scheduler is required. The enabled sources list is the validated set, initially empty until checks succeed; empty means none, not “all packaged sources.” In particular, fix the existing empty-list/fallback semantics for new NZ campaigns explicitly.

### 4.2 Ingestion and provenance

[P] Reuse discovery/enrichment/storage but carry a normalized observation envelope through every engine:

```text
source_id + provider_job_id/requisition_id if available
campaign_country, advertised_country, country_evidence, country_status
original_listing_url, application_url_original, application_url_resolved
application_destination_kind: employer | employer_ats | board | unknown
posted_at + posting_date_basis, first_discovered_at, last_seen_at
last_description_fetch_at, content_hash, source_valid_through
availability_status, availability_checked_at, check_target
work_arrangement + exact source text
salary (raw plus structured fields)
source_url, retrieved_at, extraction_method/version
```

Retain raw location/salary in the existing fields for compatibility. Store this envelope in proposed job JSON metadata; promoted JobSpy/Workday descriptions must still enter country/evidence assessment, not bypass it because `detail_scraped_at` is already populated. An NZ search target is provenance, not proof that a returned role is in NZ. Unknown advert country remains a discoverable review item; explicit non-NZ listings are identified as out of scope without modifying UK data.

### 4.3 Duplicates, URL identity and expiry

- Keep existing stable `jobs.url`; never mass-rekey historical URLs. Preserve original application URL including meaningful query parameters/signatures; canonical comparison must not replace the actual navigation link.
- Add source-specific identity keys for new NZ ingestion. Current `normalize_url()` preserves Indeed `jk/vjk` but drops most other query strings; this can collapse distinct query-identified jobs. NZ identity comparison must retain registered identity keys or use provider/requisition ID, with safe fallback to original URL. Do not apply a global canonicalisation rewrite to UK history.
- Exact source identity/validated direct-link duplicates map to an existing row. Employer legal ID + requisition + destination is stronger than title/company; title similarity alone only warns. Retain each new source/query observation in events/provenance and campaign membership. Repeated discovery updates `last_seen_at` without resetting first discovery or application state.
- Rediscovery is not an open-vacancy certification. Use source dates/`validThrough`, 404/410, explicit closed text and user checks. `reachable` remains distinct from open; 403/timeout means unknown, not expired.
- Recheck the **actual application destination** as well as listing where permitted. Never probe final-submit/one-time-action URLs. Proposed cache interval: 24 hours for ordinary availability observations; pre-submission review requires a check within 24 hours or an explicit user-checked source observation if automation is disallowed. These are review defaults, not guarantees; failed checks cannot manufacture a fresh success.
- Missing posting dates remain unknown. Display “first seen,” “source posted,” “last seen” and “availability checked” separately. JobSpy's board-age filter is not a complete advert-age guarantee; the 1.2.0 docs distinguish indexed age from employer posting date [X5].

### 4.4 NZ salary representation

[P] Retain `salary` text; add structured values in metadata: decimal amount_min/max, currency, interval, source basis, stated hours/week, annualisation basis, package/base/gross/net/unknown, KiwiSaver included/excluded/unknown, confidence and quote/source. Use NZD only when explicitly stated or a clearly documented NZ-source currency convention supplies it; mark inferred conventions, never infer all `$` as NZD globally.

Parse `NZ$80k–100k`, `NZD 90,000 per annum`, hourly and daily ranges with fixtures. Do not compare hourly/daily with annual preferences without declared hours/days and an explicit conversion basis. Do not compare NZD and GBP as equivalent or add FX assumptions. Unparseable/mixed-currency/package amounts remain visible. Do not infer visa wage eligibility from salary text; there is no hardcoded immigration threshold in this release. Use NZ country compensation, never the global UK salary floor.

## 5. Employer accreditation and vacancy evidence

### 5.1 Employer identity

[P] Identify the **hiring legal employer**, distinct from board, recruiter/agency, brand, parent group, direct website and ATS. Store internal employer ID, country, legal/trading names, optional NZBN string, website and relationship evidence. Validate NZBN is 13 digits; format alone does not establish ownership. A brand/website similarity cannot transfer a parent's accreditation to a subsidiary, agency client or franchise.

Matching order: exact official NZBN with corroborated hiring identity; exact legal/trading identity with user-confirmed source; otherwise ambiguous candidate matches. Fuzzy names may suggest candidates but cannot mark verified accreditation. Website data is evidence, not an official accreditation source.

### 5.2 Immutable observations; derived current state

| Proposed evidence field | Meaning |
| --- | --- |
| Employer ID / identity snapshot | Legal name, trading name and NZBN observed at the time; matching method and relationship to vacancy. |
| `kind` | `accreditation`, `employer_identity` or a relevant official adverse-status observation; never an all-purpose “sponsored” flag. |
| Observed result | `accredited`, `not_accredited`, `expired`, `suspended`, `revoked`, `not_listed`, `unknown`; negative states require explicit supporting evidence, not absent search results. |
| Official evidence source | Canonical official page/document reference, issuing authority, quoted result and query used; plain website URL alone cannot prove a result. |
| Method/provenance | `manual_official_check`, `authorised_feed`, `employer_supplied`, `user_note`; feed disabled without permission. |
| Verification timestamp | Actual time the check/observation was performed, not the time a cached record was opened. |
| Recorded timestamp / actor | When ApplyPilot stored it, candidate/authorised actor, audit event ID. |
| Last successful verification | Derived from last successfully evidenced check; retained if later attempts fail. “Successful check” may have a negative result. |
| Source effective/expiry dates | Nullable; only populated when source supplies them. Do not infer accreditation term from publication date. |
| `refresh_due_at` | Product-defined review deadline; does not mean accreditation legally expires then. |
| Evidence artifact/hash | Minimal permitted excerpt, redacted screenshot/document and SHA-256, workspace-relative ownership. |
| Verification result/error | `success`, `unavailable`, `access_not_authorised`, `identity_ambiguous`, `conflicting`; sanitized error, no fabricated result. |
| Supersession/resolution | Observation IDs, resolution note/actor/date; history retained. |

[P] Compute display state from observations: identity confirmed/ambiguous; accreditation result; evidence fresh/stale/unavailable/conflicting; source expiry known/unknown. A fresh “not listed” yields **unknown accreditation**, not `not_accredited`. Expired evidence and expired accreditation are different labels. A newer failure does not erase a prior successful observation or advance its successful-verification timestamp. Conflicting current credible observations require review, not “latest timestamp wins.” Manual notes cannot override an explicit official adverse result silently.

Default proposed freshness for successful official/manual checks: **24 hours**, configurable. A stale record says “last verified at …; recheck needed.” Background work can compute stale flags and reminders locally; it cannot query INZ. Before document approval/submission, refresh stale evidence manually where possible; unresolved status remains disclosed unknown and may proceed after the user's explicit evidence review, not a fictitious verification pass.

### 5.3 Manual official verification workflow

1. User opens an ordinary browser to the public INZ accredited employer page; ApplyPilot does not append an undocumented query URL or automate it.
2. User searches exact NZBN or verified company/trading name and checks legal identity against the vacancy.
3. User records observed result, query, source reference, check time and a permitted minimal excerpt/artifact. Mark whether checked directly on official source or only employer-supplied.
4. Server validates ownership, timestamps, field lengths, artifact type/size and state consistency; appends observation and audit event.
5. A missing result displays the official non-publication limitation and “ask employer / verification unavailable.” Positive employer statements remain separate from official proof.
6. Reverification appends an observation; it never silently overwrites the prior result. Correction/supersession is explicit. No automatic accreditation guarantee is displayed.

No INZ credentials, RealMe login, passport, visa application or immigration form submission is involved. V1 verification is an auditable user-performed check, not an API attestation by INZ.

### 5.4 Four overseas-recruitment classifications

| Required label / proposed code | Evidence and behaviour |
| --- | --- |
| **Overseas recruitment explicitly supported** / `explicit_support` | Current vacancy-specific employer text explicitly welcomes overseas applicants or offers a relevant visa process; retain exact quote and source. Does not grant work rights, job-check approval or visa eligibility. |
| **Potentially suitable; additional verification required** / `potential_review` | Relevant indirect/conditional evidence, such as confirmed current accreditation with no vacancy restriction, or employer confirmation that needs role-specific clarification. Show exactly what is missing. Accreditation alone can support this *review* category, never explicit support. |
| **Overseas recruitment explicitly restricted** / `explicit_restriction` | Unambiguous current role condition such as existing unrestricted NZ work rights/residency/citizenship or a no-overseas/no-visa-support restriction. Keep visible with quote and candidate-context comparison; do not lower technical score. |
| **Evidence unavailable or conflicting** / `unavailable_conflicting` | No usable evidence, failed/partial extraction, uncertain identity, stale sole supporting evidence, contradictory current claims, or ambiguous restriction. Default when silent. |

Store separately: raw evidence claims, country/jurisdiction, exact quote+location, source URL/document hash, fetched/check times, applicability to vacancy versus employer, negation/conditional markers, extraction/rule version, and user review/decision. Vacancy AEWV/job-check statements are **advert/employer assertions** unless independently verified by a permitted authoritative mechanism. No “sponsorship confirmed” badge is introduced.

[P] Use deterministic sentence/section rules first. Existing extraction can propose evidence candidates in the same call, but quotes must literally occur in the source and rules/user review determine classifications. No new AI call for each employer. Negative/conditional text is evaluated together; do not inherit `uk_sponsorship.py`'s negative-first ordering. If current exact role evidence conflicts, classify unavailable/conflicting until resolved; generic boilerplate does not automatically override role-specific text.

Unknown jobs stay in default search results and technical ranking. Users may filter any category or decline a restricted role; do not silently add `skipped`, `blocked` or remove it during ingestion. For an incompatible explicit application declaration, the managed flow stops for clarification/manual decision and **never supplies a false answer**. Manual review cannot rewrite the underlying evidence classification; it records a separate decision.

## 6. Matching and presentation

[E] `scoring/scorer.py::score_job()` supplies a 1–10 score, keywords, reasoning and confidence; `role_routing.py` chooses a CV. [P] Keep that single fit request and scale, with an NZ technical-only rubric and structured evidence references added to its output/report:

- Verified technical skills and genuine equivalents, clearly distinguishing related skill from claimed experience with a different tool.
- Relevant verified work/projects, qualifications/certification status, target career category and role seniority/experience requirements.
- Experience durations computed only from verified typed periods, with overlap/part-time/unknown handling; do not infer “five years Django” from total years in IT.
- Missing evidence stays unknown. No model-generated skills are added to the profile.

Three separate panels, no blended “visa fit” score:

1. **Technical match — 1–10**, evidence/reasoning, gaps and source CV. Accreditation/recruitment/work rights cannot add/subtract technical points; filter those paragraphs from the scoring context or explicitly segregate them and test invariance.
2. **Practical fit — matches / needs review / mismatch** for selected places, relocation preference, arrangement, salary basis, notice/availability. This is not immigration eligibility.
3. **NZ recruitment evidence** — the four-class label, employer identity/accreditation result/freshness, advert restriction, candidate disclosure state and evidence links.

Default sorting uses existing priority/technical score/freshness; unknown immigration evidence does not sink otherwise strong matches. The technical confidence is a model judgement, not an accreditation confidence. Updating employer evidence recalculates the immigration panel deterministically without regenerating the CV or rescoring technical skills. Location/practical changes and factual CV changes invalidate only affected assessments.

The NZ document-preparation queue must not inherit UK work-authorisation hard-constraint matching in `scoring/tailor_strategy.py`. Move destination rights to the independent panel/answer check; do not fabricate a missing qualification to overcome a technical gap. Users can prepare an honest document for a partial technical match with gaps visible.

## 7. Three master CVs and authoritative history

### 7.1 Import and routing

[E] Reuse `cv_library.py::import_files(),revision(),action(),source_text()` and `role_routing.py::route_resume_for_job()`. Originals stay under existing `cv_uploads/`; extracted editable text under `resume_variants/`; revisions remain in `cc_cv_revisions`. No actual files have been imported in this phase.

[P] Suggested logical variant keys: `nz_software`, `nz_data_bi`, `nz_it_app_support`. These are proposed labels, not existing candidate files. Use each import's **returned collision-safe key**, not an assumed filename. Import without the current global role-assignment side effect; map NZ titles inside the NZ campaign only. Extend library source metadata with country/category and reconciliation state. Rename/archive updates nested country campaign mappings as well as existing references; never delete originals as part of reconciliation.

| Master category | Exact title routes before family fallback |
| --- | --- |
| Software Development | Software Developer; Python Developer; Full Stack Developer; React Developer; Application Developer; Junior Software Engineer; Backend Developer; plus reviewed Django/TypeScript routes. |
| Data Analytics and Business Intelligence | Data Analyst; Business Intelligence Analyst; Reporting Analyst; Insights Analyst; Junior Data Engineer; Analytics Engineer; Data Quality Analyst. |
| IT Support and Application Support | IT Support Analyst; IT Technician; Application Support Engineer; Technical Support Engineer; Systems Support Analyst; Service Desk Analyst. |

Keep longest-title matching and existing weighted family fallback. Prefer multi-word family signals; shared terms such as SQL/Python alone must not dominate routing. All three families explicitly configured; NZ sector overrides/blends disabled initially. Unknown/ambiguous routes require selection among reconciled NZ variants, **not automatic fallback to the global UK `resume.txt`**. A reviewed manual override is retained on the job; it does not modify UK routes.

### 7.2 Verification and conflicts

[P] Canonical content remains in existing `profile.json` professional sections/skills/facts, with additive typed metadata and stable entity/fact IDs, not another country's employment record. Add a verification manifest referencing the canonical entity/field/value hash, evidence source, review state, verification time and approved wording variants. It records provenance/approval, not a second copy of the employment history.

On import/edit, compare known employers/official titles, periods, education/credential status, skills, durations and metrics against canonical facts. Existing literal parsing proposes candidate facts; it does not establish them. States per relevant claim: reconciled, new evidence awaiting review, conflict, excluded. Automatic equality/normalisation can match references; unresolved semantic differences require human reconciliation.

Conflict choices:

- Correct the presentation to the existing verified fact.
- Supply evidence and deliberately correct/update the shared canonical fact once; invalidate affected GB/NZ future documents, retaining historical versions.
- Exclude the conflicting claim from the generation evidence selection.
- Leave unresolved; import/original remains accessible but claim is unavailable for final application documents.

No majority vote between CVs, no “trust the NZ master over profile,” no automatic field overwrite. Changed canonical value hashes invalidate dependent verification/wording approvals. Add explicit employer/title/start/end/hours metadata where required for reliable duration computations; ambiguous free-text dates do not become verified durations. Candidate-attested professional verification is labelled as such; it is not certified immigration evidence or independent third-party verification.

### 7.3 Factual finalisation contract

Turning on strict citation flags alone does **not** satisfy decision E. Current fact IDs, token-number comparisons and an LLM reviewer do not prove semantic entailment.

[P] Use the existing writer/assembler with structured claim references and a shared finalisation gate:

1. Select only verified canonical fact IDs; source masters supply **ordering/emphasis/presentation**, not a parallel unrestricted truth source.
2. Model output proposes section ordering, selected facts and wording. Every factual span, including summary/core skills/subtitles/cover prose/answers, maps to canonical facts and their revisions. Employer/JD facts have their own source references and cannot be promoted into candidate history.
3. Deterministic rendering injects exact identity, official employment titles/employers/periods, qualifications/status and approved numeric/duration claims. Derived figures are only allowed by declared, tested computations from verified inputs; “(derived)” alone is not permission.
4. **Fail-closed V1:** final factual wording is literal verified text or a reviewed, evidence-linked wording variant. Novel free paraphrases are drafts, not automatically finalisable because a model cites an existing ID. The user reviews the source/claim mapping to accept a meaning-preserving wording variant; unsupported new facts require canonical evidence reconciliation, not an “approve anyway” button.
5. Nonfactual connective/style text uses reviewed templates; unmatched assertions are marked for review and blocked. The reviewer can flag concerns but cannot override hard checks or authorise a “minor stretch.”
6. The shared gate recomputes from final bytes after all deterministic polishing, before publish/current-pointer update, PDF/application-ready export, browser upload and submission. Manual document edits/custom CV/supplemental facts/legacy endpoints use the same gate. A hash mismatch invalidates validation and document approval.

Remove tolerance for learnable/related-tool claims, conditional lenient factual bypasses, unsupported fixed-employer examples, guessed project years/bullets and unverified capability closer injection. Preserve style-only rewriting, truthful target-role headlines, transferable positioning and UK/NHS terminology where appropriate. Correct factual historical documents remain downloadable unchanged; historical unvalidated documents are labelled unverified and cannot be reused as approved application packets without reconciliation. A draft preview/export must say **not application-ready**; it cannot silently count as a finalised document.

This bounds V1 assurance instead of pretending generic AI can perfectly fact-check arbitrary prose. It adds source-review work but no new inference framework; reusable approved wording reduces later effort/cost.

## 8. Country-scoped documents and disclosures

| Content | [P] NZ policy | UK preservation |
| --- | --- | --- |
| Professional summary | Verified evidence, target role as positioning, no invented NZ residence/local experience or duration | Existing UK tone/role emphasis, subject to common factual gate. |
| Tailored CV | A4, readable headings/text/bullets, honest role titles; configurable length (initial two-page target, never pad); country source variants and facts | Existing UK layout/section/bullet policy remains selected for GB. |
| Cover letter | Employer/JD-specific, NZ English, normal greeting/sign-off, verified examples; optional approved relocation/disclosure text | UK-specific tone and NHS/public-sector examples only if actually evidenced and relevant. |
| Supporting statement | Criteria-led or reviewed example/values structure using actual employer requirements; no automatic NHS/trust/patient/UK language | GB NHS planning and terminology remain available for NHS jobs. |
| Screening answer | Selected field, scoped verified answers/facts, claim gate and field-length constraints; sensitive items manual | Same evidence mechanism with GB answers; no cross-country exact-question fallback. |
| Relocation statement | Candidate-confirmed destination, willingness, availability and constraints only | GB availability/preferences remain independent. |
| Work-rights disclosure | Exact candidate-approved wording bound to NZ rights record, permit conditions/expiry/evidence/revision | GB authorization remains separate; legacy values never imply NZ rights. |

[E] Formats are TXT/PDF with HTML previews; PDF/DOCX/TXT/MD uploads and original downloads. [P] Retain these for V1. Generated DOCX and OCR are not dependencies. If an employer requires DOCX, clearly offer original/master or manual conversion with revalidation—do not call a PDF a compliant substitute. PDF tests must check text extraction, special characters, overflow and page counts; a length target does not guarantee fitting one page. No generated ATS certification is claimed.

[P] Versioned country policies compose shared truthfulness constraints with country-specific spelling, section/layout rules, required documents and prompt fragments. Stored policy overrides are validated bounded fields, not arbitrary server prompt execution. Do not blanket-ban “NHS” from NZ professional history when it is a genuine prior employer; prohibit unsupported target-employer/country framing. A location selection must not cause full UK prompts to remain under NZ text.

### 8.1 Rights and disclosure records

[P] Country-profile configuration contains user-confirmed `current_work_rights` (`unknown`, `none`, `restricted`, `unrestricted`), sponsorship/visa assistance need (`unknown`, `yes`, `no`, `conditional`), permit type/conditions/expiry where relevant, source/attestation metadata, review date, exact approved disclosures and explicit relocation preferences. Do not seed answers with “Yes/No” sample defaults. Do not ask for passport/visa numbers or raw immigration documents for ordinary job discovery.

No default claim that the candidate holds NZ work rights or will qualify for AEWV is supplied. The design permits wording such as “I would require an appropriate work visa before starting work in New Zealand” **only after the user confirms that fact and exact wording**. Do not infer present residence from job destination. Expired/changed permits invalidate affirmative disclosures and answer approvals. Restricted work rights require question-specific handling, not generic “Yes, eligible” auto-fill.

An application asking “authorised in Australia?” within an NZ role is not answered from NZ fields. Detect explicit jurisdiction, negation and question conditions; when ambiguous, ask the user. Employer declaration/consent must be explicitly handled by the candidate, never invented by a model. Country profiles store reusable answers scoped at least by country, with optional employer/question conditions and fact/disclosure revision; legacy global `cc_answers.question UNIQUE` cannot represent this safely.

## 9. End-to-end NZ application workspace

[P] Add a country selector to the existing native workspace, persistent label on record/Studio/browser, NZ source/evidence panel and independent assessment badges. Keep existing navigation/services. The table maps the requested journey precisely.

| Step | Existing component reused | Minimal proposed change / gate |
| --- | --- | --- |
| 1 Select NZ | `web/workspace.html`, `web/workspace.js` | Country view + feature gate, request-local context. |
| 2 Preferences | `web/pipeline.js`, `pipeline_workspace.py`, setup UI | Save NZ campaign/country preferences without altering root UK config. |
| 3 Search | Existing discovery engines and preview/runner | Only validated/permitted NZ sources; bounded effective snapshot. |
| 4 Review matches | `command_center.overview()`, Jobs cards | Technical/practical/immigration panels, unknown visible. |
| 5 Accreditation | Existing record UI/upload security patterns | Manual official-check panel, employer matching and immutable observations. |
| 6 Overseas suitability | Existing extracted JD and record inspection | Four-class reducer, quote/source/freshness and user decision. |
| 7 Save vacancy | Existing persistent jobs and Pick/unpick | Preserve country/provenance; add bounded manual URL/JD capture when needed. |
| 8 Choose master | `role_routing.py`, CV library | NZ title/family maps, reconciliation status, no UK fallback. |
| 9 Tailor | `scoring/tailor.py`, existing pipeline | Country policy + verified claim selections. |
| 10 Validate | `professional.py`, `validator.py`, reports | Hard facts/claims manifest; unresolved evidence blocks finalisation. |
| 11 Supporting docs | `writing_studio.py`, current writers/planner | Country context, exact approved rights/relocation text. |
| 12 Approve documents | Current Studio publish/packet previews | Separate persisted approval of final document hashes; not submit approval. |
| 13 Open employer | Existing application link/owned browser | Preserve actual URL, validated source/domain/mode and packet. |
| 14 Form assistance | Existing local fills/forms/question tools | Scoped rights/answers, verified claims and safe supported-site executor. |
| 15 Final approval | `Session.final_review(),submit()` | Fresh actor/page/packet/country-bound capability; universal path enforcement. |
| 16 Record evidence | Existing session screenshots/events, reconciliation | Atomic attempt state and no automatic replay on ambiguous receipt. |
| 17 Track progress | Existing hiring/visibility/events/inbox/Insights | Country association/filtering; statuses unchanged. |
| 18 Follow up/interviews | Reminders/calendar/assistant | Use submitted country/packet evidence; drafts remain unsent. |

Manual URL/JD capture is a proposed thin ingestion action using existing URL checks, `store_jobs()` and event functions, not a second tracking workflow. It cannot fetch a restricted board or bypass source-use restrictions by copying its content into an AI prompt. Users can supply their own notes and permitted employer text. Clearly distinguish a saved reference link from a fetched/reusable job description.

## 10. Universal external submission approval

### 10.1 Why existing guards need extension

[E] Integrated `Session.final_review()` and `submit()` already provide page signatures and one-use approval. Older `cli.py::apply()` defaults to Claude automation without `--submit`; `apply/launcher.py::run_job()` grants broad tools via `bypassPermissions`; `llm_engine.py` can submit with `--submit`; `scheduler.py::tick()` can launch submit mode. `GUARD` in `assisted_browser.py` intercepts selected click/submit events, but **does not prove control of arbitrary JavaScript fetch/XHR, hidden controls, service workers, Enter key events or raw MCP/browser/network tools**.

[P] A prompt saying “ask for approval,” setting `submit=False`, or reusing a page token only in one function is insufficient. Approval is an application-owned execution boundary with no alternative live unguarded actuator.

### 10.2 Review and capability lifecycle

```text
validated country packet -> document approval -> reviewed browser preparation
   -> final page and declarations inspected -> review snapshot
   -> fresh candidate "Approve and submit this application"
   -> server validates actor/session/ownership/revisions/duplicates/expiry
   -> atomically consumes approval and persists submitting intent
   -> one bounded permitted submission attempt
   -> affirmative receipt: applied
      otherwise: submission_uncertain -> existing reconciliation, no replay
```

The proposed review snapshot contains candidate/workspace identity, job URL and country, employer/requisition, resolved destination/origin, browser-session/document epoch, final-action identity, complete reviewed answer/consent/file hashes, packet hash, fact/disclosure/policy revisions, warnings/evidence-review decisions and observed time. Compute hash from normalized semantic state; benign CSS changes should not alter it, but changed answers/target/files must.

Generate random approval nonce after a human-facing current review. Bind it to the authenticated candidate session or locally authenticated interactive approval channel, expire it after **five minutes** by default, persist only its hash/metadata, and consume with a compare-and-set transaction before an external action. No tokens in prompts, subprocess arguments, logs, scheduled rows, browser storage or reusable API keys. Administrators and AI models cannot approve as a candidate. Missing/expired/replayed/changed approvals fail closed; reauthentication/logout, changed destination/revisions or server restart invalidates outstanding approvals.

Persist intent before click/request. Crash after consumption but before receipt stays uncertain even if no request is known to have been sent. Do not claim exactly-once delivery to an external ATS; guarantee at most one app-authorised attempt per approval and no automatic retry. Reconciliation that proves not submitted permits a **new** review/approval, never reuses the consumed token. Double tabs/workers must contend on the same job/attempt lock and approval revision.

### 10.3 One execution authority; supported-site boundary

[P] Reuse the owned assisted-browser runtime as the only V1 live application executor. Models can propose field/action references; they do not receive raw Playwright/MCP/CDP, arbitrary JavaScript, shell/network access, destination credentials or approval capabilities. Final action is a narrow server command. The compatibility path may retain the requested engine label for diagnostics but must disclose actual execution mode.

Keep DOM guard as defence in depth. For supported employer/ATS adapters, enforce all submission-capable actions and known network transactions at the executor, including pointer/keyboard manual inputs inside the managed view, frames/popups, Enter/implicit submits, JS-only requests and retries. Before approval, permit only adapter-classified navigation/draft saves/uploads after packet disclosure; deny unknown state-changing requests. Classification is versioned/tested application logic, not an AI decision. Block raw request/send-beacon/WebSocket or other uncontrolled channels; disable service-worker routes where necessary. After approval, allow only the reviewed submit transaction/action scope, not arbitrary further submissions.

Arbitrary web apps cannot be made safe solely by button-label heuristics. **Unknown/unsupported submission semantics stop managed automation.** Offer copied reviewed content/downloads and an ordinary external browser where the user personally reviews and submits. Label this as manual activity; ApplyPilot cannot enforce a user's independent browser actions or certify that they passed its managed approval. Record manual outcome/evidence as user-reported. Do not advertise universal managed-browser coverage without an adapter test proving the boundary.

### 10.4 Entry-point policy matrix

| Entry point | Proposed behaviour |
| --- | --- |
| Native Apply with agent | Existing reviewed preparation; hardened common final approval and executor. |
| `/api/center/apply` and runner `stages=['apply']` | Start/queue reviewed preparation or return approval-required with review location; `submit:true` expresses intent only, never grants capability. |
| `applypilot apply`, `--engine llm`, `--engine claude`, `--submit` | Preserve accepted syntax and selection options. Start controlled review/handoff; unattended invocation returns a documented approval-required outcome, never autonomous submission. No server/controlled session available: prepare artifacts and explain review requirement; fail closed. |
| Direct Python `launcher.main/run_job` or legacy LLM submit helper | Enforce the same guard internally, not just in CLI. Do not start an unmediated live browser/tool session in V1. Shared pure mapping helpers remain reusable. |
| `--dry-run` / `--gen` | Non-submitting by capability. Generated prompt no longer includes autonomous submit instructions or credential-bearing MCP run recipe. |
| Scheduled `mode=submit` | Preserve saved rows/intent; produce preparation or needs-review state and candidate action. A scheduled time is never fresh final approval. No token preallocation/storage/replay. |
| Continuous/parallel workers/retries | Queue preparations/review opportunities; each attempt separately approved. Never approve a batch or reuse stale locks to resubmit. |
| Raw legacy Claude/MCP tooling | Quarantined from external application execution until an allowlisted mediated actuator exists. Removing a final-submit phrase is not enough. No broad credentials/network/browser tool inheritance. |
| Manual applied marking/reconciliation | No external submission occurs; maintain existing status/evidence mechanisms, truthfully label user-reported versus detected receipt. |

Automatic email/job-application messages would also be external submissions; native Gmail/Outlook connectors remain read-only. Existing legacy Gmail MCP capabilities cannot become another submission route. No auto-consent, immigration forms, CAPTCHA bypass or credential creation is part of this design.

## 11. SQLite, configuration and migration design

### 11.1 Minimum new tables

All are **proposed**, candidate-workspace local, initialised through the existing additive schema mechanism, not a second persistence system.

| New table | Minimum fields and reason |
| --- | --- |
| `cc_country_profiles` | `country_code TEXT PRIMARY KEY`, `config_json TEXT NOT NULL`, `revision INTEGER NOT NULL`, `updated_at TEXT NOT NULL`. Holds country preferences, document-policy overrides, rights/disclosures and small scoped reusable-answer set. Three intended field groups share one versioned aggregate; no duplicated identity/history. |
| `cc_employers` | `id TEXT PRIMARY KEY`, `country_code TEXT NOT NULL`, `legal_name TEXT`, `trading_names TEXT`, `nzbn TEXT`, `website_url TEXT`, `identity_state TEXT`, `identity_evidence_json TEXT`, `revision INTEGER`, `created_at TEXT`, `updated_at TEXT`. Employer reuse across vacancies requires a stable identity. Unique partial index on `(country_code,nzbn)` when nonnull; never merge distinct unknown identities by brand name. |
| `cc_employer_evidence` | `id TEXT PRIMARY KEY`, `employer_id TEXT NOT NULL`, `kind TEXT NOT NULL`, `observation_json TEXT NOT NULL`, `observed_at TEXT`, `recorded_at TEXT NOT NULL`, `refresh_due_at TEXT`, `source_expires_at TEXT`, `result TEXT NOT NULL`, `supersedes_id TEXT`. Immutable observations, indexed by employer/kind/observed time; payload schema holds fields in section 5. |

Application code validates references consistently with the existing SQLite style; enable new FK enforcement only after testing legacy compatibility, not as an unreviewed global pragma change. Current accreditation/freshness/last successful verification is a deterministic view of observations, not another mutable employer-status table. Conflict resolution appends evidence/resolution events.

### 11.2 Additive columns on existing tables

| Existing table | Proposed additions/reuse |
| --- | --- |
| `jobs` | Nullable `country_code`, `employer_id`, `vacancy_metadata` JSON, `immigration_assessment` JSON. Existing sponsor columns remain UK-only and unchanged for history; no reinterpretation. Existing salary/location/document/status columns retained. |
| `cc_cv_sources` | Nullable `country_code`, `category`, `evidence_manifest` JSON holding reconciliation references/source revision/fact hash; same originals/revisions. |
| `cc_documents` | Nullable `country_code`, `context_json`: policy/fact/claim/packet validation manifest and approved hashes. Existing paths/variant/fingerprint preserved. |
| `cc_writing_documents` | Nullable `country_code`; reuse `source_json`/`validation`/parent versions for provenance and claim/document approval. |
| `cc_browser_sessions` | Nullable `country_code`, `review_json`, `approval_json`, `approval_revision INTEGER DEFAULT 0`. Approval JSON holds token hash, actor binding, expiry and consumed state; CAS checks session state/revision. Existing session PK remains attempt identity. |
| `cc_writing_requests` | Nullable `country_code`, `operation_id`, `context_json`. Reuse logical generation reservations for CV/cover/statement/answer operations; payload stores request key, lease, attempt/token bounds. Schema currently has no kind CHECK; callers need extension. |
| `cc_ai_usage`, `cc_pipeline_usage` | Nullable `country_code`, `operation_id`/request correlation. Existing token ledgers remain; do not count the same transport response twice. |

`cc_job_meta`, `cc_events`, `cc_campaign_jobs`, schedules, listing checks and reconciliations stay structurally reusable. Events use existing payload support for country/evidence/policy revisions. `cc_answers` retains its global unique question constraint and all existing rows; new country-aware paths use a bounded answers map in `cc_country_profiles`, avoiding a destructive table rebuild. Legacy answers require explicit review before becoming scoped; unscoped rows cannot answer NZ rights questions. If answer scale later justifies normalization, that is a separate migration.

### 11.3 Configuration ownership

- Existing `profile.json`: one canonical professional history plus proposed verification manifest/typed metadata. Extend `_validate_profile_patch()` and entry validators; they currently reject unknown top-level fields or strip unknown nested metadata.
- `cc_country_profiles.config_json`: validated country-sensitive preferences/rights/disclosures/answers and policy overrides, no credentials or full CV copies. Use optimistic revision checks on saves.
- `searches.yaml`: preserve root legacy settings and existing campaigns; append/modify only the explicit NZ campaign with expected revision. Proposed flag name: `nz_workflow_enabled`, default false, with a service-level deny override. This key is a proposal and must be added to validation; it is not an existing switch.
- Country policy defaults: small versioned application-owned GB/NZ policy definitions; configurable bounded overrides resolve through country profiles. Do not edit old UK templates globally for NZ; intentional common factual constraints supersede old stretch allowances.
- Existing source catalogs: extend countries/access metadata; no immigration API keys because no authorised API is established.
- Existing `.ai-settings.json`/environment: provider credentials/routing remain shared; add bounded budgets through the existing validated settings mechanism.

### 11.4 Migration compatibility and rollback

1. No database changes now. Future migration is idempotent additions only; no URL rekey, table/column removal, rewrite of historical statuses, bulk country inference or mass CV regeneration.
2. Back up each affected workspace with SQLite backup and matched files before approved upgrade. Preserve configuration bytes/hashes and a row/artifact baseline; use synthetic/backup copies for tests.
3. Replace positional inserts before extending affected tables: for example, `cv_library.py::import_files()` and `assisted_browser.py::start()` currently use full-row `INSERT ... VALUES`. Old writers fail if columns are added without explicit column lists. Audit every affected insert/export/import/trigger, including tests.
4. Existing legacy read views remain available with null country and historical unverified labels. Country adoption is explicit; global profile facts are never preverified by migration. Additive nullable indexes/columns allow old historical records to remain readable.
5. Jobs remain the application identity; new country metadata is included in retained-history snapshots, Insights/exports, backup/restore path rebasing and email matching. Include employer evidence and browser approval/receipt artifacts in a reviewed backup policy; current `workspace_transfer.py` does not export `browser_sessions/`.
6. Disable NZ entry points for rollback, cancel queued NZ operations, invalidate outstanding approvals and retain new rows/files for later recovery. Do not merge NZ rights into legacy fields. Do not drop columns/tables to roll back.
7. An old executable cannot safely handle new scoped records or positional inserts. Supported rollback is a **forward-compatible safety build** with NZ disabled, not blind binary downgrade. Emergency full restore requires downtime and explicit reconciliation of any later applications; never overwrite post-backup submissions/history.
8. Universal factual/approval controls have no production “off” switch. Disabling NZ or rolling back a feature cannot restore unattended submitting or unsupported-claim publication.

## 12. AI costs, caching and repeat prevention

[P] Keep current providers/models/routes. No LLM call for country selection, CV-title mapping, accreditation freshness, official-result recording, deterministic overseas rules or approval. No unattended INZ polling.

Generation key: workspace + country + job/source-content hash + selected CV revision + selected verified-fact/wording revisions + document kind + policy/version + exact disclosure/evidence inputs actually used + effective model settings. Employer-evidence changes invalidate only immigration assessments or documents that included those facts, not unrelated technical scores/CVs. GB/NZ or two candidate workspaces never share a document cache entry.

Current `command_center.py::fingerprint()` hashes whole search/variant files, and `role_routing.py::score_fingerprint()` hashes the whole profile. New scoped fingerprints must project actual dependencies so adding NZ campaign/verification metadata does not rescore all UK jobs. Preserve legacy hashes; only recognize a pre-activation compatibility baseline when unchanged legacy inputs can be demonstrated. Never label an unknown historical document current or fact-verified merely to avoid a stale badge.

Use `cc_writing_requests` reservations/leases and existing usage ledgers. Identical in-flight operations coalesce/reject deterministically; retry with same operation ID returns prior result. “Fresh alternative” requires user action and consumes allowance. Failed/abandoned attempts retain request accounting; recovered leases do not silently replay a charge. Every transport retry/JSON repair/draft/reviewer call belongs to the same operation and a hard request/token bound. One request records usage once even if mirrored in pipeline/interactive views.

Configurable bounds: workspace daily operation/request ceilings, optional lower country/task ceilings, per-operation transport attempts, writer drafts/reviewer calls, input/output/reasoning tokens, source windows and optional monetary ceiling where model prices are supplied. Sum across GB/NZ for the workspace cap. Use conservative maximum-token reservations before calls; reconcile actual usage without pretending missing counts are zero. If prices are unknown, enforce request/token caps and label money unknown rather than claiming a strict monetary cap.

Preserve existing default allowance configuration unless explicitly updated; expose effective intersected limits in preview. First NZ configuration can select bounded writer drafts using existing two/three-draft controls; one-draft support would be an optional small extension, not assumed existing. Reuse approved wording and render manually with no inference when possible. Optional Decisions observation remains off unless explicitly enabled and budgeted.

## 13. UK preservation, release gates and regression strategy

### 13.1 Preservation contract

With NZ disabled, existing UK searches, routing/CV sources, country-specific wording, statuses/hiring timelines, integrations and archived/submitted bytes remain intact. NZ saves cannot mutate root defaults or UK campaigns; an NZ profile edit cannot overwrite shared history unless the user explicitly performs canonical reconciliation. Preserve API/CLI syntax, utility modes, accepted configuration keys and route names. Added metadata may be nullable.

**Intentional cross-country changes:** unattended submit becomes approval-required; factual stretch/tolerance/bypass policies are removed; historical unverified artifacts cannot be presented as newly approved facts. Documented contract tests must expect these changes. It is impossible to prove “all UK behaviour unchanged” while also enforcing E; release notes and operator review must state the exceptions.

### 13.2 Gates

- `NZ enabled` [P], default false, controls UI/routes/discovery/generation and server checks; no client-only flag.
- Source readiness: permitted use + tested actual country/identity/description/link/failure handling, not a country enum.
- Manual accreditation verification and four-class evidence presentation complete, including unpublished/no-result handling.
- Country isolation validated through legacy routes, bulk APIs, CLI, subprocesses, caches, form answers, inbox, exports and browser sessions.
- Common factual finalisation and approval invariants enforced before **any** external application-ready release.
- Managed assisted application enabled only for specifically tested/permitted executor/ATS paths; unsupported paths are manual handoff.

### 13.3 Required regression suites

Reuse the current test/eval foundation: `tests/test_global_search_and_visuals.py`, `test_pipeline_workspace.py`, `test_pipeline_reliability.py`, `test_native_studios.py`, `test_tailor_strategy.py`, `test_resume_assembly.py`, `test_supporting_statement.py`, `test_nhs_writing_guidance.py`, `test_assisted_browser.py`, `test_question_assistant.py`, `test_apply_lock_recovery.py`, `test_submission_reconciliation.py`, `test_job_lifecycle.py`, `test_dashboard_multi_user.py`, `test_ai_routing.py`, `test_workspace_transfer.py`, and strict offline AI eval scripts.

Add cases for: GB/NZ interleaved tabs/tasks; no-scope legacy queue exclusions; malicious wrong-country IDs; adopted/unassigned historical records; same exact question with different rights; NZ upload not changing UK mapping; no-result accreditation unknown; stale/expired/conflicting identity evidence; negated/conditional overseas text; fresh evidence failure not extending success; meaningful URL query identity; NZD/hour/package parse ambiguity; unsupported factual employer/title/duration/number/tool/credential; post-polish/manual/custom text bypass; single-use/expired/wrong-actor approval; hidden JS submission/Enter/popup/frame/raw-tool paths; concurrent double-click; scheduler/CLI bypass; crash windows and uncertain reconciliation; migration rerun/positional insert/old snapshot restoration; missing usage/unknown prices and duplicate in-flight generation. No real employer submissions are tests.

## 14. Security, privacy and evidence retention

Reuse current candidate authentication/session/CSRF, workspace path ownership, public-URL/redirect/address checks, size/type/ZIP limits, vault and redaction. Apply them consistently to new evidence uploads and source/browser adapters; no new role is necessary. Country is a data/behaviour boundary within a candidate, not a replacement for user authorisation.

- Keep public employer evidence separate from private candidate immigration facts. Ordinary vacancy scoring needs professional facts only, not passport identifiers, medical history or full permit scans.
- Rights/disclosure records are personal information; restrict to owning candidate and authorised paths, minimise fields and do not include whole objects in SSE/admin/analytics/error logs. Optional proof artifacts must be access-controlled and, if stored, encrypted using the existing vault pattern with explicit backup/key handling. V1 can use source-reference/attestation rather than requiring document scans.
- AI receives only selected professional claims and approved disclosure wording if needed. Employer/board data with prohibited AI reuse is not sent for generation. Prompt injection cannot grant tools, approve facts, alter country or change source policy.
- Validate URLs/redirects/DNS and block private targets across new retrieval/executor paths; do not auto-fetch an evidence URL merely because it was entered. INZ manual evidence fields validate syntax without automated source lookup.
- Maintain append-style events with evidence/fact/policy revisions, user actor and document/attempt hashes. Logs are not secret storage; approval nonces and protected document contents are excluded. Store only necessary receipt evidence; screenshots may contain answers and require private retention.
- Explicit cleanup/retention follows existing lifecycle ownership checks. Retiring a job must not delete shared employer evidence referenced by other vacancies. Retain evidence references needed for submitted-history audit; implement user-requested artifact removal without rewriting the original submission outcome. Default retention duration requires owner choice; do not silently keep all immigration evidence forever.
- Backups include governed new data/artifacts; credential-free archives must not accidentally include decrypted protected proofs or approvals. Imported approvals are invalidated, never made usable in the destination.

## 15. Outstanding operational decisions and assumptions

Decisions A–E are accepted, not reopened. Remaining details:

1. Which initial employer/Workday sources have permission for retrieval, storage and AI use? No live reliability result or entitlement has been established.
2. Will INZ/MBIE offer a written authorised feed/integration agreement for this use? Manual official verification is the complete V1 fallback, not a reason to block the rest of the design.
3. Confirm proposed 24-hour review freshness, five-minute approval TTL, default source budgets and NZ document length target; these are configurable product defaults, not statutory values.
4. Candidate must reconcile/import the three actual masters and supply country-specific work rights/disclosures; this review did not access their private CVs or visa details.
5. Set evidence/proof/screenshot retention and optional budget prices/ceilings. Decide whether protected proof uploads are needed beyond manual source references.
6. Select initial permitted ATS adapters after tests. Unknown sites stay manual; full arbitrary-site mediated execution is not assumed feasible.

## 16. External source register

Checked 16 October 2026. Links identify research evidence, not configured API endpoints or production integrations. Some older INZ URLs returned 404; the current canonical pages below were located through public navigation. Glassdoor terms retrieval returned 403. No credentials, employer searches, registrations or forms were submitted.

| ID | Source and relevant observation |
| --- | --- |
| X1 | [INZ accredited employer list](https://www.immigration.govt.nz/work/requirements-for-work-visas/approved-employers/accredited-employer-list/) — NZBN/name searches, daily updates, unpublished employers. |
| X2 | [INZ online-system terms](https://www.immigration.govt.nz/about-us/about-this-site/using-our-website-and-online-tools/terms-of-use-of-our-online-systems/) — automation/extraction restrictions, standard browser access unless agreed otherwise. |
| X3 | [INZ Accredited Employer Work Visa](https://www.immigration.govt.nz/visas/accredited-employer-work-visa/) — accreditation/offer/job check/candidate requirements separate; no CV-only evidence assumption. |
| X4 | [INZ copyright](https://www.immigration.govt.nz/about-us/about-this-site/copyright/) and [online tools overview](https://www.immigration.govt.nz/about-us/about-this-site/using-our-website-and-online-tools/) — attribution/exceptions and distinct online services; not public accreditation API documentation. |
| X5 | [python-jobspy 1.2.0](https://pypi.org/project/python-jobspy/1.2.0/) — version-pinned country support, Google unavailable, request parameters/date limitations. [Upstream repository](https://github.com/speedyapply/JobSpy) reviewed as secondary, potentially newer guidance. |
| X6 | [Indeed NZ terms](https://nz.indeed.com/legal) — automated access restrictions, section A.3 Indeed Apply prohibition, general automated-system/AI data-use limits. A private agreement, if any, must be checked independently. |
| X7 | [LinkedIn User Agreement](https://www.linkedin.com/legal/user-agreement), section 8.2 — scraping, unauthorised bots and access-control restrictions. |
| X8 | [SEEK developer documentation](https://developer.seek.com/) — API approval required; described hirer/ATS/profile use cases do not establish an open job-search feed. |
| X9 | [Trade Me developer home](https://developer.trademe.co.nz/), [API terms](https://developer.trademe.co.nz/terms-and-conditions), [use cases](https://developer.trademe.co.nz/api-overview/use-cases) — Jobs contact, approved-purpose/access/data-combination restrictions; aggregation approval unverified. |
| X10 | [MBIE API portal](https://api.business.govt.nz/) — public portal reviewed; linked API catalogue was not retrievable. No suitable employer-accreditation entitlement established. |

## 17. Design acceptance summary

The design meets A through auditable manual official accreditation checks and an authorisation-dependent automation path; B through explicit immutable country context with shared history; C through four transparent evidence classes and unknown visibility; D through existing engines/formats/providers, source-use checks and bounded calls; E through fail-closed factual finalisation and a universal fresh human submission boundary.

Document verification: repository paths/functions and companion phase contracts were cross-checked; Markdown whitespace checks passed for both requested files. Final Git status showed these two reports as the only new paths relative to this phase's initial listing. No application tests, migration or live provider/application checks were run.

Implementation must follow the companion phased plan after explicit approval. No features have been implemented in this design phase.
