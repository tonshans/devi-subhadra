"""Composition root: the only place that wires ledger, budget, context, worker, plugins."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
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
from core.plugin_loader import PluginLoader, build_extra_tools_prompt
from core.provider_pool import AllProvidersUnavailable
from core.schemas import MissionReply, PluginOutputSchema, SupervisorDecision
from core.supervisor import Supervisor
from core.vector_store import VectorStore

Worker = Callable[..., Awaitable[object]]
Initiative = Callable[[], Awaitable[Any]]

logger = logging.getLogger(__name__)

_UNTIL_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
SLEEP_MODE_LAST_RUN_KEY = "sleep_mode_last_run"
# Local hours when Sleep Mode may run its batch job (00:00-05:59).
SLEEP_HOURS = frozenset(range(0, 6))
MAX_THREAD_ATTEMPTS = 3
RESUME_RETRY_SECONDS = 300
# One mission may call at most this many tools before it must answer.
MAX_TOOL_ROUNDS = 3


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
) -> tuple[dict[str, Any], dict[str, str]]:
    """Discover plugins once: eager Tier 1 tools plus the Tier 2 catalog."""
    loader = PluginLoader(plugins_dir)
    plugins = loader.load_all()
    for name, plugin in list(plugins.items()):
        try:
            ready = plugin.initialize(config)
        except Exception:
            ready = False
        if not ready:
            plugins.pop(name, None)
    return plugins, loader.catalog


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
        monologue_every_cycles: int = 1,
        wake_up: Optional[Callable[[], bool]] = None,
        initiative: Optional[Initiative] = None,
        curiosity_daily_limit: int = 3,
        audit_enabled: bool = True,
        deliver_file: Optional[Callable[[str], Awaitable[None]]] = None,
        notify: Optional[Callable[[str], Awaitable[None]]] = None,
        supervisor_timeout_seconds: float = 300,
        vector_store: VectorStore | None = None,
        plugin_catalog: Optional[dict[str, str]] = None,
        plugins_dir: Optional[str | Path] = None,
        plugin_config: Optional[dict[str, Any]] = None,
        archived_plugins_dir: Optional[str | Path] = None,
    ) -> None:
        self._ledger = ledger
        self._governor = governor
        store = vector_store or VectorStore()
        self._compiler = compiler or ContextCompiler(ledger, vector_store=store)
        self._supervisor = supervisor or Supervisor(
            HealerNode(), timeout_seconds=supervisor_timeout_seconds
        )
        self._plugins = plugins or {}
        self._plugin_catalog = dict(plugin_catalog or {})
        self._plugins_dir = Path(plugins_dir) if plugins_dir else None
        self._plugin_config = plugin_config or {}
        self._archived_plugins_dir = str(
            archived_plugins_dir or "/mnt/agent_atelier/archived_plugins"
        )
        self._worker = worker
        self._propose_patch = propose_patch or _no_patch
        self._cost = cost_usd
        self._initiative_cost = initiative_cost_usd
        self._input_rate = input_usd_per_million
        self._output_rate = output_usd_per_million
        self._memory = memory or MemoryManager(
            ledger,
            vector_store=store,
            plugins_dir=self._plugins_dir,
            archived_plugins_dir=self._archived_plugins_dir,
        )
        self._monologue = monologue or InnerMonologue(governor)
        self._monologue_every_cycles = max(1, monologue_every_cycles)
        self._tick_count = 0
        self._wake_up = wake_up
        self._initiative = initiative or self._default_initiative
        self._curiosity_limit = curiosity_daily_limit
        self._audit_enabled = audit_enabled
        self._deliver_file = deliver_file
        self._notify = notify

    @property
    def provider(self) -> Any | None:
        """The worker when it is a provider pool (it exposes status_lines)."""
        return self._worker if hasattr(self._worker, "status_lines") else None

    async def _default_initiative(self) -> int:
        decayed = await self._memory.decay_memories()
        await self._curiosity_cycle()
        return decayed

    async def _curiosity_prompt(self) -> str:
        directives = await self._ledger.list_core_directives()
        vision = "; ".join(
            row["content"] for row in directives if row["directive_type"] == "VISION"
        )
        queued = await self._ledger.get_next_active_thread()
        busy = (
            f"Queued mission: {queued['title']}"
            if queued
            else "No queued mission."
        )
        return (
            "IDLE CYCLE. You are between tasks.\n"
            f"VISION: {vision or 'not set'}\n"
            f"{busy}\n"
            "Choose CREATE_INITIATIVE with one concrete next step toward the "
            "vision, or SLEEP_NOOP when nothing useful remains today."
        )

    async def _curiosity_cycle(self) -> None:
        """One LLM thought per cycle, capped per UTC day, from the idle loop."""
        if self._worker is None or self._curiosity_limit <= 0:
            return
        key = f"curiosity_count:{datetime.now(timezone.utc):%Y-%m-%d}"
        used = int(await self._ledger.get_system_state(key) or 0)
        if used >= self._curiosity_limit:
            return
        try:
            # shortcut: needs a worker that accepts structured_model; a plain
            # callable just logs and skips curiosity.
            prompt = await self._curiosity_prompt()
            decision = await self._worker(
                prompt, structured_model=SupervisorDecision
            )
        except Exception:
            logger.exception("curiosity cycle failed")
            return
        usage = getattr(self._worker, "last_usage", None)
        if not isinstance(decision, SupervisorDecision):
            return

        await self._ledger.set_system_state(key, str(used + 1))
        thinking = decision.action_type == "CREATE_INITIATIVE"
        thread_id = await self._ledger.create_thread(
            f"Curiosity: {decision.thought_entry[:60]}",
            "AGENT",
            status="Active" if thinking else "Completed",
        )
        if thinking:
            # Give the resume loop a concrete goal plus a ready-now timestamp:
            # only threads with a completed attempt are resume candidates.
            await self._ledger.update(
                "ledger_threads",
                thread_id,
                expected_output=decision.thought_entry,
                last_attempt_at="2000-01-01 00:00:00.000000",
            )
        entry_id = await self._ledger.create(
            "ledger_entries",
            thread_id=thread_id,
            tag="PEMIKIRAN",
            content=f"{decision.reasoning}\n{decision.thought_entry}",
            token_count=self._compiler.estimate_tokens(decision.thought_entry),
        )
        await self._audit(
            trace_id=str(uuid.uuid4()),
            step=1,
            thread_id=thread_id,
            trigger="CURIOSITY",
            origin="AGENT",
            model=self._model_name(),
            prompt=prompt,
            prompt_tokens=int((usage or {}).get("prompt_tokens", 0)),
            cot=decision.reasoning,
            action=decision.action_type,
            response=decision.thought_entry,
            response_tokens=int((usage or {}).get("completion_tokens", 0)),
            outcome="SUCCESS",
            result_table="ledger_entries",
            result_id=entry_id,
        )

    async def blocked(self) -> bool:
        return await self._ledger.get_system_state(KILL_SWITCH_KEY) == "1"

    def _model_name(self) -> str | None:
        return getattr(self._worker, "model", None)

    async def _audit(self, **fields: Any) -> str | None:
        """One audit_logs row per agent step; never breaks the mission."""
        if not self._audit_enabled:
            return None
        try:
            return await self._ledger.create("audit_logs", **fields)
        except Exception:
            logger.exception("audit write failed")
            return None

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

    async def _tool_catalog_prompt(self) -> str:
        rows = await self._ledger.list_plugins("ATELIER", include_archived=False)
        return build_extra_tools_prompt(rows, self._archived_plugins_dir)

    async def sync_plugin_registry(self) -> None:
        """Seed SQL from disk once per startup; SQL then owns is_archived."""
        for name, plugin in self._plugins.items():
            await self._ledger.register_plugin(
                name, "CORE", getattr(plugin, "description", name)
            )
        for name, description in self._plugin_catalog.items():
            await self._ledger.register_plugin(name, "ATELIER", description)

    async def handle_mission(
        self,
        thread_id: str,
        query: str,
        initiator: str = "Telegram",
        record_request: bool = True,
        trigger: str | None = None,
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
        trigger = (trigger or initiator).upper()
        trace_id = str(uuid.uuid4())
        step = 0
        last_audit_id: str | None = None
        gate = await self._limit_gate(side)
        if gate is not None:
            # A refused mission still leaves one audit row behind.
            await self._audit(
                trace_id=trace_id,
                step=1,
                thread_id=thread_id,
                trigger=trigger,
                origin=side,
                prompt=query,
                prompt_tokens=self._compiler.estimate_tokens(query),
                action="BLOCKED",
                response=gate,
                outcome="BLOCKED",
            )
            return gate

        await self._ledger.record_thread_attempt(thread_id)
        pressure = self._governor.check_rate_limit() == WAITING_FOR_QUOTA
        context = await self._compiler.compile_context(
            thread_id,
            query,
            pressure,
            extra_tools=await self._tool_catalog_prompt(),
        )
        if record_request:
            # Persist the request so a crashed mission can be resumed later.
            await self._ledger.create(
                "ledger_entries",
                thread_id=thread_id,
                tag="TINDAKAN",
                content=query,
                token_count=self._compiler.estimate_tokens(query),
            )

        paid = thread["origin"] != "HUMAN"
        priced = self._input_rate > 0 or self._output_rate > 0
        reserved = (
            self._token_cost(context.token_count, 0)
            if paid and priced
            else self._cost
        )

        usages: list[dict] = []
        unavailable = False
        prompt = context.prompt
        reply = "Worker failed."
        files: list[str] = []
        worker_succeeded = False
        tool_rounds = 0
        cost = 0.0
        outcome_status = "budget_exhausted"

        try:
            bucket = self._governor.authorize_call(
                reserved,
                origin=thread["origin"],
                is_human_endorsed=bool(thread["is_human_endorsed"]),
            )
        except BudgetExceededError:
            return "Budget exhausted; initiative paused."

        while True:
            round_start = len(usages)
            started = time.perf_counter()
            round_error: str | None = None

            async def run_worker() -> PluginOutputSchema:
                nonlocal unavailable
                unavailable = False
                # Every attempt is one provider request, including healer retries.
                try:
                    return PluginOutputSchema(
                        success=True, data={"reply": await self._worker(prompt)}
                    )
                except AllProvidersUnavailable as exc:
                    unavailable = True
                    # One notice per down episode; the next attempt reports again.
                    if exc.should_notify:
                        await self._notice(exc.message)
                    return PluginOutputSchema(
                        success=False,
                        data={"reply": exc.message},
                        error_message=exc.message,
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
            outcome_status = outcome.status
            raw_reply: object = ""
            if outcome.status == "completed":
                result = outcome.result
                worker_succeeded = (
                    result.success if isinstance(result, PluginOutputSchema) else True
                )
                raw_reply = (
                    result.data.get("reply", "")
                    if isinstance(result, PluginOutputSchema)
                    else result
                )
                if isinstance(result, PluginOutputSchema):
                    round_error = result.error_message
                if isinstance(raw_reply, MissionReply):
                    reply = raw_reply.reply
                    files = raw_reply.files
                else:
                    reply = str(raw_reply)
                    files = []
            elif outcome.heal_result is not None:
                worker_succeeded = outcome.heal_result.success
                reply = outcome.heal_result.report
                files = []
            else:
                worker_succeeded = False
                reply = "Worker failed."
                files = []

            round_usages = usages[round_start:]
            round_cost = self._settle_round(
                bucket,
                reserved,
                round_usages,
                unavailable=unavailable,
                paid=paid,
                priced=priced,
            )
            cost += round_cost

            plugin = (
                raw_reply.plugin if isinstance(raw_reply, MissionReply) else None
            )
            reasoning = (
                raw_reply.reasoning if isinstance(raw_reply, MissionReply) else ""
            )
            step += 1
            last_audit_id = await self._audit(
                trace_id=trace_id,
                step=step,
                thread_id=thread_id,
                trigger=trigger,
                origin=side,
                model=self._model_name(),
                prompt=prompt,
                prompt_tokens=sum(
                    int(item.get("prompt_tokens", 0)) for item in round_usages
                ),
                cot=reasoning or None,
                action=(
                    "HEAL"
                    if outcome.heal_result is not None
                    else "CALL_PLUGIN"
                    if plugin and worker_succeeded
                    else "LLM_REPLY"
                ),
                response=reply,
                response_tokens=sum(
                    int(item.get("completion_tokens", 0)) for item in round_usages
                ),
                outcome="SUCCESS" if worker_succeeded else "FAILED",
                error=round_error,
                latency_ms=int((time.perf_counter() - started) * 1000),
                cost_usd=round_cost,
            )

            if (
                not worker_succeeded
                or not plugin
                or tool_rounds >= MAX_TOOL_ROUNDS
            ):
                break
            tool_rounds += 1
            tool_started = time.perf_counter()
            output = await self.run_plugin(plugin, raw_reply.plugin_payload or {})
            step += 1
            last_audit_id = await self._audit(
                trace_id=trace_id,
                step=step,
                thread_id=thread_id,
                trigger=trigger,
                origin=side,
                model=self._model_name(),
                action="CALL_PLUGIN",
                action_target=plugin,
                action_payload=json.dumps(raw_reply.plugin_payload or {}),
                response=output.model_dump_json()[:4000],
                outcome="SUCCESS" if output.success else "FAILED",
                error=output.error_message,
                latency_ms=int((time.perf_counter() - tool_started) * 1000),
            )
            prompt = (
                f"{context.prompt}\n\n"
                f"TOOL RESULT ({plugin}): {output.model_dump_json()[:4000]}\n"
                "Answer the user now; do not request the same tool again."
            )
            try:
                # Each extra round pays for one more provider request.
                bucket = self._governor.authorize_call(
                    reserved,
                    origin=thread["origin"],
                    is_human_endorsed=bool(thread["is_human_endorsed"]),
                )
            except BudgetExceededError:
                reply = f"{reply}\nBudget exhausted; initiative paused."
                break

        reply = await self._deliver_files(reply, files)

        gate = await self._limit_gate(side)
        if gate is not None:
            reply = f"{reply}\n{gate}"

        if worker_succeeded:
            await self._ledger.set_thread_status(thread_id, "Completed")

        await self._ledger.create(
            "activity_logs",
            thread_id=thread_id,
            source="AGENT",
            initiator=initiator,
            reasoning=(
                f"Mission reply via {outcome_status}"
                if worker_succeeded
                else f"Mission failed via {outcome_status}"
            ),
            budget_bucket=bucket,
            tokens_used=context.token_count,
            cost_usd=cost,
        )
        entry_id = await self._ledger.create(
            "ledger_entries",
            thread_id=thread_id,
            tag="FAKTA" if worker_succeeded else "TINDAKAN",
            content=reply,
            token_count=self._compiler.estimate_tokens(reply),
        )
        if last_audit_id is not None:
            # Point the last audit row at the reply artifact.
            await self._ledger.update(
                "audit_logs",
                last_audit_id,
                result_table="ledger_entries",
                result_id=entry_id,
            )
        return reply

    def _settle_round(
        self,
        bucket: str,
        reserved: float,
        usages: list[dict],
        *,
        unavailable: bool,
        paid: bool,
        priced: bool,
    ) -> float:
        """Settle one LLM round so each tool loop round is billed separately."""
        if not paid:
            return 0.0
        if unavailable:
            # No provider answered, so refund the reservation.
            self._governor.settle(bucket, reserved, 0.0)
            return 0.0
        if not priced:
            return self._cost
        if not usages:
            return reserved
        cost = self._token_cost(
            sum(int(item.get("prompt_tokens", 0)) for item in usages),
            sum(int(item.get("completion_tokens", 0)) for item in usages),
        )
        self._governor.settle(bucket, reserved, cost)
        return cost

    async def _deliver_files(self, reply: str, files: list[str]) -> str:
        for path in files:
            try:
                if self._deliver_file is None:
                    raise RuntimeError("file delivery is not configured")
                await self._deliver_file(path)
            except Exception as exc:
                reply = f"{reply}\nFile not sent ({path}): {exc}"
        return reply

    def _load_plugin(self, name: str) -> Any | None:
        if self._plugins_dir is None:
            return None
        plugin = PluginLoader(self._plugins_dir).load_plugin(name)
        if plugin is None:
            return None
        try:
            if not plugin.initialize(self._plugin_config):
                return None
        except Exception:
            return None
        return plugin

    async def run_plugin(self, name: str, payload: dict) -> PluginOutputSchema:
        if await self.blocked():
            raise MissionBlockedError("Kill switch is active")
        plugin = self._plugins.get(name)
        if plugin is None:
            plugin = await asyncio.to_thread(self._load_plugin, name)
            if plugin is not None:
                self._plugins[name] = plugin
        if plugin is None:
            return PluginOutputSchema(
                success=False, data={}, error_message=f"Unknown plugin: {name}"
            )
        output = await asyncio.to_thread(plugin.execute, payload)
        if output.success:
            await self._ledger.log_plugin_usage(name)
        return output

    async def sleep_mode_tick(self, now: datetime | None = None) -> Any:
        """Nightly batch maintenance: zero LLM tokens, once per local date."""
        now = now or datetime.now()
        if now.hour not in SLEEP_HOURS:
            return None
        today = now.strftime("%Y-%m-%d")
        if await self._ledger.get_system_state(SLEEP_MODE_LAST_RUN_KEY) == today:
            return None
        try:
            try:
                await self._memory.decay_plugins(now)
            except Exception:
                logger.exception("sleep-mode plugin decay failed; retrying next night")
            return await self._memory.vectorize_pending(now)
        except Exception:
            # One attempt per night: a missing model or a dead Chroma must not
            # hammer the disk all night; rows stay is_vectorized=False for retry.
            logger.exception("sleep-mode vectorization failed; retrying next night")
            return None
        finally:
            await self._ledger.set_system_state(SLEEP_MODE_LAST_RUN_KEY, today)

    async def resume_next_active(self, now: datetime | None = None) -> str | None:
        """Run one Active thread whose cooldown elapsed; crash/limit recovery."""
        now = now or datetime.now(timezone.utc)
        cutoff = (now - timedelta(seconds=RESUME_RETRY_SECONDS)).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )
        thread = await self._ledger.get_next_active_thread(ready_before=cutoff)
        if thread is None:
            return None
        thread_id = str(thread["thread_id"])
        if int(thread.get("attempt_count") or 0) >= MAX_THREAD_ATTEMPTS:
            await self._ledger.set_thread_status(thread_id, "Completed")
            await self._notice(
                f"Mission dropped after {MAX_THREAD_ATTEMPTS} attempts: "
                f"{thread['title']}"
            )
            return None

        entries = await self._ledger.list_ledger_entries(
            thread_id, limit=1, tags=("TINDAKAN",)
        )
        query = (
            entries[-1]["content"]
            if entries
            else thread.get("expected_output") or f"Lanjutkan misi: {thread['title']}"
        )
        return await self.handle_mission(
            thread_id, query, record_request=False, trigger="SCHEDULER_RESUME"
        )

    async def scheduled_tick(self, now: datetime | None = None) -> Any:
        await self.auto_resume()
        if await self.blocked():
            return None
        if await self._ledger.get_system_state(AGENT_PAUSE_REASON_KEY):
            return None
        if await self._limit_gate("AGENT") is not None:
            return None
        if self._wake_up is not None:
            self._wake_up()
        await self.resume_next_active()
        await self.sleep_mode_tick(now)
        self._tick_count += 1
        if self._tick_count % self._monologue_every_cycles:
            return None
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
