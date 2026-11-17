"""Unit tests for the Tier 2 cheat-sheet plugin."""

import pytest
from pydantic import ValidationError

from plugins.equip_tool import EquipTool

PLUGIN_SOURCE = """
from typing import Any, Dict

from core.base_plugin import BaseAgentPlugin
from core.schemas import PluginOutputSchema, StrictBaseModel


class Input(StrictBaseModel):
    text: str


class SampleTool(BaseAgentPlugin):
    plugin_name = "sample_tool"
    description = "Describe the sample."
    input_schema = Input

    def initialize(self, config: Dict[str, Any]) -> bool:
        return True

    def execute(self, payload: Dict[str, Any]) -> PluginOutputSchema:
        return PluginOutputSchema(success=True, data=payload)

    def shutdown(self) -> None:
        return None
"""


def test_returns_description_and_input_schema(tmp_path):
    (tmp_path / "sample_tool.py").write_text(PLUGIN_SOURCE)
    plugin = EquipTool()
    plugin.initialize({"plugins_dir": str(tmp_path)})

    result = plugin.execute({"target_plugin_name": "sample_tool"})

    assert result.success is True
    assert result.data["description"] == "Describe the sample."
    assert result.data["input_schema"]["properties"]["text"]["type"] == "string"
    assert "[sample_tool] Describe the sample." in result.data["cheat_sheet"]
    assert "INPUT SCHEMA (JSON):" in result.data["cheat_sheet"]


def test_missing_plugin_suggests_restore_or_create(tmp_path):
    plugin = EquipTool()
    plugin.initialize({"plugins_dir": str(tmp_path)})

    result = plugin.execute({"target_plugin_name": "gone_tool"})

    assert result.success is False
    assert "restore" in result.error_message
    assert "create" in result.error_message


def test_uninitialized_plugin_fails_cleanly():
    result = EquipTool().execute({"target_plugin_name": "sample_tool"})

    assert result.success is False
    assert "not initialized" in result.error_message


def test_rejects_path_traversal_in_plugin_name(tmp_path):
    plugin = EquipTool()
    plugin.initialize({"plugins_dir": str(tmp_path)})

    with pytest.raises(ValidationError):
        plugin.execute({"target_plugin_name": "../escape"})
