"""Unit tests for Smart Chunker, wake-up cooldown, and long-response spillover."""

import asyncio

from api.telegram_gateway import TelegramGateway, chunk_message


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