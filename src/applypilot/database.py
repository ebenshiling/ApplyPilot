"""ApplyPilot database layer: schema, migrations, stats, and connection helpers.

Single source of truth for the jobs table schema. All columns from every
pipeline stage are created up front so any stage can run independently
without migration ordering issues.
"""

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from applypilot.config import DB_PATH

# Thread-local connection storage — each thread gets its own connection
# (required for SQLite thread safety with parallel workers)
_local = threading.local()


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Get a thread-local cached SQLite connection with WAL mode enabled.

    Each thread gets its own connection (required for SQLite thread safety).
    Connections are cached and reused within the same thread.

    Args:
        db_path: Override the default DB_PATH. Useful for testing.

    Returns:
        sqlite3.Connection configured with WAL mode and row factory.
    """
    path = str(db_path or DB_PATH)

    if not hasattr(_local, "connections"):
        _local.connections = {}

    conn = _local.connections.get(path)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except sqlite3.ProgrammingError:
            pass

    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    _local.connections[path] = conn
    return conn


def close_connection(db_path: Path | str | None = None) -> None:
    """Close the cached connection for the current thread."""
    path = str(db_path or DB_PATH)
    if hasattr(_local, "connections"):
        conn = _local.connections.pop(path, None)
        if conn is not None:
            conn.close()


def init_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Create the full jobs table with all columns from every pipeline stage.

    This is idempotent -- safe to call on every startup. Uses CREATE TABLE IF NOT EXISTS
    so it won't destroy existing data.

    Schema columns by stage:
      - Discovery:  url, title, salary, description, location, site, strategy, discovered_at
      - Enrichment: full_description, application_url, detail_scraped_at, detail_error
      - Scoring:    fit_score, score_reasoning, scored_at
      - Tailoring:  tailored_resume_path, tailored_at, tailor_attempts
      - Cover:      cover_letter_path, cover_letter_at, cover_attempts
      - Apply:      applied_at, apply_status, apply_error, apply_attempts,
                   agent_id, last_attempted_at, apply_duration_ms, apply_task_id,
                   verification_confidence

    Args:
        db_path: Override the default DB_PATH.

    Returns:
        sqlite3.Connection with the schema initialized.
    """
    path = db_path or DB_PATH

    # Ensure parent directory exists
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            -- Discovery stage (smart_extract / job_search)
            url                   TEXT PRIMARY KEY,
            title                 TEXT,
            search_query          TEXT,
            salary                TEXT,
            description           TEXT,
            location              TEXT,
            site                  TEXT,
            strategy              TEXT,
            discovered_at         TEXT,

            -- Enrichment stage (detail_scraper)
            full_description      TEXT,
            application_url       TEXT,
            detail_scraped_at     TEXT,
            detail_error          TEXT,

            -- Scoring stage (job_scorer)
            fit_score             INTEGER,
            score_confidence      REAL,
            score_reasoning       TEXT,
            scored_at             TEXT,

            -- Tailoring stage (resume tailor)
            tailored_resume_path  TEXT,
            tailored_at           TEXT,
            tailor_attempts       INTEGER DEFAULT 0,

            -- Cover letter stage
            cover_letter_path     TEXT,
            cover_letter_at       TEXT,
            cover_attempts        INTEGER DEFAULT 0,

            -- Application stage
            applied_at            TEXT,
            apply_status          TEXT,
            apply_error           TEXT,
            apply_attempts        INTEGER DEFAULT 0,
            agent_id              TEXT,
            last_attempted_at     TEXT,
            apply_duration_ms     INTEGER,
            apply_task_id         TEXT,
            verification_confidence TEXT
        )
    """)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS blocked_urls (
            prefix      TEXT PRIMARY KEY,
            reason      TEXT,
            created_at  TEXT
        )
        """
    )
    conn.commit()

    # Run migrations for any columns added after initial schema
    ensure_columns(conn)

    return conn


