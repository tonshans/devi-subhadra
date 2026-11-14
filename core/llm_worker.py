"""OpenRouter chat-completions worker; pass as Runtime(worker=...)."""

from __future__ import annotations

from typing import Optional

import httpx

DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterWorker:
    def __init__(
        self,
        api_key: str,
        model: str,
        client: Optional[httpx.AsyncClient] = None,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: float = 120,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client
        self._endpoint = endpoint
        self._timeout = timeout
        # Set per call from the response `usage` block; None when absent.
        self.last_usage: Optional[dict] = None

    async def _post(self, payload: dict) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if self._client is not None:
            return await self._client.post(
                self._endpoint, json=payload, headers=headers
            )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.post(self._endpoint, json=payload, headers=headers)

    async def __call__(self, prompt: str) -> str:
        self.last_usage = None
        response = await self._post(
            {
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
            }
        )
        response.raise_for_status()
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                "OpenRouter response missing choices[0].message.content"
            ) from exc
        usage = data.get("usage")
        self.last_usage = (
            {
                "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                "completion_tokens": int(usage.get("completion_tokens", 0)),
            }
            if isinstance(usage, dict)
            else None
        )
        return content
