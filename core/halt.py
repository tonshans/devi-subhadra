"""Halt-file kill switch: an empty HALT or STOP file stops all agent work."""

from __future__ import annotations

from pathlib import Path

HALT_FILE_NAMES = ("HALT", "STOP")


def resolve_halt_dir(configured: str | Path, workspace_dir: str | Path) -> Path:
    """Prefer the configured mount; fall back to the project workspace path."""
    configured_path = Path(configured)
    if configured_path.is_dir():
        return configured_path
    return Path(workspace_dir)


def find_halt_file(configured: str | Path, workspace_dir: str | Path) -> str | None:
    """Case-sensitive file name, so a stray `stop` from another app is ignored."""
    directory = resolve_halt_dir(configured, workspace_dir)
    for name in HALT_FILE_NAMES:
        if (directory / name).is_file():
            return name
    return None


def halt_message(configured: str | Path, workspace_dir: str | Path) -> str | None:
    name = find_halt_file(configured, workspace_dir)
    if name is None:
        return None
    directory = resolve_halt_dir(configured, workspace_dir)
    return f"Agent halted: {name} file found in {directory}. Remove it to continue."
