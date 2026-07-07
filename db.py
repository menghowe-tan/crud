"""SQLite data layer for the pastebin app.

The database file defaults to ``items.db`` in the working directory and can be
overridden with the ``PASTEBIN_DB`` environment variable (used by tests).
Timestamps are stored as ISO-8601 strings in UTC.
"""

import os
import sqlite3
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0,
    file_name TEXT,
    file_type TEXT,
    file_data BLOB,
    triage TEXT,
    triaged_at TEXT
);
"""

# Columns added after the initial schema; init_db() backfills them via ALTER TABLE.
_MIGRATED_COLUMNS = (
    ("file_name", "TEXT"), ("file_type", "TEXT"), ("file_data", "BLOB"),
    ("triage", "TEXT"), ("triaged_at", "TEXT"),
)


def _db_path() -> str:
    return os.environ.get("PASTEBIN_DB", "items.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def init_db() -> None:
    with _connect() as conn:
        conn.execute(_SCHEMA)
        # Migrate databases created before newer columns existed.
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(items)")}
        for name, decl in _MIGRATED_COLUMNS:
            if name not in cols:
                conn.execute(f"ALTER TABLE items ADD COLUMN {name} {decl}")


def create_item(
    content: str,
    expires_at: datetime,
    file_name: str | None = None,
    file_type: str | None = None,
    file_data: bytes | None = None,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO items (content, created_at, expires_at, file_name, file_type, file_data)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (content, now_utc().isoformat(), expires_at.isoformat(),
             file_name, file_type, file_data),
        )
        return cur.lastrowid


def get_items() -> list[dict]:
    """Return all items, newest first."""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM items ORDER BY id DESC").fetchall()
        return [dict(row) for row in rows]


def get_item(item_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None


def update_item(item_id: int, content: str, expires_at: datetime) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE items SET content = ?, expires_at = ? WHERE id = ?",
            (content, expires_at.isoformat(), item_id),
        )


def set_attachment(
    item_id: int,
    file_name: str | None,
    file_type: str | None,
    file_data: bytes | None,
) -> None:
    """Replace the item's attachment; pass all-None to remove it."""
    with _connect() as conn:
        conn.execute(
            "UPDATE items SET file_name = ?, file_type = ?, file_data = ? WHERE id = ?",
            (file_name, file_type, file_data, item_id),
        )


def set_triage(item_id: int, text: str | None) -> None:
    """Store the agent's triage note on the item; pass None to clear it."""
    with _connect() as conn:
        conn.execute(
            "UPDATE items SET triage = ?, triaged_at = ? WHERE id = ?",
            (text, now_utc().isoformat() if text is not None else None, item_id),
        )


def set_processed(item_id: int, flag: bool) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE items SET processed = ? WHERE id = ?",
            (1 if flag else 0, item_id),
        )


def delete_item(item_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))


def is_expired(item: dict, now: datetime | None = None) -> bool:
    now = now or now_utc()
    return datetime.fromisoformat(item["expires_at"]) < now


def status_of(item: dict, now: datetime | None = None) -> str:
    """Derived status. Processed wins over expired; expired wins over active."""
    if item["processed"]:
        return "Processed"
    if is_expired(item, now):
        return "Expired"
    return "Active"
