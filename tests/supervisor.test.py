"""Unit tests for tiered routing and DLQ timeout recovery."""

import asyncio

import pytest

from core.healer_node import HealerNode
from core.schemas import HealerPatchProposal, PluginOutputSchema
from core.supervisor import Supervisor


@pytest.mark.parametrize(
    ("priority_score", "is_human_endorsed", "expected_tier"),
    [
        (10, False, "TIER_1_LIGHT"),
        (20, False, "TIER_1_LIGHT"),
        (100, False, "TIER_2_HEAVY"),
        (150, False, "TIER_2_HEAVY"),
        (10, True, "TIER_2_HEAVY"),
    ],
)
def test_selects_model_tier_by_priority(priority_score, is_human_endorsed, expected_tier):
    supervisor = Supervisor(HealerNode())

    assert (
        supervisor.select_model_tier(priority_score, is_human_endorsed)
        == expected_tier
    )


def test_delegates_worker_without_healing_when_it_finishes():
    async def scenario():
        supervisor = Supervisor(HealerNode(), timeout_seconds=0.05)

        async def worker():
            return {"answer": 42}

        async def propose_patch(attempt, error):
            raise AssertionError("healer must not run")

        outcome = await supervisor.delegate_worker(worker, propose_patch)

        assert outcome.status == "completed"
        assert outcome.result == {"answer": 42}
        assert outcome.heal_result is None

    asyncio.run(scenario())


def test_dlq_worker_timeout_triggers_healer_node():
    async def scenario():
        supervisor = Supervisor(HealerNode(), timeout_seconds=0.01)
        worker_calls = []
        proposed_errors = []

        async def worker():
            worker_calls.append(True)
            if len(worker_calls) == 1:
                await asyncio.sleep(0.05)
            return PluginOutputSchema(success=True, data={"recovered": True})

        async def propose_patch(attempt, error):
            proposed_errors.append(error)
            return HealerPatchProposal(
                attempt_number=attempt,
                root_cause_analysis="Worker timed out on the first execution.",
                patched_code="assert payload['recovered'] is True",
                embedded_assertions=["payload['recovered'] is True"],
            )

        outcome = await supervisor.delegate_worker(worker, propose_patch)

        assert outcome.status == "dlq"
        assert outcome.result is None
        assert outcome.heal_result is not None
        assert outcome.heal_result.success is True
        assert outcome.heal_result.attempts == 1
        assert len(worker_calls) == 2
        assert "timeout" in proposed_errors[0].lower()

    asyncio.run(scenario())


def test_worker_exception_triggers_healer_node():
    async def scenario():
        supervisor = Supervisor(HealerNode(), timeout_seconds=0.05)
        calls = []
        proposed_errors = []

        async def worker():
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("upstream 500")
            return PluginOutputSchema(success=True, data={"recovered": True})

        async def propose_patch(attempt, error):
            proposed_errors.append(error)
            return HealerPatchProposal(
                attempt_number=attempt,
                root_cause_analysis="Upstream provider failed on first call.",
                patched_code="assert True",
                embedded_assertions=["True"],
            )

        outcome = await supervisor.delegate_worker(worker, propose_patch)

        assert outcome.status == "dlq"
        assert outcome.heal_result.success is True
        assert outcome.heal_result.attempts == 1
        assert len(calls) == 2
        assert "upstream 500" in proposed_errors[0]

    asyncio.run(scenario())


def test_dlq_reports_terminal_healer_failure():
    async def scenario():
        supervisor = Supervisor(HealerNode(), timeout_seconds=0.01)

        async def worker():
            await asyncio.sleep(0.05)

        async def propose_patch(attempt, error):
            return HealerPatchProposal(
                attempt_number=attempt,
                root_cause_analysis="Worker still times out after retry.",
                patched_code="assert True",
                embedded_assertions=["True"],
            )

        outcome = await supervisor.delegate_worker(worker, propose_patch)

        assert outcome.status == "dlq"
        assert outcome.heal_result.success is False
        assert outcome.heal_result.attempts == 3
        assert "ESCALATION" in outcome.heal_result.report

    asyncio.run(scenario())

def test_delegate_worker_uses_the_supplied_patch_executor():
    async def scenario():
        supervisor = Supervisor(HealerNode(), timeout_seconds=0.01)
        patched = []

        async def worker():
            await asyncio.sleep(0.05)

        async def execute_patch(code):
            patched.append(code)
            return PluginOutputSchema(success=True, data={})

        async def propose_patch(attempt, error):
            return HealerPatchProposal(
                attempt_number=attempt,
                root_cause_analysis="Apply the supplied patch instead of retrying.",
                patched_code="print('fixed')",
                embedded_assertions=["True"],
            )

        outcome = await supervisor.delegate_worker(
            worker, propose_patch, execute_patch=execute_patch
        )

        assert patched == ["print('fixed')"]
        assert outcome.heal_result.success is True

    asyncio.run(scenario())
