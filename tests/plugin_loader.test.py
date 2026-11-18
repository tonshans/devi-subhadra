"""Unit tests for dynamic plugin discovery and illegal-plugin rejection."""

from pathlib import Path
from textwrap import dedent

from core.base_plugin import BaseAgentPlugin
from core.plugin_loader import PluginLoader, build_extra_tools_prompt
from core.schemas import StrictBaseModel

VALID_PLUGIN = dedent(
    """
    from typing import Any, Dict

    from core.base_plugin import BaseAgentPlugin
    from core.schemas import PluginOutputSchema, StrictBaseModel


    class EchoInput(StrictBaseModel):
        text: str


    class EchoPlugin(BaseAgentPlugin):
        plugin_name = "echo"
        description = "Echo the payload."

        input_schema = EchoInput

        def initialize(self, config: Dict[str, Any]) -> bool:
            return True

        def execute(self, payload: Dict[str, Any]) -> PluginOutputSchema:
            return PluginOutputSchema(success=True, data={"text": payload["text"]})

        def shutdown(self) -> None:
            return None
    """
)

MISSING_LIFECYCLE_PLUGIN = dedent(
    """
    from core.base_plugin import BaseAgentPlugin
    from core.schemas import StrictBaseModel


    class OnlySchema(StrictBaseModel):
        text: str


    class HalfPlugin(BaseAgentPlugin):
        plugin_name = "half"
        description = "Missing lifecycle methods."
        input_schema = OnlySchema
    """
)

BAD_SCHEMA_PLUGIN = dedent(
    """
    from typing import Any, Dict

    from core.base_plugin import BaseAgentPlugin
    from core.schemas import PluginOutputSchema


    class NoSchemaPlugin(BaseAgentPlugin):
        plugin_name = "no_schema"
        description = "input_schema is not Pydantic."
        input_schema = dict

        def initialize(self, config: Dict[str, Any]) -> bool:
            return True

        def execute(self, payload: Dict[str, Any]) -> PluginOutputSchema:
            return PluginOutputSchema(success=True, data={})

        def shutdown(self) -> None:
            return None
    """
)

DUPLICATE_PLUGIN = dedent(
    """
    from typing import Any, Dict

    from core.base_plugin import BaseAgentPlugin
    from core.schemas import PluginOutputSchema, StrictBaseModel


    class Input(StrictBaseModel):
        text: str


    class DuplicatePlugin(BaseAgentPlugin):
        plugin_name = "echo"
        description = "Second plugin with the same name."
        input_schema = Input

        def initialize(self, config: Dict[str, Any]) -> bool:
            return True

        def execute(self, payload: Dict[str, Any]) -> PluginOutputSchema:
            return PluginOutputSchema(success=True, data={})

        def shutdown(self) -> None:
            return None
    """
)


def _write(directory: Path, filename: str, source: str) -> Path:
    path = directory / filename
    path.write_text(source, encoding="utf-8")
    return path


def test_loads_valid_plugin_and_registers_input_schema(tmp_path):
    _write(tmp_path, "echo_plugin.py", VALID_PLUGIN)

    plugins = PluginLoader(tmp_path, core_plugins={"echo"}).load_all()

    assert set(plugins) == {"echo"}
    assert isinstance(plugins["echo"], BaseAgentPlugin)
    assert issubclass(plugins["echo"].input_schema, StrictBaseModel)
    assert plugins["echo"].input_schema.__name__ == "EchoInput"


def test_rejects_plugin_missing_required_lifecycle_methods(tmp_path):
    _write(tmp_path, "half_plugin.py", MISSING_LIFECYCLE_PLUGIN)
    loader = PluginLoader(tmp_path)

    plugins = loader.load_all()

    assert plugins == {}
    assert "half_plugin.py" in loader.errors
    assert "initialize" in loader.errors["half_plugin.py"]


