"""OpenRouter chat-completions worker; pass as Runtime(worker=...)."""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any, Optional

import httpx
from pydantic import BaseModel, ValidationError

from core.schemas import MissionReply

DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"
STRUCTURED_INSTRUCTION = (
    'Reply ONLY as JSON: {"reply": "<text for the user>", '
    '"files": ["<local file path inside workspace or atelier>"], '
    '"plugin": "<tool name or null>", "plugin_payload": {}, '
    '"reasoning": "<one or two sentences on why you chose this>"}. '
    "Use an empty files list when no file should be sent. "
    "Set plugin only when a listed tool is needed; the tool runs and you are "
    "asked again for the final reply."
)


def _schema_instruction(model: type[BaseModel]) -> str:
    return (
        "Reply ONLY as JSON matching this schema: "
        + json.dumps(model.model_json_schema())
    )


_UNSET = object()


class LastUsageState:
    """Per-task `last_usage` so concurrent calls on one worker stay isolated."""

    def __init__(self) -> None:
        self._last_usage_var: ContextVar[Any] = ContextVar(
            "last_usage", default=_UNSET
        )
        # Fallback for readers in another context (tests, sync callers).
        self._last_usage_fallback: Optional[dict] = None

    @property
    def last_usage(self) -> Optional[dict]:
        value = self._last_usage_var.get()
        return self._last_usage_fallback if value is _UNSET else value

    @last_usage.setter
    def last_usage(self, value: Optional[dict]) -> None:
        self._last_usage_var.set(value)
        self._last_usage_fallback = value


class OpenRouterWorker(LastUsageState):
    def __init__(
        self,
        api_key: str,
        model: str,
        client: Optional[httpx.AsyncClient] = None,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: float = 120,
        structured: bool = False,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client
        self._endpoint = endpoint
        self._timeout = timeout
        self._structured = structured
        super().__init__()

    @property
    def model(self) -> str:
        return self._model

    async def _post(self, payload: dict) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if self._client is not None:
            return await self._client.post(
                self._endpoint, json=payload, headers=headers
            )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.post(self._endpoint, json=payload, headers=headers)

    async def __call__(
        self, prompt: str, structured_model: Optional[type[BaseModel]] = None
    ) -> str | BaseModel:
        self.last_usage = None
        model = structured_model or (MissionReply if self._structured else None)
        messages = [{"role": "user", "content": prompt}]
        payload = {"model": self._model, "messages": messages}
        if model is not None:
            instruction = (
                STRUCTURED_INSTRUCTION
                if model is MissionReply
                else _schema_instruction(model)
            )
            messages.insert(0, {"role": "system", "content": instruction})
            payload["response_format"] = {"type": "json_object"}
        response = await self._post(payload)
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
        if model is not None:
            try:
                return model.model_validate_json(content)
            except ValidationError as exc:
                raise RuntimeError(
                    f"OpenRouter response is not a valid {model.__name__}"
                ) from exc
        return content


class GeminiWorker(LastUsageState):
    """Gemini generateContent worker; same call contract as OpenRouterWorker."""

    def __init__(
        self,
        api_key: str,
        model: str,
        client: Optional[httpx.AsyncClient] = None,
        endpoint: str = GEMINI_ENDPOINT,
        timeout: float = 120,
        structured: bool = False,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client
        self._endpoint = endpoint
        self._timeout = timeout
        self._structured = structured
        super().__init__()

    @property
    def model(self) -> str:
        return self._model

    def _url(self) -> str:
        return f"{self._endpoint}/{self._model}:generateContent"

    async def _post(self, payload: dict) -> httpx.Response:
        # Keep the key in a header so it never lands in the request URL or logs.
        headers = {"x-goog-api-key": self._api_key}
        if self._client is not None:
            return await self._client.post(self._url(), json=payload, headers=headers)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.post(self._url(), json=payload, headers=headers)

    async def __call__(
        self, prompt: str, structured_model: Optional[type[BaseModel]] = None
    ) -> str | BaseModel:
        self.last_usage = None
        model = structured_model or (MissionReply if self._structured else None)
        payload: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}]
        }
        if model is not None:
            instruction = (
                STRUCTURED_INSTRUCTION
                if model is MissionReply
                else _schema_instruction(model)
            )
            payload["systemInstruction"] = {"parts": [{"text": instruction}]}
            payload["generationConfig"] = {"responseMimeType": "application/json"}
        response = await self._post(payload)
        response.raise_for_status()
        try:
            data = response.json()
            parts = data["candidates"][0]["content"]["parts"]
            content = "".join(part.get("text", "") for part in parts)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                "Gemini response missing candidates[0].content.parts"
            ) from exc
        usage = data.get("usageMetadata")
        self.last_usage = (
            {
                "prompt_tokens": int(usage.get("promptTokenCount", 0)),
                "completion_tokens": int(usage.get("candidatesTokenCount", 0)),
            }
            if isinstance(usage, dict)
            else None
        )
        if model is not None:
            try:
                return model.model_validate_json(content)
            except ValidationError as exc:
                raise RuntimeError(
                    f"Gemini response is not a valid {model.__name__}"
                ) from exc
        return content
