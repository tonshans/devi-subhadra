"""JSON-backed scheduled task list shared by the scheduler plugin and runtime tick."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

DEFAULT_TASKS_PATH = Path(__file__).resolve().parents[1] / "data" / "scheduled_tasks.json"

_LOCK = asyncio.Lock()
logger = logging.getLogger(__name__)


class ScheduledTaskStore:
    """Read-modify-write of one small JSON list; safe within a single process."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or DEFAULT_TASKS_PATH)

    def _load(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [task for task in raw if isinstance(task, dict)] if isinstance(raw, list) else []

    def _save(self, tasks: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(tasks, indent=2), encoding="utf-8")
        os.replace(temp, self.path)

    async def load(self) -> list[dict[str, Any]]:
        async with _LOCK:
            return self._load()

    async def add(self, task: dict[str, Any]) -> dict[str, Any]:
        task.setdefault("id", uuid.uuid4().hex)
        task.setdefault("done", False)
        async with _LOCK:
            tasks = self._load()
            tasks.append(task)
            self._save(tasks)
        return task

    async def remove(self, task_id: str) -> bool:
        async with _LOCK:
            tasks = self._load()
            remaining = [task for task in tasks if task.get("id") != task_id]
            if len(remaining) == len(tasks):
                return False
            self._save(remaining)
            return True

    async def update(self, task_id: str, **fields: Any) -> bool:
        """Patch one task in place so a concurrent add() is never overwritten."""
        async with _LOCK:
            tasks = self._load()
            for task in tasks:
                if task.get("id") == task_id:
                    task.update(fields)
                    self._save(tasks)
                    return True
            return False
