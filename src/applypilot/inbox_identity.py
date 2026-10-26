"""Stable delivery identities and conservative, non-destructive legacy reconciliation."""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import datetime


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", html.unescape(str(text or "")))).strip()


def message_id(value) -> str | None:
    value = str(value or "").strip()
    return value if len(value) <= 1000 and re.fullmatch(r"<?[^\s<>]+@[^\s<>]+>?", value) else None


def content_signature(row: dict) -> str:
    sender = str(row.get("sender_address") or "").casefold()
    return hashlib.sha256((sender + "\n" + normalize(row.get("subject", "")).casefold() + "\n" + normalize(row.get("body", ""))).encode()).hexdigest()


def canonical_id(conn, ident: int) -> int:
    seen = set()
    while ident not in seen:
        seen.add(ident)
        row = conn.execute("SELECT duplicate_of FROM cc_messages WHERE id=?", (ident,)).fetchone()
        if not row or not row[0]:
            return ident
        ident = int(row[0])
    raise ValueError("Invalid duplicate identity chain")


def bind_identity(conn, ident: int, source: str, source_id: str | None) -> None:
    if not source_id or source == "manual":
        return
    ident = canonical_id(conn, ident)
    existing = conn.execute("SELECT message_id FROM cc_message_identities WHERE provider=? AND source_id=?", (source, source_id)).fetchone()
    if existing and existing[0] == ident:
        return
    if existing and canonical_id(conn, existing[0]) != ident:
        raise ValueError("Conflicting message identity")
    conn.execute("INSERT INTO cc_message_identities(provider,source_id,message_id) VALUES(?,?,?) ON CONFLICT(provider,source_id) DO UPDATE SET message_id=excluded.message_id", (source, source_id, ident))


def native_keys(conn, row: dict) -> set[tuple]:
    keys = {(r[0], r[1]) for r in conn.execute("SELECT provider,source_id FROM cc_message_identities WHERE message_id=?", (row["id"],))}
    if row.get("source") != "manual" and row.get("source_id"):
        keys.add((row["source"], row["source_id"]))
    return keys


def same_delivery_metadata(a: dict, b: dict) -> bool:
    if not a.get("sender_address") or a.get("sender_address", "").casefold() != b.get("sender_address", "").casefold():
        return False
    if normalize(a.get("subject", "")).casefold() != normalize(b.get("subject", "")).casefold():
        return False
    if a.get("received_at") and b.get("received_at"):
        try:
            return datetime.fromisoformat(a["received_at"].replace("Z", "+00:00")) == datetime.fromisoformat(b["received_at"].replace("Z", "+00:00"))
        except ValueError:
            return False
    return True


def same_legacy_content(legacy: dict, native: dict) -> bool:
    if not same_delivery_metadata(legacy, native):
        return False
    old = normalize(legacy.get("body", ""))
    new = normalize(native.get("body", ""))
    if old == new:
        return True
    if len(old) < 80:
        return False  # Short generic notices do not identify a legacy delivery reliably.
    # Remove only parser-added parenthesized URLs absent from the original text.
    def remove_added(match):
        return match.group(0) if normalize(match.group(1)) in old else ""
    without_inserted_links = re.sub(r"\s*\(\s*(https?://[^\s<>]+)\s*\)", remove_added, new)
    return old == normalize(without_inserted_links)


def _priority(row: dict) -> tuple:
    decision = {"accepted": 6, "ignored": 5, "dismissed": 4}.get(row.get("status"), 1)
    return decision, int(row.get("classification_source") == "manual"), int(row.get("analysis_status") == "done"), -row["id"]