def normalize_url(url: str | None) -> str:
    """Normalize a URL for prefix-based blocking.

    Strips query string, fragment, and trailing slashes.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        p = urlsplit(raw)
        if p.scheme and p.netloc:
            path = (p.path or "").strip()
            if path != "/":
                path = path.rstrip("/")
            base = urlunsplit((p.scheme.lower(), p.netloc.lower(), path, "", "")).strip()
            return base
    except Exception:
        pass
    base = raw.split("?")[0].split("#")[0].strip().rstrip("/")
    return base


def find_existing_job_url(conn: sqlite3.Connection, url: str | None) -> str | None:
    """Best-effort duplicate matcher using canonical URL + apply URL variants."""
    base = normalize_url(url)
    if not base:
        return None

    exact = [base]
    if not base.endswith("/"):
        exact.append(base + "/")
    else:
        exact.append(base.rstrip("/"))

    likes: list[str] = []
    for e in exact:
        if e:
            likes.append(e + "?%")
            likes.append(e + "#%")

    ph_exact = ",".join("?" for _ in exact)
    row = conn.execute(
        f"SELECT url FROM jobs WHERE url IN ({ph_exact}) OR application_url IN ({ph_exact}) LIMIT 1",
        tuple(exact + exact),
    ).fetchone()
    if row and row[0]:
        return str(row[0])

    for lp in likes:
        r = conn.execute(
            "SELECT url FROM jobs WHERE url LIKE ? OR application_url LIKE ? LIMIT 1",
            (lp, lp),
        ).fetchone()
        if r and r[0]:
            return str(r[0])
    return None


def add_blocked_url(
    prefix: str,
    reason: str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Add a blocked URL prefix (best-effort; idempotent)."""
    if conn is None:
        conn = get_connection()
    p = normalize_url(prefix)
    if not p:
        return False
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO blocked_urls (prefix, reason, created_at) VALUES (?, ?, ?)",
            (p, (reason or "user")[:200], now),
        )
        return True
    except Exception:
        return False


def get_blocked_url_prefixes(conn: sqlite3.Connection | None = None) -> list[str]:
    """Return blocked URL prefixes."""
    if conn is None:
        conn = get_connection()
    rows = conn.execute("SELECT prefix FROM blocked_urls").fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def is_url_blocked(
    url: str | None,
    *,
    conn: sqlite3.Connection | None = None,
    blocked_prefixes: list[str] | None = None,
) -> bool:
    """Check whether a URL is blocked by prefix."""
    base = normalize_url(url)
    if not base:
        return False

    if blocked_prefixes is not None:
        b = base.lower()
        for p in blocked_prefixes:
            if not p:
                continue
            if b.startswith(p.lower()):
                return True
        return False

    if conn is None:
        conn = get_connection()
    # SQLite-friendly prefix match.
    row = conn.execute(
        "SELECT 1 FROM blocked_urls WHERE ? LIKE prefix || '%' LIMIT 1",
        (base,),
    ).fetchone()
    return bool(row)


def block_job_by_id(job_id: int, reason: str = "user_deleted") -> int:
    """Archive a job (mark skipped) and block its URLs."""
    conn = get_connection()
    row = conn.execute(
        "SELECT url, application_url FROM jobs WHERE rowid = ?",
        (job_id,),
    ).fetchone()
    if not row:
        return 0

    conn.execute("BEGIN IMMEDIATE")
    try:
        add_blocked_url(str(row[0] or ""), reason, conn=conn)
        add_blocked_url(str(row[1] or ""), reason, conn=conn)
        conn.execute(
            """
            UPDATE jobs
               SET apply_status = 'skipped',
                   apply_error = ?,
                   apply_attempts = 99,
                   agent_id = NULL
             WHERE rowid = ?
            """,
            (reason[:200], job_id),
        )
        conn.commit()
        return 1
    except Exception:
        conn.rollback()
        raise


