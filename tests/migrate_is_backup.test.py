"""Migration script tests: SQLite `is_backup` column, backup copy, idempotency."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "user_tools" / "migrate-is-backup.py"


def _run(db: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--db", str(db)],
        cwd=_REPO,
        env={**os.environ, "PYTHONPATH": str(_REPO)},
        capture_output=True,
        text=True,
    )


def test_adds_column_preserves_rows_and_is_idempotent(tmp_path):
    db = tmp_path / "legacy.db"
    connection = sqlite3.connect(db)
    with connection:
        connection.execute(
            "CREATE TABLE core_directives ("
            "directive_id TEXT PRIMARY KEY, directive_type TEXT, content TEXT)"
        )
        connection.execute(
            "INSERT INTO core_directives VALUES ('id-1', 'VISION', 'keep')"
        )
    connection.close()

    first = _run(db)
    assert first.returncode == 0, first.stderr
    assert (tmp_path / "legacy.db.bak").is_file()

    connection = sqlite3.connect(db)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(core_directives)")}
    row = connection.execute(
        "SELECT content, is_backup FROM core_directives WHERE directive_id = 'id-1'"
    ).fetchone()
    connection.close()
    assert "is_backup" in columns
    assert row == ("keep", 0)

    second = _run(db)
    assert second.returncode == 0
    assert "tables changed: 0" in second.stdout


def test_missing_database_returns_error(tmp_path):
    result = _run(tmp_path / "missing.db")

    assert result.returncode == 2
    assert "database not found" in result.stderr
