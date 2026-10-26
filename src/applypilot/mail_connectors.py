"""Optional Gmail / Outlook OAuth connectors with an encrypted token vault.

Messages enter the evidence review inbox; connectors never infer a submission.
Application registration and credentials are supplied by the deployment owner.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet

from applypilot import command_center as cc

PROVIDERS = {
    "gmail": {"prefix": "GMAIL", "auth": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token", "scope": "https://www.googleapis.com/auth/gmail.readonly"},
    "outlook": {"prefix": "OUTLOOK", "auth": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize", "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token", "scope": "offline_access https://graph.microsoft.com/Mail.Read"},
}


def init(conn) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cc_mail (
            provider TEXT PRIMARY KEY, tokens BLOB NOT NULL, last_sync TEXT,
            status TEXT DEFAULT 'connected', error TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS cc_oauth (
            state TEXT PRIMARY KEY, provider TEXT NOT NULL, verifier TEXT NOT NULL,
            expires REAL NOT NULL
        );
    """)


def vault(app_dir: Path) -> Fernet:
    key_file = app_dir / ".connector-key"
    try:
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return Fernet(key_file.read_bytes())
    key = Fernet.generate_key()
    with os.fdopen(fd, "wb") as stream:
        stream.write(key)
    return Fernet(key)


def config(provider: str) -> tuple[dict, str, str, str]:
    if provider not in PROVIDERS:
        raise ValueError("Choose gmail or outlook")
    info = PROVIDERS[provider]
    client_id = os.environ.get(info["prefix"] + "_CLIENT_ID", "").strip()
    secret = os.environ.get(info["prefix"] + "_CLIENT_SECRET", "").strip()
    base = os.environ.get("APPLYPILOT_OAUTH_REDIRECT_BASE", "http://127.0.0.1:8765").rstrip("/")
    if not client_id or not secret:
        raise ValueError(f"Configure {info['prefix']}_CLIENT_ID and {info['prefix']}_CLIENT_SECRET on the server first")
    return info, client_id, secret, base + "/api/center/mail/callback"


def connect(conn, provider: str) -> str:
    init(conn)
    info, client_id, _, redirect = config(provider)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    conn.execute("DELETE FROM cc_oauth WHERE expires<?", (time.time(),))
    conn.execute("INSERT INTO cc_oauth(state,provider,verifier,expires) VALUES(?,?,?,?)", (state, provider, verifier, time.time() + 600))
    conn.commit()
    params = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": info["scope"], "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    if provider == "gmail":
        params.update(access_type="offline", prompt="consent")
    return info["auth"] + "?" + urlencode(params)


def callback(conn, app_dir: Path, state: str, code: str) -> None:
    init(conn)
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT * FROM cc_oauth WHERE state=? AND expires>?", (state, time.time())).fetchone()
    if not row:
        conn.rollback()
        raise ValueError("OAuth session expired or belongs to another workspace. Reconnect from Settings.")
    conn.execute("DELETE FROM cc_oauth WHERE state=?", (state,))
    conn.commit()
    info, client_id, secret, redirect = config(row["provider"])
    response = httpx.post(info["token"], data={"grant_type": "authorization_code", "client_id": client_id, "client_secret": secret, "redirect_uri": redirect, "code": code, "code_verifier": row["verifier"]}, timeout=30)
    response.raise_for_status()
    tokens = response.json()
    if not tokens.get("access_token"):
        raise ValueError("Provider did not return an access token")
    tokens["expires_at"] = time.time() + int(tokens.get("expires_in", 3600))
    encrypted = vault(app_dir).encrypt(json.dumps(tokens).encode())
    conn.execute("INSERT INTO cc_mail(provider,tokens) VALUES(?,?) ON CONFLICT(provider) DO UPDATE SET tokens=excluded.tokens,status='connected',error=''", (row["provider"], encrypted))
    conn.commit()


def connections(conn) -> list[dict]:
    init(conn)
    return [dict(row) for row in conn.execute("SELECT provider,last_sync,status,error FROM cc_mail")]


