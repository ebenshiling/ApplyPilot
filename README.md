<!-- logo here -->

# ApplyPilot

## United Kingdom and New Zealand workspaces

Open **http://localhost:8765/workspace**, then use **Country workspace → New Zealand**
above the page heading. The Docker deployment enables NZ alongside the UK workspace.
NZ includes campaigns, vacancy capture, employer/recruitment evidence, CV sources,
document preparation, applications and outcome tracking.

**Start with a job title and NZ location → Find jobs.** Open a board's search results,
save a vacancy's URL/title, then prepare and apply using the numbered steps. Employer
details are optional when saving and are not needed to search. Automated direct/Workday
imports and source configuration are under **Advanced**.

**Apply with agent** is available for UK and NZ records. It opens the employer form,
uses your reviewed packet, supports manual takeover and pauses for final submission
approval. The existing CLI engines and schedules are also available. Development-only
blanket execution locks have been removed; factual-document, country ownership,
source-readiness and duplicate/uncertain-submission checks remain operational.

See [NZ workspace guide](docs/NZ_WORKSPACE.md) for setup, release verification,
deployment controls and rollback. Phase 1–7 handoffs describe the earlier isolated
development stages; their blanket release holds are superseded by this integration.

The [Pipeline performance notes](docs/PIPELINE_PERFORMANCE.md) cover the lighter
polling and request-local read optimizations. Hard-refresh an already-open tab after updating.

**Pipeline → Review & run** previews edited roles, locations, sources and limits.
Discovery defaults to 50 matching new jobs and processes that search's new jobs,
excluding the saved backlog. See [bounded discovery scope](docs/DISCOVERY_SCOPE.md).

**Jobs → Select all shown / select cards → Delete selected** removes multiple job
records in one confirmed operation, with progress and a result summary. Individual
and bulk lifecycle actions return to Jobs. See [job lifecycle](docs/JOB_LIFECYCLE.md).

**Applied to 1,000 jobs in 2 days. Fully autonomous. Open source.**

