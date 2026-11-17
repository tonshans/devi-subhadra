"""Unit tests for the OpenRouter chat-completions worker."""

import asyncio
import json

import httpx
import pytest

from core.llm_worker import GeminiWorker, OpenRouterWorker


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


def test_structured_worker_parses_mission_reply_with_files():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"reply": "file siap", "files": ["laporan.txt"]}'
                        }
                    }
                ]
            },
        )

    async def scenario():
        async with _client(handler) as client:
            worker = OpenRouterWorker(
                "api-key", "model/x", client=client, structured=True
            )
            return await worker("halo")

    reply = asyncio.run(scenario())

    assert reply.reply == "file siap"
    assert reply.files == ["laporan.txt"]
    body = json.loads(seen[0].content)
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    assert "json" in body["messages"][0]["content"].lower()
    assert body["messages"][1] == {"role": "user", "content": "halo"}


def test_structured_worker_rejects_invalid_json():
    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "bukan json"}}]}
        )

    async def scenario():
        async with _client(handler) as client:
            worker = OpenRouterWorker(
                "api-key", "model/x", client=client, structured=True
            )
            with pytest.raises(RuntimeError, match="MissionReply"):
                await worker("halo")

    asyncio.run(scenario())


def test_gemini_worker_sends_key_in_header_not_url():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "jawaban"}]}}],
                "usageMetadata": {
                    "promptTokenCount": 7,
                    "candidatesTokenCount": 3,
                },
            },
        )

    async def scenario():
        async with _client(handler) as client:
            worker = GeminiWorker("gemini-key", "gemini-2.5-flash", client=client)
            return await worker("halo")

    reply = asyncio.run(scenario())

    assert reply == "jawaban"
    assert seen[0].url.path == (
        "/v1beta/models/gemini-2.5-flash:generateContent"
    )
    assert "gemini-key" not in str(seen[0].url)
    assert seen[0].headers["x-goog-api-key"] == "gemini-key"
    body = json.loads(seen[0].content)
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "halo"}]}
    ]


def test_gemini_worker_reports_token_usage():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
                "usageMetadata": {
                    "promptTokenCount": 100,
                    "candidatesTokenCount": 50,
                },
            },
        )

    async def scenario():
        async with _client(handler) as client:
            worker = GeminiWorker("k", "model", client=client)
            await worker("halo")
        return worker

    worker = asyncio.run(scenario())

    assert worker.last_usage == {"prompt_tokens": 100, "completion_tokens": 50}


def test_structured_gemini_worker_requests_and_parses_json():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": (
                                        '{"reply": "file siap", '
                                        '"files": ["laporan.txt"]}'
                                    )
                                }
                            ]
                        }
                    }
                ]
            },
        )

    async def scenario():
        async with _client(handler) as client:
            worker = GeminiWorker("k", "model", client=client, structured=True)
            return await worker("halo")

    reply = asyncio.run(scenario())

    assert reply.reply == "file siap"
    assert reply.files == ["laporan.txt"]
    body = json.loads(seen[0].content)
    assert body["generationConfig"] == {"responseMimeType": "application/json"}
    assert "json" in body["systemInstruction"]["parts"][0]["text"].lower()


def test_gemini_worker_raises_when_response_has_no_candidates():
    def handler(request):
        return httpx.Response(200, json={"error": {"message": "upstream down"}})

    async def scenario():
        async with _client(handler) as client:
            worker = GeminiWorker("k", "model", client=client)
            with pytest.raises(RuntimeError, match="candidates"):
                await worker("halo")

    asyncio.run(scenario())


def test_gemini_worker_raises_on_http_error():
    def handler(request):
        return httpx.Response(429, json={"error": {"message": "quota"}})

    async def scenario():
        async with _client(handler) as client:
            worker = GeminiWorker("k", "model", client=client)
            with pytest.raises(httpx.HTTPStatusError):
                await worker("halo")

    asyncio.run(scenario())



def test_worker_parses_a_per_call_structured_model():
    from core.schemas import SupervisorDecision

    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"action_type": "SLEEP_NOOP", '
                                '"model_tier_used": "TIER_1_LIGHT", '
                                '"reasoning": "nothing to do", '
                                '"thought_entry": "no useful work left"}'
                            )
                        }
                    }
                ]
            },
        )

    async def scenario():
        async with _client(handler) as client:
            worker = OpenRouterWorker("api-key", "model/x", client=client)
            return await worker("idle", structured_model=SupervisorDecision)

    decision = asyncio.run(scenario())

    assert decision.action_type == "SLEEP_NOOP"


def test_worker_rejects_content_outside_the_per_call_model():
    from core.schemas import SupervisorDecision

    def handler(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"reply": "hi"}'}}]},
        )

    async def scenario():
        async with _client(handler) as client:
            worker = OpenRouterWorker("api-key", "model/x", client=client)
            with pytest.raises(RuntimeError, match="SupervisorDecision"):
                await worker("idle", structured_model=SupervisorDecision)

    asyncio.run(scenario())
