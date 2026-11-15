"""Slash commands shared by the Telegram gateway and the local REPL.

Add a command by decorating an async handler in this module:

    @command("/hello", "Say hello.")
    async def hello(ctx: CommandContext, args: str) -> str:
        return "hello"

It becomes available on every surface that calls `dispatch`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from core.budget_governor import BudgetGovernor
from core.cognitive_ledger import (
    AGENT_FREETIME_KEY,
    AGENT_PAUSE_REASON_KEY,
    AGENT_PAUSE_UNTIL_KEY,
    BUDGET_RATIO_KEY,
    KILL_SWITCH_KEY,
    PAUSE_REASON_KEY,
    PAUSE_UNTIL_KEY,
    CognitiveLedger,
)
from core.config_loader import AppConfig

Handler = Callable[["CommandContext", str], Awaitable[str]]


@dataclass
class CommandContext:
    ledger: CognitiveLedger
    governor: BudgetGovernor
    settings: AppConfig
    started_at: float = field(default_factory=time.monotonic)
    # Optional provider pool (exposes status_lines) so /status shows each key.
    provider: Any | None = None


@dataclass(frozen=True)
class _Command:
    help_text: str
    handler: Handler


_REGISTRY: dict[str, _Command] = {}


def command(name: str, help_text: str):
    def register(handler: Handler) -> Handler:
        _REGISTRY[name] = _Command(help_text, handler)
        return handler

    return register


def available_commands() -> list[str]:
    return sorted(_REGISTRY)


async def dispatch(text: str, ctx: CommandContext) -> str | None:
    """Run a slash command; None means the text is a normal chat message."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    name, _, args = stripped.partition(" ")
    # Telegram groups address bots as /status@bot_name.
    name = name.split("@", 1)[0]
    entry = _REGISTRY.get(name)
    if entry is None:
        return f"Unknown command: {name}. Available: {', '.join(available_commands())}"
    return await entry.handler(ctx, args.strip())


def _uptime(seconds: float) -> str:
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s"


def _used_limit(used: int, limit: int) -> str:
    return f"{used}/unlimited" if limit == 0 else f"{used}/{limit}"


def _agent_usage(governor: BudgetGovernor, metric: str) -> str:
    cap = governor.agent_cap(metric)
    used = governor.used(metric, "AGENT")
    return f"{used}/unlimited" if cap is None else f"{used}/{cap}"


def _until_text(value: str | None) -> str:
    if value and value.endswith("Z"):
        return value[:-1].replace("T", " ") + " UTC"
    return value or "unknown"


async def load_governor_overrides(
    ledger: CognitiveLedger, governor: BudgetGovernor
) -> None:
    """Apply /budget-ratio and /agent-freetime overrides saved in system_state."""
    ratio = await ledger.get_system_state(BUDGET_RATIO_KEY)
    if ratio:
        try:
            governor.set_budget_ratio_agent(float(ratio))
        except ValueError:
            pass
    freetime = await ledger.get_system_state(AGENT_FREETIME_KEY)
    if freetime in {"0", "1"}:
        governor.set_agent_freetime(freetime == "1")


@command("/status", "Agent, budget, limit, and activity snapshot (no LLM calls).")
async def status(ctx: CommandContext, _args: str) -> str:
    settings = ctx.settings
    governor = ctx.governor
    logs = await ctx.ledger.list_activity_logs(limit=1)
    last = logs[0] if logs else None
    active_threads = await ctx.ledger.list_threads(status="Active")
    paused = await ctx.ledger.get_system_state(KILL_SWITCH_KEY) == "1"
    pause_reason = await ctx.ledger.get_system_state(PAUSE_REASON_KEY)
    pause_until = await ctx.ledger.get_system_state(PAUSE_UNTIL_KEY)
    agent_pause = await ctx.ledger.get_system_state(AGENT_PAUSE_REASON_KEY)
    agent_pause_until = await ctx.ledger.get_system_state(AGENT_PAUSE_UNTIL_KEY)

    state = "paused" if paused else "running"
    if paused and pause_reason and pause_reason != "manual":
        state += f" ({pause_reason}) until {_until_text(pause_until)}"

    core_quota = settings.budget.max_initiative_budget_usd * settings.budget.core_task_ratio
    atelier_quota = (
        settings.budget.max_initiative_budget_usd * settings.budget.atelier_ratio
    )
    agent_ratio = governor.budget_ratio_agent
    human_pct = round((1 - agent_ratio) * 100)
    agent_pct = round(agent_ratio * 100)
    freetime = (
        "on (agent may use the full global limit)"
        if governor.agent_freetime
        else "off"
    )
    lines = [
        f"agent: {settings.agent_name}",
        f"state: {state}",
        f"uptime: {_uptime(time.monotonic() - ctx.started_at)}",
        f"db: {ctx.ledger.dialect}",
        f"model: {settings.models.tier_2_heavy} "
        f"(light: {settings.models.tier_1_light})",
        f"context: {last['tokens_used'] if last else 0}/"
        f"{settings.rate_limits.max_context_tokens} tokens (last mission)",
        f"budget: core ${governor.remaining_usd('CORE_70'):.4f} left of "
        f"${core_quota:.2f}; atelier "
        f"${governor.remaining_usd('ATELIER_30'):.4f} left of "
        f"${atelier_quota:.2f}",
        f"rate limit: rpm {_used_limit(governor.used('rpm'), governor.limit('rpm'))}"
        f" | tpm {_used_limit(governor.used('tpm'), governor.limit('tpm'))}",
        f"            rpd {_used_limit(governor.used('rpd'), governor.limit('rpd'))}"
        f" | tpd {_used_limit(governor.used('tpd'), governor.limit('tpd'))}",
        f"agent share: rpm {_agent_usage(governor, 'rpm')}"
        f" | tpm {_agent_usage(governor, 'tpm')}",
        f"             rpd {_agent_usage(governor, 'rpd')}"
        f" | tpd {_agent_usage(governor, 'tpd')}",
        f"budget ratio: human {human_pct}% (reserved minimum) | "
        f"agent {agent_pct}%",
        f"agent freetime: {freetime}",
    ]
    if ctx.provider is not None:
        lines.extend(ctx.provider.status_lines())
    if agent_pause:
        lines.append(
            f"agent state: paused ({agent_pause}) until "
            f"{_until_text(agent_pause_until)}"
        )
    lines.extend(
        [
            f"threads: {len(active_threads)} active",
            f"last activity: {last['created_at'] if last else 'none'}",
        ]
    )
    return "\n".join(lines)


