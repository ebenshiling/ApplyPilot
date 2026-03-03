FMEA Policy (Strict)

Purpose
This repo uses a strict Failure Modes and Effects Analysis (FMEA) policy for any change that affects:
- Job discovery/enrichment/scoring/tailoring/cover-letter generation
- Auto-apply behavior
- Dashboard live actions (`/api/*`)
- Database schema or persistence

Rules
1) Always assume production data
- The SQLite DB under the user workspace is production-like.
- No destructive migrations. No dropping tables/columns. No rewriting user configs.

2) Identify failure modes before coding
- Document at least: failure mode, cause, effect, detection method, mitigation.
- If risk touches applying/submission, treat as high impact.

3) Add detection
- Prefer tests + runtime guards.
- If you cannot add tests, add explicit validation and log/UX feedback.

4) Limit blast radius
- Add flags/options rather than changing defaults.
- Keep old code paths working until deprecation is planned.

Suggested FMEA Template (JSON)
{
  "change_id": "YYYYMMDD-xx",
  "scope": ["dashboard", "pipeline"],
  "failure_modes": [
    {
      "mode": "Dashboard fails to render",
      "cause": "HTML generator invalid",
      "effect": "User cannot manage jobs",
      "severity": 7,
      "occurrence": 3,
      "detection": "pytest + compileall",
      "mitigation": "Smoke test + compile step"
    }
  ]
}