def _merge(conn, first: dict, second: dict) -> bool:
    if first.get("analysis_status") == "running" or second.get("analysis_status") == "running":
        return False
    if first["status"] == second["status"] == "accepted" and (first.get("job_url"), first.get("proposed_state")) != (second.get("job_url"), second.get("proposed_state")):
        return False
    keep, copy = sorted([first, second], key=_priority, reverse=True)
    conn.execute("UPDATE cc_messages SET duplicate_of=? WHERE id=?", (keep["id"], copy["id"]))
    conn.execute("UPDATE cc_message_identities SET message_id=? WHERE message_id=?", (keep["id"], copy["id"]))
    for row in (first, second):
        bind_identity(conn, keep["id"], row.get("source", "manual"), row.get("source_id"))
    conn.execute("UPDATE cc_messages SET received_at=COALESCE(received_at,?),thread_id=COALESCE(thread_id,?),rfc_message_id=COALESCE(rfc_message_id,?),source=CASE WHEN source='manual' AND ?!='manual' THEN ? ELSE source END WHERE id=?", (copy.get("received_at"), copy.get("thread_id"), copy.get("rfc_message_id"), copy.get("source", "manual"), copy.get("source", "manual"), keep["id"]))
    return True


def reconcile(conn) -> int:
    """Called within schema/import transactions; retains original records and decisions."""
    conn.execute("CREATE TABLE IF NOT EXISTS cc_message_identities(provider TEXT NOT NULL,source_id TEXT NOT NULL,message_id INTEGER NOT NULL,PRIMARY KEY(provider,source_id))")
    conn.execute("CREATE INDEX IF NOT EXISTS cc_identity_message ON cc_message_identities(message_id)")
    for row in conn.execute("SELECT * FROM cc_messages WHERE source_id IS NOT NULL AND source_id!=''").fetchall():
        item = dict(row)
        bind_identity(conn, canonical_id(conn, item["id"]), item["source"], item["source_id"])
    rows = [dict(r) for r in conn.execute("SELECT * FROM cc_messages WHERE duplicate_of IS NULL")]
    groups = {}
    for row in rows:
        if row.get("sender_address"):
            groups.setdefault((row["sender_address"], normalize(row["subject"]).casefold()), []).append(row)
    merged = 0
    for group in groups.values():
        natives = [r for r in group if native_keys(conn, r)]
        legacy = [r for r in group if not native_keys(conn, r)]
        for old in legacy:
            matches = [new for new in natives if same_legacy_content(old, new)]
            if len(matches) != 1:
                continue
            new = matches[0]
            if sum(same_legacy_content(other, new) for other in legacy) != 1:
                continue
            if canonical_id(conn, old["id"]) != old["id"] or canonical_id(conn, new["id"]) != new["id"]:
                continue
            merged += int(_merge(conn, old, new))
    return merged


def find_existing(conn, incoming: dict, digest: str) -> int | None:
    source, source_id = incoming["source"], incoming.get("source_id")
    if source_id and source != "manual":
        alias = conn.execute("SELECT message_id FROM cc_message_identities WHERE provider=? AND source_id=?", (source, source_id)).fetchone()
        if alias:
            return canonical_id(conn, alias[0])
        native = conn.execute("SELECT id FROM cc_messages WHERE source=? AND source_id=?", (source, source_id)).fetchone()
        if native:
            return canonical_id(conn, native[0])
    else:
        exact = conn.execute("SELECT id FROM cc_messages WHERE digest=?", (digest,)).fetchone()
        if exact:
            return canonical_id(conn, exact[0])
    rows = [dict(r) for r in conn.execute("SELECT * FROM cc_messages WHERE duplicate_of IS NULL AND sender_address=?", (incoming["sender_address"],))] if incoming["sender_address"] else []
    if source_id and source != "manual":
        same_rfc = [r for r in rows if incoming.get("rfc_message_id") and r.get("rfc_message_id") == incoming["rfc_message_id"] and same_delivery_metadata(r, incoming) and normalize(r["body"]) == normalize(incoming["body"])]
        if len(same_rfc) == 1:
            return same_rfc[0]["id"]
        # Different known native identities are different deliveries, even with identical text.
        matches = [r for r in rows if not native_keys(conn, r) and same_legacy_content(r, incoming)]
        other_deliveries = [r for r in rows if native_keys(conn, r) and same_legacy_content(r, incoming)]
        return matches[0]["id"] if len(matches) == 1 and not other_deliveries else None
    matches = [r for r in rows if same_delivery_metadata(r, incoming) and normalize(r["body"]) == normalize(incoming["body"])]
    return matches[0]["id"] if len(matches) == 1 else None
