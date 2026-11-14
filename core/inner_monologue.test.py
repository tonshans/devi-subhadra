"""Unit tests for proactive initiative gating by budget and rate limits."""

import asyncio

import pytest

from core.budget_governor import BudgetGovernor
from core.inner_monologue import InnerMonologue


def _governor(**overrides) -> BudgetGovernor:
    params = {"max_initiative_budget_usd": 1.00, "rpm_limit": 60}
    params.update(overrides)
    return BudgetGovernor(**params)


def test_allows_initiative_and_reserves_atelier_budget():
    async def scenario():
        governor = _governor()
        monologue = InnerMonologue(governor)
        action_calls = []

        async def create_initiative():
            action_calls.append(True)
            return "initiative-1"

        result = await monologue.run_cycle(0.10, create_initiative)

        assert result == "initiative-1"
        assert action_calls == [True]
        assert governor.remaining_usd("ATELIER_30") == pytest.approx(0.20)
        assert governor.remaining_usd("CORE_70") == pytest.approx(0.70)

    asyncio.run(scenario())


def test_stops_when_atelier_budget_is_exhausted():
    async def scenario():
        governor = _governor()
        governor.authorize_call(0.30, origin="AGENT")
        monologue = InnerMonologue(governor)
        action_calls = []

        async def create_initiative():
            action_calls.append(True)
            return "initiative-1"

        assert monologue.authorize_initiative(0.10) == "BUDGET_EXHAUSTED"

        result = await monologue.run_cycle(0.10, create_initiative)

        assert result is None
        assert action_calls == []
        assert governor.remaining_usd("ATELIER_30") == 0

    asyncio.run(scenario())


def test_stops_when_rpm_quota_is_at_threshold():
    async def scenario():
        governor = _governor()
        governor.record_api_usage(requests=55, tokens=0)
        monologue = InnerMonologue(governor)
        action_calls = []

        async def create_initiative():
            action_calls.append(True)
            return "initiative-1"

        assert monologue.authorize_initiative(0.10) == "WAITING_FOR_QUOTA"

        result = await monologue.run_cycle(0.10, create_initiative)

        assert result is None
        assert action_calls == []
        assert governor.remaining_usd("ATELIER_30") == 0.30

    asyncio.run(scenario())