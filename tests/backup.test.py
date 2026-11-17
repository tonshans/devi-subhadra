"""SQLite snapshot tests: dirty flags, incremental export, replace restore."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import suppress

from sqlalchemy import create_engine

from core.backup import (
    _mark_backed_up,
    backup_loop,
    export_to_sqlite,
    restore_from_sqlite,
)
from core.cognitive_ledger import CognitiveLedger
from core.db.schema import TABLE_ORDER


async def _new_ledger(tmp_path, name: str = "agent.db") -> CognitiveLedger:
    ledger = CognitiveLedger(tmp_path / name)
    await ledger.initialize()
    return ledger


def _snapshot_rows(path, table: str, key: str) -> dict[str, dict]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return {
            row[key]: dict(row) for row in connection.execute(f"SELECT * FROM {table}")
        }
    finally:
        connection.close()


def test_export_copies_only_dirty_rows_and_resets_flags(tmp_path):
    snapshot = tmp_path / "snapshot.db"

    async def scenario():
        ledger = await _new_ledger(tmp_path)
        first_id = await ledger.create(
            "core_directives", directive_type="VISION", content="v1"
        )

        assert await export_to_sqlite(ledger.engine, snapshot) == {
            "core_directives": 1
        }
        assert (await ledger.read("core_directives", first_id))["is_backup"] is True
        # Nothing dirty: snapshot is left untouched.
        assert await export_to_sqlite(ledger.engine, snapshot) == {}

        second_id = await ledger.create(
            "core_directives", directive_type="VISION", content="v2"
        )
        assert await export_to_sqlite(ledger.engine, snapshot) == {
            "core_directives": 1
        }

        # An update must mark the row dirty again.
        await ledger.update("core_directives", first_id, content="v1-updated")
        assert await export_to_sqlite(ledger.engine, snapshot) == {
            "core_directives": 1
        }
        await ledger.dispose()
        return first_id, second_id

    first_id, second_id = asyncio.run(scenario())
    rows = _snapshot_rows(snapshot, "core_directives", "directive_id")
    assert set(rows) == {first_id, second_id}
    assert rows[first_id]["content"] == "v1-updated"
    assert all(row["is_backup"] for row in rows.values())


def test_restore_replaces_target_and_resets_kill_switch(tmp_path):
    snapshot = tmp_path / "snapshot.db"

    async def scenario():
        ledger = await _new_ledger(tmp_path)
        thread_id = await ledger.create_thread("restore me", "HUMAN")
        entry_id = await ledger.create(
            "ledger_entries",
            thread_id=thread_id,
            tag="FAKTA",
            content="keep this fact",
        )
        await ledger.set_system_state("kill_switch", "1")
        await export_to_sqlite(ledger.engine, snapshot)

        # Diverge the live database after the snapshot was taken.
        await ledger.delete("ledger_entries", entry_id)
        extra_id = await ledger.create(
            "core_directives", directive_type="VISION", content="only live"
        )
        await ledger.set_system_state("kill_switch", "0")
        await ledger.dispose()
        return entry_id, extra_id

    entry_id, extra_id = asyncio.run(scenario())

    target = create_engine(f"sqlite:///{tmp_path / 'agent.db'}")
    try:
        counts = restore_from_sqlite(snapshot, target)
    finally:
        target.dispose()
    assert counts["ledger_entries"] == 1

    async def verify():
        ledger = CognitiveLedger(tmp_path / "agent.db")
        await ledger.initialize()
        try:
            assert await ledger.read("ledger_entries", entry_id) is not None
            assert await ledger.read("core_directives", extra_id) is None
            assert await ledger.get_system_state("kill_switch") == "0"
            found = await ledger.search_ledger_entries("keep")
            assert [row["entry_id"] for row in found] == [entry_id]
        finally:
            await ledger.dispose()

    asyncio.run(verify())


def test_schema_every_table_tracks_backup_state():
    for table in TABLE_ORDER:
        assert "is_backup" in table.c, table.name


def test_mark_backed_up_skips_rows_changed_after_export(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        directive_id = await ledger.create(
            "core_directives", directive_type="VISION", content="before"
        )
        stale = await ledger.read("core_directives", directive_id)
        await ledger.update("core_directives", directive_id, content="after")
        await _mark_backed_up(
            ledger.engine, {"core_directives": [stale]}
        )
        assert (await ledger.read("core_directives", directive_id))["is_backup"] is False
        await ledger.dispose()

    asyncio.run(scenario())


def test_budget_rows_are_included_in_snapshot(tmp_path):
    snapshot = tmp_path / "snapshot.db"

    async def scenario():
        from core.budget_governor import SqliteBudgetStore

        ledger = await _new_ledger(tmp_path)
        store = SqliteBudgetStore(tmp_path / "agent.db")
        try:
            assert store.reserve("2026-01-01", "CORE_70", 1.0, 0.25) is True
        finally:
            store.dispose()
        exported = await export_to_sqlite(ledger.engine, snapshot)
        assert exported["budget_usage"] == 1
        await ledger.dispose()

    asyncio.run(scenario())
    connection = sqlite3.connect(snapshot)
    try:
        row = connection.execute(
            "SELECT spent_usd, is_backup FROM budget_usage"
        ).fetchone()
    finally:
        connection.close()
    assert row == (0.25, 1)


def test_restore_skips_tables_missing_from_an_old_snapshot(tmp_path):
    snapshot = tmp_path / "old-snapshot.db"

    async def scenario():
        ledger = await _new_ledger(tmp_path)
        await ledger.create(
            "core_directives", directive_type="VISION", content="lama"
        )
        await export_to_sqlite(ledger.engine, snapshot)
        await ledger.dispose()

    asyncio.run(scenario())
    connection = sqlite3.connect(snapshot)
    try:
        connection.execute("DROP TABLE rate_limit_usage")
        connection.commit()
    finally:
        connection.close()

    target = create_engine(f"sqlite:///{tmp_path / 'restored.db'}")
    try:
        counts = restore_from_sqlite(snapshot, target)
    finally:
        target.dispose()

    assert "rate_limit_usage" not in counts
    assert counts["core_directives"] == 1


def test_restore_requires_existing_snapshot(tmp_path):
    target = create_engine(f"sqlite:///{tmp_path / 'agent.db'}")
    try:
        try:
            restore_from_sqlite(tmp_path / "missing.db", target)
        except FileNotFoundError as exc:
            assert "missing.db" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("missing snapshot must raise")
    finally:
        target.dispose()


def test_backup_loop_keeps_running_until_cancelled(tmp_path):
    snapshot = tmp_path / "snapshot.db"

    async def scenario():
        ledger = await _new_ledger(tmp_path)
        directive_id = await ledger.create(
            "core_directives", directive_type="VISION", content="loop"
        )
        task = asyncio.create_task(backup_loop(ledger.engine, snapshot, 0.01))
        await asyncio.sleep(0.1)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        assert snapshot.is_file()
        assert (await ledger.read("core_directives", directive_id))["is_backup"] is True
        await ledger.dispose()

    asyncio.run(scenario())
