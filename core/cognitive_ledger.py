"""Async SQL state manager: portable Core schema, FTS, and backup flags."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, event, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from core.db.dialects import create_search_objects, ensure_sqlite_columns
from core.db.schema import metadata
from core.db.timestamps import utc_now_text

_FTS_OPERATORS = {"AND", "OR", "NOT", "NEAR"}


def sanitize_fts_query(query: str) -> str | None:
    """Turn raw user text into quoted FTS5 phrase terms; None means empty."""
    tokens = fts_terms(query)
    if not tokens:
        return None
    return " ".join(f'"{token}"' for token in tokens)


def fts_terms(query: str) -> list[str]:
    return [
        token
        for token in re.findall(r"\w+", query, flags=re.UNICODE)
        if token.upper() not in _FTS_OPERATORS
    ]


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
    "plugin_registry": (
        "plugin_name",
        {
            "tier_type",
            "description_summary",
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
            "attempt_count",
            "last_attempt_at",
        },
    ),
    "ledger_entries": (
        "entry_id",
        {
            "thread_id",
            "tag",
            "content",
            "token_count",
            "is_archived",
            "created_at",
            "is_vectorized",
        },
    ),
    "atelier_discoveries": (
        "discovery_id",
        {
            "topic_category",
            "content_summary",
            "source_url",
            "relevance_to_user",
            "extracted_at",
            "is_vectorized",
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
    "audit_logs": (
        "audit_id",
        {
            "trace_id",
            "step",
            "thread_id",
            "trigger",
            "trigger_ref",
            "origin",
            "model",
            "prompt",
            "prompt_tokens",
            "cot",
            "action",
            "action_target",
            "action_payload",
            "response",
            "response_tokens",
            "outcome",
            "error",
            "result_table",
            "result_id",
            "latency_ms",
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

KILL_SWITCH_KEY = "kill_switch"
RESUME_CUTOVER_KEY = "resume_cutover_v1"
PAUSE_REASON_KEY = "pause_reason"
PAUSE_UNTIL_KEY = "pause_until"
AGENT_PAUSE_REASON_KEY = "agent_pause_reason"
AGENT_PAUSE_UNTIL_KEY = "agent_pause_until"
BUDGET_RATIO_KEY = "budget_ratio_agent"
AGENT_FREETIME_KEY = "agent_freetime"
PROCESSED_UPDATES_RETENTION = 1000


def _sqlite_async_url(db_path: str | Path) -> str:
    return f"sqlite+aiosqlite:///{Path(db_path).resolve()}"


class CognitiveLedger:
    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        if engine is None:
            if db_path is None:
                raise ValueError("database path or engine is required")
            engine = create_async_engine(
                _sqlite_async_url(db_path), poolclass=NullPool
            )
        self._engine = engine
        self.processed_update_retention = PROCESSED_UPDATES_RETENTION
        if engine.dialect.name == "sqlite":

            @event.listens_for(engine.sync_engine, "connect")
            def _sqlite_pragmas(dbapi_connection, _record) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON;")
                cursor.execute("PRAGMA busy_timeout=5000;")
                cursor.close()

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def dialect(self) -> str:
        return self._engine.dialect.name

    def _table(self, name: str):
        return metadata.tables[name]

    def _insert(self, table_name: str):
        if self.dialect == "sqlite":
            return sqlite_insert(self._table(table_name))
        return pg_insert(self._table(table_name))

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

    async def initialize(self) -> None:
        if self.dialect == "sqlite":
            path = self._engine.url.database
            if path:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            async with self._engine.connect() as connection:
                await connection.execute(text("PRAGMA journal_mode=WAL;"))
                await connection.execute(text("PRAGMA synchronous=NORMAL;"))
                await connection.commit()
        async with self._engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
            await ensure_sqlite_columns(connection)
            await create_search_objects(connection)
            await self._apply_resume_cutover(connection)

    async def _apply_resume_cutover(self, connection) -> None:
        """Legacy Active threads were never resumable; close them once."""
        state = self._table("system_state")
        seen = await connection.execute(
            select(state.c.value).where(state.c.key == RESUME_CUTOVER_KEY)
        )
        if seen.scalar() is not None:
            return
        await connection.execute(
            update(self._table("ledger_threads"))
            .where(self._table("ledger_threads").c.status == "Active")
            .values(status="Completed", is_backup=False)
        )
        await connection.execute(
            self._insert("system_state").values(
                key=RESUME_CUTOVER_KEY,
                value=utc_now_text(),
                updated_at=utc_now_text(),
                # Local bookkeeping only: never export it as new snapshot data.
                is_backup=True,
            )
        )

    async def dispose(self) -> None:
        await self._engine.dispose()

    async def ping(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def get_journal_mode(self) -> str:
        if self.dialect != "sqlite":
            return self.dialect
        async with self._engine.connect() as connection:
            row = await connection.execute(text("PRAGMA journal_mode;"))
            return str(row.scalar()).lower()

    async def create(self, table: str, **fields) -> str:
        primary_key = self._check(table, fields)
        values = {primary_key: str(uuid.uuid4()), **fields, "is_backup": False}
        await self._ensure_timestamp(table, values)
        async with self._engine.begin() as connection:
            await connection.execute(self._table(table).insert().values(**values))
        return values[primary_key]

    async def _ensure_timestamp(self, table: str, values: dict) -> None:
        columns = self._table(table).c
        for name in ("created_at", "last_used_at", "updated_at", "extracted_at"):
            if name in columns and name not in values:
                values[name] = utc_now_text()

    async def read(self, table: str, record_id: str) -> dict | None:
        primary_key = self._check(table, {})
        async with self._engine.connect() as connection:
            row = await connection.execute(
                select(self._table(table)).where(
                    self._table(table).c[primary_key] == record_id
                )
            )
            found = row.mappings().first()
            return dict(found) if found else None

    async def update(self, table: str, record_id: str, **fields) -> bool:
        if not fields:
            raise ValueError("update requires at least one column")
        primary_key = self._check(table, fields)
        values = dict(fields)
        if "is_backup" in self._table(table).c:
            values["is_backup"] = False
        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(self._table(table))
                .where(self._table(table).c[primary_key] == record_id)
                .values(**values)
            )
            return result.rowcount > 0

    async def delete(self, table: str, record_id: str) -> bool:
        primary_key = self._check(table, {})
        async with self._engine.begin() as connection:
            result = await connection.execute(
                delete(self._table(table)).where(
                    self._table(table).c[primary_key] == record_id
                )
            )
            return result.rowcount > 0

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

    async def get_next_active_thread(
        self, origin: str | None = None, ready_before: str | None = None
    ) -> dict | None:
        table = self._table("ledger_threads")
        statement = select(table).where(table.c.status == "Active")
        if origin is not None:
            statement = statement.where(table.c.origin == origin)
        if ready_before is not None:
            # Only threads with a finished attempt are crash candidates; a
            # thread without one is either in flight or never started.
            statement = statement.where(
                table.c.last_attempt_at.is_not(None),
                table.c.last_attempt_at <= ready_before,
            )
        statement = statement.order_by(
            table.c.priority_score.desc(),
            table.c.created_at.asc(),
            table.c.thread_id.asc(),
        ).limit(1)
        async with self._engine.connect() as connection:
            row = await connection.execute(statement)
            found = row.mappings().first()
            return dict(found) if found else None

    async def record_thread_attempt(self, thread_id: str) -> bool:
        table = self._table("ledger_threads")
        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(table)
                .where(table.c.thread_id == thread_id)
                .values(
                    attempt_count=table.c.attempt_count + 1,
                    last_attempt_at=utc_now_text(),
                    is_backup=False,
                )
            )
            return result.rowcount > 0

    async def set_thread_status(self, thread_id: str, status: str) -> bool:
        if status not in {"Active", "Paused", "Completed", "Archived"}:
            raise ValueError(f"invalid thread status: {status}")
        table = self._table("ledger_threads")
        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(table)
                .where(table.c.thread_id == thread_id)
                .values(status=status, is_backup=False)
            )
            return result.rowcount > 0

    async def endorse_thread(
        self,
        thread_id: str,
        priority_score: int = 150,
        expected_output: str | None = None,
    ) -> dict | None:
        table = self._table("ledger_threads")
        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(table)
                .where(table.c.thread_id == thread_id)
                .values(
                    is_human_endorsed=1,
                    priority_score=priority_score,
                    expected_output=expected_output,
                    is_backup=False,
                )
            )
        return await self.read("ledger_threads", thread_id) if result.rowcount else None

    async def search_ledger_entries(self, query: str) -> list[dict]:
        match = sanitize_fts_query(query)
        if match is None:
            return []
        if self.dialect == "sqlite":
            statement = text(
                """
                SELECT e.* FROM ledger_entries e
                JOIN ledger_entries_fts f ON f.entry_id = e.entry_id
                WHERE ledger_entries_fts MATCH :match
                ORDER BY e.created_at DESC, e.entry_id DESC
                """
            )
            params = {"match": match}
        else:
            statement = text(
                """
                SELECT e.* FROM ledger_entries e
                WHERE e.content_tsv @@ plainto_tsquery('simple', :match)
                ORDER BY e.created_at DESC, e.entry_id DESC
                """
            )
            params = {"match": " ".join(fts_terms(query))}
        async with self._engine.connect() as connection:
            rows = await connection.execute(statement, params)
            return [dict(row) for row in rows.mappings().all()]

    async def search_discoveries(self, query: str) -> list[dict]:
        match = sanitize_fts_query(query)
        if match is None:
            return []
        if self.dialect == "sqlite":
            statement = text(
                """
                SELECT d.* FROM atelier_discoveries d
                JOIN atelier_discoveries_fts f ON f.discovery_id = d.discovery_id
                WHERE atelier_discoveries_fts MATCH :match
                ORDER BY d.extracted_at DESC, d.discovery_id DESC
                """
            )
            params = {"match": match}
        else:
            statement = text(
                """
                SELECT d.* FROM atelier_discoveries d
                WHERE d.search_tsv @@ plainto_tsquery('simple', :match)
                ORDER BY d.extracted_at DESC, d.discovery_id DESC
                """
            )
            params = {"match": " ".join(fts_terms(query))}
        async with self._engine.connect() as connection:
            rows = await connection.execute(statement, params)
            return [dict(row) for row in rows.mappings().all()]

    async def list_core_directives(self) -> list[dict]:
        table = self._table("core_directives")
        async with self._engine.connect() as connection:
            rows = await connection.execute(
                select(table).order_by(
                    table.c.updated_at.asc(), table.c.directive_id.asc()
                )
            )
            return [dict(row) for row in rows.mappings().all()]

    async def list_semantic_memories(self, include_archived: bool = False) -> list[dict]:
        table = self._table("semantic_memory")
        statement = select(table)
        if not include_archived:
            statement = statement.where(table.c.is_archived == 0)
        statement = statement.order_by(
            table.c.confidence_score.desc(), table.c.rule_id.asc()
        )
        async with self._engine.connect() as connection:
            rows = await connection.execute(statement)
            return [dict(row) for row in rows.mappings().all()]

    async def register_plugin(
        self, plugin_name: str, tier_type: str, description_summary: str
    ) -> None:
        """Upsert a discovered plugin; usage history is preserved on conflict."""
        if tier_type not in {"CORE", "ATELIER"}:
            raise ValueError(f"invalid tier_type: {tier_type}")
        table = self._table("plugin_registry")
        async with self._engine.begin() as connection:
            await connection.execute(
                self._insert("plugin_registry")
                .values(
                    plugin_name=plugin_name,
                    tier_type=tier_type,
                    description_summary=description_summary,
                    last_used_at=utc_now_text(),
                    is_archived=0,
                    is_backup=False,
                )
                .on_conflict_do_update(
                    index_elements=[table.c.plugin_name],
                    set_={
                        "tier_type": tier_type,
                        "description_summary": description_summary,
                        "is_archived": 0,
                        "is_backup": False,
                    },
                )
            )

    async def log_plugin_usage(self, plugin_name: str) -> bool:
        """Stamp last_used_at; False means the plugin was never registered."""
        table = self._table("plugin_registry")
        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(table)
                .where(table.c.plugin_name == plugin_name)
                .values(last_used_at=utc_now_text(), is_backup=False)
            )
            return result.rowcount > 0

    async def list_plugins(
        self, tier_type: str | None = None, include_archived: bool = False
    ) -> list[dict]:
        table = self._table("plugin_registry")
        statement = select(table)
        if tier_type is not None:
            statement = statement.where(table.c.tier_type == tier_type)
        if not include_archived:
            statement = statement.where(table.c.is_archived == 0)
        statement = statement.order_by(table.c.plugin_name.asc())
        async with self._engine.connect() as connection:
            rows = await connection.execute(statement)
            return [dict(row) for row in rows.mappings().all()]

    async def list_stale_plugins(self, cutoff: str) -> list[dict]:
        """Active ATELIER plugins untouched before `cutoff` (UTC text)."""
        table = self._table("plugin_registry")
        async with self._engine.connect() as connection:
            rows = await connection.execute(
                select(table).where(
                    table.c.tier_type == "ATELIER",
                    table.c.is_archived == 0,
                    table.c.last_used_at < cutoff,
                )
            )
            return [dict(row) for row in rows.mappings().all()]

    async def set_plugin_archived(
        self, plugin_name: str, archived: bool = True
    ) -> bool:
        table = self._table("plugin_registry")
        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(table)
                .where(table.c.plugin_name == plugin_name)
                .values(is_archived=1 if archived else 0, is_backup=False)
            )
            return result.rowcount > 0

    async def list_ledger_entries(
        self,
        thread_id: str,
        limit: int = 20,
        tags: tuple[str, ...] | None = None,
        only_unvectorized: bool = False,
    ) -> list[dict]:
        """Last `limit` entries for a thread, returned in chronological order."""
        if tags:
            unknown = set(tags) - {"FAKTA", "TINDAKAN", "PEMIKIRAN"}
            if unknown:
                raise ValueError(f"unknown entry tags: {sorted(unknown)}")
        table = self._table("ledger_entries")
        statement = select(table).where(table.c.thread_id == thread_id)
        if only_unvectorized:
            statement = statement.where(table.c.is_vectorized.is_(False))
        if tags:
            statement = statement.where(table.c.tag.in_(tags))
        statement = (
            statement.order_by(table.c.created_at.desc(), table.c.entry_id.desc())
            .limit(limit)
        )
        async with self._engine.connect() as connection:
            rows = [dict(row) for row in (await connection.execute(statement)).mappings()]
        rows.reverse()
        return rows

    async def list_oldest_ledger_entries(
        self, thread_id: str, limit: int = 20
    ) -> list[dict]:
        table = self._table("ledger_entries")
        async with self._engine.connect() as connection:
            rows = await connection.execute(
                select(table)
                .where(table.c.thread_id == thread_id)
                .order_by(table.c.created_at.asc(), table.c.entry_id.asc())
                .limit(limit)
            )
            return [dict(row) for row in rows.mappings().all()]

    async def list_ledger_entries_since(
        self, thread_id: str, since: datetime
    ) -> list[dict]:
        cutoff = since
        if cutoff.tzinfo is not None:
            cutoff = cutoff.astimezone(timezone.utc).replace(tzinfo=None)
        table = self._table("ledger_entries")
        async with self._engine.connect() as connection:
            rows = await connection.execute(
                select(table)
                .where(
                    table.c.thread_id == thread_id,
                    table.c.created_at >= cutoff.strftime("%Y-%m-%d %H:%M:%S"),
                )
                .order_by(table.c.created_at.asc(), table.c.entry_id.asc())
            )
            return [dict(row) for row in rows.mappings().all()]

    async def list_pending_vector_entries(
        self, older_than: str, limit: int = 500
    ) -> list[dict]:
        """Old entries whose parent thread is finished and that lack a vector."""
        entries = self._table("ledger_entries")
        threads = self._table("ledger_threads")
        statement = (
            select(entries)
            .join(threads, entries.c.thread_id == threads.c.thread_id)
            .where(
                entries.c.is_vectorized.is_(False),
                entries.c.created_at < older_than,
                threads.c.status.in_(("Completed", "Archived")),
            )
            .order_by(entries.c.created_at.asc(), entries.c.entry_id.asc())
            .limit(limit)
        )
        async with self._engine.connect() as connection:
            rows = await connection.execute(statement)
            return [dict(row) for row in rows.mappings().all()]

    async def list_pending_vector_discoveries(
        self, older_than: str, limit: int = 500
    ) -> list[dict]:
        table = self._table("atelier_discoveries")
        statement = (
            select(table)
            .where(
                table.c.is_vectorized.is_(False),
                table.c.extracted_at < older_than,
            )
            .order_by(table.c.extracted_at.asc(), table.c.discovery_id.asc())
            .limit(limit)
        )
        async with self._engine.connect() as connection:
            rows = await connection.execute(statement)
            return [dict(row) for row in rows.mappings().all()]

    async def mark_vectorized(self, table: str, ids: list[str]) -> int:
        if table not in {"ledger_entries", "atelier_discoveries"}:
            raise ValueError(f"table is not vectorized: {table}")
        if not ids:
            return 0
        target = self._table(table)
        primary_key = next(iter(target.primary_key.columns)).name
        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(target)
                .where(
                    target.c[primary_key].in_(ids),
                    target.c.is_vectorized.is_(False),
                )
                .values(is_vectorized=True)
            )
            return result.rowcount

    async def count_vectorized(self) -> int:
        total = 0
        async with self._engine.connect() as connection:
            for name in ("ledger_entries", "atelier_discoveries"):
                table = self._table(name)
                row = await connection.execute(
                    select(func.count())
                    .select_from(table)
                    .where(table.c.is_vectorized.is_(True))
                )
                total += int(row.scalar() or 0)
        return total

    async def reset_vectorized_flags(self) -> int:
        """Re-embed everything: Chroma lost its index but SQL still says embedded."""
        total = 0
        async with self._engine.begin() as connection:
            for name in ("ledger_entries", "atelier_discoveries"):
                table = self._table(name)
                result = await connection.execute(
                    update(table)
                    .where(table.c.is_vectorized.is_(True))
                    .values(is_vectorized=False, is_backup=False)
                )
                total += result.rowcount
        return total

    async def list_threads(self, status: str | None = None) -> list[dict]:
        table = self._table("ledger_threads")
        statement = select(table)
        if status is not None:
            statement = statement.where(table.c.status == status)
        statement = statement.order_by(
            table.c.priority_score.desc(),
            table.c.created_at.asc(),
            table.c.thread_id.asc(),
        )
        async with self._engine.connect() as connection:
            rows = await connection.execute(statement)
            return [dict(row) for row in rows.mappings().all()]

    async def list_activity_logs(self, limit: int = 50) -> list[dict]:
        table = self._table("activity_logs")
        async with self._engine.connect() as connection:
            rows = await connection.execute(
                select(table)
                .order_by(table.c.created_at.desc(), table.c.log_id.desc())
                .limit(limit)
            )
            return [dict(row) for row in rows.mappings().all()]

    async def list_audit_logs(self, key: str, limit: int = 200) -> list[dict]:
        """Audit rows for one trace id or thread id, oldest first."""
        table = self._table("audit_logs")
        async with self._engine.connect() as connection:
            rows = await connection.execute(
                select(table)
                .where(or_(table.c.trace_id == key, table.c.thread_id == key))
                .order_by(table.c.created_at.asc(), table.c.step.asc())
                .limit(limit)
            )
            return [dict(row) for row in rows.mappings().all()]

    async def pause_active_threads(self, origin: str | None = None) -> int:
        table = self._table("ledger_threads")
        statement = update(table).where(table.c.status == "Active")
        if origin is not None:
            statement = statement.where(table.c.origin == origin)
        async with self._engine.begin() as connection:
            result = await connection.execute(
                statement.values(status="Paused", is_backup=False)
            )
            return result.rowcount

    async def unpause_threads(self, origin: str | None = None) -> int:
        table = self._table("ledger_threads")
        statement = update(table).where(table.c.status == "Paused")
        if origin is not None:
            statement = statement.where(table.c.origin == origin)
        async with self._engine.begin() as connection:
            result = await connection.execute(
                statement.values(status="Active", is_backup=False)
            )
            return result.rowcount

    async def replace_oldest_entries(
        self, thread_id: str, limit: int, summary: str, token_count: int
    ) -> bool:
        """Insert the summary and delete the oldest entries in one transaction."""
        table = self._table("ledger_entries")
        async with self._engine.begin() as connection:
            rows = await connection.execute(
                select(table.c.entry_id)
                .where(table.c.thread_id == thread_id)
                .order_by(table.c.created_at.asc(), table.c.entry_id.asc())
                .limit(limit)
            )
            entry_ids = [row[0] for row in rows.fetchall()]
            if not entry_ids:
                return False
            await connection.execute(
                table.insert().values(
                    entry_id=str(uuid.uuid4()),
                    thread_id=thread_id,
                    tag="FAKTA",
                    content=summary,
                    token_count=token_count,
                    created_at=utc_now_text(),
                    is_backup=False,
                )
            )
            await connection.execute(
                delete(table).where(table.c.entry_id.in_(entry_ids))
            )
            return True

    async def claim_telegram_update(self, update_id: int) -> bool:
        """Reserve an update id once; False means it was already processed."""
        table = self._table("processed_updates")
        async with self._engine.begin() as connection:
            result = await connection.execute(
                self._insert("processed_updates")
                .values(update_id=update_id, processed_at=utc_now_text(), is_backup=False)
                .on_conflict_do_nothing(index_elements=[table.c.update_id])
            )
            claimed = result.rowcount > 0
        await self.prune_processed_updates()
        return claimed

    async def prune_processed_updates(self, keep: int | None = None) -> int:
        """Keep only the newest N update ids; older ids may replay after N."""
        limit = self.processed_update_retention if keep is None else keep
        if limit < 0:
            raise ValueError("keep must not be negative")
        table = self._table("processed_updates")
        newest = (
            select(table.c.update_id)
            .order_by(table.c.update_id.desc())
            .limit(limit)
        )
        async with self._engine.begin() as connection:
            result = await connection.execute(
                delete(table).where(table.c.update_id.not_in(newest))
            )
            return result.rowcount

    async def release_telegram_update(self, update_id: int) -> None:
        table = self._table("processed_updates")
        async with self._engine.begin() as connection:
            await connection.execute(
                delete(table).where(table.c.update_id == update_id)
            )

    async def set_system_state(self, key: str, value: str) -> None:
        table = self._table("system_state")
        async with self._engine.begin() as connection:
            await connection.execute(
                self._insert("system_state")
                .values(key=key, value=value, updated_at=utc_now_text(), is_backup=False)
                .on_conflict_do_update(
                    index_elements=[table.c.key],
                    set_={
                        "value": value,
                        "updated_at": utc_now_text(),
                        "is_backup": False,
                    },
                )
            )

    async def get_system_state(self, key: str) -> str | None:
        table = self._table("system_state")
        async with self._engine.connect() as connection:
            row = await connection.execute(
                select(table.c.value).where(table.c.key == key)
            )
            return row.scalar()
