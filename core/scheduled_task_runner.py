"""Execute due scheduled tasks; called once per runtime scheduler tick."""

from __future__ import annotations

import logging
import os
import signal
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from core.scheduled_task_store import ScheduledTaskStore

Notify = Callable[[str], Awaitable[None]]
RunPlugin = Callable[[str, dict], Awaitable[Any]]
ReadRow = Callable[[str, str], Awaitable[Optional[dict]]]

DONE_STATUSES = {"Completed", "Archived"}
logger = logging.getLogger(__name__)


def _local(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now().astimezone()
    return moment.astimezone() if moment.tzinfo else moment.astimezone()


def _pid_alive(pid: int) -> bool:
    return pid > 0 and os.path.isdir(f"/proc/{pid}")


class ScheduledTaskRunner:
    """Fires reminders, process watches, kills, and plugin runs from the tick."""

    def __init__(
        self,
        store: ScheduledTaskStore,
        notify: Notify,
        run_plugin: RunPlugin,
        read_row: ReadRow,
    ) -> None:
        self._store = store
        self._notify = notify
        self._run_plugin = run_plugin
        self._read_row = read_row

    async def tick(self, now: datetime | None = None) -> list[str]:
        now = _local(now)
        tasks = await self._store.load()
        fired: list[str] = []
        for task in tasks:
            if task.get("done"):
                continue
            message = await self._due_message(task, now)
            if message is None:
                continue
            fired.append(message)
            await self._send(message)
            if task.get("daily_at"):
                await self._store.update(
                    task["id"], last_run_date=now.strftime("%Y-%m-%d")
                )
            else:
                await self._store.update(task["id"], done=True)
        return fired

    async def _due_message(self, task: dict[str, Any], now: datetime) -> str | None:
        kind = task.get("kind")
        if kind == "watch_process":
            pid = int(task.get("pid") or 0)
            if _pid_alive(pid):
                return None
            return task.get("message") or f"Proses {pid} sudah selesai."
        if kind == "watch_mission":
            thread_id = str(task.get("thread_id") or "")
            row = await self._read_row("ledger_threads", thread_id)
            if row is None:
                return f"Misi {thread_id} tidak ditemukan."
            if row.get("status") in DONE_STATUSES:
                return task.get("message") or (
                    f"Misi '{row.get('title')}' selesai ({row.get('status')})."
                )
            return None

        daily_at = task.get("daily_at")
        if daily_at:
            if now.strftime("%H:%M") < daily_at:
                return None
            if task.get("last_run_date") == now.strftime("%Y-%m-%d"):
                return None
            return await self._perform(task)
        run_at = task.get("run_at")
        if not run_at:
            return None
        try:
            due = datetime.fromisoformat(run_at)
        except ValueError:
            return None
        if now < _local(due):
            return None
        return await self._perform(task)

    async def _perform(self, task: dict[str, Any]) -> str:
        kind = task.get("kind")
        if kind == "remind":
            return str(task.get("message") or "Pengingat.")
        if kind == "kill_process":
            pid = int(task.get("pid") or 0)
            # Never let agent-authored data kill the runtime itself.
            if pid <= 1 or pid == os.getpid():
                return f"Ditolak: PID {pid} bukan proses yang boleh dimatikan."
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                return f"Proses {pid} sudah tidak berjalan."
            except OSError as exc:
                return f"Gagal menghentikan proses {pid}: {exc}"
            return task.get("message") or f"Proses {pid} dihentikan (SIGTERM)."
        if kind == "run_plugin":
            name = str(task.get("plugin") or "")
            payload = task.get("plugin_payload") or {}
            try:
                output = await self._run_plugin(name, payload)
            except Exception as exc:
                return f"Plugin '{name}' gagal dijalankan: {exc}"
            if getattr(output, "success", False):
                return task.get("message") or f"Plugin '{name}' selesai."
            error = getattr(output, "error_message", None) or "tanpa detail"
            return f"Plugin '{name}' gagal: {error}"
        return str(task.get("message") or "Tugas terjadwal selesai.")

    async def _send(self, message: str) -> None:
        try:
            await self._notify(message)
        except Exception:
            logger.exception("scheduled task notification failed")
