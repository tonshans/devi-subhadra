"""CLI smoke tests for scripts/db_sync.py (no Postgres required)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import db_sync

_REPO_CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"


def test_restore_requires_explicit_confirmation(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        db_sync.main(
            [
                "restore",
                "--config",
                str(_REPO_CONFIG),
                "--snapshot",
                str(tmp_path / "snapshot.db"),
            ]
        )

    assert excinfo.value.code == 2


def test_status_reports_missing_snapshot(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    snapshot = tmp_path / "snapshot.db"

    code = db_sync.main(
        ["status", "--config", str(_REPO_CONFIG), "--snapshot", str(snapshot)]
    )

    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["db_type"] == "sqlite"
    assert body["postgres_dsn_configured"] is False
    assert body["snapshot_exists"] is False
    assert body["snapshot_rows"] == {}
