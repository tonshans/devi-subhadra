"""Read-only blueprint for new plugins.

Copy this file (drop the leading underscore) and fill in the contract.
PluginLoader never imports underscore-prefixed files, so this template is inert.
"""

from typing import Any, Dict

from pydantic import Field

from core.base_plugin import BaseAgentPlugin
from core.schemas import PluginOutputSchema, StrictBaseModel


class TemplateInput(StrictBaseModel):
    task: str = Field(..., min_length=1)


class TemplatePlugin(BaseAgentPlugin):
    plugin_name = "template_plugin"
    description = "Template blueprint; copy and rename before registering."
    input_schema = TemplateInput

    def initialize(self, config: Dict[str, Any]) -> bool:
        return True

    def execute(self, payload: Dict[str, Any]) -> PluginOutputSchema:
        validated = self.input_schema.model_validate(payload)
        return PluginOutputSchema(success=True, data=validated.model_dump())

    def shutdown(self) -> None:
        return None