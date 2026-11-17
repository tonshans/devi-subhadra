"""Unit tests for sensor webhook authentication, validation, and processing."""

import asyncio

from fastapi.testclient import TestClient

from core.budget_governor import BudgetGovernor
from core.cognitive_ledger import CognitiveLedger
from core.runtime import Runtime
from main import create_app

VALID_EVENT = {
    "sensor_id": "temp-1",
    "event_type": "temperature_high",
    "payload": {"celsius": 31.5},
    "timestamp": "2026-11-14T10:00:00Z",
}


def _client(tmp_path):
    ledger = CognitiveLedger(tmp_path / "app.db")
    app = create_app(
        ledger=ledger,
        governor=BudgetGovernor(1.00),
        webhook_token="agent-secret",
    )
    return TestClient(app), ledger


def test_processes_authenticated_sensor_event(tmp_path):
    client, ledger = _client(tmp_path)

    with client:
        response = client.post(
            "/api/webhook/sensor",
            json=VALID_EVENT,
            headers={"X-Agent-Token": "agent-secret"},
        )
        assert response.status_code == 200

        body = response.json()
        assert body["status"] == "processed"
        assert isinstance(body["thread_id"], str)

        thread = asyncio.run(ledger.read("ledger_threads", body["thread_id"]))
        assert thread is not None
        assert thread["origin"] == "AGENT"
        assert thread["priority_score"] == 10
        assert "temp-1" in thread["title"]


def test_sensor_route_uses_runtime_when_provided(tmp_path):
    ledger = CognitiveLedger(tmp_path / "app.db")
    governor = BudgetGovernor(1.00)

    async def worker(prompt):
        return "sensor handled"

    runtime = Runtime(ledger, governor, worker=worker)
    app = create_app(
        ledger=ledger,
        governor=governor,
        runtime=runtime,
        webhook_token="agent-secret",
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/webhook/sensor",
            json=VALID_EVENT,
            headers={"X-Agent-Token": "agent-secret"},
        )

    assert response.status_code == 200
    logs = asyncio.run(ledger.list_activity_logs())
    assert logs[0]["initiator"] == "Webhook"
    assert logs[0]["budget_bucket"] == "ATELIER_30"
    entries = asyncio.run(ledger.list_ledger_entries(response.json()["thread_id"]))
    assert entries[-1]["content"] == "sensor handled"


def test_rejects_missing_or_wrong_agent_token(tmp_path):
    client, _ = _client(tmp_path)

    with client:
        missing = client.post("/api/webhook/sensor", json=VALID_EVENT)
        wrong = client.post(
            "/api/webhook/sensor",
            json=VALID_EVENT,
            headers={"X-Agent-Token": "wrong"},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_rejects_invalid_sensor_payload(tmp_path):
    client, _ = _client(tmp_path)
    payload = dict(VALID_EVENT)
    payload.pop("event_type")

    with client:
        response = client.post(
            "/api/webhook/sensor",
            json=payload,
            headers={"X-Agent-Token": "agent-secret"},
        )

    assert response.status_code == 422


def test_per_source_tokens_identify_the_caller(tmp_path):
    ledger = CognitiveLedger(tmp_path / "app.db")
    app = create_app(
        ledger=ledger,
        governor=BudgetGovernor(1.00),
        webhook_tokens={"token-a": "agent-a"},
    )

    with TestClient(app) as client:
        accepted = client.post(
            "/api/webhook/sensor",
            json=VALID_EVENT,
            headers={"X-Agent-Token": "token-a"},
        )
        rejected = client.post(
            "/api/webhook/sensor",
            json=VALID_EVENT,
            headers={"X-Agent-Token": "token-b"},
        )

    assert accepted.status_code == 200
    assert accepted.json()["source"] == "agent-a"
    assert rejected.status_code == 401


def test_blocks_sensor_event_while_kill_switch_active(tmp_path):
    client, ledger = _client(tmp_path)

    with client:
        asyncio.run(ledger.set_system_state("kill_switch", "1"))
        response = client.post(
            "/api/webhook/sensor",
            json=VALID_EVENT,
            headers={"X-Agent-Token": "agent-secret"},
        )

    assert response.status_code == 503
    assert asyncio.run(ledger.list_threads()) == []