"""Unit tests for the full-cycle skill manager and TDD rollback."""

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

from core.cognitive_ledger import CognitiveLedger
from core.plugin_loader import PluginLoader
from plugins.register_new_skill import RegisterNewSkill

PLUGIN_TEMPLATE = """
from typing import Any, Dict

from core.base_plugin import BaseAgentPlugin
from core.schemas import PluginOutputSchema, StrictBaseModel


class Input(StrictBaseModel):
    text: str


class GeneratedPlugin(BaseAgentPlugin):
    plugin_name = "{name}"
    description = "{description}"
    input_schema = Input

    def initialize(self, config: Dict[str, Any]) -> bool:
        return True

    def execute(self, payload: Dict[str, Any]) -> PluginOutputSchema:
        return PluginOutputSchema(success=True, data=payload)

    def shutdown(self) -> None:
        return None
"""

TEST_SOURCE = "def test_generated():\n    assert True\n"


def _code(name: str = "generated_echo", description: str = "Echo skill.") -> str:
    return PLUGIN_TEMPLATE.format(name=name, description=description)


def _payload(
    action: str = "create",
    name: str = "generated_echo",
    code: str | None = None,
    test: str | None = TEST_SOURCE,
) -> dict[str, Any]:
    return {
        "action_type": action,
        "plugin_name": name,
        "new_code": _code(name) if code is None else code,
        "new_test_code": test,
    }


def _ledger(tmp_path) -> CognitiveLedger:
    ledger = CognitiveLedger(tmp_path / "ledger.db")
    asyncio.run(ledger.initialize())
    return ledger


def _archive(plugin: RegisterNewSkill) -> None:
    plugin._archived_dir.mkdir(parents=True, exist_ok=True)


def test_create_writes_files_registers_plugin_and_sql(tmp_path):
    ledger = _ledger(tmp_path)
    plugin = RegisterNewSkill(
        plugins_dir=tmp_path / "plugins",
        archived_dir=tmp_path / "archived",
        test_runner=lambda path: True,
        ledger=ledger,
    )

    result = plugin.execute(_payload())

    assert result.success is True
    assert result.data["action_type"] == "create"
    assert (tmp_path / "plugins" / "generated_echo.py").is_file()
    assert (tmp_path / "plugins" / "generated_echo.test.py").is_file()
    loader = PluginLoader(tmp_path / "plugins")
    loader.load_all()
    assert "generated_echo" in loader.plugins or "generated_echo" in loader.catalog
    rows = asyncio.run(ledger.list_plugins())
    assert [(row["plugin_name"], row["tier_type"]) for row in rows] == [
        ("generated_echo", "ATELIER")
    ]


def test_create_refuses_to_overwrite_an_active_tool(tmp_path):
    plugin = RegisterNewSkill(
        plugins_dir=tmp_path / "plugins",
        archived_dir=tmp_path / "archived",
        test_runner=lambda path: True,
    )
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "generated_echo.py").write_text(_code())

    result = plugin.execute(_payload())

    assert result.success is False
    assert "upgrade" in result.error_message


def test_create_requires_code_and_tests(tmp_path):
    plugin = RegisterNewSkill(
        plugins_dir=tmp_path / "plugins",
        test_runner=lambda path: True,
    )

    result = plugin.execute(_payload(code=None, test=None))

    assert result.success is False
    assert "new_code" in result.error_message
    assert not (tmp_path / "plugins" / "generated_echo.py").exists()


def test_failing_tests_leave_no_partial_files(tmp_path):
    plugin = RegisterNewSkill(
        plugins_dir=tmp_path / "plugins",
        archived_dir=tmp_path / "archived",
        test_runner=lambda path: False,
    )

    result = plugin.execute(_payload())

    assert result.success is False
    assert "tests failed" in result.error_message.lower()
    assert not (tmp_path / "plugins" / "generated_echo.py").exists()
    assert not (tmp_path / "plugins" / "generated_echo.test.py").exists()


