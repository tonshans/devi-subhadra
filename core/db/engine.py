"""Backend selection: PostgreSQL when configured, SQLite fallback at startup."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from core.budget_governor import PostgresBudgetStore, SqliteBudgetStore
from core.config_loader import AppConfig, SecretNotFoundError, SecretsManager

logger = logging.getLogger(__name__)

POSTGRES_SCHEME = "postgresql+psycopg"
_PROBE_TIMEOUT_SECONDS = 5


class DatabaseUnavailableError(RuntimeError):
    """Raised when the configured database cannot be reached at startup."""


def sqlite_async_url(db_path: str | Path) -> str:
    return f"sqlite+aiosqlite:///{Path(db_path).resolve()}"


def normalize_postgres_dsn(dsn: str) -> str:
    dsn = dsn.strip()
    if dsn.startswith("postgresql+asyncpg://"):
        raise DatabaseUnavailableError(
            "POSTGRES_DSN uses asyncpg; this project uses psycopg. "
            "Rewrite it as postgresql+psycopg://user:password@host:5432/db"
        )
    if dsn.startswith("postgres://"):
        return POSTGRES_SCHEME + "://" + dsn[len("postgres://") :]
    if dsn.startswith("postgresql://"):
        return POSTGRES_SCHEME + "://" + dsn[len("postgresql://") :]
    if not dsn.startswith(POSTGRES_SCHEME + "://"):
        raise DatabaseUnavailableError(
            "POSTGRES_DSN must start with postgresql+psycopg://"
        )
    return dsn


def resolve_postgres_dsn(settings: AppConfig, dsn: str | None = None) -> str:
    candidate = (dsn or settings.postgres_dsn).strip()
    if not candidate:
        try:
            candidate = SecretsManager().get_secret("POSTGRES_DSN")
        except SecretNotFoundError:
            candidate = ""
    if not candidate:
        raise DatabaseUnavailableError(
            "db_type=postgres needs POSTGRES_DSN in the host environment or .env "
            "(or postgres_dsn in config.yaml)"
        )
    return normalize_postgres_dsn(candidate)


def _psycopg_dsn(dsn: str) -> str:
    return "postgresql://" + dsn.split("://", 1)[1]


def probe_postgres(dsn: str) -> bool:
    import psycopg

    try:
        with psycopg.connect(
            _psycopg_dsn(dsn), connect_timeout=_PROBE_TIMEOUT_SECONDS
        ) as connection:
            connection.execute("SELECT 1")
        return True
    except Exception as exc:
        # Never log the exception text: it can echo the DSN or credentials.
        logger.warning("postgres probe failed: %s", type(exc).__name__)
        return False


@dataclass
class DatabaseRuntime:
    engine: AsyncEngine
    backend: str
    sqlite_path: str
    postgres_dsn: str | None = None
    fallback: bool = False

    @property
    def is_postgres(self) -> bool:
        return self.backend == "postgres"

    def budget_store(self):
        if self.is_postgres:
            return PostgresBudgetStore(self.postgres_dsn)
        return SqliteBudgetStore(self.sqlite_path)

    async def dispose(self) -> None:
        await self.engine.dispose()


def build_database(
    settings: AppConfig,
    *,
    dsn: str | None = None,
    probe: Callable[[str], bool] | None = None,
    allow_fallback: bool | None = None,
) -> DatabaseRuntime:
    """Pick the backend once, at startup. No runtime hot-failover."""
    if settings.db_type == "sqlite":
        path = settings.sqlite_path
        return DatabaseRuntime(
            engine=create_async_engine(sqlite_async_url(path), poolclass=NullPool),
            backend="sqlite",
            sqlite_path=path,
        )

    resolved = resolve_postgres_dsn(settings, dsn)
    if (probe or probe_postgres)(resolved):
        return DatabaseRuntime(
            engine=create_async_engine(resolved, poolclass=NullPool),
            backend="postgres",
            sqlite_path=settings.fallback_sqlite_path,
            postgres_dsn=resolved,
        )

    if allow_fallback is None:
        allow_fallback = settings.fallback_sqlite
    if not allow_fallback:
        raise DatabaseUnavailableError(
            "cannot reach postgres and fallback_sqlite is false; "
            "fix POSTGRES_DSN or enable the SQLite fallback"
        )

    logger.error(
        "postgres unreachable; starting on SQLite fallback at %s",
        settings.fallback_sqlite_path,
    )
    path = settings.fallback_sqlite_path
    return DatabaseRuntime(
        engine=create_async_engine(sqlite_async_url(path), poolclass=NullPool),
        backend="sqlite-fallback",
        sqlite_path=path,
        postgres_dsn=resolved,
        fallback=True,
    )
