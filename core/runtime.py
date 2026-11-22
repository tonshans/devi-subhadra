"""Composition root: the only place that wires ledger, budget, context, worker, plugins."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import uuid
from contextvars import ContextVar
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
    KILL_SWITCH_KEY,
    CognitiveLedger,
)
from core.context_compiler import ContextCompiler
from core.halt import find_halt_file, halt_message
from core.healer_node import HealerNode, ProposePatch
from core.inner_monologue import InnerMonologue
from core.memory_manager import MemoryManager
from core.pause_controller import PauseController
from core.plugin_loader import PluginLoader, build_extra_tools_prompt
from core.provider_pool import AllProvidersUnavailable
from core.scheduled_task_runner import ScheduledTaskRunner
from core.scheduled_task_store import ScheduledTaskStore
from core.schemas import (
    HealerPatchProposal,
    MissionReply,
    PluginOutputSchema,
    SupervisorDecision,
)
from core.supervisor import Supervisor
from core.vector_store import VectorStore

Worker = Callable[..., Awaitable[object]]
Initiative = Callable[[], Awaitable[Any]]

logger = logging.getLogger(__name__)

# Mission origin of the caller running a plugin; "AGENT" unless set by a human.
_MISSION_ORIGIN: ContextVar[str] = ContextVar("mission_origin", default="AGENT")

_UNTIL_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
SLEEP_MODE_LAST_RUN_KEY = "sleep_mode_last_run"
# Local hours when Sleep Mode may run its batch job (00:00-05:59).
SLEEP_HOURS = frozenset(range(0, 6))
MAX_THREAD_ATTEMPTS = 3
RESUME_RETRY_SECONDS = 300
# One mission may call at most this many tools before it must answer.
MAX_TOOL_ROUNDS = 3


class MissionBlockedError(Exception):
    """Raised when the Overseer kill switch refuses new work."""


def load_plugins(
    plugins_dir: str | Path,
    config: dict,
    core_plugins: frozenset[str] | set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Discover plugins once: eager Tier 1 tools plus the Tier 2 catalog."""
    loader = PluginLoader(plugins_dir, core_plugins=core_plugins)
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
        halt_dir: str | Path = "/mnt/workspace",
        workspace_dir: str | Path = ".",
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
        self._propose_patch = propose_patch
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
        self._halt_dir = halt_dir
        self._workspace_dir = workspace_dir
        self._deliver_file = deliver_file
        self._notify = notify
        self._pause_controller = PauseController(
            ledger, governor, notify=self._notice
        )
        self._scheduled_tasks = ScheduledTaskStore()
        self._scheduled_task_runner = ScheduledTaskRunner(
            self._scheduled_tasks,
            notify=self._notice,
            run_plugin=self.run_plugin,
            read_row=self._ledger.read,
        )

    @property
    def provider(self) -> Any | None:
        """The worker when it is a provider pool (it exposes status_lines)."""
        return self._worker if hasattr(self._worker, "status_lines") else None

    async def _default_propose_patch(self, attempt: int, error: str) -> Any:
        """Ask the configured proposer, else the worker, for a patch."""
        if self._propose_patch is not None:
            return await self._propose_patch(attempt, error)
        if self._worker is not None:
            prompt = (
                f"HEALER ATTEMPT {attempt}. The worker failed with:\n{error}\n"
                "Propose a minimal patch that fixes the root cause. patched_code "
                "must be self-contained and embedded_assertions must verify it."
            )
            try:
                proposal = await self._worker(
                    prompt, structured_model=HealerPatchProposal
                )
            except Exception as exc:
                raise RuntimeError(
                    f"No patch proposer configured: worker cannot propose ({exc})"
                ) from exc
            if isinstance(proposal, HealerPatchProposal):
                return proposal
        raise RuntimeError("No patch proposer configured")

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
            "Choose CREATE_INITIATIVE with a concrete next step toward the vision, "
            "REPLY_USER to ask your partner (tonhan) a question/share an idea, "
            "or SLEEP_NOOP when nothing useful remains today. REPLY_USER requires "
            "reply_message to hold the exact message for tonhan; keep analysis in "
            "thought_entry."
        )

    async def _curiosity_cycle(self) -> None:
        """One LLM thought per cycle, capped per UTC day, from the idle loop."""
        if self._worker is None or self._curiosity_limit <= 0 or self.halt_file():
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
        try:
            self._worker.last_usage = None
        except AttributeError:
            pass
        tokens = (
            int(usage.get("prompt_tokens", 0))
            + int(usage.get("completion_tokens", 0))
            if usage
            else 0
        )
        self._governor.record_api_usage(1, tokens, side="AGENT")
        if not isinstance(decision, SupervisorDecision):
            return

        await self._ledger.set_system_state(key, str(used + 1))
        thinking = decision.action_type == "CREATE_INITIATIVE"
        
        if decision.action_type == "REPLY_USER":
            if not decision.reply_message:
                logger.warning("REPLY_USER without reply_message; using thought_entry")
            message = (decision.reply_message or decision.thought_entry).strip()
            thread_id = await self._ledger.create_thread(
                f"Partner Ping: {message[:60]}", "AGENT", status="Completed"
            )
            entry_id = await self._ledger.create(
                "ledger_entries",
                thread_id=thread_id,
                tag="FAKTA",
                content=message,
                token_count=self._compiler.estimate_tokens(message),
            )
            delivered = await self._notice(f"[Partner Ping]: {message}")
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
                action="REPLY_USER",
                response=message,
                response_tokens=int((usage or {}).get("completion_tokens", 0)),
                outcome="SUCCESS" if delivered else "FAILED",
                result_table="ledger_entries",
                result_id=entry_id,
            )
            return

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

    def halt_file(self) -> str | None:
        return find_halt_file(self._halt_dir, self._workspace_dir)

    def halt_notice(self) -> str | None:
        return halt_message(self._halt_dir, self._workspace_dir)

    async def blocked(self) -> bool:
        if self.halt_file() is not None:
            return True
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

    async def _notice(self, message: str) -> bool:
        if self._notify is None:
            return False
        try:
            await self._notify(message)
        except Exception:
            logger.exception("notification failed")
            return False
        logger.info("notification sent chars=%s", len(message))
        return True

    def _pause_text(self, metric: str, *, agent_only: bool) -> str:
        return self._pause_controller.pause_text(metric, agent_only=agent_only)

    async def _pause_all(self, metric: str) -> str:
        return await self._pause_controller.pause_all(metric)

    async def _pause_agent(self, metric: str) -> str:
        return await self._pause_controller.pause_agent(metric)

    async def _limit_gate(self, side: str) -> str | None:
        return await self._pause_controller.limit_gate(side)

    async def auto_resume(self) -> bool:
        """Lift rate-limit pauses once their window reset; manual pauses stay."""
        return await self._pause_controller.auto_resume()

    async def pause_notice(self) -> str | None:
        """Human-readable reason when a rate limit (not a manual pause) blocks."""
        return await self._pause_controller.pause_notice()

    def _token_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens / 1_000_000 * self._input_rate
            + completion_tokens / 1_000_000 * self._output_rate
        )

    async def _tool_catalog_prompt(self) -> str:
        rows = await self._ledger.list_plugins("ATELIER", include_archived=False)
        # run_plugin caches lazily loaded Tier 2 plugins here, so catalog names
        # are excluded to keep only the configured core tools.
        core_rows = [
            {
                "plugin_name": name,
                "description_summary": getattr(plugin, "description", name),
            }
            for name, plugin in self._plugins.items()
            if name not in self._plugin_catalog
        ]
        return build_extra_tools_prompt(
            rows, self._archived_plugins_dir, core_rows
        )

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

        thread = await self._ledger.read("ledger_threads", thread_id)
        if thread is None:
            raise ValueError(f"unknown thread: {thread_id}")

        side = thread["origin"]
        trigger = (trigger or initiator).upper()
        trace_id = str(uuid.uuid4())
        step = 0
        last_audit_id: str | None = None

        notice = self.halt_notice()
        if notice is not None:
            await self._audit(
                trace_id=trace_id,
                step=1,
                thread_id=thread_id,
                trigger=trigger,
                origin=side,
                prompt=query,
                prompt_tokens=self._compiler.estimate_tokens(query),
                action="BLOCKED",
                response=notice,
                outcome="BLOCKED",
            )
            return notice
        if await self.blocked():
            raise MissionBlockedError("Kill switch is active")
        if self._worker is None:
            raise RuntimeError("No worker configured")

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

        context = await self._prepare_mission_context(
            thread_id, query, record_request=record_request
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
            notice = self.halt_notice()
            if notice is not None:
                # Stop mid-mission: no further LLM call, tool run, or billing.
                step += 1
                last_audit_id = await self._audit(
                    trace_id=trace_id,
                    step=step,
                    thread_id=thread_id,
                    trigger=trigger,
                    origin=side,
                    action="BLOCKED",
                    response=notice,
                    outcome="BLOCKED",
                )
                reply = notice
                worker_succeeded = False
                break
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
                run_worker, self._default_propose_patch
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
            notice = self.halt_notice()
            if notice is not None:
                # Never start a side-effecting tool after a halt request.
                step += 1
                last_audit_id = await self._audit(
                    trace_id=trace_id,
                    step=step,
                    thread_id=thread_id,
                    trigger=trigger,
                    origin=side,
                    action="BLOCKED",
                    action_target=plugin,
                    response=notice,
                    outcome="BLOCKED",
                )
                reply = notice
                worker_succeeded = False
                break
            tool_rounds += 1
            tool_started = time.perf_counter()
            token = _MISSION_ORIGIN.set(side)
            try:
                output = await self.run_plugin(
                    plugin, raw_reply.plugin_payload or {}
                )
            finally:
                _MISSION_ORIGIN.reset(token)
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

        return await self._record_mission_completion(
            thread_id=thread_id,
            reply=reply,
            files=files,
            side=side,
            initiator=initiator,
            worker_succeeded=worker_succeeded,
            outcome_status=outcome_status,
            bucket=bucket,
            tokens_used=context.token_count,
            cost=cost,
            last_audit_id=last_audit_id,
        )

    async def _prepare_mission_context(
        self, thread_id: str, query: str, *, record_request: bool
    ) -> Any:
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
        return context

    async def _record_mission_completion(
        self,
        *,
        thread_id: str,
        reply: str,
        files: list[str],
        side: str,
        initiator: str,
        worker_succeeded: bool,
        outcome_status: str,
        bucket: str,
        tokens_used: int,
        cost: float,
        last_audit_id: str | None,
    ) -> str:
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
            tokens_used=tokens_used,
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

    async def _call_plugin(
        self, plugin: Any, payload: dict
    ) -> PluginOutputSchema:
        if inspect.iscoroutinefunction(plugin.execute):
            return await plugin.execute(payload)
        output = await asyncio.to_thread(plugin.execute, payload)
        if inspect.iscoroutine(output):
            output = await output
        return output

    async def run_plugin(self, name: str, payload: dict) -> PluginOutputSchema:
        notice = self.halt_notice()
        if notice is not None:
            raise MissionBlockedError(notice)
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
        if name in {"scheduler", "ethereum_wallet"}:
            # Kill tasks trust this field, so the runtime stamps it, not the LLM.
            payload = {**payload, "origin": _MISSION_ORIGIN.get()}
        output = await self._call_plugin(plugin, payload)
        if (
            name == "sandbox_executor"
            and not output.success
            and not output.assertions_passed
            and self._propose_patch is not None
        ):
            healed: list[PluginOutputSchema] = []

            async def execute_patch(patched_code: str) -> PluginOutputSchema:
                result = await self._call_plugin(
                    plugin,
                    {
                        "code": patched_code,
                        "language": payload.get("language", "python"),
                        "timeout_seconds": payload.get("timeout_seconds", 10),
                    },
                )
                healed.append(result)
                return result

            heal_result = await self._supervisor._healer.heal(
                error=output.error_message or "sandbox assertion failed",
                execute_patch=execute_patch,
                propose_patch=self._propose_patch,
            )
            if heal_result.success and healed:
                output = healed[-1]
        if output.success and name == "register_new_skill":
            reg_name = output.data.get("plugin_name")
            if isinstance(reg_name, str) and reg_name:
                reg_desc = (
                    output.data.get("description")
                    or self._plugin_catalog.get(reg_name)
                    or reg_name
                )
                if output.data.get("registered_in_ledger") is False:
                    await self._ledger.register_plugin(
                        reg_name, "ATELIER", reg_desc
                    )
                self._plugin_catalog[reg_name] = reg_desc
                # Force the next run_plugin() call to import the new version.
                self._plugins.pop(reg_name, None)
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
            decay_memories = getattr(self._memory, "decay_memories", None)
            if decay_memories is not None:
                try:
                    await decay_memories(now)
                except Exception:
                    logger.exception(
                        "sleep-mode memory decay failed; retrying next night"
                    )
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
        reply = await self.handle_mission(
            thread_id, query, record_request=False, trigger="SCHEDULER_RESUME"
        )
        thread = await self._ledger.read("ledger_threads", thread_id)
        if reply and thread is not None and thread.get("status") == "Completed":
            await self._notice(reply)
        return reply

    async def scheduled_tick(self, now: datetime | None = None) -> Any:
        await self._scheduled_task_runner.tick(now)
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
