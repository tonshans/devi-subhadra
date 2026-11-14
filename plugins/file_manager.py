"""Confined read/write/list operations inside workspace and atelier roots."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import Field

from core.base_plugin import BaseAgentPlugin
from core.schemas import PluginOutputSchema, StrictBaseModel


class FileAction(StrictBaseModel):
    action: Literal["read", "write", "list"]
    path: str = Field(..., min_length=1)
    root: Literal["workspace", "atelier"] = "workspace"
    content: Optional[str] = None


class FileManager(BaseAgentPlugin):
    plugin_name = "file_manager"
    description = "Read, write, and list files inside the mounted agent roots."
    input_schema = FileAction

    def initialize(self, config: dict) -> bool:
        paths = config.get("paths", config)
        self._roots = {
            "workspace": Path(paths["workspace_dir"]),
            "atelier": Path(paths["atelier_dir"]),
        }
        for root in self._roots.values():
            root.mkdir(parents=True, exist_ok=True)
        return True

    def _failure(self, message: str) -> PluginOutputSchema:
        return PluginOutputSchema(success=False, data={}, error_message=message)

    def _resolve(self, root: str, relative_path: str) -> Path | None:
        base = self._roots[root].resolve()
        candidate = (base / relative_path).resolve()
        if candidate != base and not candidate.is_relative_to(base):
            return None
        return candidate

    def execute(self, payload: dict) -> PluginOutputSchema:
        request = FileAction.model_validate(payload)
        if not hasattr(self, "_roots"):
            return self._failure("FileManager is not initialized.")

        target = self._resolve(request.root, request.path)
        if target is None:
            return self._failure(
                f"Path '{request.path}' resolves outside the {request.root} root."
            )

        try:
            if request.action == "write":
                if request.content is None:
                    return self._failure("write requires content.")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(request.content, encoding="utf-8")
                return PluginOutputSchema(
                    success=True,
                    data={"path": str(target), "bytes": len(request.content.encode())},
                )
            if request.action == "read":
                if not target.is_file():
                    return self._failure(f"File not found: {request.path}")
                return PluginOutputSchema(
                    success=True, data={"path": str(target), "content": target.read_text(encoding="utf-8")}
                )
            if not target.is_dir():
                return self._failure(f"Directory not found: {request.path}")
            return PluginOutputSchema(
                success=True,
                data={"path": str(target), "entries": sorted(p.name for p in target.iterdir())},
            )
        except OSError as exc:
            return self._failure(str(exc))

    def shutdown(self) -> None:
        return None