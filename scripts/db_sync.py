"""PostgreSQL <-> SQLite snapshot CLI.

Usage (run from the repository root):
    python -m scripts.db_sync status
    python -m scripts.db_sync backup
    python -m scripts.db_sync restore --yes

Options:
    --config PATH    config.yaml to read (default: repo config.yaml)
    --snapshot PATH  override backup.sqlite_path

`backup` copies only dirty rows (`is_backup = false`) from Postgres into the
single SQLite snapshot. `restore` replaces all Postgres data with that snapshot,
so it requires `--yes`; the snapshot is the source of truth. The Postgres DSN
must come from POSTGRES_DSN in the host environment or .env, never config.yaml.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from core.backup import export_to_sqlite, restore_from_sqlite
from core.config_loader import AppConfig, load_config
from core.db.engine import (
    DatabaseUnavailableError,
    build_database,
    resolve_postgres_dsn,
)
from core.db.schema import TABLE_ORDER

_DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


def _snapshot_path(settings: AppConfig, override: str | None) -> Path:
    return Path(override or settings.backup.sqlite_path)


def _snapshot_counts(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    connection = sqlite3.connect(path)
    try:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        return {
            table.name: connection.execute(
                f"SELECT COUNT(*) FROM {table.name}"
            ).fetchone()[0]
            for table in TABLE_ORDER
            if table.name in names
        }
    finally:
        connection.close()


async def _backup(settings: AppConfig, path: Path) -> dict[str, int]:
    runtime = build_database(settings, allow_fallback=False)
    try:
        return await export_to_sqlite(runtime.engine, path)
    finally:
        await runtime.dispose()


def _restore(settings: AppConfig, path: Path) -> dict[str, int]:
    engine = create_engine(resolve_postgres_dsn(settings))
    try:
        return restore_from_sqlite(path, engine)
    finally:
        engine.dispose()


def _status(settings: AppConfig, path: Path) -> dict:
    try:
        resolve_postgres_dsn(settings)
        dsn_configured = True
    except DatabaseUnavailableError:
        dsn_configured = False
    return {
        "db_type": settings.db_type,
        "postgres_dsn_configured": dsn_configured,
        "snapshot": str(path),
        "snapshot_exists": path.is_file(),
        "snapshot_rows": _snapshot_counts(path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Postgres/SQLite snapshot sync")
    parser.add_argument("command", choices=("status", "backup", "restore"))
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG))
    parser.add_argument("--snapshot", default=None)
    parser.add_argument("--yes", action="store_true", help="confirm destructive restore")
    args = parser.parse_args(argv)

    if args.command == "restore" and not args.yes:
        parser.error("restore replaces every Postgres row; pass --yes to confirm")

    settings = load_config(args.config)
    path = _snapshot_path(settings, args.snapshot)
    try:
        if args.command == "backup":
            result = asyncio.run(_backup(settings, path))
        elif args.command == "restore":
            result = _restore(settings, path)
        else:
            result = _status(settings, path)
    except (DatabaseUnavailableError, FileNotFoundError, SQLAlchemyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
