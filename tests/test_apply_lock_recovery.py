from datetime import datetime, timedelta, timezone
from pathlib import Path


def test_acquire_job_recovers_stale_in_progress(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))

    import importlib
    import applypilot.config as cfg

    importlib.reload(cfg)

    import applypilot.database as dbmod

    importlib.reload(dbmod)

    from applypilot.database import close_connection, init_db

    import applypilot.apply.launcher as launcher

    importlib.reload(launcher)

    db = tmp_path / "applypilot.db"
    conn = init_db(db)
    old = (datetime.now(timezone.utc) - timedelta(minutes=120)).isoformat()
    conn.execute(
        """
        INSERT INTO jobs (
            url, title, search_query, location, site, strategy, discovered_at,
            tailored_resume_path, fit_score, full_description, application_url,
            apply_status, last_attempted_at, agent_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "https://example.com/job/stale",
            "Data Analyst",
            "Data Analyst",
            "Remote",
            "indeed",
            "jobspy",
            old,
            str(tmp_path / "tailored_resumes" / "x.txt"),
            8,
            "long description",
            "https://example.com/job/stale",
            "in_progress",
            old,
            "worker-old",
        ),
    )
    conn.commit()

    try:
        monkeypatch.setitem(cfg.DEFAULTS, "apply_lock_ttl_minutes", 20)
        job = launcher.acquire_job(min_score=7, worker_id=9)
        assert job is not None
        assert str(job.get("url") or "") == "https://example.com/job/stale"

        row = conn.execute(
            "SELECT apply_status, agent_id, last_attempted_at FROM jobs WHERE url = ?",
            ("https://example.com/job/stale",),
        ).fetchone()
        assert row is not None
        assert str(row[0]) == "in_progress"
        assert str(row[1]) == "worker-9"
        assert str(row[2]) > old
    finally:
        close_connection(db)
