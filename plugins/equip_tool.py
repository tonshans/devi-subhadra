"""Return a plugin cheat sheet: description plus input schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import Field

from core.base_plugin import BaseAgentPlugin
from core.plugin_loader import PluginLoader
from core.schemas import PluginOutputSchema, StrictBaseModel


class EquipToolInput(StrictBaseModel):
    target_plugin_name: str = Field(
        ...,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="Nama plugin yang ingin dipakai (Atelier maupun inti).",
    )


class EquipTool(BaseAgentPlugin):
    plugin_name = "equip_tool"
    description = (
        "Baca cheat sheet (deskripsi + skema input) plugin sebelum "
        "memanggilnya. Tanpa ini agent hanya tahu nama dan fungsi singkatnya."
    )
    input_schema = EquipToolInput

    def initialize(self, config: dict[str, Any]) -> bool:
        self._plugins_dir = Path(config.get("plugins_dir") or "plugins")
        return True

    def execute(self, payload: dict[str, Any]) -> PluginOutputSchema:
        request = self.input_schema.model_validate(payload)
        if not hasattr(self, "_plugins_dir"):
            return PluginOutputSchema(
                success=False,
                data={},
                error_message="EquipTool is not initialized.",
            )

        plugin = PluginLoader(self._plugins_dir).load_plugin(
            request.target_plugin_name
        )
        if plugin is None:
            return PluginOutputSchema(
                success=False,
                data={"plugin_name": request.target_plugin_name},
                error_message=(
                    f"Tool '{request.target_plugin_name}' tidak aktif. Cek folder "
                    "arsip via register_new_skill action_type='restore', atau "
                    "buat baru dengan action_type='create'."
                ),
            )

        schema = plugin.input_schema.model_json_schema()
        cheat_sheet = (
            f"[{plugin.plugin_name}] {plugin.description}\n"
            "INPUT SCHEMA (JSON):\n"
            f"{json.dumps(schema, indent=2)}"
        )
        return PluginOutputSchema(
            success=True,
            data={
                "plugin_name": plugin.plugin_name,
                "description": plugin.description,
                "input_schema": schema,
                "cheat_sheet": cheat_sheet,
            },
        )

    def shutdown(self) -> None:
        return None
