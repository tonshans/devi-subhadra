"""Financial gate (70/30 daily quotas) and provider rate-limit tracker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db.schema import (
    budget_usage,
    metadata,
    provider_key_usage,
    rate_limit_usage,
)

WAITING_FOR_QUOTA = "WAITING_FOR_QUOTA"
OK = "OK"

SIDES = ("HUMAN", "AGENT")
REQUEST_METRICS = ("rpm", "rpd")
TOKEN_METRICS = ("tpm", "tpd")
METRICS = ("rpm", "tpm", "rpd", "tpd")


class BudgetExceededError(Exception):
    """Raised when a call would exceed its budget bucket quota."""


class MemoryBudgetStore:
    def __init__(self) -> None:
        self._spent: dict[tuple[str, str], float] = {}
        self._usage: dict[tuple[str, str], tuple[int, int]] = {}
        self._key_usage: dict[tuple[str, str], tuple[int, int]] = {}

    def spent(self, window: str, bucket: str) -> float:
        return self._spent.get((window, bucket), 0.0)

    def reserve(self, window: str, bucket: str, quota: float, cost: float) -> bool:
        current = self.spent(window, bucket)
        if cost > quota - current + 1e-9:
            return False
        self._spent[(window, bucket)] = current + cost
        return True

    def adjust(self, window: str, bucket: str, delta: float) -> None:
        self._spent[(window, bucket)] = max(
            0.0, self.spent(window, bucket) + delta
        )

    def usage(self, window: str, side: str) -> tuple[int, int]:
        return self._usage.get((window, side), (0, 0))

    def add_usage(
        self, window: str, side: str, requests: int, tokens: int
    ) -> None:
        used_requests, used_tokens = self.usage(window, side)
        self._usage[(window, side)] = (
            used_requests + requests,
            used_tokens + tokens,
        )

    def key_usage(self, window: str, key_id: str) -> tuple[int, int]:
        return self._key_usage.get((window, key_id), (0, 0))

    def add_key_usage(
        self, window: str, key_id: str, requests: int, tokens: int
    ) -> None:
        used_requests, used_tokens = self.key_usage(window, key_id)
        self._key_usage[(window, key_id)] = (
            used_requests + requests,
            used_tokens + tokens,
        )


class SqlAlchemyBudgetStore:
    """Durable daily spend on any SQLAlchemy sync engine (SQLite or Postgres)."""

    def __init__(self, url: str, **engine_kwargs) -> None:
        self._engine = create_engine(url, **engine_kwargs)
        metadata.create_all(
            self._engine,
            tables=[budget_usage, rate_limit_usage, provider_key_usage],
        )

    def _select(self, connection, window: str, bucket: str):
        return connection.execute(
            select(budget_usage.c.spent_usd)
            .where(
                budget_usage.c.window_date == window,
                budget_usage.c.bucket == bucket,
            )
            .with_for_update()
        ).scalar()

    def _upsert(self, connection, window: str, bucket: str, spent: float) -> None:
        insert = (
            sqlite_insert(budget_usage)
            if self._engine.dialect.name == "sqlite"
            else pg_insert(budget_usage)
        )
        connection.execute(
            insert.values(
                window_date=window,
                bucket=bucket,
                spent_usd=spent,
                is_backup=False,
            ).on_conflict_do_update(
                index_elements=[budget_usage.c.window_date, budget_usage.c.bucket],
                set_={"spent_usd": spent, "is_backup": False},
            )
        )

    def spent(self, window: str, bucket: str) -> float:
        with self._engine.connect() as connection:
            current = self._select(connection, window, bucket)
            return float(current) if current is not None else 0.0

    def reserve(self, window: str, bucket: str, quota: float, cost: float) -> bool:
        with self._engine.begin() as connection:
            current = self._select(connection, window, bucket)
            current = float(current) if current is not None else 0.0
            if cost > quota - current + 1e-9:
                return False
            self._upsert(connection, window, bucket, current + cost)
            return True

    def adjust(self, window: str, bucket: str, delta: float) -> None:
        with self._engine.begin() as connection:
            current = self._select(connection, window, bucket)
            current = float(current) if current is not None else 0.0
            self._upsert(connection, window, bucket, max(0.0, current + delta))

    def usage(self, window: str, side: str) -> tuple[int, int]:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(
                    rate_limit_usage.c.requests_count,
                    rate_limit_usage.c.tokens_count,
                ).where(
                    rate_limit_usage.c.window_key == window,
                    rate_limit_usage.c.side == side,
                )
            ).first()
        return (int(row[0]), int(row[1])) if row else (0, 0)

    def add_usage(
        self, window: str, side: str, requests: int, tokens: int
    ) -> None:
        insert = (
            sqlite_insert(rate_limit_usage)
            if self._engine.dialect.name == "sqlite"
            else pg_insert(rate_limit_usage)
        )
        with self._engine.begin() as connection:
            connection.execute(
                insert.values(
                    window_key=window,
                    side=side,
                    requests_count=requests,
                    tokens_count=tokens,
                    is_backup=False,
                ).on_conflict_do_update(
                    index_elements=[
                        rate_limit_usage.c.window_key,
                        rate_limit_usage.c.side,
                    ],
                    set_={
                        "requests_count": rate_limit_usage.c.requests_count
                        + requests,
                        "tokens_count": rate_limit_usage.c.tokens_count + tokens,
                        "is_backup": False,
                    },
                )
            )

    def key_usage(self, window: str, key_id: str) -> tuple[int, int]:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(
                    provider_key_usage.c.requests_count,
                    provider_key_usage.c.tokens_count,
                ).where(
                    provider_key_usage.c.window_key == window,
                    provider_key_usage.c.key_id == key_id,
                )
            ).first()
        return (int(row[0]), int(row[1])) if row else (0, 0)

    def add_key_usage(
        self, window: str, key_id: str, requests: int, tokens: int
    ) -> None:
        insert = (
            sqlite_insert(provider_key_usage)
            if self._engine.dialect.name == "sqlite"
            else pg_insert(provider_key_usage)
        )
        with self._engine.begin() as connection:
            connection.execute(
                insert.values(
                    window_key=window,
                    key_id=key_id,
                    requests_count=requests,
                    tokens_count=tokens,
                    is_backup=False,
                ).on_conflict_do_update(
                    index_elements=[
                        provider_key_usage.c.window_key,
                        provider_key_usage.c.key_id,
                    ],
                    set_={
                        "requests_count": provider_key_usage.c.requests_count
                        + requests,
                        "tokens_count": provider_key_usage.c.tokens_count + tokens,
                        "is_backup": False,
                    },
                )
            )

    def dispose(self) -> None:
        self._engine.dispose()


def _sqlite_url(db_path: str | Path) -> str:
    return f"sqlite:///{Path(db_path).resolve()}"


class SqliteBudgetStore(SqlAlchemyBudgetStore):
    def __init__(self, db_path: str | Path) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        super().__init__(_sqlite_url(db_path), connect_args={"timeout": 5})


class PostgresBudgetStore(SqlAlchemyBudgetStore):
    def __init__(self, dsn: str) -> None:
        super().__init__(dsn)


class BudgetGovernor:
    def __init__(
        self,
        max_initiative_budget_usd: float,
        core_task_ratio: float = 0.70,
        atelier_ratio: float = 0.30,
        rpm_limit: int = 60,
        tpm_limit: int = 120000,
        throttle_threshold_ratio: float = 0.90,
        store: MemoryBudgetStore | SqliteBudgetStore | None = None,
        clock: Callable[[], datetime] | None = None,
        rpd_limit: int = 0,
        tpd_limit: int = 0,
        budget_ratio_agent: float = 1.0,
        agent_freetime: bool = False,
    ) -> None:
        for name, value in (
            ("rpm_limit", rpm_limit),
            ("tpm_limit", tpm_limit),
            ("rpd_limit", rpd_limit),
            ("tpd_limit", tpd_limit),
        ):
            if value < 0:
                raise ValueError(f"{name} must not be negative")
        if not 0.0 <= budget_ratio_agent <= 1.0:
            raise ValueError("budget_ratio_agent must be between 0 and 1")
        self.core_budget_usd = max_initiative_budget_usd * core_task_ratio
        self.atelier_budget_usd = max_initiative_budget_usd * atelier_ratio
        self.rpm_limit = rpm_limit
        self.tpm_limit = tpm_limit
        self.rpd_limit = rpd_limit
        self.tpd_limit = tpd_limit
        self.throttle_threshold_ratio = throttle_threshold_ratio
        self.budget_ratio_agent = budget_ratio_agent
        self.agent_freetime = agent_freetime
        self._store = store or MemoryBudgetStore()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- financial gate ---------------------------------------------------

    def _budget_window(self) -> str:
        return self._clock().strftime("%Y-%m-%d")

    def _quota(self, bucket: str) -> float:
        if bucket == "CORE_70":
            return self.core_budget_usd
        if bucket == "ATELIER_30":
            return self.atelier_budget_usd
        raise ValueError(f"bucket has no quota: {bucket}")

    def remaining_usd(self, bucket: str) -> float:
        return self._quota(bucket) - self._store.spent(self._budget_window(), bucket)

    def authorize_call(
        self,
        cost_usd: float,
        origin: str,
        is_human_endorsed: bool = False,
    ) -> str:
        """Validate and reserve cost; returns the charged budget bucket."""
        if origin not in SIDES:
            raise ValueError(f"invalid mission origin: {origin}")
        if cost_usd < 0:
            raise ValueError("cost_usd must not be negative")
        if origin == "HUMAN":
            return "HUMAN_EXEMPT"

        bucket = "CORE_70" if is_human_endorsed else "ATELIER_30"
        remaining = self.remaining_usd(bucket)
        # Tolerance absorbs float noise so an exact-quota cost is allowed.
        if cost_usd > remaining + 1e-9 or not self._store.reserve(
            self._budget_window(), bucket, self._quota(bucket), cost_usd
        ):
            raise BudgetExceededError(
                f"{bucket} quota exceeded: requested ${cost_usd:.4f}, "
                f"remaining ${remaining:.4f}"
            )
        return bucket

    def settle(self, bucket: str, reserved: float, actual: float) -> None:
        """Replace a reservation with the real cost; a smaller actual refunds."""
        if bucket == "HUMAN_EXEMPT":
            return
        if reserved < 0 or actual < 0:
            raise ValueError("settle amounts must not be negative")
        self._store.adjust(self._budget_window(), bucket, actual - reserved)

    # -- provider rate limits ---------------------------------------------

    def now(self) -> datetime:
        return self._clock()

    def _minute_window(self) -> str:
        return self._clock().strftime("%Y-%m-%dT%H:%M")

    def _day_window(self) -> str:
        return self._clock().strftime("%Y-%m-%d")

    def _window(self, metric: str) -> str:
        return self._minute_window() if metric in ("rpm", "tpm") else self._day_window()

    def limit(self, metric: str) -> int:
        """Configured global limit; 0 means unlimited."""
        return getattr(self, f"{metric}_limit")

    def used(self, metric: str, side: str | None = None) -> int:
        """Requests (rpm/rpd) or tokens (tpm/tpd); side None sums both sides."""
        index = 0 if metric in REQUEST_METRICS else 1
        window = self._window(metric)
        sides = SIDES if side is None else (side,)
        return sum(self._store.usage(window, item)[index] for item in sides)

    def record_api_usage(
        self, requests: int = 1, tokens: int = 0, side: str = "AGENT"
    ) -> None:
        if side not in SIDES:
            raise ValueError(f"invalid usage side: {side}")
        if requests < 0 or tokens < 0:
            raise ValueError("usage counters must not be negative")
        for window in (self._minute_window(), self._day_window()):
            self._store.add_usage(window, side, requests, tokens)

    def agent_cap(self, metric: str) -> int | None:
        """Agent share of a global limit; None means unlimited."""
        if self.agent_freetime:
            return None
        total = self.limit(metric)
        if total == 0:
            return None
        return int(total * self.budget_ratio_agent)

    def hard_limit_reached(self) -> str | None:
        for metric in METRICS:
            limit = self.limit(metric)
            if limit and self.used(metric) >= limit:
                return metric.upper()
        return None

    def agent_limit_reached(self) -> str | None:
        for metric in METRICS:
            cap = self.agent_cap(metric)
            if cap is not None and self.used(metric, "AGENT") >= cap:
                return metric.upper()
        return None

    def reset_at(self, metric: str) -> datetime:
        """When the paused window reopens after hitting `metric`."""
        now = self._clock()
        if metric.upper() in ("RPM", "TPM"):
            return (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        return (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    def resume_at_iso(self, metric: str) -> str:
        return self.reset_at(metric).strftime("%Y-%m-%dT%H:%M:%SZ")

    def check_rate_limit(self) -> str:
        for metric in ("rpm", "tpm"):
            limit = self.limit(metric)
            if limit and self.used(metric) >= limit * self.throttle_threshold_ratio:
                return WAITING_FOR_QUOTA
        return OK

    def set_budget_ratio_agent(self, ratio: float) -> None:
        if not 0.0 <= ratio <= 1.0:
            raise ValueError("budget_ratio_agent must be between 0 and 1")
        self.budget_ratio_agent = ratio

    # -- per provider key usage -------------------------------------------

    def key_used(self, key_id: str, metric: str) -> int:
        index = 0 if metric in REQUEST_METRICS else 1
        return self._store.key_usage(self._window(metric), key_id)[index]

    def record_key_usage(
        self, key_id: str, requests: int = 1, tokens: int = 0
    ) -> None:
        if requests < 0 or tokens < 0:
            raise ValueError("usage counters must not be negative")
        for window in (self._minute_window(), self._day_window()):
            self._store.add_key_usage(window, key_id, requests, tokens)

    def set_agent_freetime(self, enabled: bool) -> None:
        self.agent_freetime = bool(enabled)

    @property
    def rpm_used(self) -> int:
        return self.used("rpm")

    @property
    def tpm_used(self) -> int:
        return self.used("tpm")

    @property
    def rpd_used(self) -> int:
        return self.used("rpd")

    @property
    def tpd_used(self) -> int:
        return self.used("tpd")
