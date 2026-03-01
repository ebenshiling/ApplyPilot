"""Local multi-user account store for dashboard-serve.

This module keeps account/session data in a separate auth DB and assigns
each user an isolated ApplyPilot workspace directory.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from applypilot.config import APP_DIR
from applypilot.database import init_db


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root_dir(root: Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    env = (os.environ.get("APPLYPILOT_MULTI_ROOT", "") or "").strip()
    if env:
        return Path(env).expanduser()
    return APP_DIR.parent / ".applypilot-users"


def users_db_path(root: Path | None = None) -> Path:
    return _root_dir(root) / "auth.db"


def workspaces_root(root: Path | None = None) -> Path:
    return _root_dir(root) / "workspaces"


def normalize_username(username: str) -> str:
    u = (username or "").strip().lower()
    u = re.sub(r"[^a-z0-9._-]+", "-", u)
    u = re.sub(r"-+", "-", u).strip("-._")
    return u[:40]


def user_workspace(username: str, root: Path | None = None) -> Path:
    u = normalize_username(username)
    if not u:
        raise ValueError("invalid username")
    return workspaces_root(root) / u


def _connect(root: Path | None = None) -> sqlite3.Connection:
    path = users_db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_auth_db(root: Path | None = None) -> None:
    conn = _connect(root)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name     TEXT NOT NULL,
                phone         TEXT,
                city          TEXT,
                country       TEXT,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL,
                is_active     INTEGER NOT NULL DEFAULT 1,
                is_admin      INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_audit_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at      TEXT NOT NULL,
                actor_user_id   INTEGER,
                actor_username  TEXT,
                target_user_id  INTEGER,
                target_username TEXT,
                action          TEXT NOT NULL,
                detail          TEXT
            )
            """
        )
        # Migration-safe: add columns on older auth DBs.
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "is_active" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        if "is_admin" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")

        # Ensure there is always at least one admin account.
        admin_count = int(conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0] or 0)
        if admin_count <= 0:
            first = conn.execute("SELECT id FROM users ORDER BY created_at ASC, id ASC LIMIT 1").fetchone()
            if first is not None:
                conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (int(first[0]),))
        conn.commit()
    finally:
        conn.close()


def _password_hash(password: str, salt: bytes | None = None, rounds: int = 120_000) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algo, rounds_s, salt_hex, digest_hex = (encoded or "").split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except Exception:
        return False
    got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return secrets.compare_digest(got, expected)


def _session_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _row_to_user(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "username": str(row["username"] or ""),
        "email": str(row["email"] or ""),
        "full_name": str(row["full_name"] or ""),
        "phone": str(row["phone"] or ""),
        "city": str(row["city"] or ""),
        "country": str(row["country"] or ""),
        "created_at": str(row["created_at"] or ""),
        "is_active": bool(row["is_active"]),
        "is_admin": bool(row["is_admin"]),
    }


def _user_identity(conn: sqlite3.Connection, user_id: int | None) -> tuple[int | None, str | None]:
    if user_id is None or int(user_id) <= 0:
        return None, None
    uid = int(user_id)
    row = conn.execute("SELECT username FROM users WHERE id = ?", (uid,)).fetchone()
    if row is None:
        return uid, None
    return uid, str(row["username"] or "") or None


