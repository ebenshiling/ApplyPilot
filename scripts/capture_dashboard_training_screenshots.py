from __future__ import annotations

from pathlib import Path
import tempfile

from playwright.sync_api import sync_playwright

from applypilot.database import close_connection, get_connection, init_db
from applypilot.view import generate_dashboard


def _seed_demo_jobs(db_path: Path) -> None:
    conn = get_connection(db_path)
    rows = [
        (
            "https://example.com/job/1",
            "Data Analyst - Operations",
            "GBP 45,000",
            "Build dashboards and improve data quality.",
            "London, United Kingdom",
            "indeed",
            "jobspy",
            "2026-01-01T00:00:00Z",
            "Full description for Data Analyst role.",
            "https://example.com/apply/1",
            None,
            8,
            "Strong SQL and BI match",
            "2026-01-01T00:00:00Z",
            "tailored_resumes/Data_Analyst_Example.pdf",
            "cover_letters/Data_Analyst_Example.pdf",
            None,
            "selected",
            None,
        ),
        (
            "https://example.com/job/2",
            "BI Analyst - Reporting",
            "GBP 42,000",
            "Maintain reporting and KPI packs.",
            "Remote (UK)",
            "linkedin",
            "jobspy",
            "2026-01-01T00:00:00Z",
            "Full description for BI Analyst role.",
            "https://example.com/apply/2",
            None,
            7,
            "Good analytics fit",
            "2026-01-01T00:00:00Z",
            None,
            None,
            None,
            None,
            None,
        ),
    ]

    for row in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO jobs (
                url, title, salary, description, location, site, strategy, discovered_at,
                full_description, application_url, detail_error,
                fit_score, score_reasoning, scored_at,
                tailored_resume_path, cover_letter_path,
                applied_at, apply_status, apply_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
    conn.commit()


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    shot_dir = repo / "docs" / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        app_dir = Path(td)
        db_path = app_dir / "applypilot.db"
        out_html = app_dir / "dashboard.html"

        init_db(db_path)
        _seed_demo_jobs(db_path)
        generate_dashboard(output_path=str(out_html), quiet=True, db_path=db_path, app_dir=app_dir)

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={"width": 1600, "height": 1200})
                page.goto(out_html.resolve().as_uri(), wait_until="networkidle")

                page.screenshot(path=str(shot_dir / "01_dashboard_overview.png"), full_page=True)
                page.evaluate("window.scrollTo(0, 620)")
                page.wait_for_timeout(250)
                page.screenshot(path=str(shot_dir / "02_pipeline_controls.png"), full_page=False)

                page.evaluate(
                    "(function(){const el=document.querySelector('.job-card[data-id]'); if (el) el.scrollIntoView({behavior:'instant', block:'center'});})()"
                )
                page.wait_for_timeout(250)
                page.screenshot(path=str(shot_dir / "03_job_card_actions.png"), full_page=False)

                page.evaluate(
                    "(function(){const cards=[...document.querySelectorAll('#setup-panel .job-card')]; const el=cards.find(x=>x.textContent.includes('Full Profile Editor')); if (el) el.scrollIntoView({behavior:'instant', block:'start'});})()"
                )
                page.wait_for_timeout(250)
                page.screenshot(path=str(shot_dir / "04_full_profile_editor.png"), full_page=False)

                page.evaluate(
                    "(function(){const cards=[...document.querySelectorAll('#setup-panel .job-card')]; const el=cards.find(x=>x.textContent.includes('Tailoring Intelligence')); if (el) el.scrollIntoView({behavior:'instant', block:'start'});})()"
                )
                page.wait_for_timeout(250)
                page.screenshot(path=str(shot_dir / "05_tailoring_intelligence.png"), full_page=False)

                page.evaluate(
                    "(function(){const cards=[...document.querySelectorAll('#setup-panel .job-card')]; const el=cards.find(x=>x.textContent.includes('Job Search Config')); if (el) el.scrollIntoView({behavior:'instant', block:'start'});})()"
                )
                page.wait_for_timeout(250)
                page.screenshot(path=str(shot_dir / "06_search_config_builder.png"), full_page=False)

                browser.close()
        finally:
            close_connection(db_path)

    print(f"Wrote screenshots to: {shot_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
