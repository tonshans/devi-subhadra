"""SQLite snapshot backup/restore driven by the per-row `is_backup` flag.

Backup is incremental: only rows with `is_backup = false` are upserted into the
single snapshot file, then marked `true`. The snapshot is replaced atomically,
so a running agent can always be restarted from it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from sqlalchemy import and_, create_engine, delete, insert, inspect, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from core.db.dialects import create_search_objects_sync
from core.db.schema import TABLE_ORDER, metadata, system_state
from core.db.timestamps import utc_now_text

KILL_SWITCH_KEY = "kill_switch"
# Leave headroom under SQLite's default 999 bound parameters per statement.
_ROWS_PER_INSERT = 50

logger = logging.getLogger(__name__)


def sqlite_sync_url(db_path: str | Path) -> str:
    return f"sqlite:///{Path(db_path).resolve()}"


def _remove_sqlite_files(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()


def _prepare_snapshot(connection) -> None:
    if connection.dialect.name == "sqlite":
        connection.execute(text("PRAGMA foreign_keys=ON;"))
    metadata.create_all(connection)
    create_search_objects_sync(connection)


def _chunks(rows: list[dict]) -> list[list[dict]]:
    return [
        rows[index : index + _ROWS_PER_INSERT]
        for index in range(0, len(rows), _ROWS_PER_INSERT)
    ]


def _upsert_rows(connection, table, rows: list[dict]) -> None:
    primary_keys = [column.name for column in table.primary_key.columns]
    for chunk in _chunks(rows):
        statement = sqlite_insert(table).values(chunk)
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=primary_keys,
                set_={
                    column.name: getattr(statement.excluded, column.name)
                    for column in table.columns
                    if column.name not in primary_keys
                },
            )
        )


async def _mark_backed_up(
    engine: AsyncEngine, rows_by_table: dict[str, list[dict]]
) -> None:
    """Clear dirty flags only for rows that still match what was exported."""
    async with engine.begin() as connection:
        for name, rows in rows_by_table.items():
            table = metadata.tables[name]
            primary_keys = {column.name for column in table.primary_key.columns}
            for row in rows:
                # shortcut: one guarded UPDATE per row; batch later if dirty sets grow.
                conditions = [
                    table.c[column] == row[column] for column in primary_keys
                ]
                conditions.append(table.c.is_backup.is_(False))
                conditions.extend(
                    column == row[column.name]
                    for column in table.columns
                    if column.name not in primary_keys and column.name != "is_backup"
                )
                await connection.execute(
                    update(table)
                    .where(and_(*conditions))
                    .values(is_backup=True)
                )


async def export_to_sqlite(
    engine: AsyncEngine, sqlite_path: str | Path
) -> dict[str, int]:
    """Copy dirty rows to the snapshot, publish it, then clear the dirty flags."""
    path = Path(sqlite_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    _remove_sqlite_files(temp_path)
    if path.exists():
        shutil.copy2(path, temp_path)

    dest = create_engine(sqlite_sync_url(temp_path))
    exported: dict[str, int] = {}
    rows_by_table: dict[str, list[dict]] = {}
    try:
        with dest.begin() as connection:
            _prepare_snapshot(connection)
        async with engine.connect() as source:
            if source.dialect.name == "postgresql":
                # One consistent read snapshot for every table in this backup.
                await source.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                )
            for table in TABLE_ORDER:
                rows = (
                    (
                        await source.execute(
                            select(table).where(table.c.is_backup.is_(False))
                        )
                    )
                    .mappings()
                    .all()
                )
                values = [dict(row) for row in rows]
                if not values:
                    continue
                exported[table.name] = len(values)
                rows_by_table[table.name] = values
                # shortcut: snapshot writes block the loop; move to a thread if backups grow.
                with dest.begin() as connection:
                    _upsert_rows(
                        connection,
                        table,
                        [dict(value, is_backup=True) for value in values],
                    )

        dest.dispose()
        if not exported and path.exists():
            _remove_sqlite_files(temp_path)
            return {}
        os.replace(temp_path, path)
    except Exception:
        dest.dispose()
        _remove_sqlite_files(temp_path)
        raise
    finally:
        dest.dispose()

    await _mark_backed_up(engine, rows_by_table)
    return exported


def _reset_kill_switch(connection) -> None:
    now = utc_now_text()
    result = connection.execute(
        update(system_state)
        .where(system_state.c.key == KILL_SWITCH_KEY)
        .values(value="0", updated_at=now, is_backup=False)
    )
    if result.rowcount == 0:
        connection.execute(
            insert(system_state).values(
                key=KILL_SWITCH_KEY, value="0", updated_at=now, is_backup=False
            )
        )


def restore_from_sqlite(sqlite_path: str | Path, target_engine) -> dict[str, int]:
    """Replace every target table with the snapshot; SQLite is the source of truth."""
    path = Path(sqlite_path)
    if not path.is_file():
        raise FileNotFoundError(f"snapshot not found: {path}")
    if target_engine.dialect.name == "sqlite":
        target_path = target_engine.url.database
        if target_path and Path(target_path).resolve() == path.resolve():
            raise ValueError("snapshot and restore target are the same SQLite file")

    source = create_engine(sqlite_sync_url(path))
    counts: dict[str, int] = {}
    try:
        with source.connect() as source_connection, target_engine.begin() as target:
            _prepare_snapshot(target)
            for table in reversed(TABLE_ORDER):
                target.execute(delete(table))
            # Old snapshots predate newer tables; restore what they actually have.
            present = set(inspect(source).get_table_names())
            for table in TABLE_ORDER:
                if table.name not in present:
                    continue
                rows = [
                    dict(row)
                    for row in source_connection.execute(
                        select(table)
                    ).mappings().all()
                ]
                for row in rows:
                    row["is_backup"] = True
                counts[table.name] = len(rows)
                for chunk in _chunks(rows):
                    target.execute(insert(table).values(chunk))
            _reset_kill_switch(target)
    finally:
        source.dispose()
    return counts


async def backup_loop(
    engine: AsyncEngine, sqlite_path: str | Path, interval_seconds: float
) -> None:
    """Periodic incremental backup; errors are logged so the agent keeps running."""
    while True:
        try:
            exported = await export_to_sqlite(engine, sqlite_path)
            if exported:
                logger.info("snapshot updated: %s", exported)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("snapshot backup failed; retrying next interval")
        await asyncio.sleep(interval_seconds)
