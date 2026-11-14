"""Telegram Bot API sender; pass as TelegramGateway(sender=...)."""

from __future__ import annotations

from typing import Optional

import httpx

from api.telegram_gateway import MAX_CHUNK_CHARS

DEFAULT_API_BASE = "https://api.telegram.org"


class TelegramSender:
    def __init__(
        self,
        token: str,
        client: Optional[httpx.AsyncClient] = None,
        api_base: str = DEFAULT_API_BASE,
        timeout: float = 15,
    ) -> None:
        self._token = token
        self._client = client
        self._api_base = api_base.rstrip("/")
        self._timeout = timeout

    async def _post(self, url: str, payload: dict) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(url, json=payload)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.post(url, json=payload)

    async def __call__(self, chat_id: int, message: str) -> None:
        if len(message) > MAX_CHUNK_CHARS:
            raise ValueError(
                f"message exceeds {MAX_CHUNK_CHARS} characters; chunk it first"
            )
        # Bot token lives in the URL, so never forward httpx errors verbatim.
        try:
            response = await self._post(
                f"{self._api_base}/bot{self._token}/sendMessage",
                {"chat_id": chat_id, "text": message},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"Telegram API HTTP {exc.response.status_code}") from None
        body = response.json()
        if not body.get("ok", False):
            raise RuntimeError(
                f"Telegram rejected message: {body.get('description', 'unknown error')}"
            )
