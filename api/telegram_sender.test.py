"""Unit tests for the Telegram Bot API sender."""

import asyncio
import json

import httpx
import pytest

from api.telegram_sender import TelegramSender


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_posts_message_to_bot_api():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async def scenario():
        async with _client(handler) as client:
            sender = TelegramSender("secret-token", client=client)
            await sender(42, "hello")

    asyncio.run(scenario())

    assert len(seen) == 1
    assert seen[0].url.path == "/botsecret-token/sendMessage"
    assert json.loads(seen[0].content) == {"chat_id": 42, "text": "hello"}


def test_rejects_messages_above_the_chunker_limit():
    sender = TelegramSender("token")

    with pytest.raises(ValueError):
        asyncio.run(sender(1, "x" * 4001))


def test_raises_when_telegram_reports_not_ok():
    def handler(request):
        return httpx.Response(
            200, json={"ok": False, "description": "chat not found"}
        )

    async def scenario():
        async with _client(handler) as client:
            sender = TelegramSender("token", client=client)
            with pytest.raises(RuntimeError, match="chat not found"):
                await sender(1, "hello")

    asyncio.run(scenario())


def test_raises_on_http_error_without_leaking_token():
    def handler(request):
        return httpx.Response(401, json={"ok": False, "description": "unauthorized"})

    async def scenario():
        async with _client(handler) as client:
            sender = TelegramSender("secret-token", client=client)
            with pytest.raises(RuntimeError, match="HTTP 401") as error:
                await sender(1, "hello")
            assert "secret-token" not in str(error.value)

    asyncio.run(scenario())
