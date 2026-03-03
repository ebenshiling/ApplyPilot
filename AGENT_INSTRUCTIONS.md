ApplyPilot Agent Instructions

These instructions are for automated coding agents working in this repo.

Non-Negotiables
- Do not break existing CLI workflows (`applypilot init`, `applypilot run`, `applypilot apply`, `applypilot dashboard-serve`).
- Prefer additive, backward-compatible changes.
- Treat the DB schema and `~/.applypilot*` user data as production data: no destructive migrations.

Before You Change Anything
1) Read the current system state
- `ops/repo_status.json`
- `ops/implementations/*/status.json` (the active one)
- `README.md` (user-facing expectations)

2) Create/Update an implementation record
- Create a new folder under `ops/implementations/<id>/` if the work is new.
- Update `ops/repo_status.json` to point to the active implementation.
- Every implementation must include `plan.json`, `status.json`, and `fmea.json`.

3) FMEA safety pass (required)
- Follow `docs/FMEA_POLICY.md`.
- Record the analysis in `ops/implementations/<id>/fmea.json`.

Implementation Workflow
- Write the plan first: `ops/implementations/<id>/plan.json`
- Capture pre-state: `python scripts/capture_repo_state.py --out ops/implementations/<id>/pre_state.json`
- Implement in small steps; keep diffs reviewable.
- Run verification: `python scripts/agent_verify.py`
- Capture post-state: `python scripts/capture_repo_state.py --out ops/implementations/<id>/post_state.json`
- Update `ops/implementations/<id>/status.json` and `ops/repo_status.json`.

Definition of Done
- `python scripts/agent_verify.py` passes.
- New functionality has tests (unit/smoke) or an explicit reason in the implementation status file.
- FMEA mitigations are implemented or tracked with an owner + follow-up phase.
