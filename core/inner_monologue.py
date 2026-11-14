"""Proactive initiative cycle gated by budget quotas and RPM/TPM throttling."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from core.budget_governor import (
    BudgetExceededError,
    BudgetGovernor,
    WAITING_FOR_QUOTA,
)


class InnerMonologue:
    def __init__(self, budget_governor: BudgetGovernor) -> None:
        self._budget = budget_governor

    def authorize_initiative(self, cost_usd: float) -> str:
        if self._budget.check_rate_limit() == WAITING_FOR_QUOTA:
            return "WAITING_FOR_QUOTA"
        try:
            self._budget.authorize_call(cost_usd, origin="AGENT")
        except BudgetExceededError:
            return "BUDGET_EXHAUSTED"
        return "ALLOWED"

    async def run_cycle(
        self,
        cost_usd: float,
        action: Callable[[], Awaitable[Any]],
    ) -> Any | None:
        if self.authorize_initiative(cost_usd) != "ALLOWED":
            return None
        return await action()