# Complete column registry: column_name -> SQL type with optional default.
# This is the single source of truth. Adding a column here is all that's needed
# for it to appear in both new databases and migrated ones.
_ALL_COLUMNS: dict[str, str] = {
    # Discovery
    "url": "TEXT PRIMARY KEY",
    "title": "TEXT",
    "search_query": "TEXT",
    "salary": "TEXT",
    "description": "TEXT",
    "location": "TEXT",
    "site": "TEXT",
    "strategy": "TEXT",
    "discovered_at": "TEXT",
    # Enrichment
    "full_description": "TEXT",
    "application_url": "TEXT",
    "detail_scraped_at": "TEXT",
    "detail_error": "TEXT",
    # Scoring
    "fit_score": "INTEGER",
    "score_confidence": "REAL",
    "score_reasoning": "TEXT",
    "scored_at": "TEXT",
    # Tailoring
    "tailored_resume_path": "TEXT",
    "tailored_at": "TEXT",
    "tailor_attempts": "INTEGER DEFAULT 0",
    # Cover letter
    "cover_letter_path": "TEXT",
    "cover_letter_at": "TEXT",
    "cover_attempts": "INTEGER DEFAULT 0",
    # Application
    "applied_at": "TEXT",
    "apply_status": "TEXT",
    "apply_error": "TEXT",
    "apply_attempts": "INTEGER DEFAULT 0",
    "agent_id": "TEXT",
    "last_attempted_at": "TEXT",
    "apply_duration_ms": "INTEGER",
    "apply_task_id": "TEXT",
    "verification_confidence": "TEXT",
}


def ensure_columns(conn: sqlite3.Connection | None = None) -> list[str]:
    """Add any missing columns to the jobs table (forward migration).

    Reads the current table schema via PRAGMA table_info and compares against
    the full column registry. Any missing columns are added with ALTER TABLE.

    This makes it safe to upgrade the database from any previous version --
    columns are only added, never removed or renamed.

    Args:
        conn: Database connection. Uses get_connection() if None.

    Returns:
        List of column names that were added (empty if schema was already current).
    """
    if conn is None:
        conn = get_connection()

    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    added = []

    for col, dtype in _ALL_COLUMNS.items():
        if col not in existing:
            # PRIMARY KEY columns can't be added via ALTER TABLE, but url
            # is always created with the table itself so this is safe
            if "PRIMARY KEY" in dtype:
                continue
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {dtype}")
            added.append(col)

    if added:
        conn.commit()

    return added


