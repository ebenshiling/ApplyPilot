import sqlite3

from applypilot.database import get_jobs_by_stage


def _seed_jobs(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE jobs (
            url TEXT PRIMARY KEY,
            title TEXT,
            discovered_at TEXT,
            fit_score INTEGER,
            full_description TEXT,
            tailored_resume_path TEXT,
            tailor_attempts INTEGER,
            apply_status TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO jobs (url, title, discovered_at, fit_score, full_description, tailored_resume_path, tailor_attempts, apply_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "https://example.com/1",
                "Selected Data Analyst",
                "2026-03-02T10:00:00+00:00",
                9,
                "full desc",
                None,
                0,
                "selected",
            ),
            (
                "https://example.com/2",
                "Unselected Data Engineer",
                "2026-03-02T09:00:00+00:00",
                8,
                "full desc",
                None,
                0,
                None,
            ),
        ],
    )
    conn.commit()


def test_get_jobs_by_stage_selected_only_pending_tailor() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _seed_jobs(conn)

    selected_jobs = get_jobs_by_stage(
        conn=conn,
        stage="pending_tailor",
        min_score=7,
        limit=0,
        selected_only=True,
    )
    all_jobs = get_jobs_by_stage(conn=conn, stage="pending_tailor", min_score=7, limit=0, selected_only=False)

    assert len(selected_jobs) == 1
    assert selected_jobs[0]["title"] == "Selected Data Analyst"
    assert len(all_jobs) == 2
