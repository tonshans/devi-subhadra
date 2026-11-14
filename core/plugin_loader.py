"""Dynamic discovery of plugins in a directory, with illegal plugins rejected."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import ModuleType

from core.base_plugin import BaseAgentPlugin
from core.schemas import StrictBaseModel


class PluginLoader:
    def __init__(self, plugins_dir: str | Path) -> None:
        self._plugins_dir = Path(plugins_dir)
        self.plugins: dict[str, BaseAgentPlugin] = {}
        self.errors: dict[str, str] = {}

    def load_all(self) -> dict[str, BaseAgentPlugin]:
        self.plugins = {}
        self.errors = {}
        if not self._plugins_dir.is_dir():
            return self.plugins

        for path in sorted(self._plugins_dir.glob("*.py")):
            if path.name.startswith("_") or path.name.endswith(".test.py"):
                continue
            module = self._import(path)
            if module is None:
                continue
            for candidate in vars(module).values():
                if (
                    isinstance(candidate, type)
                    and candidate is not BaseAgentPlugin
                    and issubclass(candidate, BaseAgentPlugin)
                    and candidate.__module__ == module.__name__
                ):
                    self._register(candidate, path.name)
        return self.plugins

    def _import(self, path: Path) -> ModuleType | None:
        module_name = f"devi_plugin_{uuid.uuid4().hex}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("no import spec")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except Exception as exc:
            self.errors[path.name] = f"import failed: {exc}"
            return None

    def _register(self, plugin_class: type[BaseAgentPlugin], source: str) -> None:
        try:
            plugin = plugin_class()
        except Exception as exc:
            self.errors[source] = f"{plugin_class.__name__} rejected: {exc}"
            return

        name = getattr(plugin, "plugin_name", None)
        description = getattr(plugin, "description", None)
        schema = getattr(plugin, "input_schema", None)
        methods = [
            getattr(plugin, method, None)
            for method in ("initialize", "execute", "shutdown")
        ]

        if not isinstance(name, str) or not name.strip():
            self.errors[source] = f"{plugin_class.__name__} rejected: invalid plugin_name"
            return
        if not isinstance(description, str) or not description.strip():
            self.errors[source] = f"{plugin_class.__name__} rejected: invalid description"
            return
        if not (isinstance(schema, type) and issubclass(schema, StrictBaseModel)):
            self.errors[source] = (
                f"{plugin_class.__name__} rejected: input_schema must be a "
                "StrictBaseModel subclass"
            )
            return
        if not all(callable(method) for method in methods):
            self.errors[source] = (
                f"{plugin_class.__name__} rejected: initialize/execute/shutdown "
                "must be callable"
            )
            return
        if name in self.plugins:
            self.errors[source] = f"{plugin_class.__name__} rejected: duplicate plugin_name '{name}'"
            return

        self.plugins[name] = plugin