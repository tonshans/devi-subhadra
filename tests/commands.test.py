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
    settings = load_config(_CONFIG)
    governor = BudgetGovernor(
        settings.budget.max_initiative_budget_usd,
        settings.budget.core_task_ratio,
        settings.budget.atelier_ratio,
        settings.rate_limits.rpm_limit,
        settings.rate_limits.tpm_limit,
        settings.rate_limits.throttle_threshold_ratio,
        rpd_limit=settings.rate_limits.rpd_limit,
        tpd_limit=settings.rate_limits.tpd_limit,
        budget_ratio_agent=settings.rate_limits.budget_ratio_agent,
        agent_freetime=settings.rate_limits.agent_freetime,
    )
    ctx = CommandContext(ledger, governor, settings)
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
        assert (
            f"model: {ctx.settings.models.tier_2_heavy} "
            f"(light: {ctx.settings.models.tier_1_light})"
        ) in reply
        assert "context: 123/30000 tokens" in reply
        assert "core $1.2500 left of $1.25" in reply
        assert "atelier $1.2500 left of $1.25" in reply
        assert "rpm 0/14" in reply
        assert "tpm 0/250000" in reply
        assert "rpd 0/500" in reply
        assert "tpd 0/2000000" in reply
        assert "agent share:" in reply
        assert "budget ratio: human 60% (reserved minimum) | agent 40%" in reply
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


def test_trace_command_prints_the_audit_trail(tmp_path):
    async def scenario():
        ledger, _, ctx = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        await ledger.create(
            "audit_logs",
            trace_id="trace-1",
            step=1,
            thread_id=thread_id,
            trigger="TELEGRAM",
            origin="HUMAN",
            action="LLM_REPLY",
            cot="user asked a question",
            response="jawaban",
            outcome="SUCCESS",
            result_table="ledger_entries",
            result_id="entry-1",
        )

        text = await dispatch(f"/trace {thread_id}", ctx)

        assert "audit trail:" in text
        assert "step 1 TELEGRAM/LLM_REPLY SUCCESS" in text
        assert "why: user asked a question" in text
        assert "where: ledger_entries/entry-1" in text
        assert "no audit rows" in await dispatch("/trace nope", ctx)
        assert "usage: /trace" in await dispatch("/trace", ctx)

    asyncio.run(scenario())


def test_thread_command_lists_ids_and_filters_status(tmp_path):
    async def scenario():
        ledger, _, ctx = await _setup(tmp_path)
        active = await ledger.create_thread("Active mission", "HUMAN")
        completed = await ledger.create_thread(
            "Done mission", "HUMAN", status="Completed"
        )
        paused = await ledger.create_thread(
            "Paused mission", "AGENT", status="Paused"
        )

        text = await dispatch("/thread", ctx)

        assert "threads: 3 of 3 (status=all" in text
        for thread_id in (active, completed, paused):
            assert thread_id in text

        filtered = await dispatch("/thread active", ctx)
        assert active in filtered
        assert completed not in filtered
        assert paused not in filtered
        assert "status=Active" in filtered

        assert "usage: /thread" in await dispatch("/thread bogus", ctx)
        help_text = await dispatch("/thread help", ctx)
        assert "latest 20 threads" in help_text
        assert "Active | Paused | Completed | Archived" in help_text

    asyncio.run(scenario())


def test_thread_command_caps_output_at_latest_20(tmp_path):
    async def scenario():
        ledger, _, ctx = await _setup(tmp_path)
        ids = []
        for index in range(25):
            thread_id = await ledger.create_thread(f"Mission {index:02d}", "AGENT")
            await ledger.update(
                "ledger_threads",
                thread_id,
                created_at=f"2026-11-21 00:00:{index:02d}.000000",
            )
            ids.append(thread_id)

        text = await dispatch("/thread", ctx)

        assert "threads: 20 of 25 (status=all" in text
        assert "truncated: 5 older thread(s) hidden" in text
        assert ids[-1] in text
        assert ids[5] in text
        assert ids[4] not in text
        assert len(text.splitlines()) == 22

    asyncio.run(scenario())