def access_token(conn, app_dir: Path, provider: str) -> str:
    row = conn.execute("SELECT tokens FROM cc_mail WHERE provider=?", (provider,)).fetchone()
    if not row:
        raise ValueError("Connect this mailbox first")
    tokens = json.loads(vault(app_dir).decrypt(bytes(row[0])))
    if tokens.get("expires_at", 0) > time.time() + 60:
        return tokens["access_token"]
    if not tokens.get("refresh_token"):
        raise ValueError("Mailbox authorization expired. Reconnect the mailbox.")
    info, client_id, secret, _ = config(provider)
    response = httpx.post(info["token"], data={"grant_type": "refresh_token", "client_id": client_id, "client_secret": secret, "refresh_token": tokens["refresh_token"]}, timeout=30)
    response.raise_for_status()
    new = response.json()
    tokens.update(new)
    tokens["expires_at"] = time.time() + int(new.get("expires_in", 3600))
    conn.execute("UPDATE cc_mail SET tokens=? WHERE provider=?", (vault(app_dir).encrypt(json.dumps(tokens).encode()), provider))
    conn.commit()
    return tokens["access_token"]


def html_text(content) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(content, "html.parser")
    for hidden in soup.find_all(["script", "style", "noscript"]):
        hidden.decompose()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if href.startswith(("https://", "http://")):
            anchor.append(" (" + href + ")")
    return soup.get_text(" ", strip=True)


def gmail_body(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        raw = payload["body"]["data"]
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", errors="replace")
    texts = [gmail_body(part) for part in payload.get("parts", [])]
    text = "\n".join(t for t in texts if t)
    if not text and payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        raw = payload["body"]["data"]
        text = html_text(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    return text


def sync(conn, app_dir: Path, provider: str) -> int:
    init(conn)
    token = access_token(conn, app_dir, provider)
    imported = 0
    with httpx.Client(headers={"Authorization": "Bearer " + token}, timeout=30) as client:
        if provider == "gmail":
            response = client.get("https://gmail.googleapis.com/gmail/v1/users/me/messages", params={"maxResults": 50, "q": "newer_than:90d (application OR interview OR assessment OR employment)"})
            response.raise_for_status()
            for message in response.json().get("messages", []):
                response = client.get("https://gmail.googleapis.com/gmail/v1/users/me/messages/" + message["id"], params={"format": "full"})
                response.raise_for_status()
                data = response.json()
                payload = data.get("payload", {})
                headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
                body = gmail_body(payload) or data.get("snippet", "")
                if body and headers.get("subject"):
                    received = None
                    try:
                        if data.get("internalDate"):
                            received = datetime.fromtimestamp(int(data["internalDate"]) / 1000, tz=timezone.utc).isoformat()
                        elif headers.get("date"):
                            timestamp = parsedate_to_datetime(headers["date"])
                            if timestamp.tzinfo:
                                received = timestamp.astimezone(timezone.utc).isoformat()
                    except (ValueError, TypeError, OverflowError):
                        pass
                    if not conn.execute("SELECT 1 FROM cc_mail WHERE provider=?", (provider,)).fetchone():
                        break
                    cc.import_message(conn, {"subject": headers["subject"], "body": body, "sender": headers.get("from", ""), "source": "gmail", "source_id": data.get("id") or message["id"], "thread_id": data.get("threadId"), "rfc_message_id": headers.get("message-id"), "received_at": received})
                    imported += 1
        else:
            response = client.get("https://graph.microsoft.com/v1.0/me/messages", params={"$top": 50, "$orderby": "receivedDateTime desc", "$select": "id,subject,body,from,receivedDateTime,conversationId,internetMessageId"})
            response.raise_for_status()
            for message in response.json().get("value", []):
                body = message.get("body", {}).get("content", "")
                if message.get("body", {}).get("contentType", "").lower() == "html":
                    body = html_text(body)
                if body and message.get("subject"):
                    if not conn.execute("SELECT 1 FROM cc_mail WHERE provider=?", (provider,)).fetchone():
                        break
                    cc.import_message(conn, {"subject": message["subject"], "body": body, "sender": message.get("from", {}).get("emailAddress", {}).get("address", ""), "source": "outlook", "source_id": message.get("id"), "thread_id": message.get("conversationId"), "rfc_message_id": message.get("internetMessageId"), "received_at": message.get("receivedDateTime")})
                    imported += 1
    conn.execute("UPDATE cc_mail SET last_sync=?,status='connected',error='' WHERE provider=?", (cc.now(), provider))
    conn.commit()
    return imported


def disconnect(conn, provider: str) -> None:
    init(conn)
    conn.execute("DELETE FROM cc_mail WHERE provider=?", (provider,))
    conn.execute("DELETE FROM cc_oauth WHERE provider=?", (provider,))
    conn.commit()