@command("/budget-ratio", "Show or set the agent share of provider limits.")
async def budget_ratio(ctx: CommandContext, args: str) -> str:
    parts = args.split()
    if parts:
        if len(parts) != 2:
            return "Usage: /budget-ratio <human> <agent> (whole numbers summing to 100)"
        try:
            human, agent = int(parts[0]), int(parts[1])
        except ValueError:
            return "Usage: /budget-ratio <human> <agent> (whole numbers summing to 100)"
        if human < 0 or agent < 0 or human + agent != 100:
            return "Human and agent must be 0-100 and sum to 100."
        ratio = agent / 100
        ctx.governor.set_budget_ratio_agent(ratio)
        await ctx.ledger.set_system_state(BUDGET_RATIO_KEY, str(ratio))
        return (
            f"Budget ratio set: human {human}% (reserved minimum) | "
            f"agent {agent}%. Agent share applies to RPM, TPM, RPD, and TPD."
        )

    governor = ctx.governor
    agent_ratio = governor.budget_ratio_agent
    return "\n".join(
        [
            f"budget ratio: human {round((1 - agent_ratio) * 100)}% "
            f"(reserved minimum) | agent {round(agent_ratio * 100)}%",
            f"agent share: rpm {_agent_usage(governor, 'rpm')}"
            f" | tpm {_agent_usage(governor, 'tpm')}",
            f"             rpd {_agent_usage(governor, 'rpd')}"
            f" | tpd {_agent_usage(governor, 'tpd')}",
            f"agent freetime: {'on' if governor.agent_freetime else 'off'}",
        ]
    )


@command("/agent-freetime", "Let agent initiatives use the full global limit.")
async def agent_freetime(ctx: CommandContext, args: str) -> str:
    action = args.strip().lower()
    if action in {"", "status"}:
        return f"agent freetime: {'on' if ctx.governor.agent_freetime else 'off'}"
    if action not in {"on", "off"}:
        return "Usage: /agent-freetime on|off|status"
    enabled = action == "on"
    ctx.governor.set_agent_freetime(enabled)
    await ctx.ledger.set_system_state(AGENT_FREETIME_KEY, "1" if enabled else "0")
    if enabled:
        return (
            "Agent freetime on: initiatives may use the full RPM/TPM/RPD/TPD "
            "limits (global limits still apply)."
        )
    return "Agent freetime off: the budget ratio applies to agent initiatives again."


@command("/pause-agent", "Stop all agent activity and LLM calls.")
async def pause_agent(ctx: CommandContext, _args: str) -> str:
    # Flag first so no new mission slips in while threads are paused.
    await ctx.ledger.set_system_state(KILL_SWITCH_KEY, "1")
    await ctx.ledger.set_system_state(PAUSE_REASON_KEY, "manual")
    await ctx.ledger.set_system_state(PAUSE_UNTIL_KEY, "")
    paused = await ctx.ledger.pause_active_threads()
    return f"Agent paused ({paused} active thread(s) paused)."


@command("/resume-agent", "Resume agent activity and LLM calls.")
async def resume_agent(ctx: CommandContext, _args: str) -> str:
    # Flag first: new missions are allowed once the queue is reopened.
    await ctx.ledger.set_system_state(KILL_SWITCH_KEY, "0")
    await ctx.ledger.set_system_state(PAUSE_REASON_KEY, "")
    await ctx.ledger.set_system_state(PAUSE_UNTIL_KEY, "")
    await ctx.ledger.set_system_state(AGENT_PAUSE_REASON_KEY, "")
    await ctx.ledger.set_system_state(AGENT_PAUSE_UNTIL_KEY, "")
    resumed = await ctx.ledger.unpause_threads()
    return f"Agent resumed ({resumed} thread(s) reopened)."
