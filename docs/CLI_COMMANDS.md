# ApplyPilot CLI Commands

This is a practical cheat-sheet for the common ApplyPilot workflow.

## Setup

- First-time wizard:

```bash
applypilot init
```

- Show status counters (discovered, enriched, scored, tailored, ready-to-apply, etc.):

```bash
applypilot status
```

- Verify your LLM is configured correctly:

```bash
applypilot llm-test
```

## Pipeline

- Run the full pipeline (recommended):

```bash
applypilot run discover enrich score tailor cover pdf --min-score 7
```

- Run only selected stages:

```bash
applypilot run discover enrich
applypilot run score tailor
```

- Parallelize discovery/enrichment:

```bash
applypilot run discover enrich --workers 4
```

- Dry-run (show what would run):

```bash
applypilot run --dry-run
```

## Dashboard

- Generate (and typically open) the HTML dashboard:

```bash
applypilot dashboard
```

This writes to your workspace as `dashboard.html` (for example:
`C:\Users\<you>\.applypilot-data\dashboard.html`).

## Apply

- Assisted apply (fills forms; leaves browser open; does not submit unless you pass `--submit`):

```bash
applypilot apply --engine llm --keep-open --min-score 7
```

- Apply a specific job URL:

```bash
applypilot apply --engine llm --keep-open --url "<job url>"
```

- Mark a job as applied/failed manually:

```bash
applypilot apply --mark-applied "<job url>"
applypilot apply --mark-failed "<job url>" --fail-reason "<reason>"
```

## Reset generated artifacts

- Reset tailored resumes and cover letters for high-fit jobs (keeps discovery/enrichment):

```bash
applypilot reset --all --min-score 7
```

## Key config files (workspace)

- `searches.yaml`: search queries, filters, and `skip_titles`
- `.env`: LLM keys + toggles like `SMARTE_ENABLED=1`
- `applypilot.db`: job database

Tip: set `APPLYPILOT_DIR` if you want to force the workspace location.
