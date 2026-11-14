"""Sleep-mode log consolidation, semantic dedup, and decay/archiving."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from core.cognitive_ledger import CognitiveLedger
from core.schemas import HeuristicCandidate

DEDUP_SIMILARITY_THRESHOLD = 0.40
DECAY_GRACE_DAYS = 30
ARCHIVE_SCORE_THRESHOLD = 20

Extractor = Callable[[list[dict]], Awaitable[list[HeuristicCandidate | dict]]]


@dataclass(frozen=True)
class ConsolidationReport:
    processed_entries: int
    created: int
    merged: int


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


class MemoryManager:
    def __init__(self, ledger: CognitiveLedger) -> None:
        self._ledger = ledger

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