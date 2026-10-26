# NZ Phase 2 — discovery and vacancy observations

Implemented on `feat/nz-phase2-discovery-metadata` in an isolated worktree based
on Phase 1 commit `e357acd0bb939fe6c1dea02cee70c13538ca6025`. The original dirty
checkout and real candidate databases/configuration were not used for execution.

## Available, when explicitly enabled

- NZ country discovery workspace, a dedicated campaign preset with the three
  career groups, Auckland/Wellington/Christchurch and optional NZ cities/remote.
- Scoped campaign additions/edits preserve other root/campaign bytes, including
  LF/CRLF and comments. NZ effective defaults do not inherit UK source, salary,
  title-filter, proxy or CV-routing settings. Empty source selections mean none.
- Source access metadata, exact retrieval/application hosts, identity query keys,
  documented currency conventions and server-owned, fingerprinted sample checks.
- Bounded direct-employer HTTP/JSON-LD and Workday CXS retrieval through existing
  adapter entry points. Workday retrieval is a bounded sample, with partial
  coverage explicitly reported when further pages exist.
- Nullable additive `jobs.vacancy_metadata`, conservative exact/provider-ID
  deduplication, original listing/application URLs, content hashes, distinct dates,
  advertised-country evidence, arrangement and salary provenance. Similar titles
  produce warnings rather than fuzzy merges. Existing application state remains.
- Manual employer URL/permitted job-text capture, or reference-only capture for
  restricted boards. Capture never resolves/fetches a URL or calls a model.
- Destination checks distinguish reachability, unknown/error, closed-message,
  HTTP 404/410 and source expiry; check timestamps are distinct from posting and
  discovery dates. Original signed application links survive storage/display.
- NZ salary parsing keeps currency/rate/basis/package/KiwiSaver/hour evidence.
  Unknowns remain discoverable. No implicit FX or default 2080-hour conversion.

## Capability and access limits

NZ remains **default off**. Enable `nz_workflow_enabled: true` deliberately in a
development workspace; `APPLYPILOT_NZ_DISABLED=1` still denies it. The source tree
does not enable any real source or install a campaign into a user workspace.

No live NZ source permission/reliability evidence was obtained. Source permissions
are operator attestations, not rights granted by this application. Automatic
coverage is therefore **blocked/manual mode** by default. A source must have a
current permission review and a matching server-recorded NZ sample with a title,
valid URL, NZ location and substantive description before execution.

NZ JobSpy execution is additionally blocked because the installed adapter offers
no enforceable per-HTTP-request host/rate hook. JobSpy observation normalization
is fixture-tested but is not advertised as a live NZ integration. Google Jobs,
ZipRecruiter, SEEK and Trade Me are not introduced as NZ adapters. There is no INZ
automated connector. Workday CXS permission does not imply permission to fetch
its destination HTML. Unsupported/manual destinations remain manual checks.

Only `[discover]` is allowed for an explicit NZ campaign. NZ enrichment through
the old unrestricted browser/AI route, scoring, writing, publishing and managed
applications remain contained. Phase 3 recruitment/accreditation conclusions are
not implemented here; retained literal work-rights quotes are `unassessed`.

## Interfaces

Authenticated existing same-origin/CSRF route adapters expose:

- GET `/api/center/nz/discovery?country_code=NZ`
- POST `/api/center/nz/campaign/save` (expected root config revision)
- POST `/api/center/nz/source/save`, `/source/validate`
- POST `/api/center/nz/capture`, `/listing/check`, `/start`

Mutations require `country_code: NZ`; UK IDs cannot be checked through NZ routes.
The UI sends explicit country context and discards stale country-view responses.
Existing UK routes remain available. CLI adds optional `--campaign`:

```text
applypilot run discover --country NZ --campaign nz-job-search --dry-run
```

The campaign must already be saved. Removing `--dry-run` only performs discovery;
it grants no document-generation or application authority.

## Verification

Windows Python 3.11 from the existing `.ci311` environment, source imports pointed
at this worktree, isolated temporary workspace/user roots, provider credentials
cleared, existing local CA file, no dependency installation or live providers.

`python scripts/agent_verify.py` passed:

- compileall: pass
- pytest: **668 passed, 59 skipped** in 259.42 seconds
- strict AI regression: **7/7**
- strict formal dataset: **13/13**

`APPLYPILOT_RUN_COUNTRY_UI=1` exercised local-only browser flows; external browser
requests were blocked. Legacy employer-browser suites remained disabled with
`APPLYPILOT_RUN_PLAYWRIGHT=0`; their skips are not live acceptance evidence.
`node --check src/applypilot/web/nz-discovery.js` and `git diff --check` passed.
An earlier full run hit the tool's 120-second timeout; the completed run used a
longer limit. Earlier focused failures (fixture binding, base-URL catalog scoping,
and a transient UI assertion) were corrected before the passing full run.

Rollback: deny NZ or disable an individual source. Keep additive metadata and
observations, existing URLs and application histories; do not downgrade to a
binary that ignores Phase 1 safety/country guards or destructively restore data.
