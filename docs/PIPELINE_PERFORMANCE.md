# Pipeline and workspace responsiveness

The deployed Docker app was profiled using authenticated, read-only requests and
Chromium against the existing 98-record account. No source/provider requests or
employer applications were started by the checks.

## Measured improvement

Two sequential request samples before/after the final deployment:

| Endpoint | Before | After |
|---|---:|---:|
| Main workspace | 0.67–0.75 s | 0.047–0.064 s |
| Pipeline full snapshot | 9.24–9.89 s | 0.47–0.58 s |
| Pipeline progress polling | Rebuilt full snapshot | 0.23 s |
| Run status | 0.25 s | 0.009 s |
| NZ discovery | 0.68–0.69 s | 0.058–0.059 s |
| NZ preparation | 0.69–0.88 s | 0.040–0.041 s |

Three additional Chromium navigations displayed Pipeline controls in
**0.82–0.85 seconds**. Their full-snapshot fetches took 0.36–0.38 seconds; preview
and progress requests completed in 0.82–0.92 seconds while running concurrently.
All observed API responses succeeded, and no browser long tasks or JavaScript
errors were recorded. These are local measurements, not external discovery/model
execution timings or a guarantee under every workload.

## Changes

- Display calculations reuse configuration, profile, AI environment, routing and
  fingerprint reads within a bounded workspace/country-local scope. Dictionaries
  are defensively copied. The next request reads current inputs again.
- Pipeline refreshes use `/api/center/pipeline/progress`, retaining live queue,
  problem and run information without rebuilding discovery plans or packet previews.
- Insights has its own read endpoint instead of loading the complete Pipeline state.
- Packet previews are calculated only for the 30 route samples actually returned.
- Preview requests are debounced/cancelled, and obsolete page/country responses
  cannot repaint current controls. Polling preserves unsaved settings.
- Workspace database readiness is checked by file identity and SQLite schema
  version. Normal HTTP reads, event streams and background ticks reuse initialized
  connections instead of rerunning migrations and legacy-import writes.
- First connections and WAL setup are serialized to handle concurrent cold starts.
  Explicit `init_db` calls still perform the original additive migrations.

No job rows, work-rights facts, document approvals or submission authority are
persistently cached. Execution and approval validations retain fresh reads.

## Verification and operations

- Final `python scripts/agent_verify.py`: **820 passed, 62 skipped**, compile pass,
  strict AI **7/7**, formal dataset **13/13**.
- Tests cover fresh inputs between requests, mutable-result isolation, workspace/
  country/thread isolation, queue equivalence, schema/file replacement, repeated
  concurrent cold starts, lightweight polling and unsaved control preservation.
- Authenticated deployed browser check: one catalog request plus lightweight polls,
  zero JavaScript errors, NZ switching/polling retained and all 98 UK records preserved.
- Records: `ops/implementations/20261019-pipeline-performance/`, including original/
  final API profiles, Chromium timings, deployment checks and pre/post state.
- Rollback image: `applypilot-applypilot:pipeline-pre-performance-20261019`.
- Source-only rollback archive: `.release-backups/20261019-pipeline-performance/deployed-source.tar.gz`.
  Current data volumes should be preserved; this change introduces no schema migration.

After updating, hard-refresh the workspace with **Ctrl+Shift+R** to load the updated
polling code in already-open browser tabs.
