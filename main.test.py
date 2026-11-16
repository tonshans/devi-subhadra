"""Integration tests for app boot, health, and the Telegram webhook contract."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.telegram_gateway import TelegramGateway
from core.budget_governor import BudgetGovernor
from core.cognitive_ledger import CognitiveLedger
from core.config_loader import load_config
from core.config_loader import SecretNotFoundError
from core.db.engine import DatabaseRuntime
from core.runtime import Runtime
from core.provider_pool import ProviderPool
from main import (
    build_gateway,
    build_runtime,
    build_worker,
    create_app,
    create_production_app,
)


def _app(tmp_path, **kwargs):
    ledger = CognitiveLedger(tmp_path / "app.db")
    governor = BudgetGovernor(1.00)
    kwargs.setdefault("webhook_token", "agent-secret")
    return create_app(ledger=ledger, governor=governor, **kwargs), ledger


def test_build_gateway_reads_telegram_secrets(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "123")

    gateway = build_gateway(load_config(Path(__file__).with_name("config.yaml")))

    assert gateway.is_configured is True
    assert gateway.is_authorized(123) is True
    assert gateway.is_authorized(9) is False


def test_build_runtime_wires_plugins_worker_and_wake_up(tmp_path):
    settings = load_config(Path(__file__).with_name("config.yaml"))
    ledger = CognitiveLedger(tmp_path / "app.db")
    governor = BudgetGovernor(1.00)

    class FakeGateway:
        def __init__(self):
            self.pings = 0

        def wake_up_ping(self):
            self.pings += 1
            return True

    gateway = FakeGateway()
    asyncio.run(ledger.initialize())

    async def worker(prompt):
        return "ok"

    runtime = build_runtime(
        settings=settings,
        ledger=ledger,
        governor=governor,
        gateway=gateway,
        worker=worker,
        plugins_dir=tmp_path / "plugins",
    )

    assert isinstance(runtime, Runtime)
    asyncio.run(runtime.scheduled_tick())
    assert gateway.pings == 1


def test_build_runtime_requires_gemini_secret_without_worker(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("GEMINI_API_KEY_PRIMARY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_SECONDARY", raising=False)
    settings = load_config(Path(__file__).with_name("config.yaml"))
    gateway = TelegramGateway(
        sender=lambda chat_id, message: None, allowed_chat_id=123
    )

    with pytest.raises(SecretNotFoundError):
        build_runtime(
            settings=settings,
            ledger=CognitiveLedger(tmp_path / "app.db"),
            governor=BudgetGovernor(1.00),
            gateway=gateway,
            plugins_dir=tmp_path / "plugins",
        )


def test_build_runtime_skips_gemini_keys_without_secret(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GEMINI_API_KEY_PRIMARY", "key-one")
    monkeypatch.delenv("GEMINI_API_KEY_SECONDARY", raising=False)
    settings = load_config(Path(__file__).with_name("config.yaml"))
    ledger = CognitiveLedger(tmp_path / "app.db")
    governor = BudgetGovernor(1.00)
    gateway = TelegramGateway(
        sender=lambda chat_id, message: None, allowed_chat_id=123
    )

    runtime = build_runtime(
        settings=settings,
        ledger=ledger,
        governor=governor,
        gateway=gateway,
        plugins_dir=tmp_path / "plugins",
    )

    assert runtime.provider.ready_count == 1


def test_build_worker_wraps_openrouter_in_a_provider_pool(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    settings = load_config(Path(__file__).with_name("config.yaml"))
    settings = settings.model_copy(
        update={
            "rate_limits": settings.rate_limits.model_copy(
                update={"provider": "openrouter"}
            )
        }
    )

    worker = build_worker(settings, BudgetGovernor(1.00))

    assert isinstance(worker, ProviderPool)
    assert worker.status_lines()[0] == "provider: openrouter (1 keys)"


def test_create_production_app_wires_gateway_and_runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "123")
    monkeypatch.setenv("GEMINI_API_KEY_PRIMARY", "key-one")

    app = create_production_app(
        config_path=Path(__file__).with_name("config.yaml")
    )

    assert app.state.gateway.is_configured is True
    assert isinstance(app.state.runtime, Runtime)


def test_health_reports_active_kill_switch(tmp_path):
    app, ledger = _app(tmp_path)

    with TestClient(app) as client:
        asyncio.run(ledger.set_system_state("kill_switch", "1"))
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["kill_switch_active"] is True


def test_webhook_token_is_loaded_from_host_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WEBHOOK_TOKEN", "from-env")
    ledger = CognitiveLedger(tmp_path / "app.db")
    app = create_app(ledger=ledger, governor=BudgetGovernor(1.00))

    assert app.state.webhook_token == "from-env"


def test_health_endpoint_contract(tmp_path):
    app, _ = _app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["execution_mode"] == load_config().execution_mode
    assert body["rpm_used"] == 0
    assert body["rpm_limit"] == 60
    assert body["budget_remaining_usd"] == pytest.approx(1.00)
    assert body["database"] == "ok"
    assert body["db_backend"] == "sqlite"
    assert body["db_fallback"] is False
    assert body["kill_switch_active"] is False


def test_health_reports_sqlite_fallback(tmp_path):
    ledger = CognitiveLedger(tmp_path / "app.db")
    database = DatabaseRuntime(
        engine=ledger.engine,
        backend="sqlite-fallback",
        sqlite_path=str(tmp_path / "app.db"),
        fallback=True,
    )
    app = create_app(
        ledger=ledger,
        governor=BudgetGovernor(1.00),
        webhook_token="agent-secret",
        database=database,
    )

    with TestClient(app) as client:
        body = client.get("/api/health").json()

    assert body["db_backend"] == "sqlite-fallback"
    assert body["db_fallback"] is True


def test_telegram_webhook_chunks_reply_and_creates_human_thread(tmp_path):
    sent = []

    async def sender(chat_id, message):
        sent.append((chat_id, message))

    async def responder(update, thread_id):
        return "reply " * 1900  # 11,400 characters -> 3 chunks

    gateway = TelegramGateway(sender=sender, allowed_chat_id=123)
    app, ledger = _app(tmp_path, gateway=gateway, responder=responder)

    update = {
        "update_id": 1,
        "message": {"message_id": 2, "chat": {"id": 123}, "text": "do the task"},
    }

    with TestClient(app) as client:
        response = client.post("/api/webhook/telegram", json=update)

    assert response.status_code == 200
    assert response.json() == {
        "status": "accepted",
        "chunks_sent": 3,
        "saved_to_file": None,
    }
    assert len(sent) == 3
    assert all(len(message) <= 4000 for _, message in sent)

    async def read_thread():
        return await ledger.get_next_active_thread()

    thread = asyncio.run(read_thread())
    assert thread["origin"] == "HUMAN"
    assert thread["priority_score"] == 100
    assert thread["title"] == "Telegram: do the task"


def test_telegram_webhook_rejects_malformed_update(tmp_path):
    app, _ = _app(tmp_path)

    with TestClient(app) as client:
        response = client.post("/api/webhook/telegram", json={"update_id": 1})

    assert response.status_code == 422


def test_telegram_webhook_rejects_unauthorized_chat(tmp_path):
    async def responder(update, thread_id):
        return "nope"

    gateway = TelegramGateway(allowed_chat_id=999)
    app, _ = _app(tmp_path, gateway=gateway, responder=responder)

    update = {
        "update_id": 1,
        "message": {"message_id": 2, "chat": {"id": 123}, "text": "hello"},
    }

    with TestClient(app) as client:
        response = client.post("/api/webhook/telegram", json=update)

    assert response.status_code == 403


def test_import_and_build_app_from_foreign_cwd(tmp_path):
    repo = Path(__file__).resolve().parent
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            filter(None, [str(repo), os.environ.get("PYTHONPATH")])
        ),
    }
    script = (
        "import main\n"
        "from core.cognitive_ledger import CognitiveLedger\n"
        "app = main.create_app(ledger=CognitiveLedger('unused.db'))\n"
        "assert app.state.settings.agent_name == 'devi-subhadra'\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_telegram_webhook_accepts_real_payload_with_extra_fields(tmp_path):
    sent = []

    async def sender(chat_id, message):
        sent.append((chat_id, message))

    gateway = TelegramGateway(sender=sender, allowed_chat_id=123)
    app, _ = _app(tmp_path, gateway=gateway)
    update = {
        "update_id": 10,
        "message": {
            "message_id": 11,
            "date": 1763000000,
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 123, "is_bot": False, "first_name": "Ton"},
            "text": "halo",
        },
    }

    with TestClient(app) as client:
        response = client.post("/api/webhook/telegram", json=update)

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert len(sent) == 1


def test_duplicate_update_id_is_processed_once(tmp_path):
    sent = []

    async def sender(chat_id, message):
        sent.append((chat_id, message))

    async def responder(update, thread_id):
        return "ok"

    gateway = TelegramGateway(sender=sender, allowed_chat_id=123)
    app, ledger = _app(tmp_path, gateway=gateway, responder=responder)
    update = {
        "update_id": 77,
        "message": {"message_id": 2, "chat": {"id": 123}, "text": "sekali"},
    }

    with TestClient(app) as client:
        first = client.post("/api/webhook/telegram", json=update)
        second = client.post("/api/webhook/telegram", json=update)

    assert first.status_code == 200
    assert first.json()["status"] == "accepted"
    assert second.status_code == 200
    assert second.json() == {
        "status": "duplicate",
        "chunks_sent": 0,
        "saved_to_file": None,
    }
    assert len(sent) == 1
    assert len(asyncio.run(ledger.list_threads())) == 1


def test_telegram_route_uses_runtime_when_provided(tmp_path):
    sent = []

    async def sender(chat_id, message):
        sent.append(message)

    async def worker(prompt):
        return "dari runtime"

    ledger = CognitiveLedger(tmp_path / "app.db")
    governor = BudgetGovernor(1.00)
    runtime = Runtime(ledger, governor, worker=worker)
    gateway = TelegramGateway(sender=sender, allowed_chat_id=123)
    app = create_app(
        ledger=ledger,
        governor=governor,
        gateway=gateway,
        runtime=runtime,
        webhook_token="agent-secret",
    )
    update = {
        "update_id": 21,
        "message": {"message_id": 22, "chat": {"id": 123}, "text": "tugas"},
    }

    with TestClient(app) as client:
        response = client.post("/api/webhook/telegram", json=update)

    assert response.status_code == 200
    assert sent == ["dari runtime"]
    logs = asyncio.run(ledger.list_activity_logs())
    assert logs[0]["initiator"] == "Telegram"


def test_failed_reply_does_not_replay_mission(tmp_path):
    async def sender(chat_id, message):
        pass

    async def responder(update, thread_id):
        raise RuntimeError("worker down")

    gateway = TelegramGateway(sender=sender, allowed_chat_id=123)
    app, ledger = _app(tmp_path, gateway=gateway, responder=responder)
    update = {
        "update_id": 88,
        "message": {"message_id": 2, "chat": {"id": 123}, "text": "kerja"},
    }

    with TestClient(app, raise_server_exceptions=False) as client:
        first = client.post("/api/webhook/telegram", json=update)
        second = client.post("/api/webhook/telegram", json=update)

    assert first.status_code == 500
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert len(asyncio.run(ledger.list_threads())) == 1


def test_unconfigured_gateway_fails_closed(tmp_path):
    app, _ = _app(tmp_path)
    update = {
        "update_id": 5,
        "message": {"message_id": 6, "chat": {"id": 123}, "text": "hi"},
    }

    with TestClient(app) as client:
        response = client.post("/api/webhook/telegram", json=update)

    assert response.status_code == 503


def test_kill_switch_blocks_new_telegram_missions(tmp_path):
    async def sender(chat_id, message):
        pass

    async def responder(update, thread_id):
        return "ok"

    gateway = TelegramGateway(sender=sender, allowed_chat_id=123)
    app, ledger = _app(tmp_path, gateway=gateway, responder=responder)
    update = {
        "update_id": 9,
        "message": {"message_id": 10, "chat": {"id": 123}, "text": "hi"},
    }

    with TestClient(app) as client:
        asyncio.run(ledger.set_system_state("kill_switch", "1"))
        response = client.post("/api/webhook/telegram", json=update)

    assert response.status_code == 503
    assert asyncio.run(ledger.list_threads()) == []


def test_rate_limit_pause_answers_with_reason(tmp_path):
    sent = []

    async def sender(chat_id, message):
        sent.append(message)

    async def worker(prompt):
        return "ok"

    gateway = TelegramGateway(sender=sender, allowed_chat_id=123)
    ledger = CognitiveLedger(tmp_path / "limit.db")
    governor = BudgetGovernor(1.00, rpm_limit=1)
    governor.record_api_usage(1, side="HUMAN")
    runtime = Runtime(ledger, governor, worker=worker)
    app = create_app(
        ledger=ledger,
        governor=governor,
        gateway=gateway,
        runtime=runtime,
        webhook_token="agent-secret",
    )
    update = {
        "update_id": 41,
        "message": {"message_id": 42, "chat": {"id": 123}, "text": "halo"},
    }

    with TestClient(app) as client:
        response = client.post("/api/webhook/telegram", json=update)

    assert response.status_code == 200
    assert any("RPM limit" in message for message in sent)
    assert asyncio.run(ledger.get_system_state("kill_switch")) == "1"


def test_telegram_commands_are_reachable_while_paused(tmp_path):
    sent = []

    async def sender(chat_id, message):
        sent.append(message)

    gateway = TelegramGateway(sender=sender, allowed_chat_id=123)
    app, ledger = _app(tmp_path, gateway=gateway)

    def update(update_id, text):
        return {
            "update_id": update_id,
            "message": {"message_id": update_id, "chat": {"id": 123}, "text": text},
        }

    with TestClient(app) as client:
        paused = client.post("/api/webhook/telegram", json=update(1, "/pause-agent"))
        assert asyncio.run(ledger.list_threads()) == []
        blocked = client.post("/api/webhook/telegram", json=update(2, "hello"))
        status = client.post("/api/webhook/telegram", json=update(3, "/status"))
        resumed = client.post(
            "/api/webhook/telegram", json=update(4, "/resume-agent")
        )
        mission = client.post("/api/webhook/telegram", json=update(5, "hello"))

    assert paused.status_code == 200
    assert blocked.status_code == 503
    assert status.status_code == 200
    assert resumed.status_code == 200
    assert mission.status_code == 200
    assert any("Agent paused" in message for message in sent)
    assert any("state: paused" in message for message in sent)
    assert any("Agent resumed" in message for message in sent)
    assert len(asyncio.run(ledger.list_threads())) == 1


def test_telegram_status_does_not_create_a_thread(tmp_path):
    sent = []

    async def sender(chat_id, message):
        sent.append(message)

    gateway = TelegramGateway(sender=sender, allowed_chat_id=123)
    app, ledger = _app(tmp_path, gateway=gateway)
    update = {
        "update_id": 31,
        "message": {"message_id": 32, "chat": {"id": 123}, "text": "/status"},
    }

    with TestClient(app) as client:
        response = client.post("/api/webhook/telegram", json=update)

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert "state: running" in sent[0]
    assert asyncio.run(ledger.list_threads()) == []
