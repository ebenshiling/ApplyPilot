# Assistant recovery and semi-assisted applications

## If Explain fit appears stuck

1. Open a job record and select **Explain fit**. The answer area now shows progress, inline errors, **Retry answer**, and **Stop waiting**. Closing the record or switching jobs prevents an old answer from appearing against the new job.
2. Open **Settings → AI preferences**. Provider cards show configuration status only. Select **Manage connection** for a provider to make a private change; existing keys are never displayed. A differing workspace/server connection is explained inside that dialog.
3. **Explain fit uses the scoring route**, not the general assistant route. Check the scoring provider/model in the task list.
4. In **Manage connection**, choose **Set a workspace connection** to enter a new key, or **Use server connection** to deliberately inherit the service key. Save the connection explicitly. Server inheritance removes the encrypted override and ignores the workspace `.env` key for this workspace; it preserves the `.env` file. The private input is created only when you choose to enter a new key, and is cleared and removed when the dialog closes. Connection saves are separate from model, allowance and rate edits.
5. Retry Explain fit. Authentication/payment/model failures give actionable messages. A valid key alone does not prove the linked project has generation access, quota, or billing credit.

Interactive assistant inference makes one transport attempt, with a 45-second HTTP phase timeout (connect at most 10 seconds). The browser stops waiting after 60 seconds. Pipeline clients retain their existing retry/time-out settings. Stopping browser waiting does not guarantee cancellation of an already-running provider request; it can still finish, populate the evidence cache, and count toward usage. Failed attempts also count toward the daily request budget. New usage reservations record pending/completed/failed state; historical rows retain unknown state rather than inventing outcomes. Empty answers are never cached or displayed as success.

## Visible semi-assisted applications

Use **Apply with agent** in the workspace application record for the new shared-browser workflow. It provides authenticated live viewing, login/security-check takeover, reviewed document snapshots, bounded multi-page navigation, page-bound final approval, and saved confirmation/reference evidence. See [Apply with agent](ASSISTED_BROWSER.md) for steps and setup.

The limitations below describe the older **CLI application engines → AI-assisted browser → Prepare without submitting** workflow. Its separate browser is not the new integrated session.

## What the legacy semi-assisted engine does

The **AI-assisted browser** engine uses Playwright and DOM field extraction, rather than a provider-managed agent or native screenshot-computer-use tool. It can fill common contact fields, upload an available CV PDF, and ask the configured **forms** model for field values using profile data and saved verified screening answers. The planner sees up to 40 fields and 30 verified answers.

From a job record, choose **AI-assisted browser → Prepare without submitting**. This mode does not enter the engine's final submission flow. It is a best-effort first-page fill, not a guarantee that every multi-page form is completed. LLM mapping/upload failures can leave fields incomplete, so review is necessary. **Prepared** is not submission confirmation.

### Legacy Docker handoff limitation

The Docker deployment installs Chromium, but the legacy CLI engine has no desktop display or host-desktop bridge. A Chrome window inside that container cannot appear on your Windows desktop, and its filled session is not the same session opened by **Open application** in your own browser. That legacy path does **not** provide a complete visible review/submit handoff and may fail to launch the headful browser. **Apply with agent** uses its own authenticated screenshot/input view and headless Chromium to resolve this handoff.

You can also prepare/review/download the application packet, use **Open application**, enter or paste your verified answers in your own browser, and submit yourself. This remains a separate manual workflow.

## Official agent APIs and model comparison

Prices checked on **6 October 2026**, in **USD per million tokens**, standard uncached input/output. These are model costs, not prices per completed application. No paid provider has been changed by this investigation.

| Provider/model | Input | Output | Example: 5,000 input + 1,000 output tokens |
| --- | ---: | ---: | ---: |
| OpenAI GPT-6 Luna | $0.10 | $0.50 | $0.00100 |
| OpenAI GPT-4o mini | $0.15 | $0.60 | $0.00135 |
| OpenAI GPT-4.1 mini | $0.40 | $1.60 | $0.00360 |
| DeepSeek Flash, off-peak | $0.15 | $0.60 | $0.00135 |
| DeepSeek Flash, peak | $0.30 | $1.20 | $0.00270 |
| DeepSeek V4 Pro, off-peak | $0.66 | $1.98 | $0.00528 |
| DeepSeek V4 Pro, peak | $1.32 | $3.96 | $0.01056 |
| OpenAI GPT-6.1 Sol | $2.00 | $10.00 | $0.02000 |
| Claude Sonnet 5.5 | $2.00 | $10.00 | $0.02000 |
| OpenAI GPT-6 Astra | $10.00 | $50.00 | $0.10000 |