def test_upgrade_rolls_back_to_original_on_syntax_error(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    original = _code(description="Original behaviour.")
    (plugins / "generated_echo.py").write_text(original)
    (plugins / "generated_echo.test.py").write_text(TEST_SOURCE)
    plugin = RegisterNewSkill(
        plugins_dir=plugins,
        archived_dir=tmp_path / "archived",
        test_runner=lambda path: True,
    )

    result = plugin.execute(
        _payload(action="upgrade", code="def broken(\n", test=TEST_SOURCE)
    )

    assert result.success is False
    assert "rolled back" in result.error_message
    assert (plugins / "generated_echo.py").read_text() == original


def test_upgrade_rolls_back_when_tests_fail(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    original = _code(description="Original behaviour.")
    (plugins / "generated_echo.py").write_text(original)
    (plugins / "generated_echo.test.py").write_text(TEST_SOURCE)
    plugin = RegisterNewSkill(
        plugins_dir=plugins,
        archived_dir=tmp_path / "archived",
        test_runner=lambda path: False,
    )

    result = plugin.execute(
        _payload(action="upgrade", code=_code(description="New behaviour."))
    )

    assert result.success is False
    assert (plugins / "generated_echo.py").read_text() == original


def test_approval_gate_rejects_and_rolls_back(tmp_path):
    plugin = RegisterNewSkill(
        plugins_dir=tmp_path / "plugins",
        archived_dir=tmp_path / "archived",
        test_runner=lambda path: True,
        approver=lambda name, source: False,
    )

    result = plugin.execute(_payload())

    assert result.success is False
    assert "approval" in result.error_message.lower()
    assert not (tmp_path / "plugins" / "generated_echo.py").exists()


def test_invalid_plugin_source_is_rolled_back(tmp_path):
    plugin = RegisterNewSkill(
        plugins_dir=tmp_path / "plugins",
        archived_dir=tmp_path / "archived",
        test_runner=lambda path: True,
    )

    result = plugin.execute(_payload(code="class NotAPlugin:\n    pass\n"))

    assert result.success is False
    assert not (tmp_path / "plugins" / "generated_echo.py").exists()


def test_restore_moves_archived_files_back_and_clears_sql_flag(tmp_path):
    ledger = _ledger(tmp_path)
    archive = tmp_path / "archived"
    archive.mkdir()
    (archive / "generated_echo.py").write_text(_code())
    (archive / "generated_echo.test.py").write_text(TEST_SOURCE)
    asyncio.run(ledger.register_plugin("generated_echo", "ATELIER", "Echo skill."))
    asyncio.run(ledger.set_plugin_archived("generated_echo", True))
    plugin = RegisterNewSkill(
        plugins_dir=tmp_path / "plugins",
        archived_dir=archive,
        ledger=ledger,
    )

    result = plugin.execute(
        {"action_type": "restore", "plugin_name": "generated_echo"}
    )

    assert result.success is True
    assert (tmp_path / "plugins" / "generated_echo.py").is_file()
    assert not (archive / "generated_echo.py").exists()
    row = asyncio.run(ledger.read("plugin_registry", "generated_echo"))
    assert row is not None
    assert row["is_archived"] == 0


def test_restore_refuses_when_tool_is_already_active(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "generated_echo.py").write_text(_code())
    plugin = RegisterNewSkill(
        plugins_dir=plugins,
        archived_dir=tmp_path / "archived",
    )

    result = plugin.execute(
        {"action_type": "restore", "plugin_name": "generated_echo"}
    )

    assert result.success is False
    assert "already active" in result.error_message


def test_restore_rejects_a_missing_archive_entry(tmp_path):
    plugin = RegisterNewSkill(
        plugins_dir=tmp_path / "plugins",
        archived_dir=tmp_path / "archived",
    )

    result = plugin.execute(
        {"action_type": "restore", "plugin_name": "generated_echo"}
    )

    assert result.success is False
    assert "not found" in result.error_message


def test_upgrade_from_archive_replaces_code_and_removes_archive(tmp_path):
    archive = tmp_path / "archived"
    archive.mkdir()
    (archive / "generated_echo.py").write_text(_code(description="Archived."))
    (archive / "generated_echo.test.py").write_text(TEST_SOURCE)
    plugin = RegisterNewSkill(
        plugins_dir=tmp_path / "plugins",
        archived_dir=archive,
        test_runner=lambda path: True,
    )

    result = plugin.execute(
        _payload(action="upgrade", code=_code(description="Upgraded."))
    )

    assert result.success is True
    active = (tmp_path / "plugins" / "generated_echo.py").read_text()
    assert 'description = "Upgraded."' in active
    assert not (archive / "generated_echo.py").exists()


def test_restore_rejects_path_traversal_in_plugin_name(tmp_path):
    plugin = RegisterNewSkill(
        plugins_dir=tmp_path / "plugins",
        archived_dir=tmp_path / "archived",
    )

    with pytest.raises(ValidationError):
        plugin.execute({"action_type": "restore", "plugin_name": "../escape"})
