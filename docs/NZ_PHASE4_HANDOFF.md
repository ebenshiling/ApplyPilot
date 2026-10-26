# NZ Phase 4 — shared factual finalisation and country master sources

**Implementation verified with synthetic fixtures.** The user explicitly selected
“Continue with fixtures” when asked for the three real NZ master CVs and canonical
professional evidence. Real candidate import/reconciliation is deferred; nothing
here certifies candidate history or enables production release.

## Shared factual authority

- Additive `profile.json.canonical_facts` contains stable atomic fact/entity IDs,
  source references/quotes, optional bound professional-field references, typed
  employment periods and credential/disclosure status/expiry metadata.
- Imports, old profile text, supplemental facts, model output and fact citations
  are **unverified by default**. Fact verification and meaning-preserving wording
  review are authenticated human actions bound to existing audit-ledger entries.
  Client-provided “verified” flags/manifests do not satisfy those proofs.
- Employer/project-specific assertions retain attribution; incomplete credentials
  cannot become earned qualifications. Exact verified wording or explicitly
  evidence-reviewed atomic wording is required. Global skill proof does not
  establish use at a particular employer. Typed calendar-period union avoids
  overlap double counting and makes no FTE, tool-tenure or approximate-year claim.
- The common finaliser covers actual text bytes, selected fact dependencies,
  country/policy/disclosures and target identity. Relevant edits invalidate proof;
  unrelated fact additions do not invalidate a document's factual manifest.
- Actual PDF bytes require audit-bound round-trip checks: verified assertions
  survive, no source content is added/omitted, and the resulting digest matches.
  Packet rendering validates the temporary PDF **before** replacing an older file.

## Publication and legacy paths

Batch persistence, Studio publication, legacy job-match generation, job-scoped PDF
conversion and packet readiness/download use the common factual authority. Pure
rendering and source/Studio previews are draft/source utilities and do not grant
application-ready status. Historical unverified artifacts are retained, not
automatically relabelled as verified. Active/submitted/uncertain jobs cannot have
documents published through the batch helper. Failed new drafts preserve prior
current pointers and document bytes.

Lenient style/relevance configuration cannot disable factual citation/quantity
checks. Reviewer instructions no longer allow learnable-skill/minor-stretch
fabrication. Assembly no longer guesses project years/stacks/achievements or turns
employment into invented project work. Cover instructions use candidate sources
instead of fixed example employers, and closers no longer inject new capability
claims from JD keywords. Genuine UK/NHS history remains available.

## NZ master presentation and disclosures

- Additive `cc_cv_sources.country_code/category/reconciliation_json` metadata;
  collision-safe imports retain original DOCX/PDF/TXT/MD bytes and text revisions.
- Software, data/BI and support sources route all 22 configured exact titles plus
  conservative family matches. Unknown, ambiguous, absent, archived or unreconciled
  NZ sources require explicit selection/reconciliation; there is no UK/default CV
  fallback. New NZ imports are excluded from inferred UK routes.
- NZ rename/archive actions update the dedicated campaign's nested references
  without reserializing UK configuration. Shared UK defaults cannot be replaced by
  an NZ master action. Source edits invalidate reconciliation; originals remain.
- Shared fact-review UI is available in UK Settings and the NZ workspace. The NZ
  view adds source import/original/TXT/PDF preview, presentation editing and
  reconciliation diagnostics. Generated DOCX is not claimed; uploaded originals
  remain downloadable as source documents.
- Versioned GB/NZ writer policy blocks keep NZ employer-requested evidence distinct
  from assumed NHS forms, preserve real UK/NHS history, and forbid invented NZ
  experience/rights. NZ application values do not inherit UK authorization,
  relocation, availability or compensation. Expired/foreign/negated/conditional
  rights questions are held for clarification rather than guessed from old answers.

Authenticated additions: GET `/api/center/canonical`, `/api/center/nz/cv`,
`/api/center/nz/cv/source`; POST `/api/center/canonical/action`, `/canonical/check`,
`/api/center/nz/cv/import`, `/cv/reconcile`, `/cv/action`. Country, ownership, CSRF
and optimistic fact revisions reuse existing adapters. No provider calls occur
when importing, reviewing facts or checking factual coverage.

## Verification and remaining gates

`python scripts/agent_verify.py`: compile pass; **752 passed / 59 skipped** in
265.44 seconds; strict AI **7/7**; formal dataset **13/13**. Local country-only
Chromium tests were enabled, external page requests blocked, legacy employer-browser
tests disabled, provider credentials cleared, existing dependencies reused and all
data under isolated temporary roots. Node syntax checks passed for canonical,
Studio and workspace scripts.

Initial broad regressions exposed old fixtures that treated unverified text and
blank PDFs as ready. Positive packet/legacy tests now explicitly attest synthetic
assertions through the real human-action service and render extractable PDF text;
negative tests prove unverified drafts, altered bytes, new tools/numbers/entities,
expired credentials/rights, cross-country disclosures and source edits fail closed.
No tests were removed to conceal those intentional factual-policy changes.

NZ end-to-end preparation remains gated until Phase 5 wires its explicit country
workflow. Live application execution remains contained pending Phase 6 authority.
Actual candidate masters/evidence and permitted live source/release evidence are
still absent. Phase 5 must complete scoped queues, approval/usage/portability and
dependency-scoped legacy compatibility; do not advertise a full released NZ workflow.

Rollback retains canonical facts/audit, source originals/revisions and submitted
bytes, disables affected new preparation and leaves unverified work as drafts.
Do not restore the old factual-stretch/lenient publication bypasses.
