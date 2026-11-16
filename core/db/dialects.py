"""Dialect-specific DDL: SQLite FTS5 or PostgreSQL tsvector/GIN."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_LEDGER_FTS_SQLITE = (
    """CREATE VIRTUAL TABLE IF NOT EXISTS ledger_entries_fts USING fts5(
        entry_id UNINDEXED,
        content
    )""",
    """CREATE TRIGGER IF NOT EXISTS ledger_entries_ai AFTER INSERT ON ledger_entries BEGIN
        INSERT INTO ledger_entries_fts(entry_id, content)
        VALUES (new.entry_id, new.content);
    END""",
    """CREATE TRIGGER IF NOT EXISTS ledger_entries_ad AFTER DELETE ON ledger_entries BEGIN
        DELETE FROM ledger_entries_fts WHERE entry_id = old.entry_id;
    END""",
    """CREATE TRIGGER IF NOT EXISTS ledger_entries_au
    AFTER UPDATE OF content ON ledger_entries BEGIN
        UPDATE ledger_entries_fts SET content = new.content
        WHERE entry_id = old.entry_id;
    END""",
)

_ATELIER_FTS_SQLITE = (
    """CREATE VIRTUAL TABLE IF NOT EXISTS atelier_discoveries_fts USING fts5(
        discovery_id UNINDEXED,
        topic_category,
        content_summary,
        relevance_to_user
    )""",
    """CREATE TRIGGER IF NOT EXISTS atelier_discoveries_ai
    AFTER INSERT ON atelier_discoveries BEGIN
        INSERT INTO atelier_discoveries_fts(
            discovery_id, topic_category, content_summary, relevance_to_user
        ) VALUES (
            new.discovery_id, new.topic_category,
            new.content_summary, new.relevance_to_user
        );
    END""",
    """CREATE TRIGGER IF NOT EXISTS atelier_discoveries_ad
    AFTER DELETE ON atelier_discoveries BEGIN
        DELETE FROM atelier_discoveries_fts WHERE discovery_id = old.discovery_id;
    END""",
    """CREATE TRIGGER IF NOT EXISTS atelier_discoveries_au
    AFTER UPDATE OF topic_category, content_summary, relevance_to_user
    ON atelier_discoveries BEGIN
        UPDATE atelier_discoveries_fts
        SET topic_category = new.topic_category,
            content_summary = new.content_summary,
            relevance_to_user = new.relevance_to_user
        WHERE discovery_id = old.discovery_id;
    END""",
)

_PG_FTS = (
    """ALTER TABLE ledger_entries ADD COLUMN IF NOT EXISTS content_tsv tsvector
       GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED""",
    """CREATE INDEX IF NOT EXISTS ix_ledger_entries_content_tsv
       ON ledger_entries USING gin(content_tsv)""",
    """ALTER TABLE atelier_discoveries ADD COLUMN IF NOT EXISTS search_tsv tsvector
       GENERATED ALWAYS AS (
           to_tsvector(
               'simple',
               coalesce(topic_category, '') || ' ' ||
               coalesce(content_summary, '') || ' ' ||
               coalesce(relevance_to_user, '')
           )
       ) STORED""",
    """CREATE INDEX IF NOT EXISTS ix_atelier_discoveries_search_tsv
       ON atelier_discoveries USING gin(search_tsv)""",
)


def search_statements(dialect_name: str) -> tuple[str, ...]:
    return (
        _LEDGER_FTS_SQLITE + _ATELIER_FTS_SQLITE
        if dialect_name == "sqlite"
        else _PG_FTS
    )


async def create_search_objects(connection: AsyncConnection) -> None:
    for statement in search_statements(connection.dialect.name):
        await connection.execute(text(statement))


def create_search_objects_sync(connection) -> None:
    for statement in search_statements(connection.dialect.name):
        connection.execute(text(statement))


# Columns added after the first release; old SQLite files are migrated in place.
_COLUMN_BACKFILL = {
    "core_directives": {"is_backup": "BOOLEAN NOT NULL DEFAULT 0"},
    "semantic_memory": {"is_backup": "BOOLEAN NOT NULL DEFAULT 0"},
    "ledger_threads": {"is_backup": "BOOLEAN NOT NULL DEFAULT 0"},
    "ledger_entries": {
        "is_backup": "BOOLEAN NOT NULL DEFAULT 0",
        "is_vectorized": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "atelier_discoveries": {
        "is_backup": "BOOLEAN NOT NULL DEFAULT 0",
        "is_vectorized": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "activity_logs": {"is_backup": "BOOLEAN NOT NULL DEFAULT 0"},
    "api_usage_metrics": {"is_backup": "BOOLEAN NOT NULL DEFAULT 0"},
    "system_state": {"is_backup": "BOOLEAN NOT NULL DEFAULT 0"},
    "processed_updates": {"is_backup": "BOOLEAN NOT NULL DEFAULT 0"},
    "budget_usage": {"is_backup": "BOOLEAN NOT NULL DEFAULT 0"},
}


def _missing_columns(table_info, columns: dict[str, str]) -> list[str]:
    names = {row[1] for row in table_info}
    return [name for name in columns if name not in names]


async def ensure_sqlite_columns(connection: AsyncConnection) -> None:
    """Add `is_backup`/`is_vectorized` to databases created before them."""
    if connection.dialect.name != "sqlite":
        return
    for table, columns in _COLUMN_BACKFILL.items():
        info = await connection.execute(text(f"PRAGMA table_info({table})"))
        for name in _missing_columns(info.fetchall(), columns):
            await connection.execute(
                text(
                    f"ALTER TABLE {table} ADD COLUMN {name} "
                    f"{columns[name]}"
                )
            )


def ensure_sqlite_columns_sync(connection) -> None:
    if connection.dialect.name != "sqlite":
        return
    for table, columns in _COLUMN_BACKFILL.items():
        info = connection.execute(text(f"PRAGMA table_info({table})"))
        for name in _missing_columns(info.fetchall(), columns):
            connection.execute(
                text(
                    f"ALTER TABLE {table} ADD COLUMN {name} "
                    f"{columns[name]}"
                )
            )

