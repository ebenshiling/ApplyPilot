# NZ Phase 1 — country foundation and safety containment

This branch implements **Phase 1 only** of `NZ_JOB_SEARCH_IMPLEMENTATION_PLAN.md`.
It is not a released New Zealand search, immigration-verification or application
workflow. No Phase 2 providers, campaigns or employer-accreditation connector
have been introduced.

## Branch and baseline

- Feature branch: `feat/nz-phase1-country-foundation`.
- Isolated worktree: `C:\Users\swazy\AppData\Local\Temp\opencode\applypilot-nz-phase1`.
- User-authorised prerequisite checkpoint: `18f8888` (`Checkpoint existing workspace baseline for NZ phase 1`).
- The checkpoint captures the pre-existing current application and approved
  discovery/design documents separately from Phase 1. It excludes `.env`, user
  databases/CVs, generated media, scratch artifacts and untracked operational
  histories. Files already present in committed HEAD remain inherited.
- The original `main` worktree at `/home/swazy364/code/ApplyPilot` is not edited
  or switched. Nothing is pushed, deployed or migrated in a real workspace.
- Operational evidence: `ops/implementations/20261016-nz-phase1/`.

## Country contract

`src/applypilot/country_context.py` provides canonical `GB` and `NZ` identifiers,
strict alias validation, immutable serialized snapshots and a context-local
operation scope. Dictionaries returned from snapshots are copies. Internal
configuration and private preference values are excluded from snapshot repr and
public diagnostics.

Persisted job jurisdiction wins over a request. A request cannot assign an
existing job to NZ by changing the selected tab or campaign. Legacy job country
stays SQL NULL; the UK/legacy experience continues to use existing preparation
behaviour without claiming that migration verified its destination. Unknown
explicit identifiers fail safely rather than becoming UK.

New records stored under a captured UK pipeline context persist `GB` across core,
JobSpy, Workday and direct extraction storage paths. Duplicate observations do
not backfill or reassign existing records. Country determination/provenance for
ambiguous global adverts is future ingestion work; this is not a geocoder.

The dashboard captures per-run search configuration before launching a worker,
always writes the private run override, and exports its country to the child.
A snapshot-write failure now refuses launch instead of falling back to mutable
workspace configuration. Streaming/discovery/enrichment workers explicitly copy
context. Changing workspace settings or views does not switch a running task's
captured country/search configuration.

The normal preparation queues and legacy job-list API exclude NZ before limits
are applied. Job mutations, bulk targets, direct writers, route selection and
legacy HTTP operations validate jurisdiction. Bulk target validation occurs
before job mutations. Foundation-only NZ reads do not return UK jobs/documents.

## Additive database changes

The existing `database.init_db()` forward-migration convention adds:

1. Nullable `jobs.country_code` with a GB/NZ/NULL constraint.
2. `cc_country_profiles(country_code, config_json, revision, updated_at)`.

No historical country backfill, status rewrite, document replacement, profile
copy or employment/qualification migration is performed. Repeated initialization
is idempotent. Historical documents still relate to the same immutable job URL;
document/session country columns are deferred until those later features need
them. Retained lifecycle snapshots carry country when present.

Migration tests operate on synthetic old-schema SQLite databases only. The code
will use the normal additive startup migration when an operator later chooses to
upgrade a real workspace; **that upgrade was not executed here**.

## Feature flag and scoped preferences

`nz_workflow_enabled` in `searches.yaml` defaults to false. Only literal boolean
`true` opts into foundation settings. `APPLYPILOT_NZ_DISABLED=1` is an additional
service deny; neither setting enables live application execution.

With the flag off, the native selector labels NZ as foundation-only and disables
it; explicit NZ API requests fail. With it on, NZ exposes only a clearly labelled
preference foundation. Search, scoring, CV routing, writing/publication and
application operations remain blocked for NZ even when this flag is true.

Authenticated foundation APIs:

- `GET /api/center/country/preferences?country_code=GB` (or NZ when enabled).
- `POST /api/center/country/preferences` with `country_code`, `patch` and
  `expected_revision`.

Supported sections are `search_preferences`, `work_authorization`,
`resume_routing`, `document_policy`, `sponsorship_evidence` and
`application_operations`. Saves are version-checked, bounded JSON, credential-free
and audited without logging disclosure contents. Each provided section replaces
that section; other sections remain. Existing global UK preferences, profile and
search files are not overwritten by these saves.

These are **storage/policy-reference foundations**. They do not replace the
established UK configuration consumers or implement complete NZ policies. NZ
work-right projection has no fallback to legacy global yes/no values; live NZ
forms are unavailable. Do not populate sample visa claims or treat saved settings
as verified facts.

