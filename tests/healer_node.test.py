"""Unit tests for bounded healing: max 3 attempts, assertion gate, escalation."""

import asyncio

import pytest

from core.healer_node import MAX_HEALER_RETRIES, HealResult, HealerNode
from core.schemas import HealerPatchProposal, PluginOutputSchema


def _proposal(attempt: int) -> HealerPatchProposal:
    return HealerPatchProposal(
        attempt_number=attempt,
        root_cause_analysis="Output was not validated before returning.",
        patched_code="assert payload['ok'] is True",
        embedded_assertions=["payload['ok'] is True"],
    )


def test_retry_limit_is_locked_to_three():
    assert MAX_HEALER_RETRIES == 3


@pytest.mark.parametrize("failures_before_success", [0, 1])
def test_recovers_on_first_or_second_attempt(failures_before_success):
    async def scenario():
        healer = HealerNode()
        proposed_attempts = []
        executions = []

        async def propose_patch(attempt, error):
            proposed_attempts.append(attempt)
            return _proposal(attempt)

        async def execute(patched_code):
            executions.append(patched_code)
            if len(executions) <= failures_before_success:
                raise RuntimeError("plugin crashed")
            return PluginOutputSchema(success=True, data={"ok": True})

        result = await healer.heal("plugin crashed", execute, propose_patch)

        assert result == HealResult(
            success=True,
            attempts=failures_before_success + 1,
            report=f"Recovered after {failures_before_success + 1} attempt(s)",
        )
        assert proposed_attempts == list(
            range(1, failures_before_success + 2)
        )
        assert len(executions) == failures_before_success + 1

    asyncio.run(scenario())


def test_rejects_patch_whose_assertions_did_not_pass():
    async def scenario():
        healer = HealerNode()
        outputs = [
            PluginOutputSchema(
                success=True, data={}, assertions_passed=False, error_message="assert failed"
            ),
            PluginOutputSchema(success=True, data={}, assertions_passed=True),
        ]
        executions = []

        async def propose_patch(attempt, error):
            return _proposal(attempt)

        async def execute(patched_code):
            executions.append(patched_code)
            return outputs[len(executions) - 1]

        result = await healer.heal("assert failed", execute, propose_patch)

        assert result.success is True
        assert result.attempts == 2
        assert len(executions) == 2

    asyncio.run(scenario())


def test_stops_after_third_failure_without_a_fourth_attempt():
    async def scenario():
        healer = HealerNode()
        proposed_attempts = []
        executions = []

        async def propose_patch(attempt, error):
            proposed_attempts.append(attempt)
            return _proposal(attempt)

        async def execute(patched_code):
            executions.append(patched_code)
            return PluginOutputSchema(
                success=True,
                data={},
                assertions_passed=False,
                error_message="assert payload['ok'] is True failed",
            )

        result = await healer.heal("assertion failed", execute, propose_patch)

        assert result.success is False
        assert result.attempts == 3
        assert proposed_attempts == [1, 2, 3]
        assert len(executions) == 3
        assert "ESCALATION" in result.report
        assert "3" in result.report
        assert "assert payload['ok'] is True failed" in result.report

    asyncio.run(scenario())


def test_treats_invalid_pydantic_patch_as_failed_attempt():
    async def scenario():
        healer = HealerNode()
        executions = []

        async def propose_patch(attempt, error):
            return {"attempt_number": attempt, "patched_code": "assert True"}

        async def execute(patched_code):
            executions.append(patched_code)
            return PluginOutputSchema(success=True, data={})

        result = await healer.heal("crash", execute, propose_patch)

        assert result.success is False
        assert result.attempts == 3
        assert executions == []
        assert "ESCALATION" in result.report

    asyncio.run(scenario())


def test_treats_execute_exception_on_every_attempt_as_terminal_failure():
    async def scenario():
        healer = HealerNode()
        executions = []

        async def propose_patch(attempt, error):
            return _proposal(attempt)

        async def execute(patched_code):
            executions.append(patched_code)
            raise RuntimeError("still broken")

        result = await healer.heal("broken", execute, propose_patch)

        assert result.success is False
        assert result.attempts == 3
        assert len(executions) == 3
        assert "still broken" in result.report

    asyncio.run(scenario())