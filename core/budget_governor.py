"""Financial gate (70/30 daily quotas) and provider RPM/TPM throttle tracker."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

WAITING_FOR_QUOTA = "WAITING_FOR_QUOTA"
OK = "OK"


class BudgetExceededError(Exception):
    """Raised when a call would exceed its budget bucket quota."""


class MemoryBudgetStore:
    def __init__(self) -> None:
        self._spent: dict[tuple[str, str], float] = {}

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


class SqliteBudgetStore:
    """Durable daily spend. Synchronous on purpose: tiny writes, sync sqlite3."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS budget_usage (
                    window_date TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    spent_usd REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (window_date, bucket)
                )
                """
            )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self._db_path, timeout=5, isolation_level=None)
        try:
            yield connection
        finally:
            connection.close()

    def spent(self, window: str, bucket: str) -> float:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT spent_usd FROM budget_usage WHERE window_date = ? AND bucket = ?",
                (window, bucket),
            ).fetchone()
            return float(row[0]) if row else 0.0

    def reserve(self, window: str, bucket: str, quota: float, cost: float) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT spent_usd FROM budget_usage "
                    "WHERE window_date = ? AND bucket = ?",
                    (window, bucket),
                ).fetchone()
                current = float(row[0]) if row else 0.0
                if cost > quota - current + 1e-9:
                    connection.execute("ROLLBACK")
                    return False
                connection.execute(
                    """
                    INSERT INTO budget_usage (window_date, bucket, spent_usd)
                    VALUES (?, ?, ?)
                    ON CONFLICT(window_date, bucket) DO UPDATE
                    SET spent_usd = spent_usd + excluded.spent_usd
                    """,
                    (window, bucket, cost),
                )
                connection.execute("COMMIT")
                return True
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def adjust(self, window: str, bucket: str, delta: float) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT spent_usd FROM budget_usage "
                    "WHERE window_date = ? AND bucket = ?",
                    (window, bucket),
                ).fetchone()
                current = float(row[0]) if row else 0.0
                connection.execute(
                    """
                    INSERT INTO budget_usage (window_date, bucket, spent_usd)
                    VALUES (?, ?, ?)
                    ON CONFLICT(window_date, bucket) DO UPDATE
                    SET spent_usd = excluded.spent_usd
                    """,
                    (window, bucket, max(0.0, current + delta)),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise


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
    ) -> None:
        self.core_budget_usd = max_initiative_budget_usd * core_task_ratio
        self.atelier_budget_usd = max_initiative_budget_usd * atelier_ratio
        self.rpm_limit = rpm_limit
        self.tpm_limit = tpm_limit
        self.throttle_threshold_ratio = throttle_threshold_ratio
        self._store = store or MemoryBudgetStore()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # shortcut: RPM/TPM stay process-local; single-worker assumption.
        self._window: str | None = None
        self._rpm_used = 0
        self._tpm_used = 0

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
        if origin not in {"HUMAN", "AGENT"}:
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

    def _refresh_window(self) -> None:
        window = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
        if window != self._window:
            self._window = window
            self._rpm_used = 0
            self._tpm_used = 0

    def record_api_usage(self, requests: int = 1, tokens: int = 0) -> None:
        if requests < 0 or tokens < 0:
            raise ValueError("usage counters must not be negative")
        self._refresh_window()
        self._rpm_used += requests
        self._tpm_used += tokens

    def check_rate_limit(self) -> str:
        self._refresh_window()
        if (
            self._rpm_used >= self.rpm_limit * self.throttle_threshold_ratio
            or self._tpm_used >= self.tpm_limit * self.throttle_threshold_ratio
        ):
            return WAITING_FOR_QUOTA
        return OK

    @property
    def rpm_used(self) -> int:
        self._refresh_window()
        return self._rpm_used