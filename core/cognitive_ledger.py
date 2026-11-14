"""Async SQL state manager for the 7 core tables, SQLite WAL, and FTS5 search."""

from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

_FTS_OPERATORS = {"AND", "OR", "NOT", "NEAR"}


def sanitize_fts_query(query: str) -> str | None:
    """Turn raw user text into quoted FTS5 phrase terms; None means empty."""
    tokens = [
        token
        for token in re.findall(r"\w+", query, flags=re.UNICODE)
        if token.upper() not in _FTS_OPERATORS
    ]
    if not tokens:
        return None
    return " ".join(f'"{token}"' for token in tokens)

_TABLES = {
    "core_directives": (
        "directive_id",
        {"directive_type", "content", "updated_at"},
    ),
    "semantic_memory": (
        "rule_id",
        {
            "category",
            "heuristic_text",
            "confidence_score",
            "last_used_at",
            "is_archived",
        },
    ),
    "ledger_threads": (
        "thread_id",
        {
            "title",
            "status",
            "origin",
            "priority_score",
            "is_human_endorsed",
            "expected_output",
            "created_at",
        },
    ),
    "ledger_entries": (
        "entry_id",
        {"thread_id", "tag", "content", "token_count", "is_archived", "created_at"},
    ),
    "atelier_discoveries": (
        "discovery_id",
        {
            "topic_category",
            "content_summary",
            "source_url",
            "relevance_to_user",
            "extracted_at",
        },
    ),
    "activity_logs": (
        "log_id",
        {
            "thread_id",
            "source",
            "initiator",
            "reasoning",
            "budget_bucket",
            "tokens_used",
            "cost_usd",
            "created_at",
        },
    ),
    "api_usage_metrics": (
        "metric_id",
        {
            "provider",
            "model_name",
            "window_minute",
            "requests_count",
            "tokens_count",
        },
    ),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS core_directives (
    directive_id TEXT PRIMARY KEY,
    directive_type TEXT NOT NULL CHECK(directive_type IN ('VISION', 'IMMUTABLE_PERSONA', 'SYSTEM_ANATOMY')),
    content TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS semantic_memory (
    rule_id TEXT PRIMARY KEY,
    category TEXT NOT NULL CHECK(category IN ('KOMUNIKASI', 'TEKNIS', 'PREFERENSI_USER')),
    heuristic_text TEXT NOT NULL,
    confidence_score INTEGER NOT NULL DEFAULT 50 CHECK(confidence_score >= 0 AND confidence_score <= 100),
    last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ledger_threads (
    thread_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('Active', 'Paused', 'Completed', 'Archived')),
    origin TEXT NOT NULL CHECK(origin IN ('HUMAN', 'AGENT')),
    priority_score INTEGER NOT NULL,
    is_human_endorsed INTEGER NOT NULL DEFAULT 0,
    expected_output TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    entry_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES ledger_threads(thread_id) ON DELETE CASCADE,
    tag TEXT NOT NULL CHECK(tag IN ('FAKTA', 'TINDAKAN', 'PEMIKIRAN')),
    content TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS ledger_entries_fts USING fts5(
    entry_id UNINDEXED,
    content
);

CREATE TRIGGER IF NOT EXISTS ledger_entries_ai AFTER INSERT ON ledger_entries BEGIN
    INSERT INTO ledger_entries_fts(entry_id, content) VALUES (new.entry_id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS ledger_entries_ad AFTER DELETE ON ledger_entries BEGIN
    DELETE FROM ledger_entries_fts WHERE entry_id = old.entry_id;
END;

CREATE TRIGGER IF NOT EXISTS ledger_entries_au AFTER UPDATE OF content ON ledger_entries BEGIN
    UPDATE ledger_entries_fts SET content = new.content WHERE entry_id = old.entry_id;
END;

CREATE TABLE IF NOT EXISTS atelier_discoveries (
    discovery_id TEXT PRIMARY KEY,
    topic_category TEXT NOT NULL,
    content_summary TEXT NOT NULL,
    source_url TEXT NOT NULL,
    relevance_to_user TEXT NOT NULL,
    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS atelier_discoveries_fts USING fts5(
    discovery_id UNINDEXED,
    topic_category,
    content_summary,
    relevance_to_user
);

CREATE TRIGGER IF NOT EXISTS atelier_discoveries_ai AFTER INSERT ON atelier_discoveries BEGIN
    INSERT INTO atelier_discoveries_fts(discovery_id, topic_category, content_summary, relevance_to_user)
    VALUES (new.discovery_id, new.topic_category, new.content_summary, new.relevance_to_user);
END;

CREATE TRIGGER IF NOT EXISTS atelier_discoveries_ad AFTER DELETE ON atelier_discoveries BEGIN
    DELETE FROM atelier_discoveries_fts WHERE discovery_id = old.discovery_id;
END;

CREATE TRIGGER IF NOT EXISTS atelier_discoveries_au
AFTER UPDATE OF topic_category, content_summary, relevance_to_user ON atelier_discoveries BEGIN
    UPDATE atelier_discoveries_fts
    SET topic_category = new.topic_category,
        content_summary = new.content_summary,
        relevance_to_user = new.relevance_to_user
    WHERE discovery_id = old.discovery_id;
END;

CREATE TABLE IF NOT EXISTS activity_logs (
    log_id TEXT PRIMARY KEY,
    thread_id TEXT NULL REFERENCES ledger_threads(thread_id),
    source TEXT NOT NULL CHECK(source IN ('HUMAN', 'AGENT')),
    initiator TEXT NOT NULL CHECK(initiator IN ('Telegram', 'Inner_Monologue', 'Healer_Node', 'Sleep_Mode', 'Webhook')),
    reasoning TEXT NOT NULL,
    budget_bucket TEXT NOT NULL CHECK(budget_bucket IN ('CORE_70', 'ATELIER_30', 'HUMAN_EXEMPT')),
    tokens_used INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS api_usage_metrics (
    metric_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    window_minute TEXT NOT NULL,
    requests_count INTEGER NOT NULL DEFAULT 0,
    tokens_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Retained newest N seen update ids; older rows are pruned on claim.
CREATE TABLE IF NOT EXISTS processed_updates (
    update_id INTEGER PRIMARY KEY,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

KILL_SWITCH_KEY = "kill_switch"
PROCESSED_UPDATES_RETENTION = 1000


class CognitiveLedger:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self.processed_update_retention = PROCESSED_UPDATES_RETENTION

    @asynccontextmanager
    async def _connect(self):
        connection = await aiosqlite.connect(self._db_path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys=ON;")
        await connection.execute("PRAGMA busy_timeout=5000;")
        try:
            yield connection
        finally:
            await connection.close()

    async def initialize(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(self._db_path)
        try:
            await (await connection.execute("PRAGMA journal_mode=WAL;")).fetchone()
            await connection.execute("PRAGMA synchronous=NORMAL;")
            await connection.executescript(_SCHEMA)
            await connection.commit()
        finally:
            await connection.close()

    async def ping(self) -> bool:
        try:
            async with self._connect() as connection:
                await (await connection.execute("SELECT 1")).fetchone()
            return True
        except Exception:
            return False

    async def get_journal_mode(self) -> str:
        async with self._connect() as connection:
            cursor = await connection.execute("PRAGMA journal_mode;")
            row = await cursor.fetchone()
            return row[0].lower()

    def _check(self, table: str, fields, allow_primary_key: bool = False) -> str:
        if table not in _TABLES:
            raise ValueError(f"unknown table: {table}")
        primary_key, columns = _TABLES[table]
        allowed = columns | {primary_key}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown columns for {table}: {sorted(unknown)}")
        if not allow_primary_key and primary_key in fields:
            raise ValueError(f"cannot write primary key {primary_key} directly")
        return primary_key

    async def create(self, table: str, **fields) -> str:
        primary_key = self._check(table, fields)
        fields = {primary_key: str(uuid.uuid4()), **fields}
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        async with self._connect() as connection:
            await connection.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                tuple(fields.values()),
            )
            await connection.commit()
        return fields[primary_key]

    async def read(self, table: str, record_id: str) -> dict | None:
        primary_key = self._check(table, {})
        async with self._connect() as connection:
            cursor = await connection.execute(
                f"SELECT * FROM {table} WHERE {primary_key} = ?", (record_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def update(self, table: str, record_id: str, **fields) -> bool:
        if not fields:
            raise ValueError("update requires at least one column")
        primary_key = self._check(table, fields)
        assignments = ", ".join(f"{column} = ?" for column in fields)
        async with self._connect() as connection:
            cursor = await connection.execute(
                f"UPDATE {table} SET {assignments} WHERE {primary_key} = ?",
                (*fields.values(), record_id),
            )
            await connection.commit()
            return cursor.rowcount > 0

    async def delete(self, table: str, record_id: str) -> bool:
        primary_key = self._check(table, {})
        async with self._connect() as connection:
            cursor = await connection.execute(
                f"DELETE FROM {table} WHERE {primary_key} = ?", (record_id,)
            )
            await connection.commit()
            return cursor.rowcount > 0

    async def create_thread(
        self,
        title: str,
        origin: str,
        priority_score: int | None = None,
        status: str = "Active",
    ) -> str:
        if origin not in {"HUMAN", "AGENT"}:
            raise ValueError(f"invalid thread origin: {origin}")
        if status not in {"Active", "Paused", "Completed", "Archived"}:
            raise ValueError(f"invalid thread status: {status}")
        if priority_score is None:
            priority_score = 100 if origin == "HUMAN" else 10
        return await self.create(
            "ledger_threads",
            title=title,
            status=status,
            origin=origin,
            priority_score=priority_score,
        )

    async def get_next_active_thread(self) -> dict | None:
        async with self._connect() as connection:
            cursor = await connection.execute(
                """
                SELECT * FROM ledger_threads
                WHERE status = 'Active'
                ORDER BY priority_score DESC, created_at ASC, rowid ASC
                LIMIT 1
                """
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def endorse_thread(
        self,
        thread_id: str,
        priority_score: int = 150,
        expected_output: str | None = None,
    ) -> dict | None:
        async with self._connect() as connection:
            cursor = await connection.execute(
                """
                UPDATE ledger_threads
                SET is_human_endorsed = 1, priority_score = ?, expected_output = ?
                WHERE thread_id = ?
                """,
                (priority_score, expected_output, thread_id),
            )
            await connection.commit()
            if cursor.rowcount == 0:
                return None
            row = await (
                await connection.execute(
                    "SELECT * FROM ledger_threads WHERE thread_id = ?", (thread_id,)
                )
            ).fetchone()
            return dict(row)

    async def search_ledger_entries(self, query: str) -> list[dict]:
        match = sanitize_fts_query(query)
        if match is None:
            return []
        async with self._connect() as connection:
            cursor = await connection.execute(
                """
                SELECT e.* FROM ledger_entries e
                JOIN ledger_entries_fts f ON f.entry_id = e.entry_id
                WHERE ledger_entries_fts MATCH ?
                ORDER BY e.created_at DESC
                """,
                (match,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def search_discoveries(self, query: str) -> list[dict]:
        match = sanitize_fts_query(query)
        if match is None:
            return []
        async with self._connect() as connection:
            cursor = await connection.execute(
                """
                SELECT d.* FROM atelier_discoveries d
                JOIN atelier_discoveries_fts f ON f.discovery_id = d.discovery_id
                WHERE atelier_discoveries_fts MATCH ?
                ORDER BY d.extracted_at DESC
                """,
                (match,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def list_core_directives(self) -> list[dict]:
        async with self._connect() as connection:
            cursor = await connection.execute(
                "SELECT * FROM core_directives ORDER BY updated_at ASC, rowid ASC"
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def list_semantic_memories(self, include_archived: bool = False) -> list[dict]:
        clause = "" if include_archived else "WHERE is_archived = 0"
        async with self._connect() as connection:
            cursor = await connection.execute(
                f"""
                SELECT * FROM semantic_memory
                {clause}
                ORDER BY confidence_score DESC, rowid ASC
                """
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def list_ledger_entries(
        self,
        thread_id: str,
        limit: int = 20,
        tags: tuple[str, ...] | None = None,
    ) -> list[dict]:
        """Last `limit` entries for a thread, returned in chronological order."""
        if tags:
            unknown = set(tags) - {"FAKTA", "TINDAKAN", "PEMIKIRAN"}
            if unknown:
                raise ValueError(f"unknown entry tags: {sorted(unknown)}")
        clause = ""
        params: list = [thread_id]
        if tags:
            clause = f" AND tag IN ({', '.join('?' for _ in tags)})"
            params.extend(tags)
        params.append(limit)
        async with self._connect() as connection:
            cursor = await connection.execute(
                f"""
                SELECT * FROM ledger_entries
                WHERE thread_id = ?{clause}
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                tuple(params),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
            rows.reverse()
            return rows

    async def list_oldest_ledger_entries(self, thread_id: str, limit: int = 20) -> list[dict]:
        async with self._connect() as connection:
            cursor = await connection.execute(
                """
                SELECT * FROM ledger_entries
                WHERE thread_id = ?
                ORDER BY created_at ASC, rowid ASC
                LIMIT ?
                """,
                (thread_id, limit),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def list_ledger_entries_since(self, thread_id: str, since: datetime) -> list[dict]:
        cutoff = since
        if cutoff.tzinfo is not None:
            cutoff = cutoff.astimezone(timezone.utc).replace(tzinfo=None)
        async with self._connect() as connection:
            cursor = await connection.execute(
                """
                SELECT * FROM ledger_entries
                WHERE thread_id = ? AND created_at >= ?
                ORDER BY created_at ASC, rowid ASC
                """,
                (thread_id, cutoff.strftime("%Y-%m-%d %H:%M:%S")),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def list_threads(self, status: str | None = None) -> list[dict]:
        clause = "" if status is None else "WHERE status = ?"
        params: tuple = () if status is None else (status,)
        async with self._connect() as connection:
            cursor = await connection.execute(
                f"""
                SELECT * FROM ledger_threads
                {clause}
                ORDER BY priority_score DESC, created_at ASC, rowid ASC
                """,
                params,
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def list_activity_logs(self, limit: int = 50) -> list[dict]:
        async with self._connect() as connection:
            cursor = await connection.execute(
                """
                SELECT * FROM activity_logs
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def pause_active_threads(self) -> int:
        async with self._connect() as connection:
            cursor = await connection.execute(
                "UPDATE ledger_threads SET status = 'Paused' WHERE status = 'Active'"
            )
            await connection.commit()
            return cursor.rowcount

    async def unpause_threads(self) -> int:
        async with self._connect() as connection:
            cursor = await connection.execute(
                "UPDATE ledger_threads SET status = 'Active' WHERE status = 'Paused'"
            )
            await connection.commit()
            return cursor.rowcount

    async def replace_oldest_entries(
        self, thread_id: str, limit: int, summary: str, token_count: int
    ) -> bool:
        """Insert the summary and delete the oldest entries in one transaction."""
        async with self._connect() as connection:
            cursor = await connection.execute(
                """
                SELECT entry_id FROM ledger_entries
                WHERE thread_id = ?
                ORDER BY created_at ASC, rowid ASC
                LIMIT ?
                """,
                (thread_id, limit),
            )
            entry_ids = [row["entry_id"] for row in await cursor.fetchall()]
            if not entry_ids:
                return False
            await connection.execute(
                """
                INSERT INTO ledger_entries
                    (entry_id, thread_id, tag, content, token_count)
                VALUES (?, ?, 'FAKTA', ?, ?)
                """,
                (str(uuid.uuid4()), thread_id, summary, token_count),
            )
            await connection.executemany(
                "DELETE FROM ledger_entries WHERE entry_id = ?",
                [(entry_id,) for entry_id in entry_ids],
            )
            await connection.commit()
            return True

    async def claim_telegram_update(self, update_id: int) -> bool:
        """Reserve an update id once; False means it was already processed."""
        async with self._connect() as connection:
            cursor = await connection.execute(
                "INSERT OR IGNORE INTO processed_updates (update_id) VALUES (?)",
                (update_id,),
            )
            await connection.commit()
            claimed = cursor.rowcount > 0
        await self.prune_processed_updates()
        return claimed

    async def prune_processed_updates(self, keep: int | None = None) -> int:
        """Keep only the newest N update ids; older ids may replay after N."""
        limit = self.processed_update_retention if keep is None else keep
        if limit < 0:
            raise ValueError("keep must not be negative")
        async with self._connect() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM processed_updates WHERE update_id NOT IN (
                    SELECT update_id FROM processed_updates
                    ORDER BY update_id DESC LIMIT ?
                )
                """,
                (limit,),
            )
            await connection.commit()
            return cursor.rowcount

    async def release_telegram_update(self, update_id: int) -> None:
        async with self._connect() as connection:
            await connection.execute(
                "DELETE FROM processed_updates WHERE update_id = ?", (update_id,)
            )
            await connection.commit()

    async def set_system_state(self, key: str, value: str) -> None:
        async with self._connect() as connection:
            await connection.execute(
                """
                INSERT INTO system_state (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE
                SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                """,
                (key, value),
            )
            await connection.commit()

    async def get_system_state(self, key: str) -> str | None:
        async with self._connect() as connection:
            cursor = await connection.execute(
                "SELECT value FROM system_state WHERE key = ?", (key,)
            )
            row = await cursor.fetchone()
            return row["value"] if row else None