def test_thread_command_flattens_multiline_titles(tmp_path):
    async def scenario():
        ledger, _, ctx = await _setup(tmp_path)
        await ledger.create_thread("Line one\nLine two", "HUMAN")

        text = await dispatch("/thread", ctx)

        assert "Line one Line two" in text

    asyncio.run(scenario())


def test_status_reports_halt_disk_and_temperature(tmp_path):
    async def scenario():
        _, _, ctx = await _setup(tmp_path)

        text = await dispatch("/status", ctx)

        assert "halt: none" in text
        assert "disk: " in text
        assert "cpu temp: " in text

    asyncio.run(scenario())


_STAGED_PLUGIN = '''
from typing import Any

from core.base_plugin import BaseAgentPlugin
from core.schemas import PluginOutputSchema, StrictBaseModel


class Input(StrictBaseModel):
    text: str


class WeeklyRecap(BaseAgentPlugin):
    plugin_name = "weekly_recap"
    description = "Weekly recap."
    input_schema = Input

    def initialize(self, config: dict[str, Any]) -> bool:
        return True

    def execute(self, payload: dict[str, Any]) -> PluginOutputSchema:
        return PluginOutputSchema(success=True, data=payload)

    def shutdown(self) -> None:
        return None
'''

_STAGED_TEST = "def test_ok():\n    assert True\n"


def _stage(tmp_path) -> Path:
    plugins = tmp_path / "plugins"
    pending = plugins / ".pending_skills"
    pending.mkdir(parents=True)
    (pending / "weekly_recap.py").write_text(_STAGED_PLUGIN)
    (pending / "weekly_recap.test.py").write_text(_STAGED_TEST)
    return plugins


def test_approve_activates_a_staged_skill_and_registers_it(tmp_path):
    async def scenario():
        ledger, _, ctx = await _setup(tmp_path)
        ctx.plugins_dir = _stage(tmp_path)

        reply = await dispatch("/approve weekly_recap", ctx)

        assert "is active" in reply
        assert (ctx.plugins_dir / "weekly_recap.py").is_file()
        assert not (ctx.plugins_dir / ".pending_skills" / "weekly_recap.py").exists()
        row = await ledger.read("plugin_registry", "weekly_recap")
        assert row is not None and row["tier_type"] == "ATELIER"

    asyncio.run(scenario())


def test_approve_lists_staged_skills_when_called_without_a_name(tmp_path):
    async def scenario():
        _, _, ctx = await _setup(tmp_path)
        ctx.plugins_dir = _stage(tmp_path)

        assert "weekly_recap" in await dispatch("/approve", ctx)

    asyncio.run(scenario())


def test_approve_reports_an_unknown_skill_without_creating_files(tmp_path):
    async def scenario():
        _, _, ctx = await _setup(tmp_path)
        ctx.plugins_dir = tmp_path / "plugins"

        reply = await dispatch("/approve ghost_skill", ctx)

        assert "no staged skill" in reply
        assert not (tmp_path / "plugins" / "ghost_skill.py").exists()

    asyncio.run(scenario())


def test_approve_rejects_path_traversal_and_keeps_staged_files(tmp_path):
    async def scenario():
        _, _, ctx = await _setup(tmp_path)
        ctx.plugins_dir = _stage(tmp_path)

        reply = await dispatch("/approve ../escape", ctx)

        assert "invalid skill name" in reply
        assert (ctx.plugins_dir / ".pending_skills" / "weekly_recap.py").is_file()

    asyncio.run(scenario())


def test_reject_discards_the_staged_skill(tmp_path):
    async def scenario():
        _, _, ctx = await _setup(tmp_path)
        ctx.plugins_dir = _stage(tmp_path)

        reply = await dispatch("/reject weekly_recap", ctx)

        assert "discarded" in reply
        assert not (ctx.plugins_dir / ".pending_skills" / "weekly_recap.py").exists()
        assert not (ctx.plugins_dir / "weekly_recap.py").exists()

    asyncio.run(scenario())
