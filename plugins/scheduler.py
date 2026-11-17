"""Schedule reminders, process watches/kills, and recurring plugin runs."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from pydantic import Field

from core.base_plugin import BaseAgentPlugin
from core.scheduled_task_store import DEFAULT_TASKS_PATH, ScheduledTaskStore
from core.schemas import PluginOutputSchema, StrictBaseModel

# A kill task is only accepted when a human-ordered mission created it.
_KILL_KINDS = {"kill_process"}
_AT_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%H:%M")


class SchedulerInput(StrictBaseModel):
    action: Literal["create", "list", "cancel"] = Field(
        ..., description="create a task, list tasks, or cancel by task_id."
    )
    kind: Literal[
        "remind",
        "watch_process",
        "kill_process",
        "watch_mission",
        "run_plugin",
    ] = Field(default="remind", description="Task type.")
    message: str = Field(default="", description="Text sent to the user.")
    pid: Optional[int] = Field(default=None, description="OS process id.")
    thread_id: Optional[str] = Field(default=None, description="Mission thread id.")
    plugin: Optional[str] = Field(default=None, description="Existing plugin name.")
    plugin_payload: Dict[str, Any] = Field(
        default_factory=dict, description="Payload for the plugin."
    )
    at: Optional[str] = Field(
        default=None,
        description='Local time "HH:MM" (today/tomorrow) or "YYYY-MM-DD HH:MM".',
    )
    in_minutes: Optional[int] = Field(default=None, ge=1)
    daily_at: Optional[str] = Field(default=None, description='Recurring "HH:MM".')
    task_id: Optional[str] = Field(default=None, description="For action=cancel.")
    # Filled by Runtime from the current mission origin; never trusted from the LLM.
    origin: str = Field(default="AGENT")


class SchedulerPlugin(BaseAgentPlugin):
    plugin_name = "scheduler"
    description = (
        "Jadwalkan pengingat, pantau proses/PID sampai selesai, matikan proses "
        "(hanya atas perintah manusia), pantau misi sampai selesai, atau jalankan "
        "plugin yang sudah ada secara berkala. Zona waktu mengikuti server."
    )
    input_schema = SchedulerInput

    def __init__(self, tasks_path: str | Path | None = None) -> None:
        self._store = ScheduledTaskStore(tasks_path or DEFAULT_TASKS_PATH)
        self._plugins_dir = Path(__file__).resolve().parent

    def initialize(self, config: Dict[str, Any]) -> bool:
        if config.get("scheduled_tasks_path"):
            self._store = ScheduledTaskStore(config["scheduled_tasks_path"])
        if config.get("plugins_dir"):
            self._plugins_dir = Path(config["plugins_dir"])
        return True

    def shutdown(self) -> None:
        return None

    @staticmethod
    def _failure(message: str) -> PluginOutputSchema:
        return PluginOutputSchema(success=False, data={}, error_message=message)

    @staticmethod
    def _clock() -> Dict[str, str]:
        now = datetime.now().astimezone()
        return {
            "now": now.isoformat(timespec="seconds"),
            "timezone": now.strftime("%Z %z"),
            "utc_offset_hours": f"{now.utcoffset().total_seconds() / 3600:+g}",
        }

    def _available_plugins(self) -> list[str]:
        if not self._plugins_dir.is_dir():
            return []
        return sorted(
            path.stem
            for path in self._plugins_dir.glob("*.py")
            if not path.name.startswith("_") and not path.name.endswith(".test.py")
        )

    def _schedule(self, request: SchedulerInput) -> tuple[str | None, str | None] | str:
        now = datetime.now().astimezone()
        if request.daily_at:
            try:
                datetime.strptime(request.daily_at, "%H:%M")
            except ValueError:
                return f"daily_at harus 'HH:MM', bukan '{request.daily_at}'."
            return None, request.daily_at
        if request.in_minutes is not None:
            return (now + timedelta(minutes=request.in_minutes)).isoformat(), None
        if request.at:
            for pattern in _AT_FORMATS:
                try:
                    parsed = datetime.strptime(request.at, pattern)
                except ValueError:
                    continue
                if pattern == "%H:%M":
                    parsed = now.replace(
                        hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0
                    )
                    if parsed <= now:
                        parsed += timedelta(days=1)
                return parsed.astimezone().isoformat(), None
            return f"at harus 'HH:MM' atau 'YYYY-MM-DD HH:MM', bukan '{request.at}'."
        return now.isoformat(), None

    async def execute(self, payload: Dict[str, Any]) -> PluginOutputSchema:
        request = self.input_schema.model_validate(payload)
        clock = self._clock()

        if request.action == "list":
            tasks = await self._store.load()
            return PluginOutputSchema(
                success=True,
                data={**clock, "tasks": [task for task in tasks if not task.get("done")]},
            )
        if request.action == "cancel":
            if not request.task_id:
                return self._failure("task_id wajib untuk action='cancel'.")
            if not await self._store.remove(request.task_id):
                return self._failure(f"task_id '{request.task_id}' tidak ditemukan.")
            return PluginOutputSchema(success=True, data={**clock, "cancelled": request.task_id})

        if request.kind in {"watch_process", "kill_process"}:
            if request.pid is None or request.pid <= 1:
                return self._failure("pid wajib dan harus > 1.")
        if request.kind == "watch_mission" and not request.thread_id:
            return self._failure("thread_id wajib untuk watch_mission.")
        if request.kind == "run_plugin":
            if not request.plugin:
                return self._failure("plugin wajib untuk run_plugin.")
            available = self._available_plugins()
            if request.plugin not in available:
                return self._failure(
                    f"Plugin '{request.plugin}' tidak ditemukan. Tersedia: "
                    + (", ".join(available) or "(kosong)")
                )
        if request.kind == "remind" and not request.message:
            return self._failure("message wajib untuk kind='remind'.")
        if request.kind in _KILL_KINDS and request.origin != "HUMAN":
            return self._failure(
                "kill_process hanya boleh dibuat dari perintah manusia lewat "
                "Telegram/REPL; agen tidak boleh mematikan proses sendiri."
            )

        schedule = self._schedule(request)
        if isinstance(schedule, str):
            return self._failure(schedule)
        run_at, daily_at = schedule

        task = {
            "kind": request.kind,
            "message": request.message,
            "pid": request.pid,
            "thread_id": request.thread_id,
            "plugin": request.plugin,
            "plugin_payload": request.plugin_payload,
            "run_at": run_at,
            "daily_at": daily_at,
            "origin": request.origin,
        }
        task = await self._store.add(task)
        return PluginOutputSchema(success=True, data={**clock, "task": task})
