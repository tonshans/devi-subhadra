"""Abstract plugin contract and static, LLM-free self-documentation."""

from __future__ import annotations

import ast
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Literal, Type

from core.schemas import PluginOutputSchema, StrictBaseModel

_TEMPLATE_FILE = Path(__file__).resolve().parent.parent / "plugins" / "_template_plugin.py"


class BaseAgentPlugin(ABC):
    """One-way contract every plugin must implement; core never depends on a plugin."""

    @property
    @abstractmethod
    def plugin_name(self) -> str:
        """Unique registry name."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Short human/agent-readable purpose."""

    @property
    @abstractmethod
    def input_schema(self) -> Type[StrictBaseModel]:
        """Strict Pydantic schema for the execute() payload."""

    @abstractmethod
    def initialize(self, config: Dict[str, Any]) -> bool:
        """Prepare resources; return True when ready."""

    @abstractmethod
    def execute(self, payload: Dict[str, Any]) -> PluginOutputSchema:
        """Run the plugin and return the locked output schema."""

    @abstractmethod
    def shutdown(self) -> None:
        """Release resources."""


_PLUGIN_HELP = """\
Plugin contract (core/base_plugin.py):
- Subclass BaseAgentPlugin and provide plugin_name: str, description: str,
  input_schema: Type[StrictBaseModel], initialize(config) -> bool,
  execute(payload) -> PluginOutputSchema, and shutdown() -> None.
- input_schema must be a StrictBaseModel subclass; invalid payloads raise ValidationError.
- Plugins may import only core/base_plugin.py, core/schemas.py, and
  core/config_loader.py. Never import core/cognitive_ledger.py directly.
- Put new plugins in plugins/<name>.py. PluginLoader ignores files starting with "_".
- Copy plugins/_template_plugin.py as the blueprint.
"""

_ARCHITECTURE_HELP = """\
Architecture map (see Architecture.md):
- core/config_loader.py: config.yaml validation + SecretsManager (no secrets in logs).
- core/cognitive_ledger.py: async SQL manager, 7 tables, SQLite WAL, FTS5 search.
- core/context_compiler.py: tiktoken pre-flight estimate + Dynamic Elastic Window.
- core/budget_governor.py: 70/30 budget gate and RPM/TPM throttling.
- core/supervisor.py: PydanticAI router, tiered routing, DLQ.
- core/healer_node.py: assertion/schema recovery, maximum 3 retries.
- core/plugin_loader.py: dynamic plugin discovery and schema registration.
- api/: Telegram gateway, webhooks, health endpoint.
- Flow: gateway -> supervisor -> budget_governor -> context_compiler -> LLM,
  then plugin_loader -> plugin, with healer_node on failures.
"""

_MEMORY_HELP = """\
Memory layers:
1. core_directives: immutable VISION, IMMUTABLE_PERSONA, SYSTEM_ANATOMY.
2. semantic_memory: evolving KOMUNIKASI, TEKNIS, PREFERENSI_USER heuristics with
   confidence_score 0-100; archived after 30 days unused and score below 20.
3. ledger_threads: mission state, origin HUMAN/AGENT, priority_score, endorsement.
4. ledger_entries: episodic FAKTA, TINDAKAN, PEMIKIRAN entries with FTS5 index.
Beyond the layers: atelier_discoveries stores self-directed browsing notes with
source_url and relevance_to_user. Sleep Mode deduplicates near-identical heuristics
and applies Semantic Decay over 30 days.
"""


def _template_docstring() -> str:
    tree = ast.parse(_TEMPLATE_FILE.read_text(encoding="utf-8"))
    return ast.get_docstring(tree) or ""


def agent_help_dev(topic: Literal["plugin", "architecture", "memory"]) -> str:
    """Return static technical docs without calling any LLM."""
    if topic == "plugin":
        return f"{_PLUGIN_HELP}\nTemplate blueprint:\n{_template_docstring()}"
    if topic == "architecture":
        return _ARCHITECTURE_HELP
    if topic == "memory":
        return _MEMORY_HELP
    raise ValueError(f"unknown help topic: {topic}")