# OpenAI Decisions and Agents APIs for ApplyPilot

Official documentation checked on **7 October 2026**. The [optional Decisions observer](DECISIONS_OBSERVER.md) now implements the first evaluation phase with workspace opt-in, synthetic benchmarks and browser baseline comparisons. Managed Agents orchestration remains an architecture proposal. Provider accuracy/latency on real applications has not been established by fixture tests.

## Conclusion

Both APIs can help. Keep ApplyPilot's workspace-owned browser and final approval checkpoint, evaluate **Decisions for fast page/action classification**, and consider **Agents API function tools for more capable multi-page orchestration**. The first visible-browser release provides the execution and takeover layer those integrations need.

## Decisions API

`POST /v1/decisions` is now in public beta. Currently it supports `gpt-6-luna` only. It evaluates text, images, or both, and answers supplied questions as:

- **Predicate:** estimated probability that a condition is true.
- **Choice:** one of a supplied finite set, with probabilities and confidence.
- **Score:** probability-weighted average of ordered rubric level indices (starting at zero).

OpenAI reports roughly **10× faster than Responses** for this constrained workload. This is a provider claim, not a measured ApplyPilot result.

### Useful application checks

| Question | Type | How ApplyPilot would use it |
| --- | --- | --- |
| What kind of page is this: listing, login, application, review, confirmation, unknown? | Choice | Route to filling, takeover or review |
| Does this field correspond to a supplied verified answer? | Choice over answer IDs plus no-match | Propose an existing answer; preserve field/value validation |
| Which identified control is a Next/Continue action? | Choice over inspected controls plus no-safe-action | Select a bounded candidate; runtime revalidates before clicking |
| Is visible required information missing? | Predicate | Supplement DOM validation and trigger takeover |
| Does this page show an actual submission confirmation rather than instructions or an older receipt? | Predicate | Supplement deterministic confirmation checks; retain text/reference/screenshot evidence |

Decisions cannot generate a supporting statement, extract an arbitrary reference string, emit a custom JSON plan, or execute browser actions. Keep the normal forms model/structured-output path for generation and extraction. A model confidence estimate cannot grant submission permission or establish submission by itself.

### Cost and integration

Current standard Decisions pricing is **$0.10 per million input tokens**, with no output, cache-read or cache-write token charges. Regional and long-context multipliers apply. For illustration, 5,000 input tokens cost $0.0005; 1,000 such calls cost $0.50. Calls are not completed applications, and image token usage is additional input usage.

The Python SDK examples require OpenAI SDK 3.26.0 or later; direct HTTP integration is also possible using ApplyPilot's existing `httpx` dependency. Images must be inline base64 data URLs; hosted image URLs and file IDs are not accepted by this endpoint.

## Agents API

The managed Agents API provides durable sessions, streamed progress, steering, context compaction, recovery and function/MCP tools. The beta SDK namespace is `client.beta.agents`; raw requests require `OpenAI-Beta: agents=v1`. Application keys need `api.agents.read`, `api.agents.write`, and `api.responses.write`.

### Native hosted browser: newer capability

The **Agents API now supports native computer use** with `agent.tools: [{type: "computer_use", include_screenshots: true}]` and an OpenAI-hosted desktop environment. OpenAI runs the browser, and tool activity can return screenshot images and progress titles.

It has structured handoffs for:

- Approval before access to each new website origin.
- Sign-in method selection, email/password entry and verification-code entry outside the conversation.

The documented sign-in flow does not support passkeys or QR-code login. Browser activity screenshots are not the same as a documented general mouse/keyboard takeover API.

**Important for ApplyPilot:** OpenAI explicitly states that origin approval does not enforce confirmation before individual consequential actions. A request-confirmation function depends on the agent calling it. Its docs recommend a browser runtime you control when the application must guarantee that checkpoint. The hosted computer-use configuration also does not attach to ApplyPilot's existing Playwright browser.

### Best fit: managed reasoning with our controlled browser tools

An Agents session with application-owned function tools can orchestrate the browser that the candidate already watches. Proposed tools:

1. `inspect_page`: inspected controls, fields and optionally a current screenshot.
2. `fill_verified_field`: a current field identifier and an approved profile/answer/document value key.
3. `upload_approved_document`: one of the session's immutable packet documents.
4. `advance_page`: a currently inspected non-final navigation control.
5. `request_human_help`: login, security check, missing information or an unsupported widget.
6. `request_final_review`: pause at the candidate-facing checkpoint.

Final submission stays behind the existing candidate command and page-bound, single-use approval; it is not an unrestricted agent tool. Tool outcomes need durable deduplication by session/turn/call ID, especially when transport recovery follows a side effect. OpenAI's Functions guide documents this recovery pattern.

Function tools can run in the ApplyPilot server without giving the agent shell access. A separate self-hosted executor is available if a sandbox is needed later, but is not necessary just to expose browser functions.

Agents usage is billed at the chosen model's API rates, with standard tool/container charges where applicable. Example docs use Astra; that does not establish Luna's support for every managed-browser capability. Select and verify model support before connecting a production adapter.

## Proposed next evaluation

1. Run Decisions in **observation-only mode** on saved synthetic portal DOM/screenshots. Measure page-state accuracy, false confirmation positives, latency and reported input usage. Existing browser behaviour remains the comparison baseline.
2. Add an optional bounded page classifier only if it improves those results; fall back to DOM/manual handoff on errors, refusal, unknown choice or low calibrated confidence.
3. Evaluate Agents API function orchestration against difficult multi-page fixtures using the same browser, documents and approval controls. Compare completion rate and cost against the current forms planner.
4. Use hosted native computer use as a separate experiment for public research/read-only tasks, where its managed environment is a natural fit.

## Sources

- [Decisions guide and pricing](https://developers.openai.com/api/docs/guides/decisions/)
- [Agents API overview](https://developers.openai.com/api/docs/guides/agents-api/overview)
- [Agents API computer use, origin approvals and sign-in](https://developers.openai.com/api/docs/guides/agents-api/tools/computer-use)
- [Agents API function tools and recovery](https://developers.openai.com/api/docs/guides/agents-api/tools/functions)
- [Self-hosted executor](https://developers.openai.com/api/docs/guides/agents-api/environments/self-hosted)
- [Responses computer use with a runtime you control](https://developers.openai.com/api/docs/guides/tools-computer-use)
- [DevDay announcement, 29 September 2026](https://openai.com/index/devday-2026-recap/)
