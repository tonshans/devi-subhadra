#!/usr/bin/env python3
"""Dump every row of `audit_logs` to CSV, then empty the table.

Usage (run from the repository root, agent may stay up):
    python user_tools/dump_audit_log.py --db ./data/devi_subhadra.db
    python user_tools/dump_audit_log.py --db ./data/devi_subhadra.db --out audit.csv
    python user_tools/dump_audit_log.py --dsn "$POSTGRES_DSN"

Behavior:
    - Writes the CSV first; rows are deleted only when the file is on disk.
    - Missing table is an error (exit 2) and nothing is deleted.
    - The CSV holds the full audit trail: prompt, CoT, response, cost.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db.schema import audit_logs  # noqa: E402

_DEFAULT_DB = Path("./data/devi_subhadra.db")
_COLUMNS = [column.name for column in audit_logs.columns]


def _default_out() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("./data") / f"audit_dump_{stamp}.csv"


def _write_csv(out: Path, rows) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_COLUMNS)
        writer.writeheader()
        count = 0
        for row in rows:
            writer.writerow({name: row[name] for name in _COLUMNS})
            count += 1
    return count


def dump_sqlite(db_path: Path, out: Path) -> int:
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        try:
            rows = connection.execute(
                "SELECT * FROM audit_logs ORDER BY created_at, step"
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise RuntimeError(f"audit_logs is not available: {exc}") from exc
        count = _write_csv(out, rows)
        print(f"csv written: {out} ({count} row(s))")
        with connection:
            connection.execute("DELETE FROM audit_logs")
    finally:
        connection.close()
    print(f"audit_logs emptied: {count} row(s) deleted")
    return count


def dump_postgres(dsn: str, out: Path) -> int:
    import psycopg
    from psycopg.rows import dict_row

    dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            try:
                cursor.execute(
                    "SELECT * FROM audit_logs ORDER BY created_at, step"
                )
            except psycopg.errors.UndefinedTable as exc:
                raise RuntimeError(f"audit_logs is not available: {exc}") from exc
            rows = cursor.fetchall()
        count = _write_csv(out, rows)
        print(f"csv written: {out} ({count} row(s))")
        with connection:
            connection.execute("DELETE FROM audit_logs")
    print(f"audit_logs emptied: {count} row(s) deleted")
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(_DEFAULT_DB))
    parser.add_argument("--dsn", default="")
    parser.add_argument("--out", default=str(_default_out()))
    args = parser.parse_args(argv)
    out = Path(args.out)
    try:
        if args.dsn:
            dump_postgres(args.dsn, out)
        else:
            dump_sqlite(Path(args.db), out)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
