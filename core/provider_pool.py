"""Provider key pool: fall over to the next API key when one fails or runs out."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable

import httpx

from core.budget_governor import BudgetGovernor
from core.llm_worker import LastUsageState

COOLDOWN_SECONDS = 60
DISABLED_STATUSES = {401, 403}
RATE_LIMIT_STATUS = 429
_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s?$")
PER_KEY_METRICS = (
    ("rpm", "rpm_limit"),
    ("tpm", "tpm_limit"),
    ("rpd", "rpd_limit"),
    ("tpd", "tpd_limit"),
)

WorkerFactory = Callable[["ProviderKey"], Callable[..., Awaitable[object]]]


class AllProvidersUnavailable(RuntimeError):
    """Every key is limited, cooling down, or disabled."""

    def __init__(self, message: str, *, should_notify: bool) -> None:
        super().__init__(message)
        self.message = message
        self.should_notify = should_notify


@dataclass(frozen=True)
class ProviderKey:
    id: str
    secret: str = field(repr=False)
    rpm_limit: int = 0
    tpm_limit: int = 0
    rpd_limit: int = 0
    tpd_limit: int = 0


def _text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _earliest(
    current: datetime | None, candidate: datetime | None
) -> datetime | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    return min(current, candidate)


def _tokens(usage: dict | None) -> int:
    if not usage:
        return 0
    return int(usage.get("prompt_tokens", 0)) + int(
        usage.get("completion_tokens", 0)
    )


def _delay_seconds(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    match = _DELAY.fullmatch(value.strip())
    return float(match.group(1)) if match else None


def _body_delay(response: httpx.Response) -> float | None:
    """Gemini reports throttling in the body, not the headers."""
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    if not isinstance(details, list):
        return None
    for item in details:
        if isinstance(item, dict):
            delay = _delay_seconds(item.get("retryDelay"))
            if delay is not None:
                return delay
    return None


class ProviderPool(LastUsageState):
    def __init__(
        self,
        keys: list[ProviderKey],
        worker_factory: WorkerFactory,
        governor: BudgetGovernor,
        *,
        provider: str = "gemini",
        cooldown_seconds: float = COOLDOWN_SECONDS,
    ) -> None:
        if not keys:
            raise ValueError("provider pool needs at least one key")
        self._keys = list(keys)
        self._governor = governor
        self._provider = provider
        self._cooldown_seconds = cooldown_seconds
        # shortcut: cooldown/disabled state is memory-only; restart clears it.
        self._workers = {key.id: worker_factory(key) for key in self._keys}
        self._cooldowns: dict[str, datetime] = {}
        self._disabled: set[str] = set()
        self._down_notified = False
        super().__init__()

    def _local_limit(self, key: ProviderKey) -> str | None:
        for metric, attr in PER_KEY_METRICS:
            limit = getattr(key, attr)
            if limit and self._governor.key_used(key.id, metric) >= limit:
                return metric.upper()
        return None

    def _skip_reason(
        self, key: ProviderKey, now: datetime
    ) -> tuple[str | None, datetime | None]:
        if key.id in self._disabled:
            return "disabled (auth error)", None
        cooldown = self._cooldowns.get(key.id)
        if cooldown is not None:
            if cooldown > now:
                return f"cooldown until {_text(cooldown)}", cooldown
            self._cooldowns.pop(key.id, None)
        metric = self._local_limit(key)
        if metric is not None:
            reset = self._governor.reset_at(metric)
            return f"{metric} limit reached until {_text(reset)}", reset
        return None, None

    def _consume_usage(self, worker) -> dict | None:
        usage = getattr(worker, "last_usage", None)
        try:
            worker.last_usage = None
        except AttributeError:
            pass
        return usage

    def _record(self, key: ProviderKey, usage: dict | None) -> None:
        self._governor.record_key_usage(key.id, 1, _tokens(usage))

    def _retry_after(self, response: httpx.Response) -> datetime | None:
        now = self._governor.now()
        value = response.headers.get("retry-after")
        if value:
            seconds = _delay_seconds(value)
            if seconds is not None:
                return now + timedelta(seconds=seconds)
            try:
                moment = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                moment = None
            if moment is not None:
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
                return max(now, moment)
        seconds = _body_delay(response)
        if seconds is None:
            return None
        return now + timedelta(seconds=seconds)

    async def __call__(self, prompt: str, **kwargs):
        self.last_usage = None
        now = self._governor.now()
        reasons: list[str] = []
        resume_at: datetime | None = None

        for key in self._keys:
            reason, until = self._skip_reason(key, now)
            if reason is not None:
                reasons.append(f"{key.id} {reason}")
                resume_at = _earliest(resume_at, until)
                continue

            worker = self._workers[key.id]
            try:
                reply = await worker(prompt, **kwargs)
            except httpx.HTTPStatusError as exc:
                usage = self._consume_usage(worker)
                self._record(key, usage)
                status = exc.response.status_code
                if status in DISABLED_STATUSES:
                    self._disabled.add(key.id)
                    reasons.append(f"{key.id} disabled (HTTP {status})")
                    continue
                if status == RATE_LIMIT_STATUS:
                    until = self._retry_after(
                        exc.response
                    ) or self._governor.reset_at("RPM")
                    self._cooldowns[key.id] = until
                    reasons.append(f"{key.id} rate limited until {_text(until)}")
                elif 500 <= status < 600:
                    until = now + timedelta(seconds=self._cooldown_seconds)
                    self._cooldowns[key.id] = until
                    reasons.append(f"{key.id} HTTP {status} until {_text(until)}")
                else:
                    # Model/request errors are not key problems; stop here.
                    raise
                resume_at = _earliest(resume_at, until)
                continue
            except (httpx.TimeoutException, httpx.TransportError):
                usage = self._consume_usage(worker)
                self._record(key, usage)
                until = now + timedelta(seconds=self._cooldown_seconds)
                self._cooldowns[key.id] = until
                reasons.append(f"{key.id} network error until {_text(until)}")
                resume_at = _earliest(resume_at, until)
                continue

            usage = self._consume_usage(worker)
            self._record(key, usage)
            self.last_usage = usage
            self._cooldowns.pop(key.id, None)
            self._down_notified = False
            return reply

        should_notify = not self._down_notified
        self._down_notified = True
        detail = "; ".join(reasons) or "no usable keys"
        message = f"All {self._provider} API keys are unavailable: {detail}"
        if resume_at is not None:
            message += f". Retrying automatically after {_text(resume_at)}."
        else:
            message += "."
        raise AllProvidersUnavailable(message, should_notify=should_notify)

    def _metric_text(self, key: ProviderKey, metric: str, attr: str) -> str:
        limit = getattr(key, attr)
        used = self._governor.key_used(key.id, metric)
        return f"{used}/unlimited" if limit == 0 else f"{used}/{limit}"

    def status_lines(self) -> list[str]:
        now = self._governor.now()
        lines = [f"provider: {self._provider} ({len(self._keys)} keys)"]
        for key in self._keys:
            reason, _ = self._skip_reason(key, now)
            if reason is not None:
                lines.append(f"  {key.id}: {reason}")
                continue
            metrics = " | ".join(
                f"{metric} {self._metric_text(key, metric, attr)}"
                for metric, attr in PER_KEY_METRICS
            )
            lines.append(f"  {key.id}: ready | {metrics}")
        return lines

    @property
    def model(self) -> str | None:
        return getattr(self._workers[self._keys[0].id], "model", None)

    @property
    def ready_count(self) -> int:
        now = self._governor.now()
        return sum(
            1 for key in self._keys if self._skip_reason(key, now)[0] is None
        )
