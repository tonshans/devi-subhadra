"""Unit tests for Smart Chunker, wake-up cooldown, and long-response spillover."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.telegram_gateway import (
    TELEGRAM_OFFSET_KEY,
    TelegramGateway,
    chunk_message,
    polling_loop,
)
from core.cognitive_ledger import CognitiveLedger


def test_chunks_9500_characters_into_three_bounded_messages():
    text = "word " * 1900

    chunks = chunk_message(text, max_chars=4000)

    assert len(chunks) == 3
    assert all(0 < len(chunk) <= 4000 for chunk in chunks)
    assert "".join(chunks) == text


def test_short_message_is_a_single_chunk():
    assert chunk_message("hello") == ["hello"]


def test_wake_up_ping_respects_cooldown_window():
    now = [1000.0]
    gateway = TelegramGateway(cooldown_seconds=60, clock=lambda: now[0])

    assert gateway.wake_up_ping() is True
    assert gateway.wake_up_ping() is False

    now[0] = 1059.0
    assert gateway.wake_up_ping() is False

    now[0] = 1060.0
    assert gateway.wake_up_ping() is True


def test_send_response_sends_every_chunk_via_sender():
    async def scenario():
        sent = []

        async def sender(chat_id, message):
            sent.append((chat_id, message))

        gateway = TelegramGateway(sender=sender)
        result = await gateway.send_response(42, "word " * 1900)

        assert result.chunks_sent == 3
        assert result.saved_to_file is None
        assert len(sent) == 3
        assert all(chat_id == 42 for chat_id, _ in sent)
        assert all(len(message) <= 4000 for _, message in sent)

    asyncio.run(scenario())


def test_oversized_response_is_saved_to_workspace(tmp_path):
    async def scenario():
        sent = []

        async def sender(chat_id, message):
            sent.append((chat_id, message))

        gateway = TelegramGateway(sender=sender, workspace_dir=tmp_path)
        text = "x" * 13000
        result = await gateway.send_response(42, text, response_id=7)

        expected = tmp_path / "response_7.md"
        assert result.chunks_sent == 1
        assert result.saved_to_file == str(expected)
        assert expected.read_text(encoding="utf-8") == text
        assert len(sent) == 1
        assert str(expected) in sent[0][1]

    asyncio.run(scenario())


class _DocumentSender:
    def __init__(self):
        self.documents = []

    async def __call__(self, chat_id, message):
        pass

    async def send_document(self, chat_id, path, caption=None):
        self.documents.append((chat_id, Path(path), caption))


def test_send_file_uses_configured_chat_and_allowed_roots(tmp_path):
    async def scenario():
        workspace = tmp_path / "workspace"
        atelier = tmp_path / "atelier"
        workspace.mkdir()
        atelier.mkdir()
        (workspace / "note.txt").write_text("x", encoding="utf-8")
        (atelier / "art.txt").write_text("y", encoding="utf-8")

        sender = _DocumentSender()
        gateway = TelegramGateway(
            sender=sender,
            workspace_dir=workspace,
            atelier_dir=atelier,
            allowed_chat_id=123,
        )

        await gateway.send_file("note.txt", caption="catatan")
        await gateway.send_file(atelier / "art.txt")

        assert sender.documents == [
            (123, (workspace / "note.txt").resolve(), "catatan"),
            (123, (atelier / "art.txt").resolve(), None),
        ]

    asyncio.run(scenario())


def test_send_file_rejects_paths_outside_roots(tmp_path):
    async def scenario():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        (workspace / "link.txt").symlink_to(outside)

        gateway = TelegramGateway(
            sender=_DocumentSender(), workspace_dir=workspace, allowed_chat_id=123
        )

        for path in ("../secret.txt", outside, "link.txt", "missing.txt"):
            with pytest.raises(ValueError):
                await gateway.send_file(path)

    asyncio.run(scenario())


def test_send_file_rejects_oversized_document(tmp_path, monkeypatch):
    async def scenario():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "big.txt").write_text("abcd", encoding="utf-8")
        gateway = TelegramGateway(
            sender=_DocumentSender(), workspace_dir=workspace, allowed_chat_id=123
        )

        with pytest.raises(ValueError, match="limit"):
            await gateway.send_file("big.txt")

    monkeypatch.setattr("api.telegram_gateway.MAX_DOCUMENT_BYTES", 3)
    asyncio.run(scenario())


def test_send_file_requires_configured_chat(tmp_path):
    async def scenario():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "note.txt").write_text("x", encoding="utf-8")
        gateway = TelegramGateway(
            sender=_DocumentSender(), workspace_dir=workspace
        )

        with pytest.raises(RuntimeError, match="allowlist"):
            await gateway.send_file("note.txt")

    asyncio.run(scenario())


def test_polling_loop_processes_updates_and_persists_offset(tmp_path):
    offsets = []
    sent = []

    async def sender(chat_id, message):
        sent.append(message)

    async def responder(update, thread_id):
        return "polled"

    async def fake_updates(offset=None, timeout=25):
        offsets.append(offset)
        if len(offsets) > 1:
            raise asyncio.CancelledError
        return [
            {
                "update_id": 10,
                "message": {
                    "message_id": 11,
                    "chat": {"id": 123},
                    "text": "halo",
                },
            }
        ]

    async def scenario():
        ledger = CognitiveLedger(tmp_path / "poll.db")
        await ledger.initialize()
        gateway = TelegramGateway(sender=sender, allowed_chat_id=123)
        gateway.get_updates = fake_updates
        state = SimpleNamespace(
            gateway=gateway,
            command_context=None,
            ledger=ledger,
            runtime=None,
            responder=responder,
        )

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(polling_loop(state), timeout=2)

        assert sent == ["polled"]
        assert offsets == [None, 11]
        assert await ledger.get_system_state(TELEGRAM_OFFSET_KEY) == "11"

    asyncio.run(scenario())