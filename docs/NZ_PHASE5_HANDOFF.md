# NZ Phase 5 — preparation, review, tracking, usage and portability

Verified fixture implementation following Phase 4. The user chose synthetic
candidate fixtures; no real candidate master, official employer result, provider
call or permitted live-source acceptance is claimed.

## Delivered

- Explicit NZ preparation capability requires all three current reconciled masters.
  Model preparation also requires matching retained vacancy text, source storage/AI
  permission, current access/readiness records and a confirmed/explicitly selected
  NZ destination. Reference-only records never silently fetch/send board text.
- NZ scoring projects only supported non-personal/non-disclosure master assertions
  and technical vacancy clauses. Practical fit and immigration evidence remain
  separate. Recruitment/accreditation edits do not change technical inputs or
  cause another identical score request. Existing 1–10 scoring remains.
- Operation-local queues select persisted NZ rows only; no-country legacy queues
  retain NULL/GB compatibility. Source repair/rescore is country-scoped. NZ workers
  do not fall back to `resume.txt` or the old unrestricted browser/AI enrichment.
- Bounded permitted JSON-LD/Workday detail recovery reuses normalized observations.
  Incomplete/blocked/schema failures stay truthful and recoverable through permitted
  refresh/manual capture; no unsupported extractor fallback is introduced.
- Run configuration and private country-preference snapshots cross subprocesses.
  Worker owner/country/campaign/config validation rejects missing or altered
  snapshots instead of falling back. Run IDs include a collision-resistant suffix.
- Candidate-owned CV/letter/evidence text can be prepared without AI, including
  reference-only manual handoff. New wording stays draft until factual validation.
  Original advert-country evidence is separate from explicit destination choice.
- Final-document review binds exact current TXT/PDF bytes, country, target,
  requirements, rights change epoch and recruitment/identity context. It is actor-
  bound and distinct from factual proof and later submit approval. Packet output
  distinguishes factual `ready` from reviewed `application_ready`.
- Country answers use the bounded country-profile aggregate. Exact question,
  jurisdiction, conditions, reviewed evidence, expiry and rights epochs are bound
  to audit. Old global yes/no answers cannot complete NZ requirements. Ambiguous,
  negated or foreign questions remain manual-review-only.
- NZ manual submission/hiring/notes/reminders and lifecycle/history reuse existing
  records. Matched-message jurisdiction is checked before applying an outcome.
  Analytical and retired records retain persisted country without bulk adoption.
- Existing request/usage ledgers now provide operation keys, leases, held expired/
  failed requests, concurrent duplicate reuse and explicit fresh request keys.
  Actual provider-attempt hooks enforce input/output, per-operation and shared
  GB+NZ workspace request caps, including transport retries. Unknown attempt usage
  remains unknown. Correlation keys deduplicate interactive/pipeline usage.
- Writer caches project away unrelated canonical metadata. New document inputs
  project away NZ-only sources/campaigns. Legacy fingerprint compatibility is
  accepted only from an exact known pre-edit fingerprint; unknown historical
  documents are not relabelled fact-verified/current merely to suppress a badge.
- Portable WAL snapshots are standalone SQLite images. Owned artifact paths and
  nested document metadata rebase on restore. Imported facts/wordings/document/
  answer authority, operation ownership, schedules and active sessions cannot
  resume as fresh authority. Populated applications/evidence/usage databases
  cannot be overwritten by a backup. Source sample validations are rechecked.
- Employer evidence/browser artifact bytes require explicit `--include-evidence`
  or export query opt-in; ordinary backups retain metadata. Generated factual
  sidecars participate in governed owned-file cleanup. Symlink escapes are skipped.

## Interfaces

The authenticated NZ workspace adds preparation/tracking panels and shared final-
document review. New `/api/center/nz/` routes include `preparation`, `prepare/start`,
`document/manual`, `document`, `packet/prepare`, `packet/document`,
`packet/requirements`, `documents/review`, `answer/review`, `destination/choose`,
`tracking`, `messages`, `messages/review`, `history`, `calendar`, lifecycle actions
and explicit reconciliation. Shared `/api/center/documents/review` and
`/api/center/preparation/request` retain country/owner validation. Existing GB
commands/routes remain accepted with the approved factual/approval exceptions.

## Verification

`python scripts/agent_verify.py`: compile pass; **771 passed / 59 skipped** in
309.03 seconds; strict AI **7/7**, formal dataset **13/13**. `git diff --check`
passed. Node syntax checks passed for the preparation/document-review assets.
Local-only Chromium exercised owned-CV preparation → actual PDF round trip →
human document review → external manual-submission tracking. No employer/provider
transaction occurred; external browser requests were blocked.

Tests cover concurrent duplicate generation, failed/expired leases, shared-country
budgets, known/unknown usage correlation, technical-input invariance, wrong-country
bulk actions, stale/reversed rights reviews, snapshot ownership/configuration,
retired country identity, exact document review, safe WAL restore and authority
invalidation. Earlier regressions were corrected: local-only forms do not require
an AI routing task, minimal legacy repair schemas remain supported, and serialized
WAL snapshots are converted to standalone rollback-journal images before restore.

## Release boundaries

NZ remains default-off. Actual candidate reconciliation, source permission/live
reliability and owner release approval are outstanding. Live browser execution
remains contained pending Phase 6 authority; schedules/imports cannot grant submit
approval. No complete universal real-ATS safety/coverage claim is made.

Rollback disables affected preparation sources/workflows, holds operations for
review and retains facts, imports, usage and submitted artifacts. Do not restore
old factual-stretch or autonomous-submit bypasses, destructively restore populated
application databases, or treat imported historical authority as a new approval.
