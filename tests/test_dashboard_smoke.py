import json
import tempfile
from pathlib import Path

import pytest

from applypilot.database import close_connection, get_connection, init_db
from applypilot.view import generate_dashboard


def _seed_minimal_job(db_path: Path) -> None:
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT OR REPLACE INTO jobs (
            url, title, salary, description, location, site, strategy, discovered_at,
            full_description, application_url, detail_error,
            fit_score, score_reasoning, scored_at,
            tailored_resume_path, cover_letter_path,
            applied_at, apply_status, apply_error
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?
        )
        """,
        (
            "https://example.com/job/1",
            "Example Job",
            "$100k",
            "Short desc",
            "Remote",
            "indeed",
            "jobspy",
            "2026-01-01T00:00:00Z",
            "Full description\nLine 2",
            "https://example.com/apply/1",
            None,
            7,
            "python,sql\nsolid match",
            "2026-01-01T00:00:00Z",
            None,
            None,
            None,
            None,
            None,
        ),
    )
    conn.commit()


def test_generate_dashboard_writes_html_and_contains_panels() -> None:
    with tempfile.TemporaryDirectory() as td:
        app_dir = Path(td)
        db_path = app_dir / "applypilot.db"
        try:
            init_db(db_path)
            _seed_minimal_job(db_path)

            out_path = app_dir / "dashboard.html"
            abs_out = generate_dashboard(output_path=str(out_path), quiet=True, db_path=db_path, app_dir=app_dir)
            p = Path(abs_out)
            assert p.exists()

            html = p.read_text(encoding="utf-8")
            # Key layout + interactive sections
            assert 'class="wrap"' in html
            assert 'class="page-head' in html
            assert 'id="pipeline-controls"' in html
            assert 'id="pipeline-console"' in html

            # Setup panel (served mode only)
            assert 'id="setup-panel"' in html
            assert ">Setup<" in html
            assert "Resume Template Builder (No JSON Needed)" in html
            assert "Regenerate tailored resumes now" in html
            assert "setupRegenerateTailoredResumes" in html
            assert "String.fromCharCode(10)" in html
            assert "split(cr).join(nl)" in html

            # Collapsible + scrollable panels
            assert 'class="panel"' in html
            assert 'class="panel-body"' in html
            assert "Score Distribution" in html
            assert "By Source" in html
        finally:
            # SQLite connections are cached thread-locally; explicitly close so
            # Windows can delete the temp directory.
            close_connection(db_path)


def test_blocked_prefixes_meta_present() -> None:
    with tempfile.TemporaryDirectory() as td:
        app_dir = Path(td)
        db_path = app_dir / "applypilot.db"
        try:
            init_db(db_path)
            _seed_minimal_job(db_path)

            # Seed blocked_urls to ensure meta tag is present and non-empty.
            conn = get_connection(db_path)
            conn.execute(
                "INSERT OR IGNORE INTO blocked_urls (prefix, reason, created_at) VALUES (?, ?, ?)",
                (
                    "https://example.com/job",
                    "test",
                    "2026-01-01T00:00:00Z",
                ),
            )
            conn.commit()

            out_path = app_dir / "dashboard.html"
            abs_out = generate_dashboard(output_path=str(out_path), quiet=True, db_path=db_path, app_dir=app_dir)
            html = Path(abs_out).read_text(encoding="utf-8")
            assert '<meta name="blocked-prefixes"' in html
            assert "example.com/job" in html
        finally:
            close_connection(db_path)
