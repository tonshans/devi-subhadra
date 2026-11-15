"""Backend factory tests: Postgres selection, DSN rules, startup fallback."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.budget_governor import SqliteBudgetStore
from core.config_loader import load_config
from core.db.engine import (
    DatabaseUnavailableError,
    build_database,
    normalize_postgres_dsn,
    resolve_postgres_dsn,
)

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config.yaml"
_DSN = "postgresql+psycopg://user:pass@localhost:5432/devi_subhadra"


def _settings(tmp_path, **overrides):
    values = {
        "db_type": "sqlite",
        "sqlite_path": str(tmp_path / "main.db"),
        "postgres_dsn": "",
        "fallback_sqlite": True,
        "fallback_sqlite_path": str(tmp_path / "fallback.db"),
    }
    values.update(overrides)
    return load_config(_REPO_CONFIG).model_copy(update=values)


def _dispose(runtime):
    asyncio.run(runtime.dispose())


def test_sqlite_backend_uses_configured_path(tmp_path):
    runtime = build_database(_settings(tmp_path))

    assert runtime.backend == "sqlite"
    assert runtime.is_postgres is False
    assert runtime.fallback is False
    assert runtime.engine.dialect.name == "sqlite"
    assert isinstance(runtime.budget_store(), SqliteBudgetStore)
    _dispose(runtime)


def test_postgres_backend_used_when_probe_succeeds(tmp_path):
    runtime = build_database(
        _settings(tmp_path, db_type="postgres", postgres_dsn=_DSN),
        probe=lambda dsn: True,
    )

    assert runtime.backend == "postgres"
    assert runtime.is_postgres is True
    assert runtime.fallback is False
    assert runtime.postgres_dsn == _DSN
    _dispose(runtime)


def test_failed_probe_falls_back_to_sqlite(tmp_path):
    runtime = build_database(
        _settings(tmp_path, db_type="postgres", postgres_dsn=_DSN),
        probe=lambda dsn: False,
    )

    assert runtime.backend == "sqlite-fallback"
    assert runtime.fallback is True
    assert runtime.sqlite_path == str(tmp_path / "fallback.db")
    assert runtime.engine.dialect.name == "sqlite"
    _dispose(runtime)


def test_failed_probe_without_fallback_raises(tmp_path):
    with pytest.raises(DatabaseUnavailableError):
        build_database(
            _settings(
                tmp_path, db_type="postgres", postgres_dsn=_DSN, fallback_sqlite=False
            ),
            probe=lambda dsn: False,
        )


def test_missing_dsn_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    with pytest.raises(DatabaseUnavailableError, match="POSTGRES_DSN"):
        build_database(_settings(tmp_path, db_type="postgres"))


def test_dsn_is_read_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://user:pass@db:5432/agent")
    settings = _settings(tmp_path, db_type="postgres")

    assert (
        resolve_postgres_dsn(settings)
        == "postgresql+psycopg://user:pass@db:5432/agent"
    )


def test_dsn_is_read_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "POSTGRES_DSN=postgresql://user:pass@dotenv:5432/agent\n",
        encoding="utf-8",
    )
    settings = _settings(tmp_path, db_type="postgres")

    assert (
        resolve_postgres_dsn(settings)
        == "postgresql+psycopg://user:pass@dotenv:5432/agent"
    )


def test_asyncpg_dsn_is_rejected(tmp_path):
    with pytest.raises(DatabaseUnavailableError, match="psycopg"):
        normalize_postgres_dsn("postgresql+asyncpg://user:pass@db/agent")
