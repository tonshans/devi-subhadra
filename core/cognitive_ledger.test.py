"""Unit tests for the async SQL state manager: WAL, CRUD, FTS5, priorities."""

import asyncio

import pytest

from core.cognitive_ledger import CognitiveLedger


async def _new_ledger(tmp_path) -> CognitiveLedger:
    ledger = CognitiveLedger(tmp_path / "ledger.db")
    await ledger.initialize()
    return ledger


async def _assert_crud(ledger, table, create_fields, update_fields):
    record_id = await ledger.create(table, **create_fields)
    assert isinstance(record_id, str) and record_id

    row = await ledger.read(table, record_id)
    assert row is not None
    for key, value in create_fields.items():
        assert row[key] == value

    assert await ledger.update(table, record_id, **update_fields) is True
    updated = await ledger.read(table, record_id)
    for key, value in update_fields.items():
        assert updated[key] == value

    assert await ledger.delete(table, record_id) is True
    assert await ledger.read(table, record_id) is None


def test_sqlite_uses_wal_mode(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        assert await ledger.get_journal_mode() == "wal"

    asyncio.run(scenario())


def test_crud_core_directives(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        await _assert_crud(
            ledger,
            "core_directives",
            {"directive_type": "VISION", "content": "Stay transparent."},
            {"content": "Stay fully transparent."},
        )

    asyncio.run(scenario())


def test_crud_semantic_memory(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        await _assert_crud(
            ledger,
            "semantic_memory",
            {
                "category": "TEKNIS",
                "heuristic_text": "Prefer Python for data scripts.",
                "confidence_score": 50,
            },
            {"confidence_score": 80},
        )

    asyncio.run(scenario())


def test_crud_ledger_threads(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        await _assert_crud(
            ledger,
            "ledger_threads",
            {
                "title": "Thread A",
                "status": "Active",
                "origin": "HUMAN",
                "priority_score": 100,
            },
            {"status": "Paused"},
        )

    asyncio.run(scenario())


def test_crud_ledger_entries(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        thread_id = await ledger.create_thread("Thread A", "HUMAN")
        await _assert_crud(
            ledger,
            "ledger_entries",
            {
                "thread_id": thread_id,
                "tag": "PEMIKIRAN",
                "content": "Draft thought.",
            },
            {"content": "Revised thought.", "token_count": 12},
        )

    asyncio.run(scenario())


def test_crud_atelier_discoveries(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        await _assert_crud(
            ledger,
            "atelier_discoveries",
            {
                "topic_category": "PYTHON",
                "content_summary": "Python async patterns are useful.",
                "source_url": "https://example.com/python",
                "relevance_to_user": "Improves local scripts.",
            },
            {"relevance_to_user": "Improves async services."},
        )

    asyncio.run(scenario())


def test_crud_activity_logs(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        await _assert_crud(
            ledger,
            "activity_logs",
            {
                "source": "AGENT",
                "initiator": "Inner_Monologue",
                "reasoning": "Explore a new heuristic.",
                "budget_bucket": "ATELIER_30",
            },
            {"tokens_used": 42, "cost_usd": 0.002},
        )

    asyncio.run(scenario())


def test_crud_api_usage_metrics(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        await _assert_crud(
            ledger,
            "api_usage_metrics",
            {
                "provider": "openrouter",
                "model_name": "google/gemini-2.5-flash",
                "window_minute": "2026-11-14T10:00",
            },
            {"requests_count": 3, "tokens_count": 1200},
        )

    asyncio.run(scenario())


def test_rejects_unknown_table_and_column(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)

        with pytest.raises(ValueError):
            await ledger.create("sqlite_master", name="x")

        with pytest.raises(ValueError):
            await ledger.create("core_directives", directive_type="VISION", nope="x")

    asyncio.run(scenario())


def test_ledger_entries_fts5_search_and_sync(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        thread_id = await ledger.create_thread("Thread A", "HUMAN")
        python_entry = await ledger.create(
            "ledger_entries",
            thread_id=thread_id,
            tag="FAKTA",
            content="Python asyncio patterns for the ledger.",
        )
        telegram_entry = await ledger.create(
            "ledger_entries",
            thread_id=thread_id,
            tag="TINDAKAN",
            content="Telegram message chunking at 4000 chars.",
        )

        hits = await ledger.search_ledger_entries("python")
        assert [row["entry_id"] for row in hits] == [python_entry]
        assert await ledger.search_ledger_entries("kubernetes") == []

        await ledger.update(
            "ledger_entries", python_entry, content="Rust ownership notes."
        )
        assert await ledger.search_ledger_entries("python") == []
        assert [
            row["entry_id"] for row in await ledger.search_ledger_entries("ownership")
        ] == [python_entry]

        await ledger.delete("ledger_entries", telegram_entry)
        assert await ledger.search_ledger_entries("telegram") == []

    asyncio.run(scenario())


def test_atelier_discoveries_fts5_searches_all_columns(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        python_discovery = await ledger.create(
            "atelier_discoveries",
            topic_category="PYTHON",
            content_summary="Python 3.13 REPL tricks.",
            source_url="https://example.com/python",
            relevance_to_user="Speeds up local scripting.",
        )
        telegram_discovery = await ledger.create(
            "atelier_discoveries",
            topic_category="TELEGRAM",
            content_summary="Bot API limit is 4096 chars.",
            source_url="https://example.com/telegram",
            relevance_to_user="Protects the gateway chunker.",
        )

        assert [
            row["discovery_id"]
            for row in await ledger.search_discoveries("python")
        ] == [python_discovery]
        assert [
            row["discovery_id"]
            for row in await ledger.search_discoveries("gateway")
        ] == [telegram_discovery]

    asyncio.run(scenario())


def test_get_next_active_thread_orders_by_priority(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        thread_a = await ledger.create_thread("Thread A", "AGENT")
        thread_b = await ledger.create_thread("Thread B", "HUMAN")

        assert (await ledger.read("ledger_threads", thread_a))["priority_score"] == 10
        assert (await ledger.read("ledger_threads", thread_b))["priority_score"] == 100

        next_thread = await ledger.get_next_active_thread()
        assert next_thread["thread_id"] == thread_b

    asyncio.run(scenario())


def test_endorse_thread_promotes_agent_initiative(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        thread_a = await ledger.create_thread("Thread A", "AGENT")
        thread_b = await ledger.create_thread("Thread B", "HUMAN")

        endorsed = await ledger.endorse_thread(
            thread_a, priority_score=150, expected_output="File ZIP"
        )

        assert endorsed is not None
        assert endorsed["is_human_endorsed"] == 1
        assert endorsed["priority_score"] == 150
        assert endorsed["expected_output"] == "File ZIP"

        next_thread = await ledger.get_next_active_thread()
        assert next_thread["thread_id"] == thread_a
        assert next_thread["is_human_endorsed"] == 1
        assert next_thread["expected_output"] == "File ZIP"
        assert thread_b  # both threads exist; endorsement alone decides the winner

    asyncio.run(scenario())


def test_get_next_active_thread_skips_non_active_threads(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        await ledger.create(
            "ledger_threads",
            title="Done human task",
            status="Completed",
            origin="HUMAN",
            priority_score=100,
        )
        active_agent = await ledger.create_thread("Agent task", "AGENT")

        next_thread = await ledger.get_next_active_thread()
        assert next_thread["thread_id"] == active_agent

    asyncio.run(scenario())


def test_database_checks_reject_invalid_enum_values(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)

        with pytest.raises(Exception):
            await ledger.create(
                "ledger_threads",
                title="Bad thread",
                status="Running",
                origin="HUMAN",
                priority_score=100,
            )

        with pytest.raises(Exception):
            await ledger.create(
                "semantic_memory",
                category="LAINNYA",
                heuristic_text="Invalid category.",
                confidence_score=50,
            )

    asyncio.run(scenario())


def test_fts_search_sanitizes_quotes_operators_and_wildcards(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        thread_id = await ledger.create_thread("Thread A", "HUMAN")
        await ledger.create(
            "ledger_entries",
            thread_id=thread_id,
            tag="FAKTA",
            content="hello world",
        )

        for query in ['"', "AND", "*", "   ", "NOT"]:
            assert await ledger.search_ledger_entries(query) == []

        assert len(await ledger.search_ledger_entries("hello")) == 1
        assert len(await ledger.search_ledger_entries('"hello"')) == 1
        assert len(await ledger.search_ledger_entries("hello OR")) == 1
        assert len(await ledger.search_ledger_entries("hello AND world")) == 1

    asyncio.run(scenario())


def test_create_thread_rejects_invalid_origin_and_status(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)

        with pytest.raises(ValueError):
            await ledger.create_thread("Bad origin", "ROBOT")

        with pytest.raises(ValueError):
            await ledger.create_thread("Bad status", "HUMAN", status="Running")

    asyncio.run(scenario())


def test_pause_active_threads_pauses_only_active_rows(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        first = await ledger.create_thread("Active A", "HUMAN")
        second = await ledger.create_thread("Active B", "AGENT")
        completed = await ledger.create(
            "ledger_threads",
            title="Completed C",
            status="Completed",
            origin="HUMAN",
            priority_score=100,
        )

        assert await ledger.pause_active_threads() == 2

        assert (await ledger.read("ledger_threads", first))["status"] == "Paused"
        assert (await ledger.read("ledger_threads", second))["status"] == "Paused"
        assert (await ledger.read("ledger_threads", completed))["status"] == "Completed"
        assert await ledger.get_next_active_thread() is None

    asyncio.run(scenario())


def test_replace_oldest_entries_keeps_newer_rows_and_summary(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        thread_id = await ledger.create_thread("Thread A", "HUMAN")
        for index in range(5):
            await ledger.create(
                "ledger_entries",
                thread_id=thread_id,
                tag="FAKTA",
                content=f"entry-{index}",
            )

        replaced = await ledger.replace_oldest_entries(
            thread_id, limit=3, summary="Consolidated 3 entries", token_count=4
        )

        assert replaced is True
        rows = await ledger.list_ledger_entries(thread_id, limit=100)
        assert [row["content"] for row in rows] == [
            "entry-3",
            "entry-4",
            "Consolidated 3 entries",
        ]
        assert (
            await ledger.replace_oldest_entries(
                "missing", limit=3, summary="x", token_count=1
            )
            is False
        )

    asyncio.run(scenario())


def test_claim_prunes_update_ids_beyond_retention(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)
        ledger.processed_update_retention = 3

        for update_id in range(1, 6):
            assert await ledger.claim_telegram_update(update_id) is True

        # Newest three are still deduplicated; the oldest two are forgotten.
        assert await ledger.claim_telegram_update(5) is False
        assert await ledger.claim_telegram_update(4) is False
        assert await ledger.claim_telegram_update(1) is True
        assert await ledger.prune_processed_updates(keep=0) == 3

    asyncio.run(scenario())


def test_claim_telegram_update_is_idempotent_until_released(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)

        assert await ledger.claim_telegram_update(7) is True
        assert await ledger.claim_telegram_update(7) is False

        await ledger.release_telegram_update(7)
        assert await ledger.claim_telegram_update(7) is True

    asyncio.run(scenario())


def test_system_state_roundtrip_survives_new_connection(tmp_path):
    async def scenario():
        ledger = await _new_ledger(tmp_path)

        assert await ledger.get_system_state("kill_switch") is None
        await ledger.set_system_state("kill_switch", "1")

        reopened = CognitiveLedger(tmp_path / "ledger.db")
        assert await reopened.get_system_state("kill_switch") == "1"

        await reopened.set_system_state("kill_switch", "0")
        assert await ledger.get_system_state("kill_switch") == "0"

    asyncio.run(scenario())