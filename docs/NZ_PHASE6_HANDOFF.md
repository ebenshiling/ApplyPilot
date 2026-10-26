# Phase 6 — fresh submit authority and controlled fixture executor

**No real ATS adapter is released.** Every real target remains manual handoff;
legacy native/Claude/MCP/CDP/LLM/CLI/scheduled live actuators stay contained.
Synthetic approval tests do not establish permission or universal arbitrary-site
network safety. Candidate/live-source/release inputs remain deferred.

## Implemented authority

- Existing `cc_browser_sessions` is extended additively with country, review JSON,
  approval JSON/revision and server-instance binding. Positional writers and
  browser-history copy queries now use explicit columns.
- Review binds persisted job/target, exact current TXT/PDF packet, actor-bound
  final-document review, country/rights epoch, required scoped answers, complete
  virtual field state and consent. Missing/unverified fields cannot be approved.
- Approval is an explicit authenticated candidate action, actor/session/server-
  instance bound, random, single-use and valid for at most five minutes. Only a
  SHA-256 token hash is retained. Tokens do not enter prompts, events or queues.
- A durable SQLite compare-and-swap consumes approval and records submitting intent
  before owned actuation. Concurrent consumers cannot actuate twice. Changed
  documents, fields, identity, rights, target or country invalidate the old review.
- The sole executable adapter is an owned **non-network in-process synthetic**
  adapter for non-routable `*.applypilot.invalid` targets, installed only by a
  trusted test scope. It has no HTTP/browser/arbitrary callback implementation.
  Model tools are inspection of field references and non-mutating proposals;
  pointer/keyboard/frame/popup/JavaScript/CDP/MCP/network actions are unavailable.
- No verified receipt, interruption after consumption or crash after dispatch
  becomes `submission_uncertain`, not prepared/safe-to-retry. Recovery preserves
  consumed intent and never automatically replays it. Old server/session/review
  state cannot consume fresh authority. Imports clear approval authority.
- Real targets return explicit manual handoff with original URLs. Workspace
  document/application-support controls explain the boundary. Opening a link never
  records submission; independent applications use explicit checked manual tracking.

Authenticated `/api/center/submit/{review,approve,execute,cancel}` and equivalent NZ
namespace routes reuse ownership, country and session validation. Mutating authority
requires workspace CSRF context. Client engine/submit flags do not grant authority.
There is no production configuration flag enabling the synthetic scope or a real
adapter, and no release of unmediated live tools.

## Verification

`python scripts/agent_verify.py`: compile pass, **799 passed / 59 skipped** in
333.01 seconds, strict AI **7/7**, formal dataset **13/13**. Final inspection-value
redaction delta was verified with its targeted test (**1 passed**).

The controlled suite has 28 synthetic cases: zero transactions before approval,
single consume/replay, actor/session/boot mismatch, expiry, edited files/fields,
missing identity/consent/unreviewed answers, unknown action rejection, concurrent
consumers, lost receipt and before/after-dispatch crashes. All real-target tests
remain manual and all Phase 1 actuator negatives remain enabled. No employer or
provider request, application form or production dataset was used.

An initial schema regression exposed a six-column browser-history `SELECT *`
copy; it was corrected to explicit columns before the passing full verification.

## Outstanding release coverage

P6's live adapter-dependent network/DOM mediation and actual permitted ATS semantics
remain **unverified/unreleased**. They cannot be inferred from the synthetic counter
proof. Named live adapters need separate permission, network-boundary implementation
and adversarial acceptance before activation. Current honest operating mode is
safe preparation/review/tracking plus external manual application handoff.

Rollback revokes pending authority, keeps interrupted consumed attempts uncertain,
retains evidence/receipts/history and uses manual handoff. Never restore autonomous
raw-tool execution, old approval flags, imported tokens or automatic retry.
