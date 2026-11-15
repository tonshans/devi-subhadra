"""Tests for shared slash commands (Telegram + local REPL)."""

import asyncio
from pathlib import Path

from core.budget_governor import BudgetGovernor
from core.cognitive_ledger import KILL_SWITCH_KEY, CognitiveLedger
from core.commands import (
    CommandContext,
    dispatch,
    load_governor_overrides,
)
from core.config_loader import load_config

_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


async def _setup(tmp_path):
    ledger = CognitiveLedger(tmp_path / "ledger.db")
    await ledger.initialize()
    governor = BudgetGovernor(2.50)
    ctx = CommandContext(ledger, governor, load_config(_CONFIG))
    return ledger, governor, ctx


def test_status_shows_provider_key_lines(tmp_path):
    class FakeProvider:
        def status_lines(self):
            return ["provider: gemini (1 keys)", "  primary: ready | rpm 0/15"]

    async def scenario():
        ledger, _, ctx = await _setup(tmp_path)
        ctx.provider = FakeProvider()

        reply = await dispatch("/status", ctx)

        assert "provider: gemini (1 keys)" in reply
        assert "  primary: ready | rpm 0/15" in reply

    asyncio.run(scenario())


def test_status_reads_state_without_llm(tmp_path):
    async def scenario():
        ledger, _, ctx = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        await ledger.create(
            "activity_logs",
            thread_id=thread_id,
            source="AGENT",
            initiator="Telegram",
            reasoning="Mission reply",
            budget_bucket="HUMAN_EXEMPT",
            tokens_used=123,
        )

        reply = await dispatch("/status", ctx)

        assert "agent: devi-subhadra" in reply
        assert "state: running" in reply
        assert "db: sqlite" in reply
        assert "model: gemini-2.5-flash" in reply
        assert "context: 123/30000 tokens" in reply
        assert "core $1.7500 left of $1.75" in reply
        assert "rpm 0/60" in reply
        assert "tpm 0/120000" in reply
        assert "rpd 0/unlimited" in reply
        assert "tpd 0/unlimited" in reply
        assert "agent share:" in reply
        assert "budget ratio: human 0% (reserved minimum) | agent 100%" in reply
        assert "agent freetime: off" in reply
        assert "threads: 1 active" in reply
        assert "last activity: " in reply

    asyncio.run(scenario())


def test_pause_and_resume_control_kill_switch_and_threads(tmp_path):
    async def scenario():
        ledger, _, ctx = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")

        paused = await dispatch("/pause-agent", ctx)
        assert "paused" in paused.lower()
        assert await ledger.get_system_state(KILL_SWITCH_KEY) == "1"
        assert (await ledger.read("ledger_threads", thread_id))["status"] == "Paused"
        assert "state: paused" in await dispatch("/status", ctx)

        resumed = await dispatch("/resume-agent", ctx)
        assert "resumed" in resumed.lower()
        assert await ledger.get_system_state(KILL_SWITCH_KEY) == "0"
        assert (await ledger.read("ledger_threads", thread_id))["status"] == "Active"

    asyncio.run(scenario())


def test_dispatch_ignores_plain_text_and_lists_known_commands(tmp_path):
    async def scenario():
        _, _, ctx = await _setup(tmp_path)

        assert await dispatch("halo agen", ctx) is None
        assert await dispatch("/status@devi_bot", ctx) is not None

        unknown = await dispatch("/nope", ctx)
        assert "Unknown command: /nope" in unknown
        for name in (
            "/status",
            "/budget-ratio",
            "/agent-freetime",
            "/pause-agent",
            "/resume-agent",
        ):
            assert name in unknown

    asyncio.run(scenario())


def test_budget_ratio_command_shows_and_updates_the_agent_share(tmp_path):
    async def scenario():
        ledger, governor, ctx = await _setup(tmp_path)
        governor.set_budget_ratio_agent(0.40)

        shown = await dispatch("/budget-ratio", ctx)
        assert "human 60% (reserved minimum) | agent 40%" in shown
        assert "agent share:" in shown

        updated = await dispatch("/budget-ratio 60 40", ctx)
        assert "agent 40%" in updated
        assert governor.budget_ratio_agent == 0.40
        assert await ledger.get_system_state("budget_ratio_agent") == "0.4"
        assert "agent 40%" in await dispatch("/status", ctx)

        assert "Usage" in await dispatch("/budget-ratio 60", ctx)
        assert "Usage" in await dispatch("/budget-ratio a b", ctx)
        assert "sum to 100" in await dispatch("/budget-ratio 60 30", ctx)
        assert governor.budget_ratio_agent == 0.40

    asyncio.run(scenario())


def test_agent_freetime_command_toggles_and_persists(tmp_path):
    async def scenario():
        ledger, governor, ctx = await _setup(tmp_path)
        governor.set_budget_ratio_agent(0.40)

        assert "agent freetime: off" in await dispatch("/agent-freetime status", ctx)

        on = await dispatch("/agent-freetime on", ctx)
        assert "on" in on
        assert governor.agent_freetime is True
        assert await ledger.get_system_state("agent_freetime") == "1"
        assert governor.agent_cap("rpm") is None
        assert "agent freetime: on" in await dispatch("/status", ctx)

        await dispatch("/agent-freetime off", ctx)
        assert governor.agent_freetime is False
        assert await ledger.get_system_state("agent_freetime") == "0"
        assert "Usage" in await dispatch("/agent-freetime maybe", ctx)

    asyncio.run(scenario())


def test_load_governor_overrides_restores_saved_settings(tmp_path):
    async def scenario():
        ledger, governor, _ = await _setup(tmp_path)
        await ledger.set_system_state("budget_ratio_agent", "0.25")
        await ledger.set_system_state("agent_freetime", "1")

        await load_governor_overrides(ledger, governor)

        assert governor.budget_ratio_agent == 0.25
        assert governor.agent_freetime is True

    asyncio.run(scenario())
