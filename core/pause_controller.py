"""Rate-limit pause lifecycle: global/agent gates, notices, and auto-resume."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from core.budget_governor import BudgetGovernor
from core.cognitive_ledger import (
    AGENT_PAUSE_REASON_KEY,
    AGENT_PAUSE_UNTIL_KEY,
    KILL_SWITCH_KEY,
    PAUSE_REASON_KEY,
    PAUSE_UNTIL_KEY,
    CognitiveLedger,
)

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


class PauseController:
    """Owns the pause kill switch and the per-side rate-limit gate."""

    def __init__(
        self,
        ledger: CognitiveLedger,
        governor: BudgetGovernor,
        notify: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        self._ledger = ledger
        self._governor = governor
        self._notify = notify

    async def _notice(self, message: str) -> None:
        if self._notify is None:
            return
        try:
            await self._notify(message)
        except Exception:
            logger.exception("pause notification failed")

    def pause_text(self, metric: str, *, agent_only: bool) -> str:
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

    async def pause_all(self, metric: str) -> str:
        reason = f"rate_limit:{metric}"
        message = self.pause_text(metric, agent_only=False)
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

    async def pause_agent(self, metric: str) -> str:
        reason = f"budget_ratio:{metric}"
        message = self.pause_text(metric, agent_only=True)
        previous = await self._ledger.get_system_state(AGENT_PAUSE_REASON_KEY)
        await self._ledger.set_system_state(AGENT_PAUSE_REASON_KEY, reason)
        await self._ledger.set_system_state(
            AGENT_PAUSE_UNTIL_KEY, self._governor.resume_at_iso(metric)
        )
        await self._ledger.pause_active_threads(origin="AGENT")
        if previous != reason:
            await self._notice(message)
        return message

    async def limit_gate(self, side: str) -> str | None:
        """Block new work when the global limit or the agent share is spent."""
        metric = self._governor.hard_limit_reached()
        if metric is not None:
            return await self.pause_all(metric)
        if side != "AGENT":
            return None
        reason = await self._ledger.get_system_state(AGENT_PAUSE_REASON_KEY)
        if reason and reason.startswith("budget_ratio:"):
            return self.pause_text(reason.split(":", 1)[1], agent_only=True)
        metric = self._governor.agent_limit_reached()
        if metric is not None:
            return await self.pause_agent(metric)
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
