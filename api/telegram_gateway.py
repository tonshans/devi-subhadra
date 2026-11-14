"""Telegram webhook gateway: Smart Chunker, cooldown, and long-reply spillover."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.cognitive_ledger import KILL_SWITCH_KEY
from core.runtime import MissionBlockedError

MAX_CHUNK_CHARS = 4000
SPILLOVER_CHARS = 12000


class _TelegramInbound(BaseModel):
    """Telegram sends extra fields (date, from, chat.type); ignore them."""

    model_config = ConfigDict(extra="ignore")


class TelegramChat(_TelegramInbound):
    id: int


class TelegramMessage(_TelegramInbound):
    message_id: int
    chat: TelegramChat
    text: str = Field(..., min_length=1)


class TelegramUpdate(_TelegramInbound):
    update_id: int
    message: TelegramMessage


@dataclass(frozen=True)
class SendResult:
    chunks_sent: int
    saved_to_file: Optional[str]


def chunk_message(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split text sequentially, preferring newline/space boundaries."""
    if not text:
        return []
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        cut = max(window.rfind("\n"), window.rfind(" "))
        if cut <= 0:
            cut = max_chars
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    chunks.append(remaining)
    return chunks


class TelegramGateway:
    def __init__(
        self,
        sender: Optional[Callable[[int, str], Awaitable[None]]] = None,
        workspace_dir: str | Path = "mnt/workspace",
        cooldown_seconds: float = 60,
        allowed_chat_id: Optional[int] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._sender = sender
        self._workspace_dir = Path(workspace_dir)
        self._cooldown_seconds = cooldown_seconds
        self._allowed_chat_id = allowed_chat_id
        self._clock = clock or time.monotonic
        self._last_wake_up: Optional[float] = None

    @property
    def is_configured(self) -> bool:
        """Fail closed: refuse traffic until a sender and chat allowlist exist."""
        return self._sender is not None and self._allowed_chat_id is not None

    def is_authorized(self, chat_id: int) -> bool:
        return self._allowed_chat_id is None or chat_id == self._allowed_chat_id

    def wake_up_ping(self) -> bool:
        now = self._clock()
        if (
            self._last_wake_up is not None
            and now - self._last_wake_up < self._cooldown_seconds
        ):
            return False
        self._last_wake_up = now
        return True

    async def _send(self, chat_id: int, message: str) -> None:
        if self._sender is None:
            raise RuntimeError("No Telegram sender configured.")
        await self._sender(chat_id, message)

    async def send_response(
        self, chat_id: int, text: str, response_id: Optional[int] = None
    ) -> SendResult:
        if len(text) > SPILLOVER_CHARS:
            path = self._workspace_dir / f"response_{response_id or chat_id}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            await self._send(chat_id, f"Response saved to {path}")
            return SendResult(chunks_sent=1, saved_to_file=str(path))

        chunks = chunk_message(text)
        for chunk in chunks:
            await self._send(chat_id, chunk)
        return SendResult(chunks_sent=len(chunks), saved_to_file=None)


router = APIRouter(prefix="/api/webhook", tags=["telegram"])


@router.post("/telegram")
async def telegram_webhook(update: TelegramUpdate, request: Request) -> dict:
    gateway: TelegramGateway = request.app.state.gateway
    chat_id = update.message.chat.id
    if not gateway.is_authorized(chat_id):
        raise HTTPException(status_code=403, detail="Unauthorized chat")
    if not gateway.is_configured:
        raise HTTPException(status_code=503, detail="Telegram gateway is not configured")

    ledger = request.app.state.ledger
    if await ledger.get_system_state(KILL_SWITCH_KEY) == "1":
        raise HTTPException(status_code=503, detail="Kill switch is active")
    # Telegram retries on non-2xx; claim first so a retry cannot duplicate a mission.
    if not await ledger.claim_telegram_update(update.update_id):
        return {"status": "duplicate", "chunks_sent": 0, "saved_to_file": None}

    thread_id = None
    try:
        thread_id = await ledger.create_thread(
            f"Telegram: {update.message.text[:60]}", "HUMAN"
        )
        runtime = getattr(request.app.state, "runtime", None)
        if runtime is not None:
            try:
                reply = await runtime.handle_mission(
                    thread_id, update.message.text, initiator="Telegram"
                )
            except MissionBlockedError as exc:
                raise HTTPException(status_code=503, detail=str(exc))
        else:
            reply = await request.app.state.responder(update, thread_id)
        result = await gateway.send_response(
            chat_id, reply, response_id=update.message.message_id
        )
    except Exception:
        # Keep the claim once a mission exists; retrying would duplicate the mission.
        if thread_id is None:
            await ledger.release_telegram_update(update.update_id)
        raise

    return {
        "status": "accepted",
        "chunks_sent": result.chunks_sent,
        "saved_to_file": result.saved_to_file,
    }