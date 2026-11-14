"""Unit tests for Assertion-Driven sandbox execution and command blocking."""

from plugins.sandbox_executor import SandboxExecutor


def _executor() -> SandboxExecutor:
    plugin = SandboxExecutor()
    assert plugin.initialize({"execution_mode": "local"}) is True
    return plugin


def test_rejects_code_without_assert_statement():
    result = _executor().execute({"code": "print('hello')"})

    assert result.success is False
    assert result.assertions_passed is False
    assert "assert" in result.error_message.lower()


def test_runs_assertion_driven_python_and_returns_stdout():
    result = _executor().execute(
        {"code": "assert 1 + 1 == 2\nprint('ok')", "timeout_seconds": 5}
    )

    assert result.success is True
    assert result.assertions_passed is True
    assert "ok" in result.data["stdout"]


def test_failing_assertion_is_reported_as_failure():
    result = _executor().execute({"code": "assert 1 == 2", "timeout_seconds": 5})

    assert result.success is False
    assert result.assertions_passed is False
    assert result.data["returncode"] != 0


def test_blocks_dangerous_commands_before_execution():
    result = _executor().execute(
        {
            "code": "import os\nassert True\nos.system('rm -rf /')",
            "timeout_seconds": 5,
        }
    )

    assert result.success is False
    assert result.assertions_passed is False
    assert "forbidden" in result.error_message.lower()


def test_rejects_import_outside_the_allowlist():
    result = _executor().execute({"code": "import os\nassert True", "timeout_seconds": 5})

    assert result.success is False
    assert result.assertions_passed is False
    assert "forbidden" in result.error_message.lower()


def test_allows_safe_stdlib_import():
    result = _executor().execute(
        {"code": "import math\nassert math.sqrt(9) == 3", "timeout_seconds": 5}
    )

    assert result.success is True
    assert result.assertions_passed is True


def test_rejects_dangerous_builtins():
    result = _executor().execute(
        {"code": "assert open('/etc/passwd').read()", "timeout_seconds": 5}
    )

    assert result.success is False
    assert "forbidden" in result.error_message.lower()


def test_kills_script_that_exceeds_timeout():
    result = _executor().execute(
        {
            "code": "import time\ntime.sleep(2)\nassert True",
            "timeout_seconds": 1,
        }
    )

    assert result.success is False
    assert "timed out" in result.error_message.lower()