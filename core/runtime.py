"""Composition root: the only place that wires ledger, budget, context, worker, plugins."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from core.budget_governor import (
    WAITING_FOR_QUOTA,
    BudgetExceededError,
    BudgetGovernor,
)
from core.cognitive_ledger import KILL_SWITCH_KEY, CognitiveLedger
from core.context_compiler import ContextCompiler
from core.healer_node import HealerNode, ProposePatch
from core.inner_monologue import InnerMonologue
from core.memory_manager import MemoryManager
from core.plugin_loader import PluginLoader
from core.schemas import PluginOutputSchema
from core.supervisor import Supervisor

Worker = Callable[[str], Awaitable[str]]
Initiative = Callable[[], Awaitable[Any]]

logger = logging.getLogger(__name__)


class MissionBlockedError(Exception):
    """Raised when the Overseer kill switch refuses new work."""


async def _no_patch(attempt: int, error: str) -> dict:
    raise RuntimeError("No patch proposer configured")


def load_plugins(
    plugins_dir: str | Path, config: dict
) -> dict[str, Any]:
    """Discover and initialize plugins once, at startup, with real config."""
    plugins = PluginLoader(plugins_dir).load_all()
    for name, plugin in list(plugins.items()):
        try:
            ready = plugin.initialize(config)
        except Exception:
            ready = False
        if not ready:
            plugins.pop(name, None)
    return plugins


class Runtime:
    def __init__(
        self,
        ledger: CognitiveLedger,
        governor: BudgetGovernor,
        *,
        compiler: ContextCompiler | None = None,
        supervisor: Supervisor | None = None,
        plugins: Optional[dict[str, Any]] = None,
        worker: Optional[Worker] = None,
        propose_patch: Optional[ProposePatch] = None,
        cost_usd: float = 0.0,
        initiative_cost_usd: float = 0.0,
        input_usd_per_million: float = 0.0,
        output_usd_per_million: float = 0.0,
        memory: MemoryManager | None = None,
        monologue: InnerMonologue | None = None,
        wake_up: Optional[Callable[[], bool]] = None,
        initiative: Optional[Initiative] = None,
        supervisor_timeout_seconds: float = 300,
    ) -> None:
        self._ledger = ledger
        self._governor = governor
        self._compiler = compiler or ContextCompiler(ledger)
        self._supervisor = supervisor or Supervisor(
            HealerNode(), timeout_seconds=supervisor_timeout_seconds
        )
        self._plugins = plugins or {}
        self._worker = worker
        self._propose_patch = propose_patch or _no_patch
        self._cost = cost_usd
        self._initiative_cost = initiative_cost_usd
        self._input_rate = input_usd_per_million
        self._output_rate = output_usd_per_million
        self._memory = memory or MemoryManager(ledger)
        self._monologue = monologue or InnerMonologue(governor)
        self._wake_up = wake_up
        self._initiative = initiative or self._default_initiative

    async def _default_initiative(self) -> int:
        return await self._memory.decay_memories()

    async def blocked(self) -> bool:
        return await self._ledger.get_system_state(KILL_SWITCH_KEY) == "1"

    def _token_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens / 1_000_000 * self._input_rate
            + completion_tokens / 1_000_000 * self._output_rate
        )

    async def handle_mission(
        self, thread_id: str, query: str, initiator: str = "Telegram"
    ) -> str:
        if await self.blocked():
            raise MissionBlockedError("Kill switch is active")
        if self._worker is None:
            raise RuntimeError("No worker configured")

        thread = await self._ledger.read("ledger_threads", thread_id)
        if thread is None:
            raise ValueError(f"unknown thread: {thread_id}")

        pressure = self._governor.check_rate_limit() == WAITING_FOR_QUOTA
        context = await self._compiler.compile_context(thread_id, query, pressure)

        paid = thread["origin"] != "HUMAN"
        priced = self._input_rate > 0 or self._output_rate > 0
        reserved = (
            self._token_cost(context.token_count, 0)
            if paid and priced
            else self._cost
        )

        try:
            bucket = self._governor.authorize_call(
                reserved,
                origin=thread["origin"],
                is_human_endorsed=bool(thread["is_human_endorsed"]),
            )
        except BudgetExceededError:
            return "Budget exhausted; initiative paused."

        async def run_worker() -> PluginOutputSchema:
            return PluginOutputSchema(
                success=True, data={"reply": await self._worker(context.prompt)}
            )

        outcome = await self._supervisor.delegate_worker(
            run_worker, self._propose_patch
        )
        # Consume usage so a later failure cannot bill these tokens twice.
        usage = getattr(self._worker, "last_usage", None)
        try:
            self._worker.last_usage = None
        except AttributeError:
            pass
        if outcome.status == "completed":
            result = outcome.result
            reply = (
                str(result.data.get("reply", ""))
                if isinstance(result, PluginOutputSchema)
                else str(result)
            )
        elif outcome.heal_result is not None:
            reply = outcome.heal_result.report
        else:
            reply = "Worker failed."

        cost = 0.0 if bucket == "HUMAN_EXEMPT" else self._cost
        if paid and priced:
            if usage:
                cost = self._token_cost(
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                )
                self._governor.settle(bucket, reserved, cost)
            else:
                cost = reserved

        await self._ledger.create(
            "activity_logs",
            thread_id=thread_id,
            source="AGENT",
            initiator=initiator,
            reasoning=f"Mission reply via {outcome.status}",
            budget_bucket=bucket,
            tokens_used=context.token_count,
            cost_usd=cost,
        )
        await self._ledger.create(
            "ledger_entries",
            thread_id=thread_id,
            tag="FAKTA",
            content=reply,
            token_count=self._compiler.estimate_tokens(reply),
        )
        return reply

    async def run_plugin(self, name: str, payload: dict) -> PluginOutputSchema:
        if await self.blocked():
            raise MissionBlockedError("Kill switch is active")
        plugin = self._plugins.get(name)
        if plugin is None:
            return PluginOutputSchema(
                success=False, data={}, error_message=f"Unknown plugin: {name}"
            )
        return await asyncio.to_thread(plugin.execute, payload)

    async def scheduled_tick(self) -> Any:
        if self._wake_up is not None:
            self._wake_up()
        return await self._monologue.run_cycle(
            self._initiative_cost, self._initiative
        )


async def scheduler_loop(runtime: Runtime, interval_seconds: float) -> None:
    while True:
        try:
            await runtime.scheduled_tick()
        except Exception:
            logger.exception("scheduled tick failed; retrying next interval")
        await asyncio.sleep(interval_seconds)
