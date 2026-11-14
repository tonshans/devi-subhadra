"""Tiered cognitive routing and DLQ timeout handling via the Healer Node."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from core.healer_node import HealResult, HealerNode, ProposePatch


@dataclass(frozen=True)
class SupervisorOutcome:
    status: str
    result: Any = None
    heal_result: HealResult | None = None


class Supervisor:
    def __init__(self, healer: HealerNode, timeout_seconds: float = 300) -> None:
        self._healer = healer
        self.timeout_seconds = timeout_seconds

    def select_model_tier(
        self, priority_score: int, is_human_endorsed: bool = False
    ) -> str:
        if is_human_endorsed or priority_score >= 100:
            return "TIER_2_HEAVY"
        if priority_score <= 20:
            return "TIER_1_LIGHT"
        return "TIER_2_HEAVY"

    async def delegate_worker(
        self,
        worker: Callable[[], Awaitable[Any]],
        propose_patch: ProposePatch,
        timeout_seconds: float | None = None,
    ) -> SupervisorOutcome:
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        try:
            result = await asyncio.wait_for(worker(), timeout)
        except asyncio.TimeoutError:
            error = f"DLQ worker timeout after {timeout} seconds"
        except Exception as exc:
            # Provider/network errors take the same recovery path as timeouts.
            error = f"Worker error: {exc}"
        else:
            return SupervisorOutcome(status="completed", result=result)

        heal_result = await self._healer.heal(
            error=error,
            execute_patch=lambda _patched_code: worker(),
            propose_patch=propose_patch,
        )
        return SupervisorOutcome(status="dlq", heal_result=heal_result)