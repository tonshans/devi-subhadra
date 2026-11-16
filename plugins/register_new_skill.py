"""Full-cycle skill manager: create, upgrade, restore, with TDD rollback."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from pydantic import Field

from core.base_plugin import BaseAgentPlugin
from core.plugin_loader import PluginLoader
from core.schemas import PluginOutputSchema, StrictBaseModel


class SkillInput(StrictBaseModel):
    action_type: Literal["create", "restore", "upgrade"] = Field(
        ..., description="create tool baru, upgrade yang ada, atau restore dari arsip."
    )
    plugin_name: str = Field(..., pattern=r"^[a-z][a-z0-9_]*$")
    new_code: Optional[str] = Field(
        default=None, description="Wajib untuk create/upgrade."
    )
    new_test_code: Optional[str] = Field(
        default=None, description="Wajib untuk create/upgrade."
    )


class RegisterNewSkill(BaseAgentPlugin):
    plugin_name = "register_new_skill"
    description = (
        "Skill manager: create/upgrade dengan TDD (rollback saat gagal) atau "
        "restore plugin Atelier dari folder arsip."
    )
    input_schema = SkillInput

    def __init__(
        self,
        plugins_dir: str | Path | None = None,
        test_runner: Optional[Callable[[Path], bool]] = None,
        approver: Optional[Callable[[str, str], bool]] = None,
        archived_dir: str | Path | None = None,
        ledger: Any | None = None,
    ) -> None:
        self._plugins_dir = Path(plugins_dir or "plugins")
        self._archived_dir = Path(archived_dir) if archived_dir else None
        self._test_runner = test_runner
        # shortcut: no approver means no human gate; wire one before production.
        self._approver = approver
        self._ledger = ledger

    def initialize(self, config: dict[str, Any]) -> bool:
        if config.get("plugins_dir"):
            self._plugins_dir = Path(config["plugins_dir"])
        paths = config.get("paths", config)
        archived = config.get("archived_plugins_dir")
        if archived:
            self._archived_dir = Path(archived)
        elif paths.get("atelier_dir"):
            self._archived_dir = Path(paths["atelier_dir"]) / "archived_plugins"
        if config.get("ledger") is not None:
            self._ledger = config["ledger"]
        return True

    def shutdown(self) -> None:
        return None

    @staticmethod
    def _failure(message: str) -> PluginOutputSchema:
        return PluginOutputSchema(success=False, data={}, error_message=message)

    def _plugin_path(self, name: str) -> Path:
        return self._plugins_dir / f"{name}.py"

    def _test_path(self, name: str) -> Path:
        return self._plugins_dir / f"{name}.test.py"

    def _archived_plugin(self, name: str) -> Path | None:
        return self._archived_dir / f"{name}.py" if self._archived_dir else None

    def _archived_test(self, name: str) -> Path | None:
        return self._archived_dir / f"{name}.test.py" if self._archived_dir else None

    def _default_runner(self, test_path: Path) -> bool:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(test_path)],
            capture_output=True,
            text=True,
            cwd=Path.cwd(),
        )
        return completed.returncode == 0

    @staticmethod
    def _restore_file(path: Path, content: str | None) -> None:
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def _rollback(
        self,
        plugin_path: Path,
        test_path: Path,
        plugin_source: str | None,
        test_source: str | None,
    ) -> None:
        self._restore_file(plugin_path, plugin_source)
        self._restore_file(test_path, test_source)

    def _register(self, plugin: BaseAgentPlugin) -> str | None:
        if self._ledger is None:
            return None
        try:
            asyncio.run(
                self._ledger.register_plugin(
                    plugin.plugin_name, "ATELIER", plugin.description
                )
            )
        except Exception as exc:
            return str(exc)
        return None

    def _discard_archive(self, name: str) -> None:
        for path in (self._archived_plugin(name), self._archived_test(name)):
            if path is not None and path.is_file():
                path.unlink()

    def execute(self, payload: dict[str, Any]) -> PluginOutputSchema:
        request = self.input_schema.model_validate(payload)
        if request.action_type == "restore":
            return self._restore(request)
        return self._create_or_upgrade(request)

    def _create_or_upgrade(self, request: SkillInput) -> PluginOutputSchema:
        if not request.new_code or not request.new_test_code:
            return self._failure(
                f"{request.action_type} requires new_code and new_test_code."
            )

        plugin_path = self._plugin_path(request.plugin_name)
        test_path = self._test_path(request.plugin_name)
        archived_plugin = self._archived_plugin(request.plugin_name)
        archived_test = self._archived_test(request.plugin_name)
        archived_exists = archived_plugin is not None and archived_plugin.is_file()

        if request.action_type == "create" and plugin_path.exists():
            return self._failure(
                f"{request.plugin_name} already exists; use action_type='upgrade'."
            )
        if request.action_type == "upgrade" and not plugin_path.exists() and not archived_exists:
            return self._failure(
                f"{request.plugin_name} is neither active nor archived; use 'create'."
            )

        if plugin_path.exists():
            plugin_source = plugin_path.read_text(encoding="utf-8")
        elif archived_exists:
            plugin_source = archived_plugin.read_text(encoding="utf-8")
        else:
            plugin_source = None
        if test_path.exists():
            test_source = test_path.read_text(encoding="utf-8")
        elif archived_test is not None and archived_test.is_file():
            test_source = archived_test.read_text(encoding="utf-8")
        else:
            test_source = None

        self._plugins_dir.mkdir(parents=True, exist_ok=True)
        plugin_path.write_text(request.new_code, encoding="utf-8")
        test_path.write_text(request.new_test_code, encoding="utf-8")

        try:
            passed = bool((self._test_runner or self._default_runner)(test_path))
        except Exception as exc:
            self._rollback(plugin_path, test_path, plugin_source, test_source)
            return self._failure(f"Test runner failed: {exc}")
        if not passed:
            self._rollback(plugin_path, test_path, plugin_source, test_source)
            return self._failure("Unit tests failed; changes rolled back.")

        loaded = PluginLoader(self._plugins_dir).load_plugin(request.plugin_name)
        if loaded is None:
            self._rollback(plugin_path, test_path, plugin_source, test_source)
            return self._failure("Plugin rejected after tests; changes rolled back.")

        if self._approver is not None and not self._approver(
            request.plugin_name, request.new_code
        ):
            self._rollback(plugin_path, test_path, plugin_source, test_source)
            return self._failure("Human approval was not granted; changes rolled back.")

        error = self._register(loaded)
        if error is not None:
            self._rollback(plugin_path, test_path, plugin_source, test_source)
            return self._failure(f"Registry update failed: {error}")

        self._discard_archive(request.plugin_name)
        return PluginOutputSchema(
            success=True,
            data={
                "action_type": request.action_type,
                "plugin_name": request.plugin_name,
                "path": str(plugin_path),
                "tests_passed": True,
            },
        )

    def _restore(self, request: SkillInput) -> PluginOutputSchema:
        plugin_path = self._plugin_path(request.plugin_name)
        test_path = self._test_path(request.plugin_name)
        archived_plugin = self._archived_plugin(request.plugin_name)
        archived_test = self._archived_test(request.plugin_name)

        if plugin_path.exists():
            return self._failure(
                f"{request.plugin_name} is already active; use action_type='upgrade'."
            )
        if archived_plugin is None or not archived_plugin.is_file():
            return self._failure(
                f"{request.plugin_name}.py not found in the archive."
            )

        self._plugins_dir.mkdir(parents=True, exist_ok=True)
        plugin_path.write_text(
            archived_plugin.read_text(encoding="utf-8"), encoding="utf-8"
        )
        if archived_test is not None and archived_test.is_file():
            test_path.write_text(
                archived_test.read_text(encoding="utf-8"), encoding="utf-8"
            )

        loaded = PluginLoader(self._plugins_dir).load_plugin(request.plugin_name)
        if loaded is None:
            plugin_path.unlink(missing_ok=True)
            test_path.unlink(missing_ok=True)
            return self._failure("Archived plugin is invalid; restore aborted.")

        error = self._register(loaded)
        if error is not None:
            plugin_path.unlink(missing_ok=True)
            test_path.unlink(missing_ok=True)
            return self._failure(f"Registry update failed: {error}")

        self._discard_archive(request.plugin_name)
        return PluginOutputSchema(
            success=True,
            data={
                "action_type": "restore",
                "plugin_name": request.plugin_name,
                "path": str(plugin_path),
                "tests_passed": False,
            },
        )
