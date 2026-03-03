## UK Workflow (Assisted Apply)

This repo is set up to run UK-focused discovery + scoring + tailoring, then use the
assisted apply engine (fills forms but does not submit by default).

### 1) Configure your workspace (Windows)

- Workspace directory: `C:\Users\<you>\.applypilot-data\`
- Search config: `C:\Users\<you>\.applypilot-data\searches.yaml`
- Database: `C:\Users\<you>\.applypilot-data\applypilot.db`

If you want to force the workspace location:

```powershell
$env:APPLYPILOT_DIR = "$HOME\.applypilot-data"
```

Copy a starter config from the package templates:

- `src/applypilot/config/searches.uk.data.yaml`
- `src/applypilot/config/searches.uk.broad.yaml`

### 2) Run the pipeline

```bash
applypilot run discover enrich score tailor cover pdf --min-score 7
```

If you want UK direct-site scraping (Reed, GOV.UK Find a job, etc.), ensure smart
extract is enabled in your workspace `.env`:

```env
SMARTE_ENABLED=1
```

Optional knobs while tuning / rate-limits:

```env
# Cap number of site targets per run (0 = no cap)
SMARTE_MAX_TARGETS=0
```

Check status:

```bash
applypilot status
```

### 3) Review in the HTML dashboard

```bash
applypilot dashboard
```

This writes and opens:

- `C:\Users\<you>\.applypilot-data\dashboard.html`

### 4) Assisted apply (recommended default)

Fill the application and leave the browser open for manual review/submission:

```bash
applypilot apply --engine llm --keep-open --min-score 7
```

Apply a specific job:

```bash
applypilot apply --engine llm --keep-open --url "<job url>"
```

### Notes

- `skip_titles` in `searches.yaml` skips jobs during tailor/apply.
- UK direct sites are configured in `src/applypilot/config/sites.yaml` and are scraped
  via smart extract during discovery.
