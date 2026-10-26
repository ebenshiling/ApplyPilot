# NZ integration — verification and release decision

> **Historical Phase 7 decision.** The user subsequently authorised integration into
> the current Docker app and release of live application workflows. The current
> release is documented in [NZ workspace guide](NZ_WORKSPACE.md) and
> `ops/implementations/20261018-nz-live-integration/`. The historical default-off and
> blanket real-execution holds below no longer describe the integrated deployment.

## Decision

**Fixture implementation and offline rehearsal are complete through Phase 7.
Production release is not approved. NZ remains default-off and all real ATS
automation remains contained/manual-only.**

The user authorised continuation through the approved phases and explicitly chose
“Continue with fixtures” for missing candidate inputs. This decision did not supply
real candidate facts, source rights, live adapter evidence or deployment approval.

## Delivered phase records

| Phase | Implemented/verified scope | Handoff |
|---|---|---|
| 1 | Previously committed country foundation and live containment | [Phase 1](NZ_PHASE1_HANDOFF.md) |
| 2 | NZ campaigns, permitted-source readiness, observations/salary/URLs, manual capture | [Phase 2](NZ_PHASE2_HANDOFF.md) |
| 3 | Legal employer identity, immutable manual evidence and recruitment labels | [Phase 3](NZ_PHASE3_HANDOFF.md) |
| 4 | Shared canonical claims, factual final-byte gates and country master sources | [Phase 4](NZ_PHASE4_HANDOFF.md) |
| 5 | Gated preparation, exact document review, tracking/answers, bounded usage and portability | [Phase 5](NZ_PHASE5_HANDOFF.md) |
| 6 | Fresh actor/session/boot-bound approval, single consume, owned synthetic executor, uncertain recovery | [Phase 6](NZ_PHASE6_HANDOFF.md) |
| 7 | Double migration, end-to-end manual country journey, rollback and release assessment | This document |

Phase records are under `ops/implementations/20261016-nz-phase2/` through
`20261016-nz-phase7/`. They identify verified fixture scope and external gates;
“fixture complete” must not be presented as real candidate/source/ATS acceptance.

## Final verification

`python scripts/agent_verify.py` passed on the final source state:

- Python compilation: **pass**.
- Pytest: **802 passed, 59 skipped**, 335.48 seconds.
- Strict AI regression: **7/7**.
- Strict formal dataset: **13/13**.
- `git diff --check`: **pass**.
- Node syntax checks for new preparation/review assets: **pass**.

Environment: existing Windows Python 3.11 `.ci311` dependencies, explicit worktree
`PYTHONPATH`, isolated temporary workspace/user roots and final pytest base directory,
provider credentials cleared, existing local CA, no installs/upgrades. Country-only
Chromium tests were enabled and blocked external browser requests. Legacy employer
integration suites remained disabled; 59 skips are not live acceptance evidence.

The rehearsal proves: migration twice preserves legacy NULL country, original URLs,
statuses/config/artifact bytes; three fixture masters route without UK fallback;
unknown accreditation remains unknown; candidate-owned text can pass factual/PDF
checks and human review without AI/source fetch; manual outcomes remain separate;
wrong-country changes fail closed; disabled NZ retains history; replay/session/
expiry/field/file/crash/restore negatives cannot actuate a real target.

Additional final hardening rejects new numbers/durations and employer-specific use
of a global tool from wording approval alone. This is still a bounded authority on
explicitly reviewed candidate evidence, not independent proof of real-world truth.

## Mandatory outstanding release gates

| Gate | Actual result | Owner / next evidence |
|---|---|---|
| Actual candidate masters and canonical evidence | **Deferred by explicit user choice** | Candidate/product owner supplies three actual files and evidence, reconciles claims and country disclosures |
| Permitted live NZ discovery source set | **Not established** | Operator documents retrieval/storage/AI rights, terms/review bounds and actual read-only NZ sample acceptance |
| NZ JobSpy activation | **Blocked technically and by unestablished rights** | Bounded per-request provider transport plus permitted live validation; geography support alone is insufficient |
| INZ automated connector | **Not available/activated** | Manual official checking remains the complete V1 workflow; no hidden/public API is invented |
| Managed real ATS execution | **No released adapter** | Named permission, owned network/DOM mediation and site-specific adversarial acceptance; every current real target uses manual handoff |
| Production/staging migration and deployment | **Not performed** | Approved operational rehearsal on authorised backups/targets, compatible rollback and explicit owner decision |
| Product-owner release approval | **Not given** | Owner reviews actual candidate/source/adapter/FMEA evidence and explicitly approves a named cohort |

No source entitlement, employer accreditation, sponsorship, visa/job-check approval,
actual provider pricing or universal arbitrary-site safety is inferred from fixture
data. Missing rights/availability/salary evidence remains visible and unassessed.

## Repository and operational boundary

All changes remain uncommitted in the isolated feature worktree:

```text
Branch: feat/nz-phase2-discovery-metadata
Worktree: C:\Users\swazy\AppData\Local\Temp\opencode\applypilot-nz-phase2
Base: e357acd0bb939fe6c1dea02cee70c13538ca6025
```

The original dirty `main` checkout was retained. Final Git status was inspected and
six critical original files (README, database, dashboard server, pipeline, tailor,
workspace transfer) were blob-checked against authorised baseline checkpoint
`18f888806ca30665f6b0aff008b341681538a9ee`; all matched. No commit, push, PR,
deployment, real workspace startup/migration, provider test or employer submission
was performed in this continuation.

## Rollback / safe continuation

Keep `nz_workflow_enabled` false or set `APPLYPILOT_NZ_DISABLED=1`. Stop queued new
operations, revoke outstanding authority, and reconcile any consumed interrupted
attempt as uncertain. Preserve additive rows, original/source/submitted artifacts,
observations and usage. Restore backups into fresh workspaces; populated history
cannot be overwritten. Imports cannot revive current approvals or source validations.

Use a compatible contained build, not an old binary that ignores country/factual/
approval fields. Real adapters stay manual until their own gates pass. Final enablement
requires the named missing evidence and an owner release decision.
