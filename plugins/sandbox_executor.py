"""Sandboxed code execution with mandatory assertions and command blocking."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tempfile
from typing import Literal

from pydantic import Field

from core.base_plugin import BaseAgentPlugin
from core.schemas import PluginOutputSchema, StrictBaseModel

_FORBIDDEN = (
    "rm -rf /",
    "mkfs",
    "shutdown",
    "reboot",
    "sudo ",
    "dd if=",
    ":(){",
    "chmod 777 /",
    "> /dev/sd",
)

# shortcut: AST allowlist replaces real isolation; move to LXC before untrusted use.
_ALLOWED_IMPORTS = {
    "collections",
    "datetime",
    "decimal",
    "fractions",
    "functools",
    "itertools",
    "json",
    "math",
    "random",
    "re",
    "statistics",
    "string",
    "textwrap",
    "time",
    "uuid",
}
_FORBIDDEN_NAMES = {
    "__import__",
    "breakpoint",
    "compile",
    "eval",
    "exec",
    "exit",
    "globals",
    "input",
    "locals",
    "open",
    "quit",
    "vars",
}
_FORBIDDEN_ATTRS = {
    "__builtins__",
    "__class__",
    "__globals__",
    "__import__",
    "__subclasses__",
    "fork",
    "popen",
    "spawn",
    "system",
}


def _python_policy_error(code: str) -> str | None:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"Syntax error: {exc}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in _ALLOWED_IMPORTS:
                    return f"Forbidden import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level or root not in _ALLOWED_IMPORTS:
                return f"Forbidden import: {node.module or '.'}"
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            return f"Forbidden name: {node.id}"
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_ATTRS:
            return f"Forbidden attribute: {node.attr}"
    return None


class SandboxInput(StrictBaseModel):
    code: str = Field(..., min_length=1)
    language: Literal["python", "bash"] = "python"
    timeout_seconds: int = Field(default=10, ge=1, le=30)


class SandboxExecutor(BaseAgentPlugin):
    plugin_name = "sandbox_executor"
    description = "Run assertion-driven scripts in an isolated subprocess."
    input_schema = SandboxInput

    def initialize(self, config: dict) -> bool:
        self.execution_mode = config.get("execution_mode", "local")
        return True

    def execute(self, payload: dict) -> PluginOutputSchema:
        request = SandboxInput.model_validate(payload)

        if not re.search(r"\bassert\b", request.code):
            return PluginOutputSchema(
                success=False,
                data={},
                assertions_passed=False,
                error_message=(
                    "Assertion-Driven Execution violation: code must contain an "
                    "'assert' statement."
                ),
            )

        lowered = request.code.lower()
        if any(pattern in lowered for pattern in _FORBIDDEN):
            return PluginOutputSchema(
                success=False,
                data={},
                assertions_passed=False,
                error_message="Forbidden command detected; execution blocked.",
            )

        if request.language == "python":
            policy_error = _python_policy_error(request.code)
            if policy_error is not None:
                return PluginOutputSchema(
                    success=False,
                    data={},
                    assertions_passed=False,
                    error_message=f"Forbidden code: {policy_error}",
                )

        if request.language == "python":
            command = [sys.executable, "-I", "-c", request.code]
        else:
            command = ["bash", "-c", request.code]

        try:
            with tempfile.TemporaryDirectory() as working_dir:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=request.timeout_seconds,
                    cwd=working_dir,
                )
        except subprocess.TimeoutExpired:
            return PluginOutputSchema(
                success=False,
                data={},
                assertions_passed=False,
                error_message=f"Script timed out after {request.timeout_seconds}s.",
            )

        passed = completed.returncode == 0
        return PluginOutputSchema(
            success=passed,
            data={
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "returncode": completed.returncode,
            },
            assertions_passed=passed,
            error_message=None if passed else f"Script exited with code {completed.returncode}.",
        )

    def shutdown(self) -> None:
        return None