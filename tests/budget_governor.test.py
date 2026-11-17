"""Unit tests for the 70/30 budget gate and RPM/TPM throttling tracker."""

from datetime import datetime, timezone

import pytest

from core.budget_governor import (
    BudgetExceededError,
    BudgetGovernor,
    SqliteBudgetStore,
)


def _governor(**overrides) -> BudgetGovernor:
    params = {
        "max_initiative_budget_usd": 1.00,
        "rpm_limit": 60,
        "tpm_limit": 120000,
    }
    params.update(overrides)
    return BudgetGovernor(**params)


def test_splits_daily_budget_into_70_30_quotas():
    governor = _governor()

    assert governor.core_budget_usd == pytest.approx(0.70)
    assert governor.atelier_budget_usd == pytest.approx(0.30)
    assert governor.remaining_usd("CORE_70") == pytest.approx(0.70)
    assert governor.remaining_usd("ATELIER_30") == pytest.approx(0.30)


def test_rejects_unendorsed_atelier_initiative_over_quota():
    governor = _governor()

    with pytest.raises(BudgetExceededError):
        governor.authorize_call(0.35, origin="AGENT", is_human_endorsed=False)

    assert governor.remaining_usd("ATELIER_30") == pytest.approx(0.30)
    assert governor.remaining_usd("CORE_70") == pytest.approx(0.70)


def test_allows_and_deducts_unendorsed_atelier_cost_within_quota():
    governor = _governor()

    bucket = governor.authorize_call(0.20, origin="AGENT", is_human_endorsed=False)

    assert bucket == "ATELIER_30"
    assert governor.remaining_usd("ATELIER_30") == pytest.approx(0.10)
    assert governor.remaining_usd("CORE_70") == pytest.approx(0.70)


def test_rejects_when_cumulative_budget_is_exhausted():
    governor = _governor()
    governor.authorize_call(0.20, origin="AGENT", is_human_endorsed=False)

    with pytest.raises(BudgetExceededError):
        governor.authorize_call(0.20, origin="AGENT", is_human_endorsed=False)

    assert governor.remaining_usd("ATELIER_30") == pytest.approx(0.10)


def test_endorsed_agent_cost_charges_core_bucket_not_atelier():
    governor = _governor()

    bucket = governor.authorize_call(0.35, origin="AGENT", is_human_endorsed=True)

    assert bucket == "CORE_70"
    assert governor.remaining_usd("CORE_70") == pytest.approx(0.35)
    assert governor.remaining_usd("ATELIER_30") == pytest.approx(0.30)


def test_human_mission_is_exempt_from_initiative_budget():
    governor = _governor()

    bucket = governor.authorize_call(99.00, origin="HUMAN", is_human_endorsed=False)

    assert bucket == "HUMAN_EXEMPT"
    assert governor.remaining_usd("CORE_70") == pytest.approx(0.70)
    assert governor.remaining_usd("ATELIER_30") == pytest.approx(0.30)


@pytest.mark.parametrize("origin", ["BOT", ""])
def test_authorize_rejects_invalid_origin_or_negative_cost(origin):
    governor = _governor()

    with pytest.raises(ValueError):
        governor.authorize_call(0.01, origin=origin)

    with pytest.raises(ValueError):
        governor.authorize_call(-0.01, origin="AGENT")


def test_rate_limit_below_threshold_returns_ok():
    governor = _governor()
    governor.record_api_usage(requests=53, tokens=0)

    assert governor.check_rate_limit() == "OK"


@pytest.mark.parametrize("requests", [54, 55])
def test_rate_limit_at_or_above_90_percent_rpm_waits(requests):
    governor = _governor()
    governor.record_api_usage(requests=requests, tokens=0)

    assert governor.check_rate_limit() == "WAITING_FOR_QUOTA"


def test_rate_limit_at_90_percent_tpm_waits():
    governor = _governor(tpm_limit=1000)
    governor.record_api_usage(requests=1, tokens=900)

    assert governor.check_rate_limit() == "WAITING_FOR_QUOTA"


def test_rate_limit_below_90_percent_tpm_returns_ok():
    governor = _governor(tpm_limit=1000)
    governor.record_api_usage(requests=1, tokens=899)

    assert governor.check_rate_limit() == "OK"


def test_rate_usage_accumulates_across_calls():
    governor = _governor()
    governor.record_api_usage(requests=30, tokens=0)
    governor.record_api_usage(requests=25, tokens=0)

    assert governor.check_rate_limit() == "WAITING_FOR_QUOTA"


def test_settle_charges_or_refunds_the_actual_cost():
    governor = _governor()
    bucket = governor.authorize_call(0.05, origin="AGENT")

    governor.settle(bucket, reserved=0.05, actual=0.20)
    assert governor.remaining_usd("ATELIER_30") == pytest.approx(0.10)

    governor.settle(bucket, reserved=0.20, actual=0.10)
    assert governor.remaining_usd("ATELIER_30") == pytest.approx(0.20)


