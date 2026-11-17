"""Unit tests for the BaseAgentPlugin contract and static self-awareness help."""

from typing import Any, Dict

import pytest
from pydantic import Field, ValidationError

from core.base_plugin import BaseAgentPlugin, agent_help_dev
from core.schemas import PluginOutputSchema, StrictBaseModel
from plugins._template_plugin import TemplateInput, TemplatePlugin


class DemoSchema(StrictBaseModel):
    task: str = Field(..., min_length=1)


class DemoPlugin(BaseAgentPlugin):
    plugin_name = "demo"
    description = "Demo plugin."

    @property
    def input_schema(self):
        return DemoSchema

    def initialize(self, config: Dict[str, Any]) -> bool:
        self.config = config
        return True

    def execute(self, payload: Dict[str, Any]) -> PluginOutputSchema:
        validated = self.input_schema.model_validate(payload)
        return PluginOutputSchema(success=True, data={"task": validated.task})

    def shutdown(self) -> None:
        self.stopped = True


def test_valid_plugin_implements_lifecycle_and_returns_schema():
    plugin = DemoPlugin()

    assert plugin.initialize({"mode": "test"}) is True
    assert issubclass(plugin.input_schema, StrictBaseModel)

    result = plugin.execute({"task": "write tests"})
    assert isinstance(result, PluginOutputSchema)
    assert result.success is True
    assert result.data == {"task": "write tests"}

    plugin.shutdown()
    assert plugin.stopped is True


def test_plugin_rejects_payload_outside_input_schema():
    plugin = DemoPlugin()

    with pytest.raises(ValidationError):
        plugin.execute({})


def test_cannot_instantiate_without_three_lifecycle_methods():
    class MissingLifecycle(BaseAgentPlugin):
        plugin_name = "missing"
        description = "Missing lifecycle."
        input_schema = DemoSchema

    with pytest.raises(TypeError) as excinfo:
        MissingLifecycle()

    message = str(excinfo.value)
    assert "initialize" in message
    assert "execute" in message
    assert "shutdown" in message


def test_cannot_instantiate_without_input_schema_property():
    class MissingSchema(BaseAgentPlugin):
        plugin_name = "no_schema"
        description = "No schema."

        def initialize(self, config):
            return True

        def execute(self, payload):
            return PluginOutputSchema(success=True, data={})

        def shutdown(self):
            return None

    with pytest.raises(TypeError):
        MissingSchema()


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        (
            "plugin",
            [
                "BaseAgentPlugin",
                "plugin_name",
                "description",
                "input_schema",
                "initialize",
                "execute",
                "shutdown",
                "_template_plugin.py",
                "Read-only blueprint for new plugins.",
            ],
        ),
        (
            "architecture",
            [
                "core/config_loader.py",
                "core/cognitive_ledger.py",
                "core/context_compiler.py",
                "core/budget_governor.py",
                "core/supervisor.py",
                "SQLite WAL",
                "FTS5",
            ],
        ),
        (
            "memory",
            [
                "core_directives",
                "semantic_memory",
                "ledger_threads",
                "ledger_entries",
                "atelier_discoveries",
                "30 days",
            ],
        ),
    ],
)
def test_agent_help_dev_returns_static_documentation(topic, expected):
    documentation = agent_help_dev(topic)

    assert isinstance(documentation, str)
    for fragment in expected:
        assert fragment in documentation


def test_agent_help_dev_rejects_unknown_topic():
    with pytest.raises(ValueError):
        agent_help_dev("database")


def test_template_plugin_is_a_usable_blueprint():
    plugin = TemplatePlugin()

    assert isinstance(plugin, BaseAgentPlugin)
    assert plugin.plugin_name == "template_plugin"
    assert issubclass(plugin.input_schema, StrictBaseModel)
    assert plugin.input_schema is TemplateInput
    assert plugin.initialize({"mode": "test"}) is True

    result = plugin.execute({"task": "summarize the ledger"})
    assert isinstance(result, PluginOutputSchema)
    assert result.success is True
    assert result.data == {"task": "summarize the ledger"}

    assert plugin.shutdown() is None