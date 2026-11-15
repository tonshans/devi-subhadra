"""Tests for the provider key pool: rotation, cooldowns, and all-down notices."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from core.budget_governor import BudgetGovernor
from core.provider_pool import (
    AllProvidersUnavailable,
    ProviderKey,
    ProviderPool,
)

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _status_error(status: int, headers: dict | None = None):
    request = httpx.Request("POST", "https://example.test/generate")
    response = httpx.Response(status, request=request, headers=headers or {})
    return httpx.HTTPStatusError("boom", request=request, response=response)


class _FakeWorker:
    """Stands in for GeminiWorker: records calls, returns, raises, or reports usage."""

    def __init__(self, outcome, usage=None):
        self.outcome = outcome
        self.usage = usage
        self.calls = 0
        self.last_usage = None

    async def __call__(self, prompt):
        self.calls += 1
        self.last_usage = self.usage
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _pool(tmp_path, outcomes, *, clock=None, **key_limits):
    now = clock if clock is not None else [_NOW]
    governor = BudgetGovernor(1.00, clock=lambda: now[0])
    keys = []
    workers = {}
    for key_id, value in outcomes.items():
        outcome, usage = value if isinstance(value, tuple) else (value, None)
        keys.append(ProviderKey(id=key_id, secret=f"secret-{key_id}", **key_limits))
        workers[key_id] = _FakeWorker(outcome, usage)
    pool = ProviderPool(keys, lambda key: workers[key.id], governor)
    return pool, workers, governor, now


def test_fails_over_to_next_key_on_rate_limit(tmp_path):
    pool, workers, governor, _ = _pool(
        tmp_path,
        {"primary": _status_error(429), "secondary": "jawaban"},
    )

    assert asyncio.run(pool("halo")) == "jawaban"

    assert workers["primary"].calls == 1
    assert workers["secondary"].calls == 1
    assert governor.key_used("primary", "rpm") == 1
    assert governor.key_used("secondary", "rpm") == 1
    assert pool.ready_count == 1


def test_rate_limited_key_is_skipped_until_cooldown_ends(tmp_path):
    pool, workers, _, now = _pool(
        tmp_path,
        {"primary": _status_error(429), "secondary": "jawaban"},
    )

    asyncio.run(pool("halo"))
    asyncio.run(pool("lagi"))
    assert workers["primary"].calls == 1

    now[0] = _NOW + timedelta(minutes=2)
    asyncio.run(pool("lagi"))
    assert workers["primary"].calls == 2


def test_retry_after_header_sets_the_cooldown(tmp_path):
    error = _status_error(429, headers={"retry-after": "5"})
    pool, workers, _, now = _pool(
        tmp_path, {"primary": error, "secondary": "jawaban"}
    )

    asyncio.run(pool("halo"))
    now[0] = _NOW + timedelta(seconds=4)
    asyncio.run(pool("halo"))
    assert workers["primary"].calls == 1

    now[0] = _NOW + timedelta(seconds=6)
    asyncio.run(pool("halo"))
    assert workers["primary"].calls == 2


def test_fails_over_on_server_error_and_network_error(tmp_path):
    pool, _, _, _ = _pool(
        tmp_path,
        {"primary": _status_error(503), "secondary": "jawaban"},
    )
    assert asyncio.run(pool("halo")) == "jawaban"

    pool, _, _, _ = _pool(
        tmp_path,
        {"primary": httpx.ConnectError("down"), "secondary": "jawaban"},
    )
    assert asyncio.run(pool("halo")) == "jawaban"
    assert pool.ready_count == 1


def test_auth_error_disables_the_key_for_the_process(tmp_path):
    pool, workers, _, now = _pool(
        tmp_path,
        {"primary": _status_error(401), "secondary": "jawaban"},
    )

    asyncio.run(pool("halo"))
    now[0] = _NOW + timedelta(hours=1)
    asyncio.run(pool("halo"))

    assert workers["primary"].calls == 1
    assert "disabled (auth error)" in "\n".join(pool.status_lines())


def test_request_error_fails_fast_without_marking_the_key(tmp_path):
    pool, workers, governor, _ = _pool(
        tmp_path, {"primary": _status_error(400), "secondary": "jawaban"}
    )

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(pool("halo"))

    assert workers["secondary"].calls == 0
    assert pool.ready_count == 2
    assert governor.key_used("primary", "rpm") == 1


def test_local_per_key_limit_skips_the_key(tmp_path):
    pool, workers, _, _ = _pool(
        tmp_path,
        {"primary": "jawaban", "secondary": "cadangan"},
        rpm_limit=1,
    )

    assert asyncio.run(pool("halo")) == "jawaban"
    assert asyncio.run(pool("halo")) == "cadangan"
    assert workers["primary"].calls == 1


def test_all_down_notifies_once_per_episode(tmp_path):
    pool, _, _, _ = _pool(
        tmp_path, {"primary": _status_error(429), "secondary": _status_error(500)}
    )

    with pytest.raises(AllProvidersUnavailable) as first:
        asyncio.run(pool("halo"))
    with pytest.raises(AllProvidersUnavailable) as second:
        asyncio.run(pool("halo"))

    assert first.value.should_notify is True
    assert second.value.should_notify is False
    assert "primary rate limited until" in first.value.message
    assert "secondary HTTP 500 until" in first.value.message
    assert "Retrying automatically after" in first.value.message


def test_success_resets_the_down_episode(tmp_path):
    now = [_NOW]
    governor = BudgetGovernor(1.00, clock=lambda: now[0])
    outcome = {"value": _status_error(500)}
    worker = _FakeWorker(None)

    async def call(prompt):
        worker.outcome = outcome["value"]
        return await worker(prompt)

    pool = ProviderPool(
        [ProviderKey(id="only", secret="s")], lambda key: call, governor
    )

    with pytest.raises(AllProvidersUnavailable) as first:
        asyncio.run(pool("halo"))
    assert first.value.should_notify is True

    now[0] = _NOW + timedelta(minutes=2)
    outcome["value"] = "pulih"
    assert asyncio.run(pool("halo")) == "pulih"

    outcome["value"] = _status_error(500)
    with pytest.raises(AllProvidersUnavailable) as again:
        asyncio.run(pool("halo"))
    assert again.value.should_notify is True


def test_usage_from_successful_call_is_recorded_per_key(tmp_path):
    usage = {"prompt_tokens": 10, "completion_tokens": 5}
    pool, _, governor, _ = _pool(tmp_path, {"primary": ("jawaban", usage)})

    asyncio.run(pool("halo"))

    assert pool.last_usage == usage
    assert governor.key_used("primary", "rpm") == 1
    assert governor.key_used("primary", "tpm") == 15


def test_status_lines_show_id_and_usage(tmp_path):
    pool, _, _, _ = _pool(tmp_path, {"primary": "ok"}, rpm_limit=15)

    asyncio.run(pool("halo"))
    lines = pool.status_lines()

    assert lines[0] == "provider: gemini (1 keys)"
    assert lines[1] == (
        "  primary: ready | rpm 1/15 | tpm 0/unlimited | "
        "rpd 1/unlimited | tpd 0/unlimited"
    )


def test_pool_requires_at_least_one_key():
    with pytest.raises(ValueError):
        ProviderPool([], lambda key: None, BudgetGovernor(1.00))