def get_stats(conn: sqlite3.Connection | None = None) -> dict:
    """Return job counts by pipeline stage.

    Provides a snapshot of how many jobs are at each stage, useful for
    dashboard display and pipeline progress tracking.

    Args:
        conn: Database connection. Uses get_connection() if None.

    Returns:
        Dictionary with keys:
            total, by_site, pending_detail, with_description,
            scored, unscored, tailored, untailored_eligible,
            with_cover_letter, applied, score_distribution
    """
    if conn is None:
        conn = get_connection()

    stats: dict = {}

    # Total jobs
    stats["total"] = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    # By site breakdown
    rows = conn.execute("SELECT site, COUNT(*) as cnt FROM jobs GROUP BY site ORDER BY cnt DESC").fetchall()
    stats["by_site"] = [(row[0], row[1]) for row in rows]

    # Enrichment stage
    stats["pending_detail"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE detail_scraped_at IS NULL").fetchone()[0]

    stats["with_description"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE full_description IS NOT NULL").fetchone()[
        0
    ]

    stats["detail_errors"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE detail_error IS NOT NULL").fetchone()[0]

    # Scoring stage
    stats["scored"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL").fetchone()[0]

    stats["unscored"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE full_description IS NOT NULL AND fit_score IS NULL"
    ).fetchone()[0]

    # Score distribution
    dist_rows = conn.execute(
        "SELECT fit_score, COUNT(*) as cnt FROM jobs "
        "WHERE fit_score IS NOT NULL "
        "GROUP BY fit_score ORDER BY fit_score DESC"
    ).fetchall()
    stats["score_distribution"] = [(row[0], row[1]) for row in dist_rows]

    # Tailoring stage
    stats["tailored"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL").fetchone()[0]

    stats["untailored_eligible"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE fit_score >= 7 AND full_description IS NOT NULL "
        "AND tailored_resume_path IS NULL "
        "AND COALESCE(apply_status, '') != 'skipped'"
    ).fetchone()[0]

    stats["tailor_exhausted"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE COALESCE(tailor_attempts, 0) >= 5 AND tailored_resume_path IS NULL"
    ).fetchone()[0]

    # Cover letter stage
    stats["with_cover_letter"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE cover_letter_path IS NOT NULL"
    ).fetchone()[0]

    stats["cover_exhausted"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE COALESCE(cover_attempts, 0) >= 5 "
        "AND (cover_letter_path IS NULL OR cover_letter_path = '')"
    ).fetchone()[0]

    # Application stage
    stats["applied"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE applied_at IS NOT NULL").fetchone()[0]

    stats["apply_errors"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE apply_error IS NOT NULL").fetchone()[0]

    stats["ready_to_apply"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE tailored_resume_path IS NOT NULL "
        "AND applied_at IS NULL "
        "AND application_url IS NOT NULL"
    ).fetchone()[0]

    return stats


def store_jobs(conn: sqlite3.Connection, jobs: list[dict], site: str, strategy: str) -> tuple[int, int]:
    """Store discovered jobs, skipping duplicates by URL.

    Args:
        conn: Database connection.
        jobs: List of job dicts with keys: url, title, salary, description, location.
        site: Source site name (e.g. "RemoteOK", "Dice").
        strategy: Extraction strategy used (e.g. "json_ld", "api_response", "css_selectors").

    Returns:
        Tuple of (new_count, duplicate_count).
    """
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0

    for job in jobs:
        url = job.get("url")
        if not url:
            continue
        canonical = normalize_url(str(url))
        if is_url_blocked(canonical or url, conn=conn):
            existing += 1
            continue
        dup_url = find_existing_job_url(conn, canonical or url)
        if dup_url:
            existing += 1
            sq = job.get("search_query")
            if sq:
                try:
                    conn.execute(
                        "UPDATE jobs SET search_query = COALESCE(NULLIF(search_query, ''), ?) WHERE url = ?",
                        (sq, dup_url),
                    )
                except Exception:
                    pass
            continue
        try:
            conn.execute(
                "INSERT INTO jobs (url, title, search_query, salary, description, location, site, strategy, discovered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    canonical or url,
                    job.get("title"),
                    job.get("search_query"),
                    job.get("salary"),
                    job.get("description"),
                    job.get("location"),
                    site,
                    strategy,
                    now,
                ),
            )
            new += 1
        except sqlite3.IntegrityError:
            existing += 1

    conn.commit()
    return new, existing


def get_jobs_by_stage(
    conn: sqlite3.Connection | None = None, stage: str = "discovered", min_score: int | None = None, limit: int = 100
) -> list[dict]:
    """Fetch jobs filtered by pipeline stage.

    Args:
        conn: Database connection. Uses get_connection() if None.
        stage: One of "discovered", "enriched", "scored", "tailored", "applied".
        min_score: Minimum fit_score filter (only relevant for scored+ stages).
        limit: Maximum number of rows to return.

    Returns:
        List of job dicts.
    """
    if conn is None:
        conn = get_connection()

    conditions = {
        "discovered": "1=1",
        "pending_detail": "detail_scraped_at IS NULL",
        "enriched": "full_description IS NOT NULL",
        "pending_score": "full_description IS NOT NULL AND fit_score IS NULL",
        "scored": "fit_score IS NOT NULL",
        "pending_tailor": (
            "fit_score >= ? AND full_description IS NOT NULL "
            "AND tailored_resume_path IS NULL AND COALESCE(tailor_attempts, 0) < 5"
        ),
        "tailored": "tailored_resume_path IS NOT NULL",
        "pending_apply": ("tailored_resume_path IS NOT NULL AND applied_at IS NULL AND application_url IS NOT NULL"),
        "applied": "applied_at IS NOT NULL",
    }

    where = conditions.get(stage, "1=1")
    params: list = []

    if "?" in where and min_score is not None:
        params.append(min_score)
    elif "?" in where:
        params.append(7)  # default min_score

    if min_score is not None and "fit_score" not in where and stage in ("scored", "tailored", "applied"):
        where += " AND fit_score >= ?"
        params.append(min_score)

    query = f"SELECT * FROM jobs WHERE {where} ORDER BY fit_score DESC NULLS LAST, discovered_at DESC"
    if limit > 0:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()

    # Convert sqlite3.Row objects to dicts
    if rows:
        columns = rows[0].keys()
        return [dict(zip(columns, row)) for row in rows]
    return []