[![PyPI version](https://img.shields.io/pypi/v/applypilot?color=blue)](https://pypi.org/project/applypilot/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/Pickle-Pixel/ApplyPilot?style=social)](https://github.com/Pickle-Pixel/ApplyPilot)




https://github.com/user-attachments/assets/7ee3417f-43d4-4245-9952-35df1e77f2df


---

## What It Does

### Application command center

The served dashboard opens the redesigned application workspace at **`/workspace`**. The root URL and old dashboard URLs redirect here; classic workspace rendering is disabled. It provides CV Studio with editable qualifications and professional facts, versioned CV regeneration and PDF previews, application timelines, separate hiring outcomes, block/archive controls, reminders, scheduling, AI assistance, and optional Gmail/Outlook evidence connectors.

See [Command center guide](docs/COMMAND_CENTER.md) for usage and provider configuration, and [Upgrade roadmap](docs/UPGRADE_PLAN.md) for the phased plan and remaining refinements.

The [opportunity activity and browser guide](docs/OPPORTUNITY_INSIGHTS_AND_BROWSER.md) covers the Insights ledger, complete CSV/JSON analysis downloads including deleted history, manual activity recording, streamed browser controls and evidence-backed answers for selected application fields.

See [Pipeline audit and configuration guide](docs/PIPELINE_AUDIT.md) for multi-role searches, automatic resume routing, custom-source registration, and prioritized pipeline improvements.

The native [Pipeline workspace](docs/PIPELINE_WORKSPACE.md) adds campaigns, effective run previews, lossless search editing, resume decisions, workspace-owned source catalogs, structured recovery, application packets, and measured usage/outcome insights.

See [global locations and visual Insights](docs/GLOBAL_LOCATIONS_AND_VISUAL_INSIGHTS.md) for country/state/city searches, per-location board routing, explicit geographic filtering and interactive analytical charts. [Direct-source recovery](docs/DIRECT_SOURCE_RECOVERY.md) covers search templates and actionable extraction-preview errors.

The [professional studios guide](docs/STUDIOS.md) covers multi-CV uploads, editable source revisions, native Profile/Portfolio tools, UI-managed encrypted AI settings, and versioned Cover/Statement Studios with previews, validation and downloads.

See the [NHS application walkthrough](docs/NHS_APPLICATION_WORKFLOW.md) for official-source supporting-evidence guidance, reviewed criteria/examples, word versus character limits, and how to use both writing Studios. Workspace navigation can be collapsed with the menu button; its preference is retained in the browser.

See [Discovery/provider recovery](docs/DISCOVERY_TROUBLESHOOTING.md) for missing JobSpy dependencies, provider billing/access responses, extraction fallbacks, and UI retry actions.

Use **Apply with agent** in a workspace job record for a [visible semi-assisted application browser](docs/ASSISTED_BROWSER.md): watch the shared session, take over for login or missing answers, approve final submission, and save confirmation evidence. It works with the existing Docker Chromium installation.

An optional [Decisions observer trial](docs/DECISIONS_OBSERVER.md) in **Settings → AI preferences** compares Luna page classifications with the browser baseline, runs a nine-case synthetic benchmark, and reports latency and usage while preserving the existing application controls.

Use **Close / delete record** to record closed adverts, unmet requirements, preparation failures or hiring outcomes. [History & storage](docs/JOB_LIFECYCLE.md) retains compact timelines and references while removing exclusively owned generated files, with cleanup retries and database compaction.

See [Assistant recovery and assisted-apply comparison](docs/ASSISTANT_AND_ASSISTED_APPLY.md) for inline assistant retry/cancel controls, credential origins and service-key inheritance, legacy CLI browser-review limitations, and official OpenAI/DeepSeek/Claude API pricing.

Task-specific AI routing supports separate scoring, writing, reviewer, extraction, form, and assistant models through `LLM_<TASK>_PROVIDER` / `LLM_<TASK>_MODEL`. The effective configuration is visible in Settings and Insights; unconfigured tasks inherit the existing global provider/model.

ApplyPilot has a seven-stage preparation pipeline and a separate application-submission workflow. It discovers jobs across 5+ boards, scores them against your resume with AI, tailors your resume per job, prepares required statements and cover letters, and renders PDFs. Application automation then navigates forms, uploads documents, answers screening questions, and submits through the separate apply workflow.

Three commands. That's it.

```bash
pip install applypilot
pip install python-jobspy==1.2.0       # board discovery, including its curl_cffi runtime dependency
applypilot init          # one-time setup: resume, profile, preferences, API keys
applypilot run           # discover > enrich > score > tailor > cover letters
applypilot run -w 4      # same but parallel (4 threads for discovery/enrichment)
applypilot apply         # autonomous browser-driven submission
applypilot apply -w 3    # parallel apply (3 Chrome instances)
applypilot apply --dry-run  # fill forms without submitting
```

If you are running from a repo checkout (copied/cloned), bootstrap in one step:

```bash
python scripts/bootstrap.py
# optional: python scripts/bootstrap.py --with-playwright-browsers
```

If you want zero host dependency setup, run with Docker:

```bash
docker compose up --build
# dashboard: http://127.0.0.1:8765
```

First build is heavier (installs Chromium + system deps) so browser-driven stages also work in-container.

Windows PowerShell helper:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-docker.ps1 -Rebuild
```

---

## Two Paths

### Full Pipeline (recommended)
**Requires:** Python 3.11+, Node.js (for npx), Gemini API key (free), Claude Code CLI, Chrome

Runs seven preparation stages, followed by the separate application-submission workflow.

### Discovery + Tailoring Only
**Requires:** Python 3.11+, Gemini API key (free)

Runs the preparation pipeline: discovers jobs, enriches and scores them, tailors your resume, prepares required statements and covers, and renders PDFs. You submit applications manually with the AI-prepared materials.

---

## The Pipeline

| Stage | What Happens |
|-------|-------------|
| **1. Discover** | Scrapes 5 job boards (Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs) + 48 Workday employer portals + 30 direct career sites |
| **2. Enrich** | Fetches full job descriptions via JSON-LD, CSS selectors, or AI-powered extraction |
| **3. Score** | AI rates every job 1-10 based on your resume and preferences. Only high-fit jobs proceed |
| **4. Tailor** | AI rewrites your resume per job: reorganizes, emphasizes relevant experience, adds keywords. Never fabricates |
| **5. Statement** | Generates supporting statements when required or explicitly requested |
| **6. Cover Letter** | Generates targeted cover letters according to packet requirements |
| **7. PDF** | Renders missing or stale required documents |
| **Separate: Auto-Apply** | Navigates application forms, fills fields, uploads documents, answers questions, and submits |

Each stage is independent. Run them all or pick what you need.

---

## ApplyPilot vs The Alternatives

| Feature | ApplyPilot | AIHawk | Manual |
|---------|-----------|--------|--------|
| Job discovery | 5 boards + Workday + direct sites | LinkedIn only | One board at a time |
| AI scoring | 1-10 fit score per job | Basic filtering | Your gut feeling |
| Resume tailoring | Per-job AI rewrite | Template-based | Hours per application |
| Auto-apply | Full form navigation + submission | LinkedIn Easy Apply only | Click, type, repeat |
| Supported sites | Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs, 46 Workday portals, 28 direct sites | LinkedIn | Whatever you open |
| License | AGPL-3.0 | MIT | N/A |

---

## Requirements

| Component | Required For | Details |
|-----------|-------------|---------|
| Python 3.11+ | Everything | Core runtime |
| Node.js 18+ | Auto-apply | Needed for `npx` to run Playwright MCP server |
| Gemini API key | Scoring, tailoring, cover letters | Free tier (15 RPM / 1M tokens/day) is enough |
| Chrome/Chromium | Auto-apply | Auto-detected on most systems |
| Claude Code CLI | Auto-apply | Install from [claude.ai/code](https://claude.ai/code) |

**Gemini API key is free.** Get one at [aistudio.google.com](https://aistudio.google.com). OpenAI and local models (Ollama/llama.cpp) are also supported.

### Optional

| Component | What It Does |
|-----------|-------------|
| CapSolver API key | Solves CAPTCHAs during auto-apply (hCaptcha, reCAPTCHA, Turnstile, FunCaptcha). Without it, CAPTCHA-blocked applications just fail gracefully |

> **Note:** current builds use `python-jobspy==1.2.0` with normal dependency installation and an import smoke check. Older `--no-deps` instructions can leave `curl_cffi` missing even when the JobSpy distribution is installed.

---

## Configuration

All generated by `applypilot init`:

### `profile.json`
Your personal data in one structured file: contact info, work authorization, compensation, experience, skills, resume facts (preserved during tailoring), and EEO defaults. Powers scoring, tailoring, and form auto-fill.

### `searches.yaml`
Job search queries, target titles, locations, boards. Run multiple searches with different parameters.

### `.env`
API keys and runtime config: `GEMINI_API_KEY`, `LLM_MODEL`, `CAPSOLVER_API_KEY` (optional).

### Package configs (shipped with ApplyPilot)
- `config/employers.yaml` - Workday employer registry (48 preconfigured)
- `config/sites.yaml` - Direct career sites (30+), blocked sites, base URLs, manual ATS domains
- `config/searches.example.yaml` - Example search configuration

---

## Move to Another Computer (Easy Mode)

Use built-in workspace transfer commands.

On your current computer:

```bash
applypilot workspace-export --out ~/applypilot-transfer.zip --include-env --include-generated
```

On the new computer:

```bash
python scripts/bootstrap.py
applypilot workspace-import ~/applypilot-transfer.zip --overwrite
```

Notes:
- `--include-env` copies API keys from `.env` (share archive carefully).
- `--include-generated` copies `tailored_resumes/` and `cover_letters/`.
- If you want a clean start without old job history, export with `--no-db`.

### Docker users

Container data is persisted under `docker-data/` in this repo:
- `docker-data/workspace` -> single-user workspace (`APPLYPILOT_DIR`)
- `docker-data/multi` -> multi-user auth/workspaces (`APPLYPILOT_MULTI_ROOT`)
- `docker-data/transfer` -> place transfer zip files here

Import a transfer zip into Docker workspace:

```bash
docker compose run --rm applypilot applypilot workspace-import /transfer/applypilot-transfer-clean.zip --overwrite
```

Export from Docker workspace:

```bash
docker compose run --rm applypilot applypilot workspace-export --out /transfer/applypilot-transfer.zip --include-env --include-generated
```

---

## How Stages Work

### Discover
Queries Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs via JobSpy. Scrapes 48 Workday employer portals (configurable in `employers.yaml`). Hits 30 direct career sites with custom extractors. Deduplicates by URL.

### Enrich
Visits each job URL and extracts the full description. 3-tier cascade: JSON-LD structured data, then CSS selector patterns, then AI-powered extraction for unknown layouts.

### Score
AI scores every job 1-10 against your profile. 9-10 = strong match, 7-8 = good, 5-6 = moderate, 1-4 = skip. Only jobs above your threshold proceed to tailoring.

### Tailor
Generates a custom resume per job: reorders experience, emphasizes relevant skills, incorporates keywords from the job description. Your `resume_facts` (companies, projects, metrics) are preserved exactly. The AI reorganizes but never fabricates.

### Cover Letter
Writes a targeted cover letter per job referencing the specific company, role, and how your experience maps to their requirements.

### Auto-Apply
Claude Code launches a Chrome instance, navigates to each application page, detects the form type, fills personal information and work history, uploads the tailored resume and cover letter, answers screening questions with AI, and submits. A live dashboard shows progress in real-time.

The Playwright MCP server is configured automatically at runtime per worker. No manual MCP setup needed.

```bash
# Utility modes (no Chrome/Claude needed)
applypilot apply --mark-applied URL    # manually mark a job as applied
applypilot apply --mark-failed URL     # manually mark a job as failed
applypilot apply --reset-failed        # reset all failed jobs for retry
applypilot apply --gen --url URL       # generate prompt file for manual debugging
```

---

## CLI Reference

```
applypilot init                         # First-time setup wizard
applypilot run [stages...]              # Run pipeline stages (or 'all')
applypilot run --workers 4              # Parallel discovery/enrichment
applypilot run --stream                 # Concurrent stages (streaming mode)
applypilot run --min-score 8            # Override score threshold
applypilot run --dry-run                # Preview without executing
applypilot apply                        # Launch auto-apply
applypilot apply --workers 3            # Parallel browser workers
applypilot apply --dry-run              # Fill forms without submitting
applypilot apply --continuous           # Run forever, polling for new jobs
applypilot apply --headless             # Headless browser mode
applypilot apply --url URL              # Apply to a specific job
applypilot status                       # Pipeline statistics
applypilot dashboard                    # Open HTML results dashboard
applypilot workspace-export             # Create portable workspace zip
applypilot workspace-import ARCHIVE.zip # Restore workspace zip
```

---

## AI Eval Gates

ApplyPilot includes deterministic offline evals for scoring, tailoring, NHS Statement Studio, and structured LLM output:

```bash
python scripts/ai_regression_eval.py --strict      # fast guardrail checks
python scripts/ai_eval_dataset.py --strict         # formal dataset suites + trend log
```

The regression harness includes a redacted NHS Application Support case. It rejects copied form instructions, personal contact details, speculative joining plans, and unsupported clinical or training claims while allowing honest transferable-evidence gaps.

Formal eval trend history is written to `logs/ai_eval_history.jsonl` under your ApplyPilot workspace.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and PR guidelines.

---

## License

ApplyPilot is licensed under the [GNU Affero General Public License v3.0](LICENSE).

You are free to use, modify, and distribute this software. If you deploy a modified version as a service, you must release your source code under the same license.
