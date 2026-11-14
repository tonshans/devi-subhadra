"""Unit tests for the OpenRouter chat-completions worker."""

import asyncio
import json

import httpx
import pytest

from core.llm_worker import OpenRouterWorker


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_worker_posts_prompt_and_returns_content():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "jawaban"}}]},
        )

    async def scenario():
        async with _client(handler) as client:
            worker = OpenRouterWorker("api-key", "model/x", client=client)
            reply = await worker("halo")

        assert reply == "jawaban"

    asyncio.run(scenario())

    assert seen[0].headers["authorization"] == "Bearer api-key"
    body = json.loads(seen[0].content)
    assert body["model"] == "model/x"
    assert body["messages"] == [{"role": "user", "content": "halo"}]


def test_worker_records_token_usage_for_billing():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    async def scenario():
        async with _client(handler) as client:
            worker = OpenRouterWorker("api-key", "model/x", client=client)
            assert worker.last_usage is None
            await worker("halo")
        return worker

    worker = asyncio.run(scenario())

    assert worker.last_usage == {"prompt_tokens": 100, "completion_tokens": 50}


def test_worker_clears_stale_usage_when_response_has_no_usage():
    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )

    async def scenario():
        async with _client(handler) as client:
            worker = OpenRouterWorker("api-key", "model/x", client=client)
            worker.last_usage = {"prompt_tokens": 9, "completion_tokens": 9}
            await worker("halo")
        return worker

    worker = asyncio.run(scenario())

    assert worker.last_usage is None


def test_worker_raises_when_response_has_no_choices():
    def handler(request):
        return httpx.Response(200, json={"error": {"message": "upstream down"}})

    async def scenario():
        async with _client(handler) as client:
            worker = OpenRouterWorker("api-key", "model/x", client=client)
            with pytest.raises(RuntimeError, match="missing"):
                await worker("halo")

    asyncio.run(scenario())


def test_worker_raises_on_http_error():
    def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    async def scenario():
        async with _client(handler) as client:
            worker = OpenRouterWorker("api-key", "model/x", client=client)
            with pytest.raises(httpx.HTTPStatusError):
                await worker("halo")

    asyncio.run(scenario())
