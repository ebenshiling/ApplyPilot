# NZ Phase 3 — manual employer and recruitment evidence

Phase 3 follows the verified Phase 2 implementation in the same isolated feature
worktree. The user authorised continuation through the remaining approved phases.
No real candidate DB/configuration, official service or live employer was accessed.

## Delivered

- Additive `cc_employers`, immutable `cc_employer_evidence`, nullable
  `jobs.employer_id` and `jobs.immigration_assessment`. Schema creation is rerunnable.
- Legal/trading names and optional 13-digit NZBN, explicit identity corroboration,
  revisions and separate hiring-employer/recruiter/brand/group relationships.
  NZBN format/name similarity never confirms ownership or merges employers.
- Exact NZBN/name suggestions precede fuzzy suggestions; every link still requires
  manual hiring-identity confirmation. Identity edits invalidate old vacancy links.
- Manual official INZ checks, employer assertions and user notes retain separate
  provenance. Actual query, observed/recorded timestamps, actor, identity snapshot,
  exact quote/hash, audit event, optional source effective/expiry dates and bounded
  owned evidence artifacts are retained. The server derives the authenticated actor.
- Immutable history uses append-only corrections with explicit superseded IDs and
  resolution notes. Notes/employer assertions cannot override official evidence;
  failed checks cannot retract successful evidence. Artifact digest changes make
  associated evidence unavailable rather than silently accepting it.
- Fresh/stale, source-expired, identity-ambiguous, unavailable and conflicting states
  remain distinct from an observed accreditation result of `expired`. Default
  official-check refresh is 24 hours. Failed rechecks preserve prior successful dates.
- `not_listed` reduces to unknown and explains the official non-publication limitation.
  No automated INZ client, feed, search, page preview or hidden endpoint exists.
- Literal deterministic vacancy evidence carries quote offsets, jurisdiction,
  applicability, negation/conditions, source hash/URL/date and rule version.
- Four recruitment labels: explicit support, potential review, explicit restriction,
  and unavailable/conflicting. Accreditation alone can support only potential review.
  Official adverse evidence and ambiguous identity cannot silently become support.
  Stale sole evidence stays unknown. No technical score/status is changed.
- Separate candidate review decisions and saved NZ disclosures, without inventing
  rights/visa/job-check approval. Unknown jobs remain in default views/filter results.
- NZ workspace identity/link/manual-check forms, evidence history/artifact download,
  local stale reminders and recruitment-class filtering. Official links open in the
  user's ordinary browser; they are never fetched by ApplyPilot.

## Authenticated route additions

GET `/api/center/nz/evidence`, `/evidence/history`, `/evidence/artifact`,
`/employers/suggest`; POST `/api/center/nz/employer/save`, `/employer/link`,
`/evidence/record`, `/evidence/review`. Explicit NZ context and existing workspace
ownership/same-origin/CSRF controls apply. Wrong-country job IDs are rejected.

## Verification

`python scripts/agent_verify.py`: compile pass, **712 passed / 59 skipped** in
306.34 seconds, strict AI **7/7**, formal dataset **13/13**. Existing local Windows
Python 3.11 dependencies were reused with this worktree on `PYTHONPATH`, isolated
workspace/user roots, provider credentials cleared and local CA configuration.
Country-only Chromium tests were enabled; legacy live employer tests stayed off.
No live NZ source/INZ reliability or real manual accreditation result is claimed.

`node --check src/applypilot/web/nz-evidence.js` and `git diff --check` passed.
The focused suite exposed a conditional-sponsorship parsing omission; the rule
was corrected and the completed full suite includes that case.

## Continuing boundaries

NZ remains default-off. Phase 1 application containment and NZ document-generation
guards remain enforced. Phase 4 must reconcile the three supplied CV masters with
canonical candidate evidence before finalisation. No source/evidence record in a
synthetic test grants permission, official accreditation, sponsorship or visa rights.

Rollback disables NZ evidence edits while retaining identity, immutable observations,
artifact hashes and safe unknown rendering. Do not delete history or restore UK-only
sponsorship assumptions onto NZ jobs. UK sponsor fields/history are not reinterpreted.
