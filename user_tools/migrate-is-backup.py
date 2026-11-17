#!/usr/bin/env python3
"""One-shot migration: add the `is_backup` column to existing tables.

Usage (run from the repository root):
    python user_tools/migrate-is-backup.py --db ./data/devi_subhadra.db
    python user_tools/migrate-is-backup.py --dsn "$POSTGRES_DSN"

Behavior:
    - SQLite (`--db`): copies the database to `<db>.bak` before the first change
      (if no .bak yet), then adds the column where missing.
    - PostgreSQL (`--dsn`): runs `ADD COLUMN IF NOT EXISTS`; take a `pg_dump`
      yourself first, this script does not create a backup for Postgres.
    - Idempotent: tables that already have `is_backup` are left untouched.
    - New rows default to 0, so the first backup copies the whole database once.
    - Stop the agent before running this.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db.schema import TABLE_ORDER  # noqa: E402

_DEFAULT_DB = Path("./data/devi_subhadra.db")


def _has_column(connection: sqlite3.Connection, table: str, column: str) -> bool:
    return column in {
        row[1] for row in connection.execute(f"PRAGMA table_info({table})")
    }


def migrate_sqlite(db_path: Path) -> int:
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    backup_path = Path(str(db_path) + ".bak")
    if not backup_path.exists():
        shutil.copy2(db_path, backup_path)
        print(f"backup written: {backup_path}")

    connection = sqlite3.connect(db_path)
    changed = 0
    try:
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        with connection:
            for table in TABLE_ORDER:
                if table.name not in existing:
                    continue
                if _has_column(connection, table.name, "is_backup"):
                    continue
                connection.execute(
                    f"ALTER TABLE {table.name} ADD COLUMN "
                    "is_backup BOOLEAN NOT NULL DEFAULT 0"
                )
                changed += 1
                print(f"added is_backup: {table.name}")
    finally:
        connection.close()
    print(f"tables changed: {changed}")
    return changed


def migrate_postgres(dsn: str) -> int:
    import psycopg

    dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    changed = 0
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for table in TABLE_ORDER:
                cursor.execute(
                    f"ALTER TABLE IF EXISTS {table.name} ADD COLUMN "
                    "IF NOT EXISTS is_backup BOOLEAN NOT NULL DEFAULT false"
                )
                if cursor.rowcount:
                    changed += 1
                    print(f"added is_backup: {table.name}")
    print(f"tables changed: {changed}")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(_DEFAULT_DB))
    parser.add_argument("--dsn", default="")
    args = parser.parse_args(argv)
    if args.dsn:
        migrate_postgres(args.dsn)
        return 0
    try:
        migrate_sqlite(Path(args.db))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
