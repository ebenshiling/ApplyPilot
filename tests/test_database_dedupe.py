from pathlib import Path


def test_store_jobs_canonical_url_dedupe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))

    import importlib
    import applypilot.config as cfg

    importlib.reload(cfg)

    from applypilot.database import close_connection, init_db, store_jobs

    db = tmp_path / "applypilot.db"
    conn = init_db(db)
    try:
        jobs = [
            {
                "url": "https://example.com/jobs/123?utm_source=x",
                "title": "Data Analyst",
                "search_query": "Data Analyst",
                "salary": None,
                "description": "desc",
                "location": "London",
            },
            {
                "url": "https://EXAMPLE.com/jobs/123",
                "title": "Data Analyst",
                "search_query": "Data Analyst",
                "salary": None,
                "description": "desc",
                "location": "London",
            },
        ]
        new, existing = store_jobs(conn, jobs, site="indeed", strategy="jobspy")
        assert new == 1
        assert existing == 1

        row = conn.execute("SELECT url FROM jobs LIMIT 1").fetchone()
        assert row is not None
        assert str(row[0]) == "https://example.com/jobs/123"
    finally:
        close_connection(db)
