"""Unit tests for sleep-mode extraction, semantic dedup, decay, and archiving."""

import asyncio
from datetime import datetime, timedelta, timezone

from core.cognitive_ledger import CognitiveLedger
from core.memory_manager import MemoryManager
from core.schemas import HeuristicCandidate

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
DB_TIME = "%Y-%m-%d %H:%M:%S"


async def _setup(tmp_path):
    ledger = CognitiveLedger(tmp_path / "ledger.db")
    await ledger.initialize()
    return ledger, MemoryManager(ledger)


def test_consolidates_daily_logs_and_merges_similar_heuristic(tmp_path):
    async def scenario():
        ledger, manager = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Thread A", "HUMAN")
        await ledger.create(
            "ledger_entries",
            thread_id=thread_id,
            tag="TINDAKAN",
            content="User asked for a Python data script.",
        )
        await ledger.create(
            "ledger_entries",
            thread_id=thread_id,
            tag="TINDAKAN",
            content="Old entry from years ago.",
            created_at="2020-01-01 00:00:00",
        )
        existing_id = await ledger.create(
            "semantic_memory",
            category="TEKNIS",
            heuristic_text="Pengguna menyukai bahasa Python untuk script data",
            confidence_score=50,
            last_used_at="2020-01-01 00:00:00",
        )

        seen_entries = []

        async def extractor(entries):
            seen_entries.extend(entries)
            return [
                HeuristicCandidate(
                    category="TEKNIS",
                    heuristic_text="Gunakan Python untuk pemrosesan data pengguna",
                    confidence_delta=10,
                ),
                HeuristicCandidate(
                    category="KOMUNIKASI",
                    heuristic_text="Selalu balas dengan sapaan formal",
                    confidence_delta=5,
                ),
            ]

        report = await manager.consolidate_daily_logs(thread_id, extractor, now=NOW)

        assert [entry["content"] for entry in seen_entries] == [
            "User asked for a Python data script."
        ]
        assert report.processed_entries == 1
        assert report.merged == 1
        assert report.created == 1

        rows = await ledger.list_semantic_memories()
        assert len(rows) == 2

        existing = await ledger.read("semantic_memory", existing_id)
        assert existing["heuristic_text"] == (
            "Pengguna menyukai bahasa Python untuk script data"
        )
        assert existing["confidence_score"] == 60
        assert existing["last_used_at"] != "2020-01-01 00:00:00"

        created = next(row for row in rows if row["rule_id"] != existing_id)
        assert created["heuristic_text"] == "Selalu balas dengan sapaan formal"
        assert created["category"] == "KOMUNIKASI"
        assert created["confidence_score"] == 50

    asyncio.run(scenario())


def test_deduplicates_repeated_candidate_within_one_batch(tmp_path):
    async def scenario():
        ledger, manager = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Thread A", "HUMAN")
        await ledger.create(
            "ledger_entries",
            thread_id=thread_id,
            tag="FAKTA",
            content="Python is preferred.",
        )
        candidate = HeuristicCandidate(
            category="TEKNIS",
            heuristic_text="Gunakan Python untuk pemrosesan data pengguna",
            confidence_delta=10,
        )

        async def extractor(entries):
            return [candidate, candidate]

        report = await manager.consolidate_daily_logs(thread_id, extractor, now=NOW)

        assert report.created == 1
        assert report.merged == 1
        rows = await ledger.list_semantic_memories()
        assert len(rows) == 1
        assert rows[0]["confidence_score"] == 60

    asyncio.run(scenario())


def test_decay_lowers_unused_scores_and_archives_below_20(tmp_path):
    async def scenario():
        ledger, manager = await _setup(tmp_path)
        forty_days_ago = (NOW - timedelta(days=40)).strftime(DB_TIME)
        ten_days_ago = (NOW - timedelta(days=10)).strftime(DB_TIME)
        exactly_thirty_days_ago = (NOW - timedelta(days=30)).strftime(DB_TIME)

        archive_me = await ledger.create(
            "semantic_memory",
            category="TEKNIS",
            heuristic_text="Low confidence heuristic.",
            confidence_score=25,
            last_used_at=forty_days_ago,
        )
        keep_me = await ledger.create(
            "semantic_memory",
            category="TEKNIS",
            heuristic_text="Healthy confidence heuristic.",
            confidence_score=50,
            last_used_at=forty_days_ago,
        )
        recent = await ledger.create(
            "semantic_memory",
            category="TEKNIS",
            heuristic_text="Recently used heuristic.",
            confidence_score=30,
            last_used_at=ten_days_ago,
        )
        boundary = await ledger.create(
            "semantic_memory",
            category="TEKNIS",
            heuristic_text="Exactly at the grace boundary.",
            confidence_score=40,
            last_used_at=exactly_thirty_days_ago,
        )

        archived = await manager.decay_memories(now=NOW)

        assert archived == 1

        archived_row = await ledger.read("semantic_memory", archive_me)
        assert archived_row["confidence_score"] == 15
        assert archived_row["is_archived"] == 1

        kept_row = await ledger.read("semantic_memory", keep_me)
        assert kept_row["confidence_score"] == 40
        assert kept_row["is_archived"] == 0

        recent_row = await ledger.read("semantic_memory", recent)
        assert recent_row["confidence_score"] == 30
        assert recent_row["is_archived"] == 0

        boundary_row = await ledger.read("semantic_memory", boundary)
        assert boundary_row["confidence_score"] == 40
        assert boundary_row["is_archived"] == 0

        active = await ledger.list_semantic_memories()
        assert archive_me not in {row["rule_id"] for row in active}

    asyncio.run(scenario())


def test_update_memory_archives_below_20_and_revives_above(tmp_path):
    async def scenario():
        ledger, manager = await _setup(tmp_path)
        rule_id = await ledger.create(
            "semantic_memory",
            category="TEKNIS",
            heuristic_text="Old heuristic.",
            confidence_score=50,
        )

        updated = await manager.update_memory(
            rule_id, heuristic_text="New heuristic.", confidence_score=10
        )
        assert updated["heuristic_text"] == "New heuristic."
        assert updated["is_archived"] == 1

        revived = await manager.update_memory(rule_id, confidence_score=90)
        assert revived["is_archived"] == 0

    asyncio.run(scenario())


def test_decay_does_not_touch_archived_rows(tmp_path):
    async def scenario():
        ledger, manager = await _setup(tmp_path)
        rule_id = await ledger.create(
            "semantic_memory",
            category="TEKNIS",
            heuristic_text="Already archived heuristic.",
            confidence_score=5,
            last_used_at="2020-01-01 00:00:00",
            is_archived=1,
        )

        archived = await manager.decay_memories(now=NOW)

        assert archived == 0
        row = await ledger.read("semantic_memory", rule_id)
        assert row["confidence_score"] == 5
        assert row["is_archived"] == 1

    asyncio.run(scenario())