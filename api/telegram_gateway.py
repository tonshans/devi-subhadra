"""Telegram webhook gateway: Smart Chunker, cooldown, and long-reply spillover."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.cognitive_ledger import KILL_SWITCH_KEY
from core.commands import dispatch
from core.runtime import MissionBlockedError

MAX_CHUNK_CHARS = 4000
SPILLOVER_CHARS = 12000
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
TELEGRAM_OFFSET_KEY = "telegram_offset"
logger = logging.getLogger(__name__)


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
        atelier_dir: Optional[str | Path] = None,
    ) -> None:
        self._sender = sender
        self._workspace_dir = Path(workspace_dir)
        self._atelier_dir = Path(atelier_dir) if atelier_dir else None
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
        logger.debug("telegram send chat=%s chars=%s", chat_id, len(message))
        await self._sender(chat_id, message)

    async def webhook_info(self) -> Optional[dict]:
        """Delegate to the sender when it can query Telegram's webhook state."""
        fetch = getattr(self._sender, "get_webhook_info", None)
        if fetch is None:
            return None
        return await fetch()

    async def delete_webhook(self) -> None:
        delete = getattr(self._sender, "delete_webhook", None)
        if delete is not None:
            await delete()

    async def get_updates(
        self, offset: Optional[int] = None, timeout: int = 25
    ) -> list[dict]:
        fetch = getattr(self._sender, "get_updates", None)
        if fetch is None:
            raise RuntimeError("No Telegram polling sender configured.")
        return await fetch(offset=offset, timeout=timeout)

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

    def _resolve_file(self, path: str | Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self._workspace_dir / candidate
        target = candidate.resolve()
        roots = [self._workspace_dir.resolve()]
        if self._atelier_dir is not None:
            roots.append(self._atelier_dir.resolve())
        if not any(target.is_relative_to(root) for root in roots):
            raise ValueError(f"file is outside workspace/atelier: {path}")
        if not target.is_file():
            raise ValueError(f"file not found: {path}")
        if target.stat().st_size > MAX_DOCUMENT_BYTES:
            raise ValueError(
                f"file exceeds {MAX_DOCUMENT_BYTES} bytes limit: {path}"
            )
        return target

    async def send_file(
        self, path: str | Path, caption: Optional[str] = None
    ) -> None:
        """Send a local file to the configured chat; the agent cannot pick a chat."""
        if self._allowed_chat_id is None:
            raise RuntimeError("Telegram chat allowlist is not configured.")
        send_document = getattr(self._sender, "send_document", None)
        if send_document is None:
            raise RuntimeError("No Telegram document sender configured.")
        await send_document(self._allowed_chat_id, self._resolve_file(path), caption)

    async def notify(self, message: str) -> None:
        """Push a system notice to the allowlisted chat (no user input needed)."""
        if self._allowed_chat_id is None:
            raise RuntimeError("Telegram chat allowlist is not configured.")
        await self._send(self._allowed_chat_id, message)


router = APIRouter(prefix="/api/webhook", tags=["telegram"])


async def process_update(update: TelegramUpdate, state: Any) -> dict:
    """Shared by the webhook route and the long-polling loop."""
    gateway: TelegramGateway = state.gateway
    chat_id = update.message.chat.id
    logger.info(
        "telegram update %s chat=%s text=%r",
        update.update_id,
        chat_id,
        update.message.text[:80],
    )
    if not gateway.is_authorized(chat_id):
        logger.warning("telegram chat %s is not authorized", chat_id)
        raise HTTPException(status_code=403, detail="Unauthorized chat")
    if not gateway.is_configured:
        logger.warning("telegram gateway is not configured")
        raise HTTPException(status_code=503, detail="Telegram gateway is not configured")

    # Commands bypass the kill switch so /status and /resume-agent stay reachable.
    command_context = getattr(state, "command_context", None)
    if command_context is not None:
        reply = await dispatch(update.message.text, command_context)
        if reply is not None:
            logger.info("telegram command %r handled", update.message.text[:40])
            result = await gateway.send_response(
                chat_id, reply, response_id=update.message.message_id
            )
            return {
                "status": "accepted",
                "chunks_sent": result.chunks_sent,
                "saved_to_file": result.saved_to_file,
            }

    ledger = state.ledger
    runtime = getattr(state, "runtime", None)
    if runtime is not None:
        await runtime.auto_resume()
    if await ledger.get_system_state(KILL_SWITCH_KEY) == "1":
        # A rate-limit pause must still explain itself; a manual pause stays 503.
        notice = await runtime.pause_notice() if runtime is not None else None
        if notice is not None:
            logger.warning("telegram mission blocked: %s", notice.splitlines()[0])
            result = await gateway.send_response(
                chat_id, notice, response_id=update.message.message_id
            )
            return {
                "status": "accepted",
                "chunks_sent": result.chunks_sent,
                "saved_to_file": result.saved_to_file,
            }
        logger.warning("telegram mission blocked by kill switch")
        raise HTTPException(status_code=503, detail="Kill switch is active")
    # Telegram retries on non-2xx; claim first so a retry cannot duplicate a mission.
    if not await ledger.claim_telegram_update(update.update_id):
        logger.info("telegram update %s is a duplicate", update.update_id)
        return {"status": "duplicate", "chunks_sent": 0, "saved_to_file": None}

    thread_id = None
    started = time.monotonic()
    try:
        thread_id = await ledger.create_thread(
            f"Telegram: {update.message.text[:60]}", "HUMAN"
        )
        logger.info("telegram mission thread=%s started", thread_id)
        if runtime is not None:
            try:
                reply = await runtime.handle_mission(
                    thread_id, update.message.text, initiator="Telegram"
                )
            except MissionBlockedError:
                # Paused mid-flight by a rate limit: say why instead of failing.
                notice = await runtime.pause_notice()
                if notice is None:
                    raise HTTPException(
                        status_code=503, detail="Kill switch is active"
                    )
                reply = notice
        else:
            reply = await state.responder(update, thread_id)
        result = await gateway.send_response(
            chat_id, reply, response_id=update.message.message_id
        )
        logger.info(
            "telegram mission thread=%s done in %.1fs chunks=%s",
            thread_id,
            time.monotonic() - started,
            result.chunks_sent,
        )
    except Exception:
        logger.exception("telegram mission failed thread=%s", thread_id)
        # Keep the claim once a mission exists; retrying would duplicate the mission.
        if thread_id is None:
            await ledger.release_telegram_update(update.update_id)
        raise

    return {
        "status": "accepted",
        "chunks_sent": result.chunks_sent,
        "saved_to_file": result.saved_to_file,
    }


@router.post("/telegram")
async def telegram_webhook(update: TelegramUpdate, request: Request) -> dict:
    return await process_update(update, request.app.state)


async def polling_loop(state: Any, timeout: int = 25) -> None:
    """Own the Telegram update queue via getUpdates (no public URL needed)."""
    gateway: TelegramGateway = state.gateway
    ledger = state.ledger
    offset: Optional[int] = None
    stored = await ledger.get_system_state(TELEGRAM_OFFSET_KEY)
    if stored:
        try:
            offset = int(stored)
        except ValueError:
            logger.warning("telegram polling: bad stored offset %r", stored)

    logger.info("telegram polling started (offset=%s)", offset)
    while True:
        try:
            updates = await gateway.get_updates(offset=offset, timeout=timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            delay = 30 if "HTTP 409" in str(exc) else 5
            logger.error("telegram polling failed (%s); retry in %ss", exc, delay)
            await asyncio.sleep(delay)
            continue

        if not updates:
            await asyncio.sleep(1)
            continue
        logger.info("telegram polling: %s update(s)", len(updates))
        for payload in updates:
            update_id = payload.get("update_id") if isinstance(payload, dict) else None
            try:
                update = TelegramUpdate.model_validate(payload)
            except ValidationError:
                logger.warning("telegram polling: malformed update %s", update_id)
            else:
                try:
                    await process_update(update, state)
                except HTTPException as exc:
                    logger.warning(
                        "telegram polling: update %s rejected: %s",
                        update.update_id,
                        exc.detail,
                    )
                except Exception:
                    logger.exception(
                        "telegram polling: update %s failed", update.update_id
                    )
            if isinstance(update_id, int):
                offset = update_id + 1
                await ledger.set_system_state(TELEGRAM_OFFSET_KEY, str(offset))