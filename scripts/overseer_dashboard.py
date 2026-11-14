"""Read-only Overseer metrics plus direct DB interventions (audit/kill/override)."""

from __future__ import annotations

from core.cognitive_ledger import KILL_SWITCH_KEY, CognitiveLedger
from core.memory_manager import MemoryManager


class OverseerDashboard:
    """Shadow monitor: reads cost zero LLM tokens; writes only explicit interventions."""

    def __init__(self, ledger: CognitiveLedger) -> None:
        self._ledger = ledger
        self._memory = MemoryManager(ledger)

    async def character_trajectory(self) -> dict:
        rows = await self._ledger.list_semantic_memories(include_archived=True)
        active = [row for row in rows if not row["is_archived"]]

        categories: dict[str, dict] = {}
        for row in active:
            bucket = categories.setdefault(
                row["category"], {"count": 0, "confidence_sum": 0}
            )
            bucket["count"] += 1
            bucket["confidence_sum"] += row["confidence_score"]

        category_metrics = {
            category: {
                "count": values["count"],
                "average_confidence": round(
                    values["confidence_sum"] / values["count"], 2
                ),
            }
            for category, values in sorted(categories.items())
        }
        average_confidence = (
            round(sum(row["confidence_score"] for row in active) / len(active), 2)
            if active
            else 0.0
        )

        return {
            "total": len(rows),
            "active": len(active),
            "archived": len(rows) - len(active),
            "average_confidence": average_confidence,
            "categories": category_metrics,
        }

    async def read_audit_log(self, limit: int = 50) -> list[dict]:
        return await self._ledger.list_activity_logs(limit=limit)

    async def kill_switch(self) -> int:
        # Set the flag first so no new mission slips in while threads are paused.
        await self._ledger.set_system_state(KILL_SWITCH_KEY, "1")
        return await self._ledger.pause_active_threads()

    async def resume(self) -> int:
        # Flag first: new missions are allowed once the queue is reopened.
        await self._ledger.set_system_state(KILL_SWITCH_KEY, "0")
        return await self._ledger.unpause_threads()

    async def override_priority(
        self,
        thread_id: str,
        priority_score: int,
        expected_output: str | None = None,
    ) -> dict | None:
        if priority_score < 0:
            raise ValueError("priority_score must not be negative")
        updated = await self._ledger.update(
            "ledger_threads",
            thread_id,
            priority_score=priority_score,
            expected_output=expected_output,
            is_human_endorsed=1,
        )
        return await self._ledger.read("ledger_threads", thread_id) if updated else None

    async def edit_memory(
        self,
        rule_id: str,
        heuristic_text: str | None = None,
        confidence_score: int | None = None,
    ) -> dict | None:
        return await self._memory.update_memory(
            rule_id, heuristic_text, confidence_score
        )