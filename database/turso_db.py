"""Turso overflow storage for media metadata.

The bot keeps Telegram media payloads in Telegram channels. This module stores only
the searchable metadata that the MongoDB media index stores.
"""
import asyncio
import logging
from typing import Any, Iterable

from info import TURSO_AUTH_TOKEN, TURSO_DATABASE_URL

logger = logging.getLogger(__name__)
ENABLED = bool(TURSO_DATABASE_URL and TURSO_AUTH_TOKEN)

_COLUMNS = (
    "file_id", "file_unique_id", "file_ref", "file_name", "file_size",
    "file_type", "mime_type", "caption", "cover", "created_at",
)


def _connect():
    if not ENABLED:
        raise RuntimeError("Turso is not configured. Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN.")
    import turso_serverless

    return turso_serverless.connect(TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)


def _ensure_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media (
            file_id TEXT PRIMARY KEY,
            file_unique_id TEXT,
            file_ref TEXT,
            file_name TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            file_type TEXT,
            mime_type TEXT,
            caption TEXT,
            cover TEXT,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_file_unique_id ON media(file_unique_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_file_name ON media(file_name)")
    conn.commit()


def _run_sync(sql: str, params: Iterable[Any] = (), fetch: bool = False):
    conn = _connect()
    try:
        _ensure_schema(conn)
        cursor = conn.execute(sql, tuple(params))
        if fetch:
            rows = cursor.fetchall()
            names = [column[0] for column in (cursor.description or ())]
            result = [dict(zip(names, row)) for row in rows]
        else:
            result = cursor.rowcount
        conn.commit()
        return result
    finally:
        conn.close()


async def execute(sql: str, params: Iterable[Any] = ()):
    """Run a write statement without blocking the bot's asyncio event loop."""
    return await asyncio.to_thread(_run_sync, sql, params, False)


async def fetch_all(sql: str, params: Iterable[Any] = ()):
    """Run a read statement without blocking the bot's asyncio event loop."""
    return await asyncio.to_thread(_run_sync, sql, params, True)


async def is_duplicate(file_id: str, file_unique_id: str | None = None) -> bool:
    rows = await fetch_all(
        "SELECT file_id FROM media WHERE file_id = ? OR (? IS NOT NULL AND file_unique_id = ?) LIMIT 1",
        (file_id, file_unique_id, file_unique_id),
    )
    return bool(rows)


async def insert_media(record: dict[str, Any]):
    return await execute(
        """
        INSERT INTO media
        (file_id, file_unique_id, file_ref, file_name, file_size, file_type, mime_type, caption, cover)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.get("file_id"), record.get("file_unique_id"), record.get("file_ref"),
            record.get("file_name"), record.get("file_size"), record.get("file_type"),
            record.get("mime_type"), record.get("caption"), record.get("cover"),
        ),
    )


async def search_media(regex, search_terms, file_type=None, limit=1000):
    """Fetch likely candidates from Turso, then apply the bot's existing regex semantics."""
    terms = [str(term).strip().lower() for term in search_terms if str(term).strip()]
    if not terms:
        return []
    conditions = []
    params = []
    for term in terms:
        # Every supported bot query's regex starts with this first search term.
        first_word = term.split()[0]
        conditions.append("(LOWER(file_name) LIKE ? OR LOWER(COALESCE(caption, '')) LIKE ?)")
        wildcard = f"%{first_word}%"
        params.extend((wildcard, wildcard))
    where = "(" + " OR ".join(conditions) + ")"
    if file_type:
        where += " AND file_type = ?"
        params.append(file_type)
    rows = await fetch_all(
        f"SELECT {', '.join(_COLUMNS)} FROM media WHERE {where} ORDER BY created_at DESC LIMIT ?",
        (*params, int(limit)),
    )
    matched = []
    for row in rows:
        name = row.get("file_name") or ""
        caption = row.get("caption") or ""
        if regex.search(name) or regex.search(caption):
            matched.append(row)
    return matched


async def delete_file(file_id: str) -> int:
    return await execute("DELETE FROM media WHERE file_id = ?", (file_id,))


async def delete_matching(file_name: str, file_size: int, mime_type: str) -> int:
    return await execute(
        "DELETE FROM media WHERE file_name = ? AND file_size = ? AND mime_type IS ?",
        (file_name, file_size, mime_type),
    )


async def delete_all() -> int:
    return await execute("DELETE FROM media")


async def fetch_latest(limit: int):
    return await fetch_all(
        f"SELECT {', '.join(_COLUMNS)} FROM media ORDER BY created_at DESC LIMIT ?",
        (int(limit),),
    )
