"""Durable scheduled applications, executed by the existing per-workspace runner."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from applypilot import command_center as cc


def schedule(conn, job_id: int, when: str, mode: str) -> int:
    job = cc.get_job(conn, job_id)
    if mode not in {"prepare", "submit"}:
        raise ValueError("Schedule mode must be prepare or submit")
    parsed = datetime.fromisoformat(when.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Scheduled time must include a time zone")
    if parsed <= datetime.now(timezone.utc):
        raise ValueError("Choose a future schedule time")
    if job.get("applied_at") or job.get("apply_status") in cc.ACTIVE_STATES | {"submission_uncertain"}:
        raise ValueError("Only unsubmitted, inactive jobs can be scheduled")
    duplicate = conn.execute("SELECT 1 FROM cc_schedules WHERE job_url=? AND state IN ('queued','running')", (job["url"],)).fetchone()
    if duplicate:
        raise ValueError("This job already has a queued or running schedule")
    cur = conn.execute("INSERT INTO cc_schedules(job_url,run_at,mode,created_at) VALUES(?,?,?,?)", (job["url"], parsed.astimezone(timezone.utc).isoformat(), mode, cc.now()))
    cc.event(conn, job["url"], "schedule", f"{mode.title()} scheduled for {when}", source="manual")
    conn.commit()
    return int(cur.lastrowid)


def tick(conn, runner) -> None:
    """One workspace tick. A crashed running attempt is reviewed, never replayed."""
    status = runner.status()
    running = conn.execute("SELECT * FROM cc_schedules WHERE state='running'").fetchall()
    for row in running:
        if status.get("run_id") != row["run_id"]:
            conn.execute("UPDATE cc_schedules SET state='needs_review',detail='Runner restarted; reconcile submission before retrying' WHERE id=?", (row["id"],))
        elif not status.get("running") and not status.get("starting"):
            job = conn.execute("SELECT apply_status FROM jobs WHERE url=?", (row["job_url"],)).fetchone()
            success = job and job[0] in {"applied", "prepared"}
            conn.execute("UPDATE cc_schedules SET state=?,detail=? WHERE id=?", ("completed" if success else "needs_review", "Inspect application record for the outcome", row["id"]))
    conn.commit()
    if status.get("running") or status.get("starting"):
        return
    budget = max(0, int(os.environ.get("APPLYPILOT_SCHEDULE_DAILY_LIMIT", "20")))
    used = conn.execute("SELECT COUNT(*) FROM cc_schedules WHERE state IN ('running','completed','needs_review') AND run_at>=? AND run_at<=?", (cc.now()[:10], cc.now())).fetchone()[0]
    if used >= budget:
        return
    row = conn.execute("SELECT * FROM cc_schedules WHERE state='queued' AND run_at<=? ORDER BY run_at,id LIMIT 1", (cc.now(),)).fetchone()
    if not row:
        return
    from applypilot.apply.safety import require_live_execution, SubmissionContained
    try:
        require_live_execution()
    except SubmissionContained as exc:
        conn.execute("UPDATE cc_schedules SET state='needs_review',detail=? WHERE id=? AND state='queued'", (str(exc), row["id"]))
        cc.event(conn, row["job_url"], "schedule", str(exc), state="needs_review")
        conn.commit()
        return
    job = conn.execute("SELECT rowid AS id,* FROM jobs WHERE url=?", (row["job_url"],)).fetchone()
    hidden = conn.execute("SELECT visibility FROM cc_job_meta WHERE job_url=?", (row["job_url"],)).fetchone()
    if not job or job["applied_at"] or job["apply_status"] in cc.ACTIVE_STATES | {"submission_uncertain"} or cc.blocked(conn, row["job_url"], job["company"]) or (hidden and hidden[0] != "active") or not job["tailored_resume_path"]:
        conn.execute("UPDATE cc_schedules SET state='needs_review',detail='Job unavailable, blocked, active, uncertain, or lacks an approved CV' WHERE id=?", (row["id"],))
        conn.commit()
        return
    if cc.preference_exclusions(conn, dict(job)):
        conn.execute("UPDATE cc_schedules SET state='needs_review',detail='Application excluded by current preferences' WHERE id=?", (row["id"],))
        conn.commit()
        return
    listing = cc.check_listing(conn, job["id"])
    if listing["status"] != "reachable":
        conn.execute("UPDATE cc_schedules SET state='needs_review',detail=? WHERE id=?", (listing["detail"], row["id"]))
        conn.commit()
        return
    # Persist intent before launch. If the server crashes here, no automatic replay.
    conn.execute("UPDATE cc_schedules SET state='running' WHERE id=? AND state='queued'", (row["id"],))
    conn.commit()
    ok, result = runner.start({"stages": ["apply"], "application_target": row["job_url"], "engine": "llm", "submit": row["mode"] == "submit", "dry_run": False,
                               "country_code": job["country_code"], "campaign_id": job["campaign_id"] if job["country_code"] == "NZ" else None})
    conn.execute("UPDATE cc_schedules SET run_id=?,state=?,detail=? WHERE id=?", (result.get("run_id"), "running" if ok else "needs_review", result.get("error", "Started"), row["id"]))
    conn.commit()
