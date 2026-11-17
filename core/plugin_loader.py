"""Two-tier plugin discovery, lazy loading, and the extra-tools prompt."""

from __future__ import annotations

import importlib.util
import re
import uuid
from pathlib import Path
from types import ModuleType

from core.base_plugin import BaseAgentPlugin
from core.schemas import StrictBaseModel

# Tier 1: schemas are loaded eagerly and the tools are always available.
CORE_PLUGIN_NAMES = frozenset(
    {
        "file_manager",
        "web_browser",
        "sandbox_executor",
        "antigravity_delegate",
        "register_new_skill",
        "mcp_connector",
        "equip_tool",
        "academic_search",
        "community_pulse",
        "scheduler",
    }
)

_PLUGIN_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def build_extra_tools_prompt(rows: list[dict], archived_dir: str | Path) -> str:
    """One prompt block: the ATELIER catalog plus the anti-amnesia SOP."""
    lines = ["[KATALOG ALAT EKSTRA] Kamu memiliki alat-alat tambahan ini:"]
    if rows:
        lines.extend(
            f"- {row['plugin_name']}: {row['description_summary']}" for row in rows
        )
    else:
        lines.append("- (belum ada alat tambahan yang aktif)")
    lines.append("Untuk menggunakannya, panggil tool 'equip_tool' terlebih dahulu.")
    lines.append("")
    lines.append(
        "[SOP ANTI-AMNESIA] Jika kamu butuh fungsi baru yang tidak ada di katalog "
        "ini, JANGAN LANGSUNG MEMBUAT DARI NOL. Cek folder "
        f"'{archived_dir}/'. Jika kamu pernah membuat alat serupa di masa lalu, "
        "gunakan tool 'register_new_skill' dengan mode 'restore' atau 'upgrade' "
        "untuk memodifikasi alat tersebut!"
    )
    lines.append(
        "[SOP PAKETISASI] Pekerjaan berulang yang butuh LEBIH DARI 3 langkah JANGAN "
        "disimpan sebagai script lepas. Bungkus jadi satu plugin lewat "
        "tool 'register_new_skill' (action_type='create') beserta test-nya, supaya "
        "lain kali pekerjaan yang sama cukup satu panggilan tool. Kerangka kode "
        "yang benar: plugins/_template_plugin.py. Plugin baru menunggu persetujuan "
        "manusia: minta pemilik menjalankan /approve <nama> atau /reject <nama>."
    )
    return "\n".join(lines)


class PluginLoader:
    def __init__(
        self,
        plugins_dir: str | Path,
        core_plugins: frozenset[str] | set[str] | None = None,
    ) -> None:
        self._plugins_dir = Path(plugins_dir)
        self._core_plugins = (
            CORE_PLUGIN_NAMES if core_plugins is None else frozenset(core_plugins)
        )
        self.plugins: dict[str, BaseAgentPlugin] = {}
        # Tier 2: name -> short description; schemas stay on disk until equipped.
        self.catalog: dict[str, str] = {}
        self.errors: dict[str, str] = {}

    def load_all(self) -> dict[str, BaseAgentPlugin]:
        self.plugins = {}
        self.catalog = {}
        self.errors = {}
        if not self._plugins_dir.is_dir():
            return self.plugins

        for path in sorted(self._plugins_dir.glob("*.py")):
            if path.name.startswith("_") or path.name.endswith(".test.py"):
                continue
            module = self._import(path)
            if module is None:
                continue
            for candidate in self._classes(module):
                self._register(candidate, path.name)
        return self.plugins

    def load_plugin(self, name: str) -> BaseAgentPlugin | None:
        """Import and instantiate one active plugin by name (Tier 2 lazy path)."""
        if not _PLUGIN_NAME.fullmatch(name):
            return None
        path = self._plugins_dir / f"{name}.py"
        if not path.is_file():
            return None
        module = self._import(path)
        if module is None:
            return None
        for candidate in self._classes(module):
            plugin = self._validate(candidate, path.name)
            if plugin is not None and plugin.plugin_name == name:
                return plugin
        return None

    @staticmethod
    def _classes(module: ModuleType):
        for candidate in vars(module).values():
            if (
                isinstance(candidate, type)
                and candidate is not BaseAgentPlugin
                and issubclass(candidate, BaseAgentPlugin)
                and candidate.__module__ == module.__name__
            ):
                yield candidate

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
        plugin = self._validate(plugin_class, source)
        if plugin is None:
            return

        name = plugin.plugin_name
        if name in self.plugins or name in self.catalog:
            self.errors[source] = (
                f"{plugin_class.__name__} rejected: duplicate plugin_name '{name}'"
            )
            return
        if name in self._core_plugins:
            self.plugins[name] = plugin
        else:
            self.catalog[name] = plugin.description

    def _validate(
        self, plugin_class: type[BaseAgentPlugin], source: str
    ) -> BaseAgentPlugin | None:
        try:
            plugin = plugin_class()
        except Exception as exc:
            self.errors[source] = f"{plugin_class.__name__} rejected: {exc}"
            return None

        name = getattr(plugin, "plugin_name", None)
        description = getattr(plugin, "description", None)
        schema = getattr(plugin, "input_schema", None)
        methods = [
            getattr(plugin, method, None)
            for method in ("initialize", "execute", "shutdown")
        ]

        if not isinstance(name, str) or not name.strip():
            self.errors[source] = f"{plugin_class.__name__} rejected: invalid plugin_name"
            return None
        if not isinstance(description, str) or not description.strip():
            self.errors[source] = f"{plugin_class.__name__} rejected: invalid description"
            return None
        if not (isinstance(schema, type) and issubclass(schema, StrictBaseModel)):
            self.errors[source] = (
                f"{plugin_class.__name__} rejected: input_schema must be a "
                "StrictBaseModel subclass"
            )
            return None
        if not all(callable(method) for method in methods):
            self.errors[source] = (
                f"{plugin_class.__name__} rejected: initialize/execute/shutdown "
                "must be callable"
            )
            return None
        return plugin
