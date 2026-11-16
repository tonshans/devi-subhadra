"""Sleep-mode log consolidation, semantic dedup, decay/archiving, vector batching."""

from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlparse

from core.cognitive_ledger import CognitiveLedger
from core.schemas import HeuristicCandidate
from core.vector_store import VectorStore

DEDUP_SIMILARITY_THRESHOLD = 0.40
DECAY_GRACE_DAYS = 30
PLUGIN_DECAY_GRACE_DAYS = 30
ARCHIVE_SCORE_THRESHOLD = 20
LEDGER_VECTOR_AGE_DAYS = 30
DISCOVERY_VECTOR_AGE_DAYS = 1
CHROMA_BATCH_SIZE = 64

# Domain tiers; add or remove one domain by editing one frozenset.
VERIFIED_SOURCE_DOMAINS = frozenset(
    {
        "docs.python.org",
        "docs.pydantic.dev",
        "aws.amazon.com",
        "cloud.google.com",
        "github.com",
    }
)
REPUTABLE_SOURCE_DOMAINS = frozenset(
    {"stackoverflow.com", "medium.com", "towardsdatascience.com"}
)
COMMUNITY_SOURCE_DOMAINS = frozenset(
    {"reddit.com", "quora.com", "news.ycombinator.com"}
)

_VECTOR_TABLES = {
    "ledger_entries": "entry_id",
    "atelier_discoveries": "discovery_id",
}

Extractor = Callable[[list[dict]], Awaitable[list[HeuristicCandidate | dict]]]


@dataclass(frozen=True)
class ConsolidationReport:
    processed_entries: int
    created: int
    merged: int


@dataclass(frozen=True)
class VectorizationReport:
    ledger_entries: int
    discoveries: int


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _utc_text(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_db_time(value: str | datetime) -> datetime:
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _entry_document(row: dict) -> str:
    return row["content"]


def _entry_metadata(row: dict) -> dict:
    return {"source": "ledger", "thread_id": row["thread_id"], "tag": row["tag"]}


def _discovery_document(row: dict) -> str:
    return f"{row['topic_category']}: {row['content_summary']}"


def _discovery_metadata(row: dict) -> dict:
    tag, score = MemoryManager.score_domain(row["source_url"])
    return {
        "source": "atelier",
        "source_url": row["source_url"],
        "topic_category": row["topic_category"],
        "epistemic_tag": tag,
        "credibility_score": score,
    }


class MemoryManager:
    def __init__(
        self,
        ledger: CognitiveLedger,
        vector_store: VectorStore | None = None,
        *,
        plugins_dir: str | Path | None = None,
        archived_plugins_dir: str | Path | None = None,
    ) -> None:
        self._ledger = ledger
        self._vector_store = vector_store
        self._plugins_dir = Path(plugins_dir) if plugins_dir else None
        self._archived_plugins_dir = (
            Path(archived_plugins_dir) if archived_plugins_dir else None
        )

    @property
    def vector_store(self) -> VectorStore:
        if self._vector_store is None:
            self._vector_store = VectorStore()
        return self._vector_store

    @staticmethod
    def score_domain(url: str) -> tuple[str, int]:
        """Zero-token credibility tag from the source URL host (no LLM call)."""
        host = (urlparse(url or "").hostname or "").lower().strip(".")
        if host.startswith("www."):
            host = host[4:]
        if not host:
            return "UNVERIFIED_CLAIM", 30
        if host.endswith(".gov") or host.endswith(".edu"):
            return "VERIFIED_FACT", 95
        for domains, tag, score in (
            (VERIFIED_SOURCE_DOMAINS, "VERIFIED_FACT", 95),
            (REPUTABLE_SOURCE_DOMAINS, "VERIFIED_FACT", 80),
            (COMMUNITY_SOURCE_DOMAINS, "COMMUNITY_OPINION", 50),
        ):
            if any(host == domain or host.endswith("." + domain) for domain in domains):
                return tag, score
        return "UNVERIFIED_CLAIM", 30

    async def vectorize_pending(
        self, now: datetime | None = None
    ) -> VectorizationReport:
        """Sleep Mode batch: embed aged SQL rows into Chroma, then mark them done."""
        now = now or datetime.now(timezone.utc)
        entries = await self._ledger.list_pending_vector_entries(
            _utc_text(now - timedelta(days=LEDGER_VECTOR_AGE_DAYS))
        )
        discoveries = await self._ledger.list_pending_vector_discoveries(
            _utc_text(now - timedelta(days=DISCOVERY_VECTOR_AGE_DAYS))
        )
        if not entries and not discoveries:
            return VectorizationReport(0, 0)
        return VectorizationReport(
            ledger_entries=await self._store_rows(
                "ledger_entries", entries, _entry_document, _entry_metadata
            ),
            discoveries=await self._store_rows(
                "atelier_discoveries",
                discoveries,
                _discovery_document,
                _discovery_metadata,
            ),
        )

    async def _store_rows(
        self,
        table: str,
        rows: list[dict],
        document_of: Callable[[dict], str],
        metadata_of: Callable[[dict], dict],
    ) -> int:
        primary_key = _VECTOR_TABLES[table]
        store = self.vector_store
        stored = 0
        for start in range(0, len(rows), CHROMA_BATCH_SIZE):
            batch = rows[start : start + CHROMA_BATCH_SIZE]
            ids: list[str] = []
            documents: list[str] = []
            metadatas: list[dict] = []
            blank: list[str] = []
            for row in batch:
                document = document_of(row).strip()
                if not document:
                    blank.append(row[primary_key])
                    continue
                ids.append(row[primary_key])
                documents.append(document)
                metadatas.append(metadata_of(row))
            if blank:
                # Nothing to embed, but flag it so Sleep Mode does not retry forever.
                await self._ledger.mark_vectorized(table, blank)
                stored += len(blank)
            if ids:
                await asyncio.to_thread(store.upsert, ids, documents, metadatas)
                await self._ledger.mark_vectorized(table, ids)
                stored += len(ids)
        return stored

    async def consolidate_daily_logs(
        self,
        thread_id: str,
        extractor: Extractor,
        now: datetime | None = None,
    ) -> ConsolidationReport:
        """Run Sleep Mode extraction over the last 24h and upsert heuristics."""
        now = now or datetime.now(timezone.utc)
        entries = await self._ledger.list_ledger_entries_since(
            thread_id, now - timedelta(days=1)
        )
        raw_candidates = await extractor(entries)
        candidates = [HeuristicCandidate.model_validate(item) for item in raw_candidates]

        existing = await self._ledger.list_semantic_memories()
        created = 0
        merged = 0

        for candidate in candidates:
            best_score = 0.0
            match = None
            for row in existing:
                if row["category"] != candidate.category:
                    continue
                score = _similarity(row["heuristic_text"], candidate.heuristic_text)
                if score > best_score:
                    best_score = score
                    match = row

            timestamp = _utc_text(now)
            if match is not None and best_score >= DEDUP_SIMILARITY_THRESHOLD:
                new_score = min(100, match["confidence_score"] + candidate.confidence_delta)
                await self._ledger.update(
                    "semantic_memory",
                    match["rule_id"],
                    confidence_score=new_score,
                    last_used_at=timestamp,
                )
                match["confidence_score"] = new_score
                match["last_used_at"] = timestamp
                merged += 1
            else:
                rule_id = await self._ledger.create(
                    "semantic_memory",
                    category=candidate.category,
                    heuristic_text=candidate.heuristic_text,
                    confidence_score=50,
                    last_used_at=timestamp,
                )
                existing.append(
                    {
                        "rule_id": rule_id,
                        "category": candidate.category,
                        "heuristic_text": candidate.heuristic_text,
                        "confidence_score": 50,
                        "last_used_at": timestamp,
                        "is_archived": 0,
                    }
                )
                created += 1

        return ConsolidationReport(
            processed_entries=len(entries), created=created, merged=merged
        )

    async def update_memory(
        self,
        rule_id: str,
        heuristic_text: str | None = None,
        confidence_score: int | None = None,
    ) -> dict | None:
        fields: dict = {}
        if heuristic_text is not None:
            fields["heuristic_text"] = heuristic_text
        if confidence_score is not None:
            if not 0 <= confidence_score <= 100:
                raise ValueError("confidence_score must be between 0 and 100")
            fields["confidence_score"] = confidence_score
            fields["is_archived"] = 1 if confidence_score < ARCHIVE_SCORE_THRESHOLD else 0
        if not fields:
            raise ValueError("update_memory requires at least one field")

        updated = await self._ledger.update("semantic_memory", rule_id, **fields)
        return await self._ledger.read("semantic_memory", rule_id) if updated else None

    async def decay_memories(self, now: datetime | None = None) -> int:
        """Subtract one confidence point per day past the grace period; archive <20."""
        now = now or datetime.now(timezone.utc)
        archived = 0

        for row in await self._ledger.list_semantic_memories():
            days_unused = (now - _parse_db_time(row["last_used_at"])).days
            if days_unused <= DECAY_GRACE_DAYS:
                continue

            score = max(0, row["confidence_score"] - (days_unused - DECAY_GRACE_DAYS))
            fields = {"confidence_score": score}
            if score < ARCHIVE_SCORE_THRESHOLD:
                fields["is_archived"] = 1
                archived += 1
            await self._ledger.update("semantic_memory", row["rule_id"], **fields)

        return archived

    async def decay_plugins(self, now: datetime | None = None) -> int:
        """Archive ATELIER plugins untouched for 30 days: move files, then SQL."""
        if self._plugins_dir is None:
            return 0
        now = now or datetime.now(timezone.utc)
        cutoff = _utc_text(now - timedelta(days=PLUGIN_DECAY_GRACE_DAYS))
        archived_dir = self._archived_plugins_dir or (
            self._plugins_dir.parent / "archived_plugins"
        )
        archived = 0

        for row in await self._ledger.list_stale_plugins(cutoff):
            name = str(row["plugin_name"])
            if Path(name).name != name:
                continue
            plugin_path = self._plugins_dir / f"{name}.py"
            if not plugin_path.is_file():
                continue
            archived_dir.mkdir(parents=True, exist_ok=True)
            for path in (plugin_path, self._plugins_dir / f"{name}.test.py"):
                if path.is_file():
                    shutil.move(str(path), str(archived_dir / path.name))
            if await self._ledger.set_plugin_archived(name, True):
                archived += 1

        return archived