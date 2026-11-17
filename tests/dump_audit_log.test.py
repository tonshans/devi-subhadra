"""dump_audit_log script tests: CSV export, table emptied, safe failures."""

from __future__ import annotations

import asyncio
import csv
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from core.cognitive_ledger import CognitiveLedger

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "user_tools" / "dump_audit_log.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        cwd=_REPO,
        env={**os.environ, "PYTHONPATH": str(_REPO)},
        capture_output=True,
        text=True,
    )


def _audit_db(tmp_path: Path, rows: int = 2) -> Path:
    db = tmp_path / "agent.db"

    async def scenario():
        ledger = CognitiveLedger(db)
        await ledger.initialize()
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        for step in range(1, rows + 1):
            await ledger.create(
                "audit_logs",
                trace_id="trace-1",
                step=step,
                thread_id=thread_id,
                trigger="TELEGRAM",
                origin="HUMAN",
                action="LLM_REPLY",
                cot=f"reason {step}",
                response=f"reply, with comma {step}",
                outcome="SUCCESS",
            )
        await ledger.dispose()

    asyncio.run(scenario())
    return db


def _count_audit(db: Path) -> int:
    connection = sqlite3.connect(db)
    try:
        return connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    finally:
        connection.close()


def test_dumps_rows_to_csv_and_empties_the_table(tmp_path):
    db = _audit_db(tmp_path)
    out = tmp_path / "audit.csv"

    result = _run("--db", str(db), "--out", str(out))

    assert result.returncode == 0, result.stderr
    assert "2 row(s) deleted" in result.stdout
    with open(out, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["step"] for row in rows] == ["1", "2"]
    assert rows[0]["cot"] == "reason 1"
    assert rows[0]["response"] == "reply, with comma 1"
    assert rows[0]["trace_id"] == "trace-1"
    assert _count_audit(db) == 0


def test_missing_table_deletes_nothing_and_exits_2(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    out = tmp_path / "audit.csv"

    result = _run("--db", str(db), "--out", str(out))

    assert result.returncode == 2
    assert "audit_logs is not available" in result.stderr
    assert not out.exists()


def test_missing_database_exits_2(tmp_path):
    result = _run("--db", str(tmp_path / "nope.db"))

    assert result.returncode == 2
    assert "database not found" in result.stderr


def test_only_prompt_and_cot_columns_exist_in_export_header(tmp_path):
    db = _audit_db(tmp_path)
    out = tmp_path / "audit.csv"

    assert _run("--db", str(db), "--out", str(out)).returncode == 0
    with open(out, newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))

    assert header == [
        "audit_id",
        "trace_id",
        "step",
        "thread_id",
        "trigger",
        "trigger_ref",
        "origin",
        "model",
        "prompt",
        "prompt_tokens",
        "cot",
        "action",
        "action_target",
        "action_payload",
        "response",
        "response_tokens",
        "outcome",
        "error",
        "result_table",
        "result_id",
        "latency_ms",
        "cost_usd",
        "created_at",
        "is_backup",
    ]