def test_rejects_plugin_without_pydantic_input_schema(tmp_path):
    _write(tmp_path, "bad_schema.py", BAD_SCHEMA_PLUGIN)
    loader = PluginLoader(tmp_path)

    plugins = loader.load_all()

    assert plugins == {}
    assert "input_schema" in loader.errors["bad_schema.py"]


def test_rejects_duplicate_plugin_names(tmp_path):
    _write(tmp_path, "a_echo.py", VALID_PLUGIN)
    _write(tmp_path, "b_echo.py", DUPLICATE_PLUGIN)
    loader = PluginLoader(tmp_path, core_plugins={"echo"})

    plugins = loader.load_all()

    assert set(plugins) == {"echo"}
    assert "duplicate" in loader.errors["b_echo.py"]


def test_import_failure_is_reported_and_other_plugins_still_load(tmp_path):
    _write(tmp_path, "broken.py", "import definitely_missing_package_xyz\n")
    _write(tmp_path, "echo_plugin.py", VALID_PLUGIN)
    loader = PluginLoader(tmp_path, core_plugins={"echo"})

    plugins = loader.load_all()

    assert set(plugins) == {"echo"}
    assert "broken.py" in loader.errors
    assert "import failed" in loader.errors["broken.py"]


def test_ignores_underscore_files_and_plain_classes(tmp_path):
    _write(
        tmp_path,
        "_draft.py",
        dedent(
            """
            raise RuntimeError("underscore modules must never be imported")
            """
        ),
    )
    _write(
        tmp_path,
        "not_a_plugin.py",
        dedent(
            """
            class Helper:
                plugin_name = "helper"
            """
        ),
    )

    plugins = PluginLoader(tmp_path).load_all()

    assert plugins == {}


def test_real_plugins_directory_skips_template_blueprint():
    plugins_dir = Path(__file__).resolve().parent.parent / "plugins"

    plugins = PluginLoader(plugins_dir).load_all()

    assert "template_plugin" not in plugins
    assert {
        "sandbox_executor",
        "file_manager",
        "register_new_skill",
        "equip_tool",
        "scheduler",
    } <= set(plugins)


def test_two_tier_split_keeps_atelier_out_of_plugins(tmp_path):
    _write(tmp_path, "echo_plugin.py", VALID_PLUGIN)
    loader = PluginLoader(tmp_path, core_plugins={"file_manager"})

    plugins = loader.load_all()

    assert plugins == {}
    assert loader.catalog == {"echo": "Echo the payload."}


def test_load_plugin_imports_one_active_atelier_plugin(tmp_path):
    _write(tmp_path, "echo.py", VALID_PLUGIN)

    plugin = PluginLoader(tmp_path).load_plugin("echo")

    assert plugin is not None
    assert plugin.plugin_name == "echo"
    assert PluginLoader(tmp_path).load_plugin("../escape") is None
    assert PluginLoader(tmp_path).load_plugin("missing") is None


def test_extra_tools_prompt_combines_catalog_and_anti_amnesia_sop():
    prompt = build_extra_tools_prompt(
        [{"plugin_name": "echo", "description_summary": "Echo the payload."}],
        "/mnt/agent_atelier/archived_plugins",
    )

    assert "[KATALOG ALAT EKSTRA]" in prompt
    assert "- echo: Echo the payload." in prompt
    assert "equip_tool" in prompt
    assert "[SOP ANTI-AMNESIA]" in prompt
    assert "/mnt/agent_atelier/archived_plugins/" in prompt
    assert "restore" in prompt and "upgrade" in prompt


def test_extra_tools_prompt_tells_the_agent_to_package_repeated_multistep_work():
    prompt = build_extra_tools_prompt([], "/mnt/agent_atelier/archived_plugins")

    assert "[SOP PAKETISASI]" in prompt
    assert "LEBIH DARI 3 langkah" in prompt
    assert "plugins/_template_plugin.py" in prompt
    assert "/approve" in prompt