`applypilot run --country GB ...` accepts an explicit operation country. Invalid
country values and NZ preparation requests fail. Existing geographically global
search configuration is not converted wholesale into GB; only recognised UK/NZ
context values participate in this new foundation.

## UK sponsorship isolation

UK enrichment resolves explicit jurisdiction or conservative legacy UK evidence;
an NZ location, conflicting NZ evidence or an unknown explicit country cannot
trigger a UK annotation. The UK text detector, sponsor matcher and annotator all
require explicit resolved UK context; omitted/NZ/invalid context returns Unknown
without a register lookup. Known UK enrichment passes `country_code="GB"` and
retains the existing register/text implementation.

Existing previously stored signals are not mass-cleared or reclassified. There
is no NZ accreditation claim, new official API or immigration eligibility logic.

## Submission safety: contained, not universal approval implemented

**All live application browser execution is disabled in this branch.**
`apply/safety.py` has no configuration or environment bypass. Phase 1 does not
claim that the older DOM guard proves safe final submission on arbitrary sites.

Containment covers:

- Native assisted-browser start, run, submit and external input/action methods.
- Claude worker/main/run/prompt-MCP provisioning and Chrome worker launch.
- Legacy LLM workers, final-submit helper and live browser actuator primitives.
- API/runner apply launch, including normalized legacy stage spelling.
- CLI apply modes, including `--submit`, `--continuous`, `--dry-run` and `--gen`.
- Scheduled browser execution: due queued prepare/submit intentions are retained
  but held `needs_review`; no worker or employer availability request is launched.

Legacy actuator names remain importable but quarantined wrappers do not retain a
`__wrapped__` callable escape hatch. Pure parsing, planning, evidence, PDF and
document functions remain usable for UK/legacy preparation.

The apply CLI reports containment with exit code **3**. Manual utility modes and
tracking remain; failed-job reset no longer clears `submission_uncertain` and
does not sweep NZ rows into legacy retry queues. Reconciliation and historical
submission evidence remain available. No imported/static boolean counts as final
approval, and a UI control is not the enforcement mechanism.

Users can prepare/review/download documents, follow an employer application link
in their ordinary browser, submit personally and record a manual outcome. The
platform cannot control independent user-browser actions. An external/manual
submission is not falsely labelled as a detected managed-browser receipt.

Re-enabling mediated automation needs a later authorised phase implementing and
testing the complete actor/page/packet-bound approval and actuator boundary.
Changing a flag or deleting this containment check is not a supported rollback.

## Verification evidence and limitations

Actual command results and failure/recovery history are recorded in
`ops/implementations/20261016-nz-phase1/status.json`. Required verification uses
the existing Python 3.11 `.ci311` Windows environment, explicit `PYTHONPATH` to
this worktree and dedicated temporary `APPLYPILOT_DIR`/`APPLYPILOT_MULTI_ROOT`.
No dependencies were installed and no production/staging data was used.

Final `python scripts/agent_verify.py`: **620 passed, 59 skipped**, strict AI
regression **7/7** and formal dataset **13/13**; compilation passed. The full
passing suite includes **78 new focused cases**, including the explicitly enabled
local-only country UI check. JavaScript syntax verification also passed. Skipped
legacy browser/symlink/environment-gated cases are not represented as passed.

The existing interpreter/dependency paths are UNC. Repeated HTTPX SSL context
construction against a UNC CA bundle caused timeouts. Tests use the **existing
local certifi bundle** through `SSL_CERT_FILE`; TLS verification is not disabled.
Provider credentials/local-model URL are cleared. The new local-only country UI
test is enabled with `APPLYPILOT_RUN_COUNTRY_UI=1`; its browser blocks nonlocal
requests. Old employer-browser suites remain environment-gated and are not
claimed as verified final-submission coverage.

The initial Windows pre-state script could not resolve Linux linked-worktree Git
metadata. Its null Git fields are annotated, not treated as a clean baseline.
Native WSL Git verifies branch/checkpoint/changes and captures the post-state.

Known deferred work includes NZ sources/campaigns, employer verification,
canonical-fact reconciliation, complete country prompts/disclosures, historical
analytics partitioning and approved managed applications. UK writer factual
tolerances are not globally redesigned here; direct NZ preparation/publication
is blocked pending the later shared factual gate.

## Operational handoff and rollback

1. Review the separate baseline checkpoint and the subsequent Phase 1 commit.
2. Back up real user workspaces before any separately approved upgrade.
3. Keep NZ off in normal operation. The foundation flag is not a release switch.
4. To contain a defect, disable NZ settings/use a compatible safety build and
   preserve new rows, historical files and uncertain-attempt evidence.
5. Do not drop the new schema, infer historical countries, restore an old DB over
   newer submissions or restore unattended application tooling.
6. Do not begin Phase 2, deploy or push without further authorisation.
