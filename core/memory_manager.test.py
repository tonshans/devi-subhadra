"""Unit tests for sleep-mode extraction, semantic dedup, decay, and vector batching."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import core.memory_manager as memory_manager_module
from core.cognitive_ledger import CognitiveLedger
from core.memory_manager import MemoryManager
from core.schemas import HeuristicCandidate

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
DB_TIME = "%Y-%m-%d %H:%M:%S"


class FakeVectorStore:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    def upsert(self, ids, documents, metadatas) -> None:
        if self.fail:
            raise RuntimeError("chroma down")
        self.calls.append(
            {"ids": ids, "documents": documents, "metadatas": metadatas}
        )

    def query(self, text, k=5):
        return []


async def _setup(tmp_path, vector_store=None):
    ledger = CognitiveLedger(tmp_path / "ledger.db")
    await ledger.initialize()
    return ledger, MemoryManager(ledger, vector_store=vector_store)


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

def test_vectorize_pending_respects_age_and_thread_status(tmp_path):
    async def scenario():
        store = FakeVectorStore()
        ledger, manager = await _setup(tmp_path, vector_store=store)
        old_done = await ledger.create(
            "ledger_threads",
            title="Finished",
            status="Completed",
            origin="HUMAN",
            priority_score=100,
        )
        old_active = await ledger.create_thread("Running", "HUMAN")
        recent_done = await ledger.create(
            "ledger_threads",
            title="Fresh",
            status="Archived",
            origin="HUMAN",
            priority_score=100,
        )
        thirty_one_days = (NOW - timedelta(days=31)).strftime(DB_TIME)
        five_days = (NOW - timedelta(days=5)).strftime(DB_TIME)

        vector_me = await ledger.create(
            "ledger_entries",
            thread_id=old_done,
            tag="FAKTA",
            content="Old completed fact.",
            created_at=thirty_one_days,
        )
        active_entry = await ledger.create(
            "ledger_entries",
            thread_id=old_active,
            tag="FAKTA",
            content="Old but still active.",
            created_at=thirty_one_days,
        )
        recent_entry = await ledger.create(
            "ledger_entries",
            thread_id=recent_done,
            tag="FAKTA",
            content="Recent completed fact.",
            created_at=five_days,
        )
        old_discovery = await ledger.create(
            "atelier_discoveries",
            topic_category="PYTHON",
            content_summary="Pydantic docs update.",
            source_url="https://docs.pydantic.dev/latest/",
            relevance_to_user="Typed config.",
            extracted_at=(NOW - timedelta(days=2)).strftime(DB_TIME),
        )
        young_discovery = await ledger.create(
            "atelier_discoveries",
            topic_category="TELEGRAM",
            content_summary="Fresh bot news.",
            source_url="https://reddit.com/r/telegram",
            relevance_to_user="Gateway ideas.",
            extracted_at=(NOW - timedelta(hours=12)).strftime(DB_TIME),
        )

        report = await manager.vectorize_pending(now=NOW)

        assert report.ledger_entries == 1
        assert report.discoveries == 1
        entry_ids = [
            row_id
            for call in store.calls
            for row_id in call["ids"]
            if row_id in {vector_me, active_entry, recent_entry}
        ]
        assert entry_ids == [vector_me]
        discovery_ids = [
            row_id
            for call in store.calls
            for row_id in call["ids"]
            if row_id in {old_discovery, young_discovery}
        ]
        assert discovery_ids == [old_discovery]

        assert (await ledger.read("ledger_entries", vector_me))["is_vectorized"]
        assert not (await ledger.read("ledger_entries", active_entry))["is_vectorized"]
        assert not (await ledger.read("ledger_entries", recent_entry))["is_vectorized"]
        assert (await ledger.read("atelier_discoveries", old_discovery))[
            "is_vectorized"
        ]
        assert not (await ledger.read("atelier_discoveries", young_discovery))[
            "is_vectorized"
        ]

    asyncio.run(scenario())


def test_vectorize_pending_stores_epistemic_metadata(tmp_path):
    async def scenario():
        store = FakeVectorStore()
        ledger, manager = await _setup(tmp_path, vector_store=store)
        discovery_id = await ledger.create(
            "atelier_discoveries",
            topic_category="PYTHON",
            content_summary="Community says X.",
            source_url="https://www.reddit.com/r/python/comments/1",
            relevance_to_user="Opinion only.",
            extracted_at=(NOW - timedelta(days=2)).strftime(DB_TIME),
        )

        await manager.vectorize_pending(now=NOW)

        metadata = store.calls[0]["metadatas"][0]
        assert store.calls[0]["ids"] == [discovery_id]
        assert metadata["source"] == "atelier"
        assert metadata["source_url"] == "https://www.reddit.com/r/python/comments/1"
        assert metadata["epistemic_tag"] == "COMMUNITY_OPINION"
        assert metadata["credibility_score"] == 50

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.nasa.gov/missions", ("VERIFIED_FACT", 95)),
        ("https://mit.edu/research", ("VERIFIED_FACT", 95)),
        ("https://docs.python.org/3/library", ("VERIFIED_FACT", 95)),
        ("https://docs.pydantic.dev/latest/", ("VERIFIED_FACT", 95)),
        ("https://aws.amazon.com/s3/", ("VERIFIED_FACT", 95)),
        ("https://cloud.google.com/run/docs", ("VERIFIED_FACT", 95)),
        ("https://github.com/org/repo", ("VERIFIED_FACT", 95)),
        ("https://stackoverflow.com/questions/1", ("VERIFIED_FACT", 80)),
        ("https://medium.com/@writer/post", ("VERIFIED_FACT", 80)),
        ("https://towardsdatascience.com/article", ("VERIFIED_FACT", 80)),
        ("https://www.reddit.com/r/python", ("COMMUNITY_OPINION", 50)),
        ("https://quora.com/What-is-x", ("COMMUNITY_OPINION", 50)),
        ("https://news.ycombinator.com/item?id=1", ("COMMUNITY_OPINION", 50)),
        ("https://example.com/blog", ("UNVERIFIED_CLAIM", 30)),
        ("https://evilgov.com/fake", ("UNVERIFIED_CLAIM", 30)),
        ("not-a-url", ("UNVERIFIED_CLAIM", 30)),
        ("", ("UNVERIFIED_CLAIM", 30)),
    ],
)
def test_score_domain_maps_every_tier(url, expected):
    assert MemoryManager.score_domain(url) == expected


def test_failed_upsert_keeps_rows_pending(tmp_path):
    async def scenario():
        store = FakeVectorStore(fail=True)
        ledger, manager = await _setup(tmp_path, vector_store=store)
        thread_id = await ledger.create(
            "ledger_threads",
            title="Finished",
            status="Completed",
            origin="HUMAN",
            priority_score=100,
        )
        entry_id = await ledger.create(
            "ledger_entries",
            thread_id=thread_id,
            tag="FAKTA",
            content="Will fail to embed.",
            created_at=(NOW - timedelta(days=31)).strftime(DB_TIME),
        )

        with pytest.raises(RuntimeError):
            await manager.vectorize_pending(now=NOW)

        assert not (await ledger.read("ledger_entries", entry_id))["is_vectorized"]

    asyncio.run(scenario())


def test_vectorize_pending_sends_backlog_in_batches(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(memory_manager_module, "CHROMA_BATCH_SIZE", 2)
        store = FakeVectorStore()
        ledger, manager = await _setup(tmp_path, vector_store=store)
        thread_id = await ledger.create(
            "ledger_threads",
            title="Finished",
            status="Completed",
            origin="HUMAN",
            priority_score=100,
        )
        ids = [
            await ledger.create(
                "ledger_entries",
                thread_id=thread_id,
                tag="FAKTA",
                content=f"backlog-{index}",
                created_at=(NOW - timedelta(days=31)).strftime(DB_TIME),
            )
            for index in range(5)
        ]

        report = await manager.vectorize_pending(now=NOW)

        assert report.ledger_entries == 5
        assert [len(call["ids"]) for call in store.calls] == [2, 2, 1]
        assert {row_id for call in store.calls for row_id in call["ids"]} == set(ids)

    asyncio.run(scenario())


def test_blank_content_is_marked_without_embedding(tmp_path):
    async def scenario():
        store = FakeVectorStore()
        ledger, manager = await _setup(tmp_path, vector_store=store)
        thread_id = await ledger.create(
            "ledger_threads",
            title="Finished",
            status="Archived",
            origin="HUMAN",
            priority_score=100,
        )
        entry_id = await ledger.create(
            "ledger_entries",
            thread_id=thread_id,
            tag="FAKTA",
            content="   ",
            created_at=(NOW - timedelta(days=31)).strftime(DB_TIME),
        )

        report = await manager.vectorize_pending(now=NOW)

        assert report.ledger_entries == 1
        assert store.calls == []
        assert (await ledger.read("ledger_entries", entry_id))["is_vectorized"]

    asyncio.run(scenario())


def test_vectorize_pending_without_rows_never_touches_the_store(tmp_path):
    async def scenario():
        store = FakeVectorStore()
        _, manager = await _setup(tmp_path, vector_store=store)

        report = await manager.vectorize_pending(now=NOW)

        assert (report.ledger_entries, report.discoveries) == (0, 0)
        assert store.calls == []

    asyncio.run(scenario())