def test_settle_persists_actual_cost_to_sqlite(tmp_path):
    governor = BudgetGovernor(
        max_initiative_budget_usd=1.00,
        store=SqliteBudgetStore(tmp_path / "budget.db"),
    )
    bucket = governor.authorize_call(0.05, origin="AGENT")
    governor.settle(bucket, reserved=0.05, actual=0.20)

    restarted = BudgetGovernor(
        max_initiative_budget_usd=1.00,
        store=SqliteBudgetStore(tmp_path / "budget.db"),
    )

    assert restarted.remaining_usd("ATELIER_30") == pytest.approx(0.10)


def test_sqlite_store_persists_spend_across_restart(tmp_path):
    first = BudgetGovernor(
        max_initiative_budget_usd=1.00,
        store=SqliteBudgetStore(tmp_path / "budget.db"),
    )
    first.authorize_call(0.20, origin="AGENT")

    restarted = BudgetGovernor(
        max_initiative_budget_usd=1.00,
        store=SqliteBudgetStore(tmp_path / "budget.db"),
    )

    assert restarted.remaining_usd("ATELIER_30") == pytest.approx(0.10)
    with pytest.raises(BudgetExceededError):
        restarted.authorize_call(0.20, origin="AGENT")


def test_sqlite_store_resets_on_new_daily_window(tmp_path):
    now = [datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)]
    governor = BudgetGovernor(
        max_initiative_budget_usd=1.00,
        store=SqliteBudgetStore(tmp_path / "budget.db"),
        clock=lambda: now[0],
    )
    governor.authorize_call(0.20, origin="AGENT")
    assert governor.remaining_usd("ATELIER_30") == pytest.approx(0.10)

    now[0] = datetime(2026, 1, 2, 0, 1, tzinfo=timezone.utc)

    assert governor.remaining_usd("ATELIER_30") == pytest.approx(0.30)


def test_agent_share_limits_only_the_agent_side():
    governor = _governor(rpm_limit=10, budget_ratio_agent=0.5)

    governor.record_api_usage(5, side="AGENT")

    assert governor.agent_limit_reached() == "RPM"
    assert governor.hard_limit_reached() is None

    governor.record_api_usage(5, side="HUMAN")

    assert governor.hard_limit_reached() == "RPM"


def test_agent_freetime_ignores_share_but_keeps_the_global_limit():
    governor = _governor(rpm_limit=10, budget_ratio_agent=0.5)
    governor.set_agent_freetime(True)

    governor.record_api_usage(5, side="AGENT")
    assert governor.agent_limit_reached() is None

    governor.record_api_usage(5, side="AGENT")
    assert governor.hard_limit_reached() == "RPM"


def test_zero_limit_is_unlimited_and_zero_ratio_blocks_agent():
    governor = _governor(rpm_limit=0, tpm_limit=0, rpd_limit=0, tpd_limit=0)
    governor.record_api_usage(1000, 10**9)

    assert governor.hard_limit_reached() is None
    assert governor.check_rate_limit() == "OK"

    strict = _governor(rpm_limit=10, budget_ratio_agent=0.0)
    assert strict.agent_cap("rpm") == 0
    assert strict.agent_limit_reached() == "RPM"


def test_daily_usage_resets_when_the_utc_day_changes():
    now = [datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)]
    governor = _governor(rpd_limit=2, clock=lambda: now[0])
    governor.record_api_usage(2, side="HUMAN")

    assert governor.hard_limit_reached() == "RPD"
    assert governor.resume_at_iso("RPD") == "2026-01-02T00:00:00Z"

    now[0] = datetime(2026, 1, 2, 0, 1, tzinfo=timezone.utc)

    assert governor.hard_limit_reached() is None
    assert governor.used("rpd") == 0


def test_usage_persists_across_restart(tmp_path):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    path = tmp_path / "usage.db"
    first = BudgetGovernor(
        1.00, rpm_limit=10, store=SqliteBudgetStore(path), clock=lambda: now
    )
    first.record_api_usage(3, 100, side="AGENT")

    restarted = BudgetGovernor(
        1.00, rpm_limit=10, store=SqliteBudgetStore(path), clock=lambda: now
    )

    assert restarted.used("rpm") == 3
    assert restarted.used("tpm", "AGENT") == 100
    assert restarted.used("tpd", "HUMAN") == 0


def test_per_key_usage_persists_across_restart(tmp_path):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    path = tmp_path / "usage.db"
    first = BudgetGovernor(
        1.00, rpm_limit=10, store=SqliteBudgetStore(path), clock=lambda: now
    )
    first.record_key_usage("primary", 2, 300)

    restarted = BudgetGovernor(
        1.00, rpm_limit=10, store=SqliteBudgetStore(path), clock=lambda: now
    )

    assert restarted.key_used("primary", "rpm") == 2
    assert restarted.key_used("primary", "tpm") == 300
    assert restarted.key_used("secondary", "rpm") == 0