"""Composition root: the only place that wires ledger, budget, context, worker, plugins."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from core.budget_governor import (
    WAITING_FOR_QUOTA,
    BudgetExceededError,
    BudgetGovernor,
)
from core.cognitive_ledger import (
    AGENT_PAUSE_REASON_KEY,
    AGENT_PAUSE_UNTIL_KEY,
    KILL_SWITCH_KEY,
    PAUSE_REASON_KEY,
    PAUSE_UNTIL_KEY,
    CognitiveLedger,
)
from core.context_compiler import ContextCompiler
from core.healer_node import HealerNode, ProposePatch
from core.inner_monologue import InnerMonologue
from core.memory_manager import MemoryManager
from core.plugin_loader import PluginLoader
from core.provider_pool import AllProvidersUnavailable
from core.schemas import MissionReply, PluginOutputSchema
from core.supervisor import Supervisor

Worker = Callable[[str], Awaitable[str | MissionReply]]
Initiative = Callable[[], Awaitable[Any]]

logger = logging.getLogger(__name__)

_UNTIL_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _parse_until(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, _UNTIL_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _display_until(value: str | None) -> str:
    moment = _parse_until(value)
    return moment.strftime("%Y-%m-%d %H:%M UTC") if moment else "unknown"


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
        deliver_file: Optional[Callable[[str], Awaitable[None]]] = None,
        notify: Optional[Callable[[str], Awaitable[None]]] = None,
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
        self._deliver_file = deliver_file
        self._notify = notify

    @property
    def provider(self) -> Any | None:
        """The worker when it is a provider pool (it exposes status_lines)."""
        return self._worker if hasattr(self._worker, "status_lines") else None

    async def _default_initiative(self) -> int:
        return await self._memory.decay_memories()

    async def blocked(self) -> bool:
        return await self._ledger.get_system_state(KILL_SWITCH_KEY) == "1"

    async def _notice(self, message: str) -> None:
        if self._notify is None:
            return
        try:
            await self._notify(message)
        except Exception:
            logger.exception("pause notification failed")

    def _pause_text(self, metric: str, *, agent_only: bool) -> str:
        metric_name = metric.lower()
        if agent_only:
            used = self._governor.used(metric_name, "AGENT")
            cap = self._governor.agent_cap(metric_name)
            return (
                f"Agent initiative paused: agent budget ratio reached on {metric} "
                f"({used}/{cap}). Human chat still works. Auto-resume at "
                f"{self._governor.resume_at_iso(metric)}; use "
                "/agent-freetime on to let the agent use the full limit."
            )
        used = self._governor.used(metric_name)
        limit = self._governor.limit(metric_name)
        return (
            f"Agent paused: {metric} limit {used}/{limit} reached. "
            f"No LLM calls until {self._governor.resume_at_iso(metric)}. "
            "Send /status for usage or /resume-agent to resume now."
        )

    async def _pause_all(self, metric: str) -> str:
        reason = f"rate_limit:{metric}"
        message = self._pause_text(metric, agent_only=False)
        previous = await self._ledger.get_system_state(PAUSE_REASON_KEY)
        await self._ledger.set_system_state(PAUSE_REASON_KEY, reason)
        await self._ledger.set_system_state(
            PAUSE_UNTIL_KEY, self._governor.resume_at_iso(metric)
        )
        await self._ledger.set_system_state(KILL_SWITCH_KEY, "1")
        await self._ledger.pause_active_threads()
        if previous != reason:
            await self._notice(message)
        return message

    async def _pause_agent(self, metric: str) -> str:
        reason = f"budget_ratio:{metric}"
        message = self._pause_text(metric, agent_only=True)
        previous = await self._ledger.get_system_state(AGENT_PAUSE_REASON_KEY)
        await self._ledger.set_system_state(AGENT_PAUSE_REASON_KEY, reason)
        await self._ledger.set_system_state(
            AGENT_PAUSE_UNTIL_KEY, self._governor.resume_at_iso(metric)
        )
        await self._ledger.pause_active_threads(origin="AGENT")
        if previous != reason:
            await self._notice(message)
        return message

    async def _limit_gate(self, side: str) -> str | None:
        """Block new work when the global limit or the agent share is spent."""
        metric = self._governor.hard_limit_reached()
        if metric is not None:
            return await self._pause_all(metric)
        if side != "AGENT":
            return None
        reason = await self._ledger.get_system_state(AGENT_PAUSE_REASON_KEY)
        if reason and reason.startswith("budget_ratio:"):
            return self._pause_text(reason.split(":", 1)[1], agent_only=True)
        metric = self._governor.agent_limit_reached()
        if metric is not None:
            return await self._pause_agent(metric)
        return None

    async def auto_resume(self) -> bool:
        """Lift rate-limit pauses once their window reset; manual pauses stay."""
        resumed = False
        now = datetime.now(timezone.utc)
        reason = await self._ledger.get_system_state(PAUSE_REASON_KEY)
        if reason and reason.startswith("rate_limit:"):
            until = _parse_until(
                await self._ledger.get_system_state(PAUSE_UNTIL_KEY)
            )
            if (
                until is not None
                and now >= until
                and self._governor.hard_limit_reached() is None
            ):
                await self._ledger.set_system_state(PAUSE_REASON_KEY, "")
                await self._ledger.set_system_state(PAUSE_UNTIL_KEY, "")
                await self._ledger.set_system_state(KILL_SWITCH_KEY, "0")
                await self._ledger.unpause_threads()
                resumed = True
        reason = await self._ledger.get_system_state(AGENT_PAUSE_REASON_KEY)
        if reason and reason.startswith("budget_ratio:"):
            until = _parse_until(
                await self._ledger.get_system_state(AGENT_PAUSE_UNTIL_KEY)
            )
            if (
                until is not None
                and now >= until
                and self._governor.agent_limit_reached() is None
            ):
                await self._ledger.set_system_state(AGENT_PAUSE_REASON_KEY, "")
                await self._ledger.set_system_state(AGENT_PAUSE_UNTIL_KEY, "")
                await self._ledger.unpause_threads(origin="AGENT")
                resumed = True
        return resumed

    async def pause_notice(self) -> str | None:
        """Human-readable reason when a rate limit (not a manual pause) blocks."""
        reason = await self._ledger.get_system_state(PAUSE_REASON_KEY)
        if not reason or not reason.startswith("rate_limit:"):
            return None
        metric = reason.split(":", 1)[1]
        until = await self._ledger.get_system_state(PAUSE_UNTIL_KEY)
        return (
            f"Agent paused: {metric} limit reached. No LLM calls until "
            f"{_display_until(until)}. Send /status for usage or /resume-agent "
            "to resume now."
        )

    def _token_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens / 1_000_000 * self._input_rate
            + completion_tokens / 1_000_000 * self._output_rate
        )

    async def handle_mission(
        self, thread_id: str, query: str, initiator: str = "Telegram"
    ) -> str:
        await self.auto_resume()
        if await self.blocked():
            raise MissionBlockedError("Kill switch is active")
        if self._worker is None:
            raise RuntimeError("No worker configured")

        thread = await self._ledger.read("ledger_threads", thread_id)
        if thread is None:
            raise ValueError(f"unknown thread: {thread_id}")

        side = thread["origin"]
        gate = await self._limit_gate(side)
        if gate is not None:
            return gate

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

        usages: list[dict] = []

        async def run_worker() -> PluginOutputSchema:
            # Every attempt is one provider request, including healer retries.
            try:
                return PluginOutputSchema(
                    success=True, data={"reply": await self._worker(context.prompt)}
                )
            except AllProvidersUnavailable as exc:
                # One notice per down episode; the next attempt reports again.
                if exc.should_notify:
                    await self._notice(exc.message)
                return PluginOutputSchema(
                    success=True, data={"reply": exc.message}
                )
            finally:
                usage = getattr(self._worker, "last_usage", None)
                try:
                    self._worker.last_usage = None
                except AttributeError:
                    pass
                if usage:
                    usages.append(usage)
                tokens = (
                    int(usage.get("prompt_tokens", 0))
                    + int(usage.get("completion_tokens", 0))
                    if usage
                    else 0
                )
                self._governor.record_api_usage(1, tokens, side=side)

        outcome = await self._supervisor.delegate_worker(
            run_worker, self._propose_patch
        )
        if outcome.status == "completed":
            result = outcome.result
            raw_reply = (
                result.data.get("reply", "")
                if isinstance(result, PluginOutputSchema)
                else result
            )
            if isinstance(raw_reply, MissionReply):
                reply = raw_reply.reply
                files = raw_reply.files
            else:
                reply = str(raw_reply)
                files = []
        elif outcome.heal_result is not None:
            reply = outcome.heal_result.report
            files = []
        else:
            reply = "Worker failed."
            files = []
        reply = await self._deliver_files(reply, files)

        prompt_tokens = sum(int(item.get("prompt_tokens", 0)) for item in usages)
        completion_tokens = sum(
            int(item.get("completion_tokens", 0)) for item in usages
        )
        cost = 0.0 if bucket == "HUMAN_EXEMPT" else self._cost
        if paid and priced:
            if usages:
                cost = self._token_cost(prompt_tokens, completion_tokens)
                self._governor.settle(bucket, reserved, cost)
            else:
                cost = reserved

        gate = await self._limit_gate(side)
        if gate is not None:
            reply = f"{reply}\n{gate}"

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

    async def _deliver_files(self, reply: str, files: list[str]) -> str:
        for path in files:
            try:
                if self._deliver_file is None:
                    raise RuntimeError("file delivery is not configured")
                await self._deliver_file(path)
            except Exception as exc:
                reply = f"{reply}\nFile not sent ({path}): {exc}"
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
        await self.auto_resume()
        if await self.blocked():
            return None
        if await self._ledger.get_system_state(AGENT_PAUSE_REASON_KEY):
            return None
        if await self._limit_gate("AGENT") is not None:
            return None
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