def _log_admin_action(
    conn: sqlite3.Connection,
    *,
    action: str,
    actor_user_id: int | None,
    target_user_id: int | None,
    target_username: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    actor_id, actor_username = _user_identity(conn, actor_user_id)
    target_id = int(target_user_id) if target_user_id is not None else None
    target_name = (target_username or "").strip() or None
    if target_name is None and target_id is not None:
        _, target_name = _user_identity(conn, target_id)
    try:
        import json

        detail_json = json.dumps(detail or {}, ensure_ascii=True, sort_keys=True)
    except Exception:
        detail_json = "{}"

    conn.execute(
        """
        INSERT INTO admin_audit_log (
            created_at, actor_user_id, actor_username, target_user_id, target_username, action, detail
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (_now_iso(), actor_id, actor_username, target_id, target_name, str(action or "").strip(), detail_json),
    )


def create_user(
    *,
    username: str,
    email: str,
    password: str,
    full_name: str,
    phone: str = "",
    city: str = "",
    country: str = "",
    root: Path | None = None,
) -> dict[str, Any]:
    init_auth_db(root)
    u = normalize_username(username)
    e = (email or "").strip().lower()
    n = (full_name or "").strip()
    p = (password or "").strip()
    if len(u) < 3:
        raise ValueError("username must be at least 3 characters")
    if "@" not in e or "." not in e:
        raise ValueError("invalid email")
    if len(p) < 8:
        raise ValueError("password must be at least 8 characters")
    if not n:
        raise ValueError("full name is required")

    conn = _connect(root)
    try:
        now = _now_iso()
        conn.execute("BEGIN IMMEDIATE")
        # First registered account becomes admin.
        existing = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] or 0)
        is_admin = 1 if existing == 0 else 0
        conn.execute(
            """
            INSERT INTO users (username, email, password_hash, full_name, phone, city, country, created_at, updated_at, is_admin)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                u,
                e,
                _password_hash(p),
                n,
                (phone or "").strip(),
                (city or "").strip(),
                (country or "").strip(),
                now,
                now,
                is_admin,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE username = ?", (u,)).fetchone()
        user = _row_to_user(row)
        if user is None:
            raise RuntimeError("failed to create user")
        return user
    except sqlite3.IntegrityError as e2:
        msg = str(e2).lower()
        if "users.username" in msg or "username" in msg:
            raise ValueError("username already exists")
        if "users.email" in msg or "email" in msg:
            raise ValueError("email already exists")
        raise ValueError("user already exists")
    finally:
        conn.close()


def authenticate(login: str, password: str, root: Path | None = None) -> dict[str, Any] | None:
    ident = (login or "").strip().lower()
    if not ident or not password:
        return None
    conn = _connect(root)
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE (username = ? OR email = ?) AND is_active = 1 LIMIT 1",
            (ident, ident),
        ).fetchone()
        if row is None:
            return None
        if not _verify_password(password, str(row["password_hash"] or "")):
            return None
        return _row_to_user(row)
    finally:
        conn.close()


def create_session(user_id: int, *, root: Path | None = None, ttl_days: int = 30) -> str:
    init_auth_db(root)
    token = secrets.token_urlsafe(36)
    th = _session_hash(token)
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=max(1, int(ttl_days or 30)))
    conn = _connect(root)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (th, int(user_id), now.isoformat(), exp.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def get_user_by_session(token: str, root: Path | None = None) -> dict[str, Any] | None:
    t = (token or "").strip()
    if not t:
        return None
    now = _now_iso()
    conn = _connect(root)
    try:
        row = conn.execute(
            """
            SELECT u.*
              FROM sessions s
              JOIN users u ON u.id = s.user_id
             WHERE s.token_hash = ?
               AND s.expires_at > ?
               AND u.is_active = 1
             LIMIT 1
            """,
            (_session_hash(t), now),
        ).fetchone()
        return _row_to_user(row)
    finally:
        conn.close()


def revoke_session(token: str, root: Path | None = None) -> None:
    t = (token or "").strip()
    if not t:
        return
    conn = _connect(root)
    try:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_session_hash(t),))
        conn.commit()
    finally:
        conn.close()


def ensure_workspace_for_user(username: str, root: Path | None = None) -> Path:
    ws = user_workspace(username, root)
    ws.mkdir(parents=True, exist_ok=True)
    init_db(ws / "applypilot.db")
    return ws


def list_users(root: Path | None = None) -> list[dict[str, Any]]:
    init_auth_db(root)
    conn = _connect(root)
    try:
        rows = conn.execute(
            """
            SELECT id, username, email, full_name, phone, city, country, created_at, updated_at, is_active, is_admin
              FROM users
             ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": int(r["id"]),
                    "username": str(r["username"] or ""),
                    "email": str(r["email"] or ""),
                    "full_name": str(r["full_name"] or ""),
                    "phone": str(r["phone"] or ""),
                    "city": str(r["city"] or ""),
                    "country": str(r["country"] or ""),
                    "created_at": str(r["created_at"] or ""),
                    "updated_at": str(r["updated_at"] or ""),
                    "is_active": bool(r["is_active"]),
                    "is_admin": bool(r["is_admin"]),
                }
            )
        return out
    finally:
        conn.close()


def set_user_active(
    user_id: int,
    *,
    is_active: bool,
    root: Path | None = None,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    init_auth_db(root)
    uid = int(user_id)
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if row is None:
            raise ValueError("user not found")

        target_is_admin = bool(row["is_admin"])
        if not is_active and target_is_admin:
            other_admins = int(
                conn.execute(
                    "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1 AND id != ?",
                    (uid,),
                ).fetchone()[0]
                or 0
            )
            if other_admins <= 0:
                raise ValueError("cannot disable the last active admin")

        conn.execute(
            "UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?",
            (1 if is_active else 0, _now_iso(), uid),
        )
        if not is_active:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
        _log_admin_action(
            conn,
            action="user_enable" if is_active else "user_disable",
            actor_user_id=actor_user_id,
            target_user_id=uid,
            detail={"is_active": bool(is_active)},
        )
        conn.commit()
        out = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        user = _row_to_user(out)
        if user is None:
            raise ValueError("user not found")
        return user
    finally:
        conn.close()


def reset_user_password(
    user_id: int,
    new_password: str,
    root: Path | None = None,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    init_auth_db(root)
    uid = int(user_id)
    p = (new_password or "").strip()
    if len(p) < 8:
        raise ValueError("password must be at least 8 characters")
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if row is None:
            raise ValueError("user not found")
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (_password_hash(p), _now_iso(), uid),
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
        _log_admin_action(
            conn,
            action="user_reset_password",
            actor_user_id=actor_user_id,
            target_user_id=uid,
            detail={"session_revoked": True},
        )
        conn.commit()
        out = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        user = _row_to_user(out)
        if user is None:
            raise ValueError("user not found")
        return user
    finally:
        conn.close()


def set_user_admin(
    user_id: int,
    *,
    is_admin: bool,
    root: Path | None = None,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    init_auth_db(root)
    uid = int(user_id)
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if row is None:
            raise ValueError("user not found")

        cur_is_admin = bool(row["is_admin"])
        target_is_active = bool(row["is_active"])

        if cur_is_admin and not is_admin and target_is_active:
            other_admins = int(
                conn.execute(
                    "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1 AND id != ?",
                    (uid,),
                ).fetchone()[0]
                or 0
            )
            if other_admins <= 0:
                raise ValueError("cannot demote the last active admin")

        if cur_is_admin != is_admin:
            conn.execute(
                "UPDATE users SET is_admin = ?, updated_at = ? WHERE id = ?", (1 if is_admin else 0, _now_iso(), uid)
            )

        _log_admin_action(
            conn,
            action="user_promote_admin" if is_admin else "user_demote_admin",
            actor_user_id=actor_user_id,
            target_user_id=uid,
            detail={"is_admin": bool(is_admin)},
        )
        conn.commit()
        out = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        user = _row_to_user(out)
        if user is None:
            raise ValueError("user not found")
        return user
    finally:
        conn.close()


def delete_user(
    user_id: int,
    *,
    root: Path | None = None,
    purge_workspace: bool = True,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    init_auth_db(root)
    uid = int(user_id)
    conn = _connect(root)
    deleted_user: dict[str, Any] | None = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if row is None:
            raise ValueError("user not found")

        target_is_admin = bool(row["is_admin"])
        if target_is_admin:
            other_admins = int(
                conn.execute(
                    "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1 AND id != ?",
                    (uid,),
                ).fetchone()[0]
                or 0
            )
            if other_admins <= 0:
                raise ValueError("cannot delete the last active admin")

        deleted_user = _row_to_user(row)
        deleted_username = str((deleted_user or {}).get("username") or "")
        _log_admin_action(
            conn,
            action="user_delete",
            actor_user_id=actor_user_id,
            target_user_id=uid,
            target_username=deleted_username,
            detail={"purge_workspace": bool(purge_workspace)},
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))
        conn.commit()
    finally:
        conn.close()

    if deleted_user is None:
        raise ValueError("user not found")

    if purge_workspace:
        try:
            ws = user_workspace(str(deleted_user.get("username") or ""), root)
            if ws.exists() and ws.is_dir():
                shutil.rmtree(ws)
        except Exception:
            pass

    return deleted_user


def list_admin_audit_logs(root: Path | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
    init_auth_db(root)
    lim = max(1, min(1000, int(limit or 200)))
    conn = _connect(root)
    try:
        rows = conn.execute(
            """
            SELECT id, created_at, actor_user_id, actor_username, target_user_id, target_username, action, detail
              FROM admin_audit_log
             ORDER BY id DESC
             LIMIT ?
            """,
            (lim,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": int(r["id"]),
                    "created_at": str(r["created_at"] or ""),
                    "actor_user_id": int(r["actor_user_id"]) if r["actor_user_id"] is not None else None,
                    "actor_username": str(r["actor_username"] or ""),
                    "target_user_id": int(r["target_user_id"]) if r["target_user_id"] is not None else None,
                    "target_username": str(r["target_username"] or ""),
                    "action": str(r["action"] or ""),
                    "detail": str(r["detail"] or ""),
                }
            )
        return out
    finally:
        conn.close()
