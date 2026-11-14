"""Sensor/IoT and inter-agent webhooks with shared-token authentication."""

from __future__ import annotations

import hmac
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from core.cognitive_ledger import KILL_SWITCH_KEY
from core.runtime import MissionBlockedError
from core.schemas import StrictBaseModel


class SensorEvent(StrictBaseModel):
    sensor_id: str = Field(..., min_length=1)
    event_type: str = Field(..., min_length=1)
    payload: Dict[str, Any]
    timestamp: str = Field(..., min_length=1)


router = APIRouter(prefix="/api/webhook", tags=["webhooks"])


def _require_agent_token(request: Request) -> str:
    supplied = request.headers.get("X-Agent-Token")
    tokens = getattr(request.app.state, "webhook_tokens", None)
    if tokens:
        for token, source in tokens.items():
            if supplied and hmac.compare_digest(token, supplied):
                return source
        raise HTTPException(status_code=401, detail="Invalid agent token")

    expected = getattr(request.app.state, "webhook_token", None)
    if expected and supplied and hmac.compare_digest(expected, supplied):
        return "legacy"
    raise HTTPException(status_code=401, detail="Invalid agent token")


@router.post("/sensor")
async def sensor_webhook(event: SensorEvent, request: Request) -> dict:
    source = _require_agent_token(request)
    ledger = request.app.state.ledger
    if await ledger.get_system_state(KILL_SWITCH_KEY) == "1":
        raise HTTPException(status_code=503, detail="Kill switch is active")
    thread_id = await ledger.create_thread(
        f"Sensor {event.sensor_id}: {event.event_type}", "AGENT"
    )
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is not None:
        try:
            await runtime.handle_mission(
                thread_id, f"{event.event_type}: {event.payload}", initiator="Webhook"
            )
        except MissionBlockedError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
    return {"status": "processed", "thread_id": thread_id, "source": source}