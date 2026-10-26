"""Lifecycle-bound per-workspace scheduling and optional mailbox synchronization."""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_threads: dict[str, threading.Thread] = {}
_stop = threading.Event()
enabled = False


def start(runner, authorized=None) -> None:
    if not enabled:
        return
    key = str(runner._app_dir)
    with _lock:
        if key in _threads and _threads[key].is_alive():
            return
        _stop.clear()

        def work():
            from applypilot.database import close_connection, workspace_connection
            from applypilot.scheduler import tick
            from applypilot.mail_connectors import connections, sync

            last_mail = 0.0
            try:
                while not _stop.is_set() and runner._app_dir.exists():
                    if authorized is not None and not authorized():
                        break
                    conn = workspace_connection(runner._app_dir / "applypilot.db")
                    try:
                        tick(conn, runner)
                    except Exception:
                        conn.rollback()
                    if time.monotonic() - last_mail > 300:
                        last_mail = time.monotonic()
                        for mailbox in connections(conn):
                            if _stop.is_set():
                                break
                            try:
                                sync(conn, runner._app_dir, mailbox["provider"])
                            except Exception:
                                conn.rollback()
                                conn.execute("UPDATE cc_mail SET status='error',error='Sync unavailable; check authorization and provider configuration' WHERE provider=?", (mailbox["provider"],))
                                conn.commit()
                    _stop.wait(10)
            finally:
                close_connection(runner._app_dir / "applypilot.db")

        thread = threading.Thread(target=work, name="applypilot-workspace-services", daemon=True)
        _threads[key] = thread
        thread.start()


def stop() -> None:
    global enabled
    enabled = False
    _stop.set()