For 1,000 requests of that exact illustrative size, multiply the last column by 1,000: Luna $1; DeepSeek Flash $1.35–$2.70; DeepSeek Pro $5.28–$10.56. Actual application runs may require multiple calls, image inputs, more history, reasoning tokens, and retries. Cache hits can reduce input charges; token counts differ by tokenizer. These are not quality benchmarks. OpenAI prices above use short contexts (at most 272K input tokens).

DeepSeek peak hours are **01:00–04:00 and 06:00–10:00 UTC, Monday–Friday**, excluding Chinese public holidays. Other times are off-peak. Current API IDs are `deepseek-flash` (DeepSeek-V4.1-Flash) and `deepseek-v4-pro` (DeepSeek-V4-Pro-0813). Flash cached input is $0.003/$0.006 off-peak/peak; Pro cached input is $0.022/$0.044. Legacy Flash IDs are aliases to the current Flash model, not separate cheaper models.

### Integration choices

- **Existing Playwright/DOM planner:** the simplest low-cost path. The effective forms route in this deployment is already GPT-6 Luna with reasoning disabled. Retain it as the starting point and measure field accuracy/latency before moving to a more expensive model.
- **DeepSeek:** official OpenAI-compatible Chat Completions, Responses, JSON output and function calling are available. Flash also accepts images; V4 Pro currently does not. A model can propose browser actions through application-owned tools, but ApplyPilot still must execute them and maintain the browser. The current settings UI has no first-class DeepSeek provider/key selector; a provider adapter and encrypted UI credential field are needed for a clean integration. The generic OpenAI-compatible client is groundwork, not verified DeepSeek integration. Non-thinking form mapping should explicitly use `thinking: {type: "disabled"}`: DeepSeek otherwise defaults to high-effort thinking, which affects latency and output cost. Thinking tool loops also have provider-specific history requirements.
- **OpenAI Responses computer use:** the official native example uses GPT-6.1 Sol and the `computer` tool. Your app executes actions and returns screenshots. GPT-6 Astra's documentation recommends a code-execution approach using Playwright/PyAutoGUI. Neither is implemented in ApplyPilot's current form engine. Do not assume a cheap text model supports the native computer tool.
- **OpenAI managed Agents API:** a separate beta managed Codex harness with durable sessions, events and hosted/self-hosted environments. Its newer native computer-use tool runs an OpenAI-hosted browser, returns optional screenshots, and supports origin-access and structured sign-in handoffs. It does not attach to the candidate's existing browser, and origin approval does not enforce approval before individual actions such as submission. For ApplyPilot's final checkpoint, use a runtime we control with application-owned function tools. Model tokens and built-in tools use standard prices; hosted containers currently list $0.03 per 20-minute 1-GB container session, with eligible minute billing and a five-minute minimum. See the [updated Decisions/Agents assessment](OPENAI_APPLICATION_AGENT_APIS.md).
- **OpenAI Decisions API:** now in public beta, with Luna-only finite-choice, predicate and rubric-score answers from text/images. Standard pricing is $0.10 per million input tokens and no output-token charge. It is a promising page-state/action classifier, while document generation and browser execution remain separate responsibilities.
- **Claude computer/browser use:** application-executed tool loops, with token and screenshot costs; current computer toolset definition alone adds about 4,500 input tokens. Claude Managed Agents is a distinct option, with **$0.08 per running session-hour** plus model/tool charges. Sonnet 5.5 is cheaper than premium Astra/Fable tokens, but substantially more expensive per token than Luna or DeepSeek Flash.
- **Gemini:** its official pricing/computer-use pages could not be retrieved during this investigation, so no current Google price is quoted. Existing Gemini scoring routes remain supported.

### Recommendation

Keep **Luna for forms** while fixing browser handoff. Evaluate **DeepSeek Flash** as an alternative using the same saved-profile/verified-answer fixtures; consider **V4 Pro** for harder text reasoning only if it improves measured results enough to justify the extra cost. Both are inexpensive compared with premium models, but neither beats the current Luna uncached token rates. Evaluate screenshot agents only for forms the DOM approach cannot handle. For a semi-assisted workflow, preserve candidate review and manual final submission.

## Official sources

- [DeepSeek models and pricing](https://api-docs.deepseek.com/quick_start/pricing)
- [DeepSeek API setup](https://api-docs.deepseek.com/)
- [DeepSeek tool calls](https://api-docs.deepseek.com/guides/tool_calls)
- [DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode)
- [DeepSeek vision](https://api-docs.deepseek.com/guides/vision)
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)
- [OpenAI computer use](https://developers.openai.com/api/docs/guides/tools-computer-use)
- [OpenAI Agents API overview and pricing](https://developers.openai.com/api/docs/guides/agents-api/overview)
- [OpenAI Agents API quickstart](https://developers.openai.com/api/docs/guides/agents-api/quickstart)
- [Claude API and managed-agent pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Claude computer use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)
