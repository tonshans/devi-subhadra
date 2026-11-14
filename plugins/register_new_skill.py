"""Meta-tool: generate a plugin in TDD or Fast-Track mode, then register it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable, Literal, Optional

from pydantic import Field

from core.base_plugin import BaseAgentPlugin
from core.plugin_loader import PluginLoader
from core.schemas import PluginOutputSchema, StrictBaseModel


class SkillInput(StrictBaseModel):
    skill_name: str = Field(..., pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(..., min_length=5)
    source: str = Field(..., min_length=1)
    test_source: Optional[str] = None
    mode: Literal["TDD", "FAST_TRACK"] = "TDD"


class RegisterNewSkill(BaseAgentPlugin):
    plugin_name = "register_new_skill"
    description = "Create a new plugin; TDD mode blocks registration when tests fail."
    input_schema = SkillInput

    def __init__(
        self,
        plugins_dir: str | Path | None = None,
        test_runner: Optional[Callable[[Path], bool]] = None,
        approver: Optional[Callable[[str, str], bool]] = None,
    ) -> None:
        self._plugins_dir = Path(plugins_dir or "plugins")
        self._test_runner = test_runner
        # shortcut: no approver means no human gate; wire one before production.
        self._approver = approver

    def initialize(self, config: dict) -> bool:
        if config.get("plugins_dir"):
            self._plugins_dir = Path(config["plugins_dir"])
        return True

    def _default_runner(self, test_path: Path) -> bool:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(test_path)],
            capture_output=True,
            text=True,
            cwd=Path.cwd(),
        )
        return completed.returncode == 0

    @staticmethod
    def _cleanup(*paths: Path) -> None:
        for path in paths:
            path.unlink(missing_ok=True)

    def execute(self, payload: dict) -> PluginOutputSchema:
        request = SkillInput.model_validate(payload)
        if request.mode == "TDD" and not request.test_source:
            return PluginOutputSchema(
                success=False,
                data={},
                error_message="TDD mode requires test_source.",
            )

        self._plugins_dir.mkdir(parents=True, exist_ok=True)
        plugin_path = self._plugins_dir / f"{request.skill_name}.py"
        test_path = self._plugins_dir / f"{request.skill_name}.test.py"

        plugin_path.write_text(request.source, encoding="utf-8")
        if request.mode == "TDD":
            test_path.write_text(request.test_source, encoding="utf-8")
            try:
                passed = bool((self._test_runner or self._default_runner)(test_path))
            except Exception as exc:
                self._cleanup(plugin_path, test_path)
                return PluginOutputSchema(
                    success=False,
                    data={},
                    error_message=f"Test runner failed: {exc}",
                )
            if not passed:
                self._cleanup(plugin_path, test_path)
                return PluginOutputSchema(
                    success=False,
                    data={},
                    error_message="Unit tests failed; plugin not registered.",
                )

        if self._approver is not None and not self._approver(
            request.skill_name, request.source
        ):
            self._cleanup(plugin_path, test_path)
            return PluginOutputSchema(
                success=False,
                data={},
                error_message="Human approval was not granted; plugin not registered.",
            )

        loader = PluginLoader(self._plugins_dir)
        loaded = loader.load_all()
        if request.skill_name not in loaded:
            self._cleanup(plugin_path, test_path)
            details = "; ".join(loader.errors.values()) or "not a valid BaseAgentPlugin"
            return PluginOutputSchema(
                success=False,
                data={},
                error_message=f"Plugin rejected: {details}",
            )

        return PluginOutputSchema(
            success=True,
            data={
                "plugin_name": request.skill_name,
                "path": str(plugin_path),
                "tests_passed": request.mode == "TDD",
            },
        )

    def shutdown(self) -> None:
        return None