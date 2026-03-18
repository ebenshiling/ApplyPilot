"""ApplyPilot CLI — the main entry point."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from applypilot import __version__

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)

app = typer.Typer(
    name="applypilot",
    help="AI-powered end-to-end job application pipeline.",
    no_args_is_help=True,
)
console = Console()
log = logging.getLogger(__name__)

# Valid pipeline stages (in execution order)
VALID_STAGES = ("discover", "enrich", "score", "tailor", "statement", "cover", "pdf")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bootstrap() -> None:
    """Common setup: load env, create dirs, init DB."""
    from applypilot.config import load_env, ensure_dirs
    from applypilot.database import init_db

    # Avoid leaking secrets (e.g. Gemini API key in query params) via httpx INFO logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    load_env()
    ensure_dirs()
    init_db()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold]applypilot[/bold] {__version__}")
        raise typer.Exit()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """ApplyPilot — AI-powered end-to-end job application pipeline."""


@app.command()
def init() -> None:
    """Run the first-time setup wizard (profile, resume, search config)."""
    from applypilot.wizard.init import run_wizard

    run_wizard()


@app.command()
def run(
    stages: Optional[list[str]] = typer.Argument(
        None,
        help=(f"Pipeline stages to run. Valid: {', '.join(VALID_STAGES)}, all. Defaults to 'all' if omitted."),
    ),
    min_score: int = typer.Option(7, "--min-score", help="Minimum fit score for tailor/cover stages."),
    workers: int = typer.Option(1, "--workers", "-w", help="Parallel threads for discovery/enrichment stages."),
    selected_only: bool = typer.Option(
        False,
        "--selected-only",
        help="Tailor/cover/apply stages only process jobs marked as selected.",
    ),
    tailor_lenient: bool = typer.Option(
        False,
        "--tailor-lenient",
        help="Use lenient validation mode for tailoring (fewer strict blockers).",
    ),
    stream: bool = typer.Option(False, "--stream", help="Run stages concurrently (streaming mode)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview stages without executing."),
) -> None:
    """Run pipeline stages: discover, enrich, score, tailor, statement, cover, pdf."""
    _bootstrap()

    from applypilot.pipeline import run_pipeline

    stage_list = stages if stages else ["all"]

    # Validate stage names
    for s in stage_list:
        if s != "all" and s not in VALID_STAGES:
            console.print(f"[red]Unknown stage:[/red] '{s}'. Valid stages: {', '.join(VALID_STAGES)}, all")
            raise typer.Exit(code=1)

    # Gate AI stages behind Tier 2
    llm_stages = {"score", "tailor", "statement", "cover"}
    if any(s in stage_list for s in llm_stages) or "all" in stage_list:
        from applypilot.config import check_tier

        check_tier(2, "AI scoring/tailoring")

    if selected_only:
        os.environ["APPLYPILOT_SELECTED_ONLY"] = "1"
        os.environ["APPLYPILOT_APPLY_SELECTED_ONLY"] = "1"
    if tailor_lenient:
        os.environ["APPLYPILOT_TAILOR_LENIENT"] = "1"

    result = run_pipeline(
        stages=stage_list,
        min_score=min_score,
        dry_run=dry_run,
        stream=stream,
        workers=workers,
    )

    if result.get("errors"):
        raise typer.Exit(code=1)


@app.command()
def apply(
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Max applications to submit."),
    workers: int = typer.Option(1, "--workers", "-w", help="Number of parallel browser workers."),
    min_score: int = typer.Option(7, "--min-score", help="Minimum fit score for job selection."),
    model: str = typer.Option("haiku", "--model", "-m", help="Claude model name."),
    continuous: bool = typer.Option(False, "--continuous", "-c", help="Run forever, polling for new jobs."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview actions without submitting."),
    headless: bool = typer.Option(False, "--headless", help="Run browsers in headless mode."),
    url: Optional[str] = typer.Option(None, "--url", help="Apply to a specific job URL."),
    gen: bool = typer.Option(False, "--gen", help="Generate prompt file for manual debugging instead of running."),
    mark_applied: Optional[str] = typer.Option(None, "--mark-applied", help="Manually mark a job URL as applied."),
    mark_failed: Optional[str] = typer.Option(
        None, "--mark-failed", help="Manually mark a job URL as failed (provide URL)."
    ),
    fail_reason: Optional[str] = typer.Option(None, "--fail-reason", help="Reason for --mark-failed."),
    block_id: Optional[int] = typer.Option(
        None,
        "--block-id",
        help="Archive a job (mark skipped) and block it from future discovery by dashboard/CLI ID.",
    ),
    reset_failed: bool = typer.Option(False, "--reset-failed", help="Reset all failed jobs for retry."),
    engine: str = typer.Option(
        "claude",
        "--engine",
        "-e",
        help="Apply engine: claude (auto) or llm (assisted fill; can submit with --submit).",
    ),
    keep_open: bool = typer.Option(
        True,
        "--keep-open/--close",
        help="(llm engine) Keep the browser open for manual review/submission.",
    ),
    submit: bool = typer.Option(
        False,
        "--submit",
        help="(llm engine) Attempt to complete and submit the application.",
    ),
) -> None:
    """Launch auto-apply to submit job applications."""
    _bootstrap()

    from applypilot.config import check_tier, PROFILE_PATH as _profile_path
    from applypilot.database import get_connection

    # --- Utility modes (no Chrome/Claude needed) ---

    if block_id is not None:
        from applypilot.database import block_job_by_id

        n = block_job_by_id(int(block_id), reason="user_deleted")
        if n == 0:
            console.print("[red]No matching job found for that ID.[/red]")
            raise typer.Exit(code=2)
        console.print(f"[green]Blocked and archived job:[/green] {block_id}")
        return

    if mark_applied:
        from applypilot.apply.launcher import mark_job

        updated = mark_job(mark_applied, "applied")
        if updated == -1:
            console.print(
                "[red]Multiple jobs matched that URL.[/red] "
                "Re-run --mark-applied with the exact canonical job URL from `applypilot status`."
            )
            raise typer.Exit(code=2)
        if updated == 0:
            console.print(
                "[red]No matching job found for that URL.[/red] "
                "Try the canonical job URL from `applypilot status` or run discovery/enrich first."
            )
            raise typer.Exit(code=2)
        console.print(f"[green]Marked as applied ({updated}):[/green] {mark_applied}")
        return

    if mark_failed:
        from applypilot.apply.launcher import mark_job

        updated = mark_job(mark_failed, "failed", reason=fail_reason)
        if updated == -1:
            console.print(
                "[red]Multiple jobs matched that URL.[/red] "
                "Re-run --mark-failed with the exact canonical job URL from `applypilot status`."
            )
            raise typer.Exit(code=2)
        if updated == 0:
            console.print(
                "[red]No matching job found for that URL.[/red] "
                "Try the canonical job URL from `applypilot status` or run discovery/enrich first."
            )
            raise typer.Exit(code=2)
        console.print(f"[yellow]Marked as failed ({updated}):[/yellow] {mark_failed} ({fail_reason or 'manual'})")
        return

    if reset_failed:
        from applypilot.apply.launcher import reset_failed as do_reset

        count = do_reset()
        console.print(f"[green]Reset {count} failed job(s) for retry.[/green]")
        return

    # --- Full apply mode ---

    effective_limit = limit if limit is not None else (0 if continuous else 1)

    engine = (engine or "claude").strip().lower()
    if engine not in ("claude", "llm"):
        console.print("[red]Invalid --engine.[/red] Use: claude, llm")
        raise typer.Exit(code=1)

    # Tier checks differ by engine
    if engine == "claude":
        # Tier 3 required (Claude Code CLI + Chrome)
        check_tier(3, "auto-apply")
    else:
        # Tier 2 required (LLM API key) for assisted fill
        check_tier(2, "assisted apply")
        # Also require Chrome/Chromium for browser automation
        from applypilot import config as _cfg

        try:
            _cfg.get_chrome_path()
        except FileNotFoundError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=1)

    # Check 2: Profile exists
    if not _profile_path.exists():
        console.print("[red]Profile not found.[/red]\nRun [bold]applypilot init[/bold] to create your profile first.")
        raise typer.Exit(code=1)

    # Check 3: Tailored resumes exist (skip for --gen with --url)
    if not (gen and url):
        conn = get_connection()
        ready = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL AND applied_at IS NULL"
        ).fetchone()[0]
        if ready == 0:
            console.print(
                "[red]No tailored resumes ready.[/red]\n"
                "Run [bold]applypilot run score tailor[/bold] first to prepare applications."
            )
            raise typer.Exit(code=1)

    if gen:
        if engine != "claude":
            console.print("[red]--gen is only supported with --engine claude.[/red]")
            raise typer.Exit(code=1)
        from applypilot.apply.launcher import gen_prompt, BASE_CDP_PORT

        target = url or ""
        if not target:
            console.print("[red]--gen requires --url to specify which job.[/red]")
            raise typer.Exit(code=1)
        prompt_file = gen_prompt(target, min_score=min_score, model=model)
        if not prompt_file:
            console.print("[red]No matching job found for that URL.[/red]")
            raise typer.Exit(code=1)
        mcp_path = _profile_path.parent / ".mcp-apply-0.json"
        console.print(f"[green]Wrote prompt to:[/green] {prompt_file}")
        console.print(f"\n[bold]Run manually:[/bold]")
        console.print(
            f"  claude --model {model} -p --mcp-config {mcp_path} --permission-mode bypassPermissions < {prompt_file}"
        )
        return

    if engine == "llm":
        from applypilot.apply.llm_engine import main as llm_apply_main

        llm_apply_main(
            limit=effective_limit,
            target_url=url,
            min_score=min_score,
            headless=headless,
            dry_run=dry_run,
            continuous=continuous,
            workers=workers,
            keep_open=keep_open,
            submit=submit,
        )
        return

    from applypilot.apply.launcher import main as apply_main

    console.print("\n[bold blue]Launching Auto-Apply[/bold blue]")
    console.print(f"  Limit:    {'unlimited' if continuous else effective_limit}")
    console.print(f"  Workers:  {workers}")
    console.print(f"  Model:    {model}")
    console.print(f"  Headless: {headless}")
    console.print(f"  Dry run:  {dry_run}")
    if url:
        console.print(f"  Target:   {url}")
    console.print()

    apply_main(
        limit=effective_limit,
        target_url=url,
        min_score=min_score,
        headless=headless,
        model=model,
        dry_run=dry_run,
        continuous=continuous,
        workers=workers,
    )


@app.command("score")
def score_cmd(
    limit: int = typer.Option(25, "--limit", "-l", help="Max jobs to score in this run (0 = no limit)."),
    rescore: bool = typer.Option(False, "--rescore", help="Re-score jobs that already have a score."),
) -> None:
    """Score jobs with the configured LLM (useful for small batches)."""
    _bootstrap()

    from applypilot.config import check_tier

    check_tier(2, "AI scoring")

    from applypilot.scoring.scorer import run_scoring

    run_scoring(limit=limit, rescore=rescore)


@app.command("score-repair")
def score_repair_cmd() -> None:
    """Repair jobs incorrectly saved with fit_score=0 from truncated LLM JSON."""
    _bootstrap()

    from applypilot.scoring.scorer import run_score_repair

    result = run_score_repair()
    console.print(
        "[green]Score repair complete.[/green] "
        f"Candidates: {result['candidates']} | "
        f"Recovered: {result['recovered']} | "
        f"Remaining zero scores: {result['remaining_zero']}"
    )


@app.command("llm-test")
def llm_test(
    prompt: str = typer.Option("Reply with exactly: OK", "--prompt", help="Prompt to send to the LLM."),
) -> None:
    """Verify LLM connectivity and configuration."""
    _bootstrap()

    from applypilot.config import check_tier

    check_tier(2, "LLM test")

    from applypilot.llm import get_client

    client = get_client()
    text = client.ask(prompt, temperature=0.0, max_tokens=64)
    console.print(f"\n[bold]LLM response:[/bold] {text}\n")


@app.command("reset")
def reset_cmd(
    min_score: int = typer.Option(7, "--min-score", help="Only reset jobs with fit_score >= min_score."),
    tailor: bool = typer.Option(False, "--tailor", help="Reset tailored resumes (also clears cover letters)."),
    cover: bool = typer.Option(False, "--cover", help="Reset cover letters."),
    all_: bool = typer.Option(False, "--all", help="Reset both tailored resumes and cover letters."),
    include_applied: bool = typer.Option(False, "--include-applied", help="Also reset jobs already marked applied."),
) -> None:
    """Reset generated artifacts in the database (cross-platform)."""
    _bootstrap()

    from applypilot.database import get_connection

    do_tailor = bool(all_ or tailor)
    do_cover = bool(all_ or cover or do_tailor)  # cover depends on tailored resume

    if not (do_tailor or do_cover):
        console.print("[red]Nothing to reset.[/red] Use --cover, --tailor, or --all")
        raise typer.Exit(code=1)

    where = "fit_score >= ?"
    params: list = [min_score]
    if not include_applied:
        where += " AND applied_at IS NULL"

    conn = get_connection()
    before = conn.total_changes

    if do_tailor:
        conn.execute(
            f"UPDATE jobs SET tailored_resume_path=NULL, tailored_at=NULL, tailor_attempts=0 WHERE {where}",
            params,
        )

    if do_cover:
        conn.execute(
            f"UPDATE jobs SET cover_letter_path=NULL, cover_letter_at=NULL, cover_attempts=0 WHERE {where}",
            params,
        )

    conn.commit()
    changed = conn.total_changes - before
    console.print(f"[green]Reset complete.[/green] Rows updated: {changed}")


@app.command("profile-sync")
def profile_sync_cmd(
    personal: bool = typer.Option(True, "--personal/--no-personal", help="Sync name/email/phone from resume.txt."),
    skills: bool = typer.Option(True, "--skills/--no-skills", help="Sync skills_boundary from resume.txt."),
    facts: bool = typer.Option(
        True, "--facts/--no-facts", help="Sync resume_facts (companies, metrics, etc.) from resume.txt."
    ),
) -> None:
    """Sync profile.json fields from resume.txt (cross-platform).

    This is designed to avoid PowerShell quoting issues when updating JSON.
    """
    _bootstrap()

    import json
    import re

    from applypilot.config import PROFILE_PATH, RESUME_PATH
    from applypilot.scoring.validator import sanitize_text

    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in resume_text.splitlines()]

    def _first_non_empty() -> str:
        for ln in lines:
            if ln.strip():
                return ln.strip()
        return ""

    def _extract_email() -> str:
        m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", resume_text)
        return m.group(0) if m else ""

    def _extract_phone() -> str:
        # Prefer UK mobile format like 07799 833024
        m = re.search(r"\b0\d{4}\s?\d{6}\b", resume_text)
        if m:
            return m.group(0)
        # Fallback: any long digit run
        m = re.search(r"\+?\d[\d\s\-]{9,}\d", resume_text)
        return (m.group(0) if m else "").strip()

    def _block(start_header: str, end_headers: list[str]) -> list[str]:
        start = None
        for i, ln in enumerate(lines):
            if ln.strip().upper() == start_header.upper():
                start = i + 1
                break
        if start is None:
            return []

        out: list[str] = []
        for ln in lines[start:]:
            if ln.strip().upper() in {h.upper() for h in end_headers}:
                break
            out.append(ln)
        return out

    changes: list[str] = []

    if personal:
        name = sanitize_text(_first_non_empty())
        # If the resume header uses ALL-CAPS, normalize profile name for
        # application forms and cover letters.
        letters_only = re.sub(r"[^A-Za-z]", "", name)
        if letters_only and letters_only.isupper():
            name = name.title()
        email = sanitize_text(_extract_email())
        phone = sanitize_text(_extract_phone())

        p = profile.setdefault("personal", {})
        if name and p.get("full_name") != name:
            p["full_name"] = name
            changes.append("personal.full_name")
        if name and p.get("preferred_name") != name:
            p["preferred_name"] = name
            changes.append("personal.preferred_name")
        if email and p.get("email") != email:
            p["email"] = email
            changes.append("personal.email")
        if phone and p.get("phone") != phone:
            p["phone"] = phone
            changes.append("personal.phone")

        # Heuristic: update country when resume explicitly says UK.
        if re.search(r"\bUK\b", resume_text, flags=re.IGNORECASE):
            if p.get("country") != "United Kingdom":
                p["country"] = "United Kingdom"
                changes.append("personal.country")

        # Experience headline (optional)
        exp = profile.setdefault("experience", {})
        if exp.get("current_company") != "Sike Property Maintenance Ltd":
            exp["current_company"] = "Sike Property Maintenance Ltd"
            changes.append("experience.current_company")
        if exp.get("current_job_title") != "Data & Quality Systems Analyst":
            exp["current_job_title"] = "Data & Quality Systems Analyst"
            changes.append("experience.current_job_title")

    if skills:
        core_lines = _block("CORE SKILLS", ["TECHNICAL PROFICIENCY", "PROFESSIONAL EXPERIENCE"])
        core_skills: list[str] = []
        for ln in core_lines:
            s = ln.strip()
            if not s:
                continue
            if s.startswith("\u2022") or s.startswith("-") or s.startswith("*"):
                s = s.lstrip("\u2022-* ").strip()
            if s and len(s) > 2:
                core_skills.append(sanitize_text(s))

        tools_block = _block(
            "Programming & Data Tools", ["Data Practices", "Governance & Compliance", "PROFESSIONAL EXPERIENCE"]
        )
        tools_line = ""
        for ln in tools_block:
            if ln.strip():
                tools_line = ln.strip()
                break

        tool_items: list[str] = []
        if tools_line:
            for part in [p.strip() for p in tools_line.split("|") if p.strip()]:
                # Expand parentheses: "Python (Pandas, NumPy)" -> Python + Pandas + NumPy
                m = re.match(r"^(.*?)\((.*?)\)\s*$", part)
                if m:
                    base = sanitize_text(m.group(1)).strip()
                    inside = m.group(2)
                    if base and base.lower() != "excel":
                        tool_items.append(base)
                    for sub in [x.strip() for x in inside.split(",") if x.strip()]:
                        sub = sanitize_text(sub)
                        if sub.lower() == "advanced":
                            continue
                        tool_items.append(sub)
                else:
                    # Drop qualifiers like "Excel (Advanced)"
                    part2 = sanitize_text(re.sub(r"\(.*?\)", "", part)).strip()
                    if part2:
                        tool_items.append(part2)

        data_practice_lines = _block("Data Practices", ["Governance & Compliance", "PROFESSIONAL EXPERIENCE"])
        data_practices = [sanitize_text(ln) for ln in data_practice_lines if ln.strip()]

        gov_lines = _block("Governance & Compliance", ["PROFESSIONAL EXPERIENCE", "EDUCATION"])
        governance = [sanitize_text(ln) for ln in gov_lines if ln.strip()]

        # Build a boundary that matches the current resume.
        # Keep tools largely atomic so fabrication checks and prompts behave well.
        tools_set = {t.strip() for t in tool_items if t.strip()}

        def _has(term: str) -> bool:
            return term in tools_set or term in resume_text

        spreadsheets: list[str] = []
        for term in ["Excel", "Power Query", "Pivot Tables", "VBA"]:
            if _has(term):
                spreadsheets.append(term)

        boundary: dict[str, list[str]] = {
            "programming_languages": [t for t in ["SQL", "Python"] if _has(t)],
            "libraries": [t for t in ["Pandas", "NumPy"] if _has(t)],
            "bi_tools": [t for t in ["Power BI", "Tableau"] if _has(t)],
            "spreadsheets": spreadsheets,
            "data_practices": [s for s in data_practices if s],
            "governance": [s for s in governance if s],
            "capabilities": [s for s in core_skills if s and s.lower() not in ("core skills",)],
        }

        # De-dup while preserving order
        for k, vals in list(boundary.items()):
            seen: set[str] = set()
            deduped: list[str] = []
            for v in vals:
                key = v.strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                deduped.append(v.strip())
            boundary[k] = deduped

        profile["skills_boundary"] = boundary
        changes.append("skills_boundary")

    if facts:
        # Preserve companies from the professional experience block.
        exp_lines = _block("PROFESSIONAL EXPERIENCE", ["EDUCATION"])

        def _next_non_empty(idx: int) -> int | None:
            for j in range(idx + 1, len(exp_lines)):
                if exp_lines[j].strip():
                    return j
            return None

        companies: list[str] = []
        for i, ln in enumerate(exp_lines):
            s = ln.strip()
            if not s:
                continue
            if s.startswith("\u2022") or s.startswith("-"):
                continue
            if "|" in s:
                continue
            j1 = _next_non_empty(i)
            j2 = _next_non_empty(j1) if j1 is not None else None
            if j2 is not None and "|" in (exp_lines[j2] or ""):
                # ln looks like a company header
                companies.append(sanitize_text(s))

        # Dissertation project title
        proj = ""
        m = re.search(r"^Dissertation:\s*(.+)$", resume_text, flags=re.IGNORECASE | re.MULTILINE)
        if m:
            proj = sanitize_text(m.group(1)).strip()

        # School (default to University of Derby when present)
        school = "University of Derby" if "University of Derby" in resume_text else ""

        # Only keep work metrics from the PROFESSIONAL EXPERIENCE block.
        exp_text = "\n".join(exp_lines)
        metrics: list[str] = []
        for m in re.finditer(r"\b\d+\s*%\b|\b\d+\s*percent\b", exp_text, flags=re.IGNORECASE):
            raw = sanitize_text(m.group(0)).lower()
            raw = raw.replace(" percent", "%")
            metrics.append(raw)
        # normalize metrics casing
        metrics_norm = []
        for x in metrics:
            x2 = x.strip()
            if x2 and x2 not in metrics_norm:
                metrics_norm.append(x2)

        rf = profile.setdefault("resume_facts", {})
        if companies:
            rf["preserved_companies"] = companies
            changes.append("resume_facts.preserved_companies")
        if proj:
            rf["preserved_projects"] = [proj]
            changes.append("resume_facts.preserved_projects")
        if school:
            rf["preserved_school"] = school
            changes.append("resume_facts.preserved_school")
        if metrics_norm:
            rf["real_metrics"] = metrics_norm
            changes.append("resume_facts.real_metrics")

    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=True), encoding="utf-8")
    if not changes:
        console.print("[green]Profile already matches resume.txt (no changes).[/green]")
        return
    console.print("[green]Profile updated from resume.txt:[/green] " + ", ".join(sorted(set(changes))))


@app.command("workspace-export")
def workspace_export_cmd(
    out: Optional[str] = typer.Option(
        None,
        "--out",
        "-o",
        help="Output zip path. Defaults to '<workspace-parent>/applypilot-transfer-YYYYMMDD-HHMMSS.zip'.",
    ),
    include_env: bool = typer.Option(False, "--include-env", help="Include .env secrets in archive."),
    include_db: bool = typer.Option(True, "--include-db/--no-db", help="Include applypilot.db job history."),
    include_generated: bool = typer.Option(
        True,
        "--include-generated/--no-generated",
        help="Include tailored_resumes and cover_letters directories.",
    ),
    include_logs: bool = typer.Option(False, "--include-logs", help="Include logs/ directory."),
) -> None:
    """Create a portable workspace archive for moving to another computer."""
    from datetime import datetime

    from applypilot.config import APP_DIR
    from applypilot.workspace_transfer import export_workspace

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    default_out = APP_DIR.parent / f"applypilot-transfer-{ts}.zip"
    archive_path = Path(out).expanduser() if out else default_out

    try:
        result = export_workspace(
            APP_DIR,
            archive_path,
            include_env=include_env,
            include_db=include_db,
            include_generated=include_generated,
            include_logs=include_logs,
        )
    except Exception as e:
        console.print(f"[red]Workspace export failed:[/red] {e}")
        raise typer.Exit(code=1)

    console.print(f"[green]Workspace archive created:[/green] {result['archive']}")
    console.print(f"Included files: {result['count']}")
    missing = result.get("missing") or []
    if missing:
        console.print("Missing (not found in workspace): " + ", ".join(str(x) for x in missing))
    if include_env:
        console.print("[yellow]Note:[/yellow] archive includes .env secrets. Share carefully.")


@app.command("workspace-import")
def workspace_import_cmd(
    archive: str = typer.Argument(..., help="Path to workspace zip archive created by workspace-export."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing files in current workspace."),
) -> None:
    """Import a portable workspace archive into the current machine workspace."""
    from applypilot.config import APP_DIR
    from applypilot.workspace_transfer import import_workspace

    archive_path = Path(archive).expanduser()
    try:
        result = import_workspace(archive_path, APP_DIR, overwrite=overwrite)
    except Exception as e:
        console.print(f"[red]Workspace import failed:[/red] {e}")
        raise typer.Exit(code=1)

    console.print(f"[green]Workspace import complete.[/green] Restored files: {result['count']}")
    console.print(f"Workspace path: {result['workspace']}")

    skipped_exists = result.get("skipped_exists") or []
    skipped_unknown = result.get("skipped_unknown") or []
    skipped_unsafe = result.get("skipped_unsafe") or []

    if skipped_exists:
        console.print(f"Skipped existing files: {len(skipped_exists)} (use --overwrite to replace)")
    if skipped_unknown:
        console.print(f"Skipped unknown archive entries: {len(skipped_unknown)}")
    if skipped_unsafe:
        console.print(f"Skipped unsafe archive entries: {len(skipped_unsafe)}")


@app.command()
def status() -> None:
    """Show pipeline statistics from the database."""
    _bootstrap()

    from applypilot.database import get_stats

    stats = get_stats()

    console.print("\n[bold]ApplyPilot Pipeline Status[/bold]\n")

    # Summary table
    summary = Table(title="Pipeline Overview", show_header=True, header_style="bold cyan")
    summary.add_column("Metric", style="bold")
    summary.add_column("Count", justify="right")

    summary.add_row("Total jobs discovered", str(stats["total"]))
    summary.add_row("With full description", str(stats["with_description"]))
    summary.add_row("Pending enrichment", str(stats["pending_detail"]))
    summary.add_row("Enrichment errors", str(stats["detail_errors"]))
    summary.add_row("Scored by LLM", str(stats["scored"]))
    summary.add_row("Pending scoring", str(stats["unscored"]))
    summary.add_row("Tailored resumes", str(stats["tailored"]))
    summary.add_row("Pending tailoring (7+)", str(stats["untailored_eligible"]))
    summary.add_row("Cover letters", str(stats["with_cover_letter"]))
    summary.add_row("Ready to apply", str(stats["ready_to_apply"]))
    summary.add_row("Applied", str(stats["applied"]))
    summary.add_row("Apply errors", str(stats["apply_errors"]))

    console.print(summary)

    # Score distribution
    if stats["score_distribution"]:
        dist_table = Table(title="\nScore Distribution", show_header=True, header_style="bold yellow")
        dist_table.add_column("Score", justify="center")
        dist_table.add_column("Count", justify="right")
        dist_table.add_column("Bar")

        max_count = max(count for _, count in stats["score_distribution"]) or 1
        for score, count in stats["score_distribution"]:
            bar_len = int(count / max_count * 30)
            if score >= 7:
                color = "green"
            elif score >= 5:
                color = "yellow"
            else:
                color = "red"
            bar = f"[{color}]{'=' * bar_len}[/{color}]"
            dist_table.add_row(str(score), str(count), bar)

        console.print(dist_table)

    # By site
    if stats["by_site"]:
        site_table = Table(title="\nJobs by Source", show_header=True, header_style="bold magenta")
        site_table.add_column("Site")
        site_table.add_column("Count", justify="right")

        for site, count in stats["by_site"]:
            site_table.add_row(site or "Unknown", str(count))

        console.print(site_table)

    console.print()


@app.command()
def jobs(
    limit: int = typer.Option(50, "--limit", "-l", help="Max jobs to show."),
    site: Optional[str] = typer.Option(None, "--site", help="Filter by site name (substring match)."),
    status: Optional[str] = typer.Option(
        None,
        "--status",
        help="Filter by apply status (ready, prepared, in_progress, applied, failed, skipped, manual).",
    ),
    min_score: int = typer.Option(0, "--min-score", help="Minimum fit_score."),
    all_rows: bool = typer.Option(False, "--all", help="Include rows that aren't scored/ready."),
) -> None:
    """List jobs with a stable ID for manual marking."""
    _bootstrap()

    from applypilot.database import get_connection

    conn = get_connection()

    where: list[str] = []
    params: list[object] = []

    if not all_rows:
        where.append("fit_score IS NOT NULL")

    if min_score > 0:
        where.append("fit_score >= ?")
        params.append(min_score)

    if site:
        where.append("LOWER(site) LIKE ?")
        params.append(f"%{site.strip().lower()}%")

    if status:
        st = status.strip().lower()
        if st == "ready":
            where.append("(apply_status IS NULL AND applied_at IS NULL)")
        else:
            where.append("LOWER(COALESCE(apply_status, '')) = ?")
            params.append(st)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    rows = conn.execute(
        f"""
        SELECT rowid AS id,
               url, title, site, fit_score, apply_status, applied_at,
               application_url
          FROM jobs
          {where_sql}
          ORDER BY COALESCE(fit_score, 0) DESC, discovered_at DESC
          LIMIT ?
        """,
        (*params, limit),
    ).fetchall()

    table = Table(title=f"Jobs (showing {len(rows)} of limit {limit})", show_header=True, header_style="bold cyan")
    table.add_column("ID", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Status")
    table.add_column("Site")
    table.add_column("Title")
    table.add_column("Apply URL")

    def _short(s: str | None, n: int = 60) -> str:
        s = (s or "").strip()
        return s if len(s) <= n else s[: n - 3] + "..."

    for r in rows:
        st_raw = (r["apply_status"] or "").strip().lower()
        if not st_raw and r["applied_at"]:
            st_raw = "applied"
        if not st_raw:
            st_raw = "ready"
        table.add_row(
            str(r["id"]),
            str(r["fit_score"] or ""),
            st_raw,
            str(r["site"] or ""),
            _short(str(r["title"] or ""), 45),
            _short(str(r["application_url"] or r["url"] or ""), 70),
        )

    console.print(table)
    console.print(
        "\nTip: mark by ID: `applypilot apply --mark-applied <ID>` or `applypilot apply --mark-failed <ID> --fail-reason manual`\n"
    )


@app.command()
def dashboard() -> None:
    """Generate and open the HTML dashboard in your browser."""
    _bootstrap()

    from applypilot.view import open_dashboard

    open_dashboard()


@app.command(name="dashboard-serve")
def dashboard_serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host (default: localhost)."),
    port: int = typer.Option(8765, "--port", "-p", help="Port to serve on."),
    multi_user: bool = typer.Option(
        False,
        "--multi-user",
        help="Enable local account login with per-user isolated workspaces.",
    ),
) -> None:
    """Serve the dashboard with live buttons (mark applied/failed/block)."""
    _bootstrap()

    import webbrowser

    from applypilot.dashboard_server import serve_dashboard

    url = f"http://{host}:{int(port)}/"
    console.print(f"[green]Dashboard server:[/green] {url}")
    if multi_user:
        console.print("[cyan]Multi-user mode:[/cyan] on (login/register required)")
    console.print("[dim]Opening in browser...[/dim]")
    webbrowser.open(url)
    serve_dashboard(host=host, port=port, multi_user=multi_user)


@app.command(name="uk-sponsors-update")
def uk_sponsors_update(
    force: bool = typer.Option(False, "--force", help="Force re-download of the Home Office sponsor register."),
    max_age_days: int = typer.Option(7, "--max-age-days", help="Use cached register if newer than this."),
) -> None:
    """Download/cache the UK Home Office licensed sponsors register (workers)."""
    _bootstrap()

    from applypilot.uk_sponsorship import ensure_sponsor_register_cached

    path = ensure_sponsor_register_cached(max_age_days=(0 if force else int(max_age_days)))
    if path:
        console.print(f"[green]OK[/green] Cached sponsor register: {path}")
    else:
        console.print("[red]Failed[/red] Could not download sponsor register (no cache present).")


if __name__ == "__main__":
    app()
