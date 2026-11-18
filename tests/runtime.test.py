"""Unit tests for the composition root: mission flow, plugins, and scheduling."""

import asyncio
import contextlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.budget_governor import BudgetGovernor
from core.cognitive_ledger import CognitiveLedger
from core.provider_pool import AllProvidersUnavailable
from core.runtime import MissionBlockedError, Runtime, load_plugins, scheduler_loop
from core.schemas import HealerPatchProposal, MissionReply, PluginOutputSchema


async def _setup(tmp_path):
    ledger = CognitiveLedger(tmp_path / "ledger.db")
    await ledger.initialize()
    return ledger, BudgetGovernor(1.00)


def test_mission_flows_through_context_budget_and_ledger(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        await ledger.create(
            "core_directives", directive_type="VISION", content="Be transparent."
        )
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        prompts = []

        async def worker(prompt):
            prompts.append(prompt)
            return "hasil akhir"

        runtime = Runtime(ledger, governor, worker=worker)

        reply = await runtime.handle_mission(thread_id, "kerjakan ini")

        assert reply == "hasil akhir"
        assert len(prompts) == 1
        assert "USER QUERY:\nkerjakan ini" in prompts[0]
        assert "Be transparent." in prompts[0]

        logs = await ledger.list_activity_logs()
        assert logs[0]["initiator"] == "Telegram"
        assert logs[0]["budget_bucket"] == "HUMAN_EXEMPT"
        assert logs[0]["tokens_used"] > 0

        entries = await ledger.list_ledger_entries(thread_id)
        assert entries[-1]["tag"] == "FAKTA"
        assert entries[-1]["content"] == "hasil akhir"

    asyncio.run(scenario())


def test_all_providers_down_notifies_once_and_replies(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        notices = []
        flags = [True, False]

        async def worker(prompt):
            raise AllProvidersUnavailable(
                "All Gemini API keys are unavailable.",
                should_notify=flags.pop(0),
            )

        async def notify(message):
            notices.append(message)

        runtime = Runtime(ledger, governor, worker=worker, notify=notify)

        first = await runtime.handle_mission(thread_id, "halo")
        second = await runtime.handle_mission(thread_id, "halo")

        assert first == "All Gemini API keys are unavailable."
        assert second == "All Gemini API keys are unavailable."
        assert notices == ["All Gemini API keys are unavailable."]
        entries = await ledger.list_ledger_entries(thread_id)
        assert entries[-1]["content"] == "All Gemini API keys are unavailable."
        assert entries[-1]["tag"] == "TINDAKAN"
        logs = await ledger.list_activity_logs()
        assert {log["reasoning"] for log in logs} == {"Mission failed via completed"}

    asyncio.run(scenario())


def test_all_providers_down_refunds_the_agent_reservation(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Initiative", "AGENT")

        async def worker(prompt):
            raise AllProvidersUnavailable("Semua key down.", should_notify=False)

        runtime = Runtime(ledger, governor, worker=worker, cost_usd=0.20)

        reply = await runtime.handle_mission(thread_id, "belajar")

        assert reply == "Semua key down."
        assert governor.remaining_usd("ATELIER_30") == pytest.approx(0.30)
        logs = await ledger.list_activity_logs()
        assert logs[0]["cost_usd"] == pytest.approx(0.0)

    asyncio.run(scenario())


def test_mission_delivers_files_from_structured_reply(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        delivered = []

        async def worker(prompt):
            return MissionReply(reply="laporan siap", files=["laporan.txt"])

        async def deliver_file(path):
            delivered.append(path)

        runtime = Runtime(
            ledger, governor, worker=worker, deliver_file=deliver_file
        )

        reply = await runtime.handle_mission(thread_id, "kirim laporan")

        assert reply == "laporan siap"
        assert delivered == ["laporan.txt"]
        entries = await ledger.list_ledger_entries(thread_id)
        assert entries[-1]["content"] == "laporan siap"

    asyncio.run(scenario())


def test_file_delivery_failure_is_reported_in_reply(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")

        async def worker(prompt):
            return MissionReply(reply="laporan siap", files=["hilang.txt"])

        async def deliver_file(path):
            raise ValueError("file not found")

        runtime = Runtime(
            ledger, governor, worker=worker, deliver_file=deliver_file
        )

        reply = await runtime.handle_mission(thread_id, "kirim laporan")

        assert reply.startswith("laporan siap")
        assert "File not sent (hilang.txt): file not found" in reply

    asyncio.run(scenario())


def test_structured_file_without_delivery_is_reported(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")

        async def worker(prompt):
            return MissionReply(reply="laporan siap", files=["laporan.txt"])

        runtime = Runtime(ledger, governor, worker=worker)

        reply = await runtime.handle_mission(thread_id, "kirim laporan")

        assert "file delivery is not configured" in reply

    asyncio.run(scenario())


def test_agent_mission_charges_atelier_budget(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Initiative", "AGENT")

        async def worker(prompt):
            return "ok"

        runtime = Runtime(ledger, governor, worker=worker, cost_usd=0.20)

        await runtime.handle_mission(thread_id, "belajar")

        assert governor.remaining_usd("ATELIER_30") == pytest.approx(0.10)
        logs = await ledger.list_activity_logs()
        assert logs[0]["budget_bucket"] == "ATELIER_30"
        assert logs[0]["cost_usd"] == pytest.approx(0.20)

    asyncio.run(scenario())


def test_agent_mission_settles_actual_token_cost(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Initiative", "AGENT")

        async def worker(prompt):
            return "ok"

        worker.last_usage = {"prompt_tokens": 100_000, "completion_tokens": 50_000}
        runtime = Runtime(
            ledger,
            governor,
            worker=worker,
            input_usd_per_million=1.0,
            output_usd_per_million=2.0,
        )

        await runtime.handle_mission(thread_id, "belajar")

        assert governor.remaining_usd("ATELIER_30") == pytest.approx(0.10)
        logs = await ledger.list_activity_logs()
        assert logs[0]["cost_usd"] == pytest.approx(0.20)
        assert worker.last_usage is None

    asyncio.run(scenario())


def test_mission_is_blocked_by_kill_switch(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        calls = []

        async def worker(prompt):
            calls.append(prompt)
            return "no"

        runtime = Runtime(ledger, governor, worker=worker)
        await ledger.set_system_state("kill_switch", "1")

        with pytest.raises(MissionBlockedError):
            await runtime.handle_mission(thread_id, "halo")

        assert calls == []

    asyncio.run(scenario())


def test_exhausted_budget_returns_message_without_calling_worker(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Initiative", "AGENT")
        calls = []

        async def worker(prompt):
            calls.append(prompt)
            return "no"

        runtime = Runtime(ledger, governor, worker=worker, cost_usd=99.0)

        reply = await runtime.handle_mission(thread_id, "belajar")

        assert "budget" in reply.lower()
        assert calls == []

    asyncio.run(scenario())


def test_worker_timeout_is_healed_and_reported(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        calls = []

        async def worker(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                await asyncio.sleep(0.05)
            return "pulih"

        async def propose_patch(attempt, error):
            from core.schemas import HealerPatchProposal

            return HealerPatchProposal(
                attempt_number=attempt,
                root_cause_analysis="Worker timed out on first execution.",
                patched_code="assert True",
                embedded_assertions=["True"],
            )

        runtime = Runtime(
            ledger,
            governor,
            worker=worker,
            propose_patch=propose_patch,
            supervisor_timeout_seconds=0.01,
        )

        reply = await runtime.handle_mission(thread_id, "halo")

        assert reply == "Recovered after 1 attempt(s)"
        assert len(calls) == 2
        # Both the failed attempt and the healed retry are provider requests.
        assert governor.used("rpm") == 2

    asyncio.run(scenario())


def test_worker_error_is_escalated_without_crashing(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        calls = []

        async def worker(prompt):
            calls.append(prompt)
            raise RuntimeError("provider down")

        runtime = Runtime(ledger, governor, worker=worker)

        reply = await runtime.handle_mission(thread_id, "halo")

        assert "ESCALATION" in reply
        assert len(calls) == 1
        logs = await ledger.list_activity_logs()
        assert logs[0]["reasoning"].endswith("dlq")

    asyncio.run(scenario())


def test_run_plugin_executes_registered_plugin(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        from core.schemas import PluginOutputSchema

        class Echo:
            def execute(self, payload):
                return PluginOutputSchema(success=True, data={"echo": payload["text"]})

        runtime = Runtime(ledger, governor, plugins={"echo": Echo()}, worker=None)

        output = await runtime.run_plugin("echo", {"text": "hi"})

        assert output.success is True
        assert output.data == {"echo": "hi"}

        missing = await runtime.run_plugin("nope", {})
        assert missing.success is False
        assert "nope" in missing.error_message

    asyncio.run(scenario())


def test_run_plugin_logs_usage_after_success(tmp_path, monkeypatch):
    async def scenario():
        ledger, governor = await _setup(tmp_path)

        class Echo:
            def execute(self, payload):
                return PluginOutputSchema(success=True, data={})

        calls = []

        async def fake_log(name):
            calls.append(name)
            return True

        monkeypatch.setattr(ledger, "log_plugin_usage", fake_log)
        runtime = Runtime(ledger, governor, plugins={"echo": Echo()}, worker=None)

        await runtime.run_plugin("echo", {})

        assert calls == ["echo"]

    asyncio.run(scenario())


def test_run_plugin_lazy_loads_an_atelier_plugin(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "lazy_echo.py").write_text(
            "from typing import Any, Dict\n"
            "from core.base_plugin import BaseAgentPlugin\n"
            "from core.schemas import PluginOutputSchema, StrictBaseModel\n"
            "class Input(StrictBaseModel):\n"
            "    text: str\n"
            "class LazyEcho(BaseAgentPlugin):\n"
            "    plugin_name = 'lazy_echo'\n"
            "    description = 'Echo lazily.'\n"
            "    input_schema = Input\n"
            "    def initialize(self, config): return True\n"
            "    def execute(self, payload):\n"
            "        return PluginOutputSchema(success=True, data={'text': payload['text']})\n"
            "    def shutdown(self): return None\n",
            encoding="utf-8",
        )
        runtime = Runtime(
            ledger,
            governor,
            plugins={},
            plugins_dir=plugin_dir,
            plugin_config={},
            worker=None,
        )

        output = await runtime.run_plugin("lazy_echo", {"text": "hi"})

        assert output.success is True
        assert output.data == {"text": "hi"}
        assert "lazy_echo" in runtime._plugins

    asyncio.run(scenario())


def test_sync_plugin_registry_splits_core_and_atelier(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)

        class CoreStub:
            description = "Always available."

        runtime = Runtime(
            ledger,
            governor,
            plugins={"core_tool": CoreStub()},
            plugin_catalog={"lazy_tool": "On demand."},
            worker=None,
        )

        await runtime.sync_plugin_registry()

        rows = await ledger.list_plugins(include_archived=True)
        assert {row["plugin_name"]: row["tier_type"] for row in rows} == {
            "core_tool": "CORE",
            "lazy_tool": "ATELIER",
        }

    asyncio.run(scenario())


def test_mission_prompt_includes_atelier_catalog_and_sop(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        await ledger.register_plugin("lazy_tool", "ATELIER", "On demand.")
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        prompts = []

        async def worker(prompt):
            prompts.append(prompt)
            return "ok"

        runtime = Runtime(ledger, governor, worker=worker)

        await runtime.handle_mission(thread_id, "halo")

        assert "[KATALOG ALAT EKSTRA]" in prompts[0]
        assert "- lazy_tool: On demand." in prompts[0]
        assert "[SOP ANTI-AMNESIA]" in prompts[0]
        assert "equip_tool" in prompts[0]

    asyncio.run(scenario())


def test_load_plugins_initializes_each_with_config(tmp_path):
    repo_plugins = Path(__file__).resolve().parent.parent / "plugins"

    plugins, catalog = load_plugins(
        repo_plugins,
        {
            "paths": {
                "workspace_dir": str(tmp_path / "workspace"),
                "atelier_dir": str(tmp_path / "atelier"),
            }
        },
    )

    assert "file_manager" in plugins
    assert "web_browser" not in plugins  # optional plugins are Tier 2
    assert (tmp_path / "workspace").is_dir()
    assert (tmp_path / "atelier").is_dir()


def test_load_plugins_core_override_moves_everything_to_catalog(tmp_path):
    repo_plugins = Path(__file__).resolve().parent.parent / "plugins"

    plugins, catalog = load_plugins(
        repo_plugins,
        {
            "paths": {
                "workspace_dir": str(tmp_path / "workspace"),
                "atelier_dir": str(tmp_path / "atelier"),
            }
        },
        core_plugins=set(),
    )

    assert plugins == {}
    assert "file_manager" in catalog


def test_scheduled_tick_pings_wake_up_and_runs_initiative(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        pings = []
        initiatives = []

        async def initiative():
            initiatives.append(True)
            return "slept"

        runtime = Runtime(
            ledger,
            governor,
            worker=None,
            wake_up=lambda: pings.append(True) or True,
            initiative=initiative,
        )

        result = await runtime.scheduled_tick()

        assert result == "slept"
        assert pings == [True]
        assert initiatives == [True]

    asyncio.run(scenario())


def test_scheduled_tick_runs_monologue_only_every_n_cycles(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        initiatives = []

        async def initiative():
            initiatives.append(True)
            return "slept"

        runtime = Runtime(
            ledger,
            governor,
            worker=None,
            initiative=initiative,
            monologue_every_cycles=3,
        )

        assert await runtime.scheduled_tick() is None
        assert await runtime.scheduled_tick() is None
        assert initiatives == []

        assert await runtime.scheduled_tick() == "slept"
        assert initiatives == [True]

    asyncio.run(scenario())


def test_scheduled_tick_is_blocked_by_kill_switch(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        initiatives = []

        async def initiative():
            initiatives.append(True)
            return "slept"

        runtime = Runtime(
            ledger,
            governor,
            worker=None,
            wake_up=lambda: True,
            initiative=initiative,
        )
        await ledger.set_system_state("kill_switch", "1")

        result = await runtime.scheduled_tick()

        assert result is None
        assert initiatives == []

    asyncio.run(scenario())


def test_scheduler_loop_logs_a_failed_tick_and_keeps_going(caplog):
    async def scenario():
        ticks = []

        class FlakyRuntime:
            async def scheduled_tick(self):
                ticks.append(True)
                if len(ticks) == 1:
                    raise RuntimeError("tick boom")

        task = asyncio.create_task(scheduler_loop(FlakyRuntime(), 0.01))
        await asyncio.sleep(0.035)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert len(ticks) >= 2

    asyncio.run(scenario())
    assert "tick boom" in caplog.text


def test_scheduler_loop_keeps_ticking_until_cancelled():
    async def scenario():
        ticks = []

        class FakeRuntime:
            async def scheduled_tick(self):
                ticks.append(True)

        task = asyncio.create_task(scheduler_loop(FakeRuntime(), 0.01))
        await asyncio.sleep(0.035)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert len(ticks) >= 2

    asyncio.run(scenario())


def test_global_limit_pauses_everything_and_explains(tmp_path):
    async def scenario():
        ledger = CognitiveLedger(tmp_path / "ledger.db")
        await ledger.initialize()
        governor = BudgetGovernor(1.00, rpm_limit=1)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        calls = []

        async def worker(prompt):
            calls.append(prompt)
            return "ok"

        runtime = Runtime(ledger, governor, worker=worker)

        reply = await runtime.handle_mission(thread_id, "halo")
        assert "Agent paused: RPM limit 1/1 reached." in reply
        assert await ledger.get_system_state("kill_switch") == "1"
        # The pause fires before the status settles, but the work did finish.
        assert (
            await ledger.read("ledger_threads", thread_id)
        )["status"] == "Completed"

        with pytest.raises(MissionBlockedError):
            await runtime.handle_mission(thread_id, "halo lagi")

        assert len(calls) == 1
        assert "RPM" in await runtime.pause_notice()

    asyncio.run(scenario())


def test_agent_share_pauses_only_agent_threads(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        governor.set_budget_ratio_agent(0.5)  # rpm cap = 30
        governor.record_api_usage(30, side="AGENT")
        agent_thread = await ledger.create_thread("Initiative", "AGENT")
        human_thread = await ledger.create_thread("Mission A", "HUMAN")
        calls = []

        async def worker(prompt):
            calls.append(prompt)
            return "ok"

        runtime = Runtime(ledger, governor, worker=worker)

        agent_reply = await runtime.handle_mission(agent_thread, "belajar")
        assert "Agent initiative paused" in agent_reply
        assert (await ledger.read("ledger_threads", agent_thread))["status"] == "Paused"
        assert await ledger.get_system_state("kill_switch") != "1"

        human_reply = await runtime.handle_mission(human_thread, "halo")

        assert human_reply == "ok"
        assert len(calls) == 1
        assert (
            await ledger.read("ledger_threads", human_thread)
        )["status"] == "Completed"

    asyncio.run(scenario())


def test_auto_resume_lifts_rate_limit_pause_after_its_window(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        await ledger.set_system_state("kill_switch", "1")
        await ledger.set_system_state("pause_reason", "rate_limit:RPM")
        await ledger.set_system_state("pause_until", "2000-01-01T00:00:00Z")
        await ledger.pause_active_threads()

        async def worker(prompt):
            return "ok"

        runtime = Runtime(ledger, governor, worker=worker)

        reply = await runtime.handle_mission(thread_id, "halo")

        assert reply == "ok"
        assert await ledger.get_system_state("kill_switch") == "0"
        assert (
            await ledger.read("ledger_threads", thread_id)
        )["status"] == "Completed"

    asyncio.run(scenario())


def test_manual_pause_is_not_auto_resumed(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        await ledger.set_system_state("kill_switch", "1")
        await ledger.set_system_state("pause_reason", "manual")
        await ledger.set_system_state("pause_until", "2000-01-01T00:00:00Z")

        async def worker(prompt):
            return "ok"

        runtime = Runtime(ledger, governor, worker=worker)

        with pytest.raises(MissionBlockedError):
            await runtime.handle_mission(thread_id, "halo")

        assert await ledger.get_system_state("kill_switch") == "1"

    asyncio.run(scenario())


def test_scheduled_tick_runs_sleep_mode_once_per_night(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        runs = []

        class FakeMemory:
            async def decay_plugins(self, now=None):
                return 0

            async def vectorize_pending(self, now=None):
                runs.append(now)
                return "report"

        async def initiative():
            return "slept"

        runtime = Runtime(
            ledger,
            governor,
            worker=None,
            memory=FakeMemory(),
            initiative=initiative,
        )

        await runtime.scheduled_tick(datetime(2026, 11, 16, 12, 0))
        assert runs == []

        await runtime.scheduled_tick(datetime(2026, 11, 16, 2, 0))
        await runtime.scheduled_tick(datetime(2026, 11, 16, 3, 0))
        assert len(runs) == 1

        await runtime.scheduled_tick(datetime(2026, 11, 17, 1, 0))
        assert len(runs) == 2

    asyncio.run(scenario())


def test_sleep_mode_failure_is_logged_and_not_retried_same_night(tmp_path, caplog):
    async def scenario():
        ledger, governor = await _setup(tmp_path)

        class ExplodingMemory:
            def __init__(self):
                self.calls = 0

            async def decay_plugins(self, now=None):
                return 0

            async def vectorize_pending(self, now=None):
                self.calls += 1
                raise RuntimeError("chroma down")

        async def initiative():
            return "slept"

        memory = ExplodingMemory()
        runtime = Runtime(
            ledger,
            governor,
            worker=None,
            memory=memory,
            initiative=initiative,
        )

        await runtime.scheduled_tick(datetime(2026, 11, 16, 2, 0))
        await runtime.scheduled_tick(datetime(2026, 11, 16, 3, 0))

        assert memory.calls == 1

    asyncio.run(scenario())
    assert "sleep-mode vectorization failed" in caplog.text


def test_resume_next_active_reruns_a_crashed_mission(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Crashed", "HUMAN")
        await ledger.create(
            "ledger_entries",
            thread_id=thread_id,
            tag="TINDAKAN",
            content="kerjakan ini",
            token_count=2,
        )
        # A crash leaves the attempt recorded but the thread still Active.
        await ledger.record_thread_attempt(thread_id)
        await ledger.update(
            "ledger_threads",
            thread_id,
            last_attempt_at="2000-01-01 00:00:00.000000",
        )
        prompts = []

        async def worker(prompt):
            prompts.append(prompt)
            return "lanjut selesai"

        runtime = Runtime(ledger, governor, worker=worker)

        reply = await runtime.resume_next_active()

        assert reply == "lanjut selesai"
        assert "USER QUERY:\nkerjakan ini" in prompts[0]
        assert (
            await ledger.read("ledger_threads", thread_id)
        )["status"] == "Completed"

    asyncio.run(scenario())


def test_resume_next_active_respects_the_retry_cooldown(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Just tried", "HUMAN")
        # Ready to resume once, then blocked by the retry cooldown.
        await ledger.update(
            "ledger_threads",
            thread_id,
            last_attempt_at="2000-01-01 00:00:00.000000",
        )
        calls = []

        async def worker(prompt):
            calls.append(prompt)
            return "ok"

        runtime = Runtime(ledger, governor, worker=worker)

        assert await runtime.resume_next_active() == "ok"
        # The attempt just happened, so the next tick must not rerun it.
        assert await runtime.resume_next_active() is None
        assert len(calls) == 1

    asyncio.run(scenario())


def test_resume_next_active_drops_a_thread_after_max_attempts(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Hopeless", "HUMAN")
        await ledger.update(
            "ledger_threads",
            thread_id,
            attempt_count=3,
            last_attempt_at="2000-01-01 00:00:00.000000",
        )
        notices = []
        calls = []

        async def worker(prompt):
            calls.append(prompt)
            return "ok"

        runtime = Runtime(
            ledger, governor, worker=worker, notify=notices.append
        )

        assert await runtime.resume_next_active() is None
        assert calls == []
        assert notices == ["Mission dropped after 3 attempts: Hopeless"]
        assert (
            await ledger.read("ledger_threads", thread_id)
        )["status"] == "Completed"

    asyncio.run(scenario())


def test_scheduled_tick_does_not_resume_while_kill_switch_is_on(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Crashed", "HUMAN")
        await ledger.set_system_state("kill_switch", "1")
        calls = []

        async def worker(prompt):
            calls.append(prompt)
            return "ok"

        runtime = Runtime(ledger, governor, worker=worker)

        await runtime.scheduled_tick()

        assert calls == []
        assert (
            await ledger.read("ledger_threads", thread_id)
        )["status"] == "Active"

    asyncio.run(scenario())


def _decision(action="CREATE_INITIATIVE", **overrides):
    from core.schemas import SupervisorDecision

    data = {
        "action_type": action,
        "model_tier_used": "TIER_1_LIGHT",
        "reasoning": "vision says keep learning",
        "thought_entry": "explore async sqlite tuning",
    }
    data.update(overrides)
    return SupervisorDecision(**data)


def test_curiosity_cycle_creates_an_agent_thread_and_thought(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        await ledger.create(
            "core_directives", directive_type="VISION", content="Stay curious."
        )
        prompts = []

        async def worker(prompt, structured_model=None):
            prompts.append((prompt, structured_model))
            return _decision()

        runtime = Runtime(ledger, governor, worker=worker)

        await runtime._default_initiative()

        assert len(prompts) == 1
        assert "Stay curious." in prompts[0][0]
        assert prompts[0][1].__name__ == "SupervisorDecision"

        active = await ledger.get_next_active_thread(origin="AGENT")
        assert active["title"].startswith("Curiosity: ")
        entries = await ledger.list_ledger_entries(active["thread_id"], tags=("PEMIKIRAN",))
        assert "explore async sqlite tuning" in entries[-1]["content"]
        assert await ledger.get_system_state(
            f"curiosity_count:{datetime.now(timezone.utc):%Y-%m-%d}"
        ) == "1"

    asyncio.run(scenario())


def test_curiosity_cycle_stops_at_the_daily_limit(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        await ledger.set_system_state(
            f"curiosity_count:{datetime.now(timezone.utc):%Y-%m-%d}", "3"
        )
        calls = []

        async def worker(prompt, structured_model=None):
            calls.append(prompt)
            return _decision()

        runtime = Runtime(ledger, governor, worker=worker)

        await runtime._default_initiative()

        assert calls == []
        assert await ledger.get_next_active_thread(origin="AGENT") is None

    asyncio.run(scenario())


def test_curiosity_cycle_marks_sleep_noop_threads_completed(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)

        async def worker(prompt, structured_model=None):
            return _decision("SLEEP_NOOP")

        runtime = Runtime(ledger, governor, worker=worker)

        await runtime._default_initiative()

        assert await ledger.get_next_active_thread(origin="AGENT") is None
        logs = await ledger.list_activity_logs()
        assert logs == []

    asyncio.run(scenario())


def test_curiosity_failure_does_not_break_the_tick(tmp_path, caplog):
    async def scenario():
        ledger, governor = await _setup(tmp_path)

        async def worker(prompt):
            return "ok"

        runtime = Runtime(ledger, governor, worker=worker)

        await runtime._default_initiative()

    asyncio.run(scenario())
    assert "curiosity cycle failed" in caplog.text


class _EchoPlugin:
    def __init__(self):
        self.payloads = []

    def execute(self, payload):
        self.payloads.append(payload)
        return PluginOutputSchema(success=True, data={"echo": payload.get("text", "")})


def test_mission_runs_a_requested_plugin_and_feeds_the_result_back(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        plugin = _EchoPlugin()
        prompts = []

        async def worker(prompt):
            prompts.append(prompt)
            if len(prompts) == 1:
                return MissionReply(
                    reply="mencari",
                    plugin="echo",
                    plugin_payload={"text": "halo dunia"},
                )
            return MissionReply(reply="hasil akhir")

        runtime = Runtime(
            ledger, governor, plugins={"echo": plugin}, worker=worker
        )

        reply = await runtime.handle_mission(thread_id, "pakai tool")

        assert reply == "hasil akhir"
        assert plugin.payloads == [{"text": "halo dunia"}]
        assert "TOOL RESULT (echo)" in prompts[1]
        assert "halo dunia" in prompts[1]
        assert (
            await ledger.read("ledger_threads", thread_id)
        )["status"] == "Completed"

    asyncio.run(scenario())


def test_mission_stops_after_the_tool_round_cap(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        plugin = _EchoPlugin()
        calls = []

        async def worker(prompt):
            calls.append(prompt)
            return MissionReply(
                reply="masih mencari",
                plugin="echo",
                plugin_payload={"text": "lagi"},
            )

        runtime = Runtime(
            ledger, governor, plugins={"echo": plugin}, worker=worker
        )

        reply = await runtime.handle_mission(thread_id, "loop")

        assert reply == "masih mencari"
        assert len(plugin.payloads) == 3
        assert len(calls) == 4

    asyncio.run(scenario())


def test_failed_plugin_result_is_still_shown_to_the_model(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        prompts = []

        async def worker(prompt):
            prompts.append(prompt)
            if len(prompts) == 1:
                return MissionReply(reply="coba", plugin="missing")
            return MissionReply(reply="tool tidak ada, lanjut manual")

        runtime = Runtime(ledger, governor, worker=worker)

        reply = await runtime.handle_mission(thread_id, "pakai tool")

        assert reply == "tool tidak ada, lanjut manual"
        assert "TOOL RESULT (missing)" in prompts[1]
        assert "Unknown plugin" in prompts[1]

    asyncio.run(scenario())


def test_resume_next_active_skips_a_thread_that_is_still_running(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        await ledger.create_thread("In flight", "HUMAN")
        calls = []

        async def worker(prompt):
            calls.append(prompt)
            return "ok"

        runtime = Runtime(ledger, governor, worker=worker)

        # Webhook/Telegram create the thread, then work it in the same call.
        assert await runtime.resume_next_active() is None
        assert calls == []

    asyncio.run(scenario())


def test_mission_writes_one_audit_row_per_step(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        plugin = _EchoPlugin()
        prompts = []

        async def worker(prompt):
            prompts.append(prompt)
            if len(prompts) == 1:
                return MissionReply(
                    reply="mencari",
                    plugin="echo",
                    plugin_payload={"text": "halo"},
                    reasoning="butuh data dari tool",
                )
            return MissionReply(reply="selesai", reasoning="tool sudah menjawab")

        runtime = Runtime(
            ledger, governor, plugins={"echo": plugin}, worker=worker
        )

        await runtime.handle_mission(thread_id, "pakai tool")

        rows = await ledger.list_audit_logs(thread_id)
        assert [row["step"] for row in rows] == [1, 2, 3]
        assert {row["trace_id"] for row in rows} == {rows[0]["trace_id"]}
        assert rows[0]["action"] == "CALL_PLUGIN"
        assert rows[0]["cot"] == "butuh data dari tool"
        assert "pakai tool" in rows[0]["prompt"]
        assert rows[1]["action"] == "CALL_PLUGIN"
        assert rows[1]["action_target"] == "echo"
        assert '"text": "halo"' in rows[1]["action_payload"]
        assert rows[2]["action"] == "LLM_REPLY"
        assert rows[2]["response"] == "selesai"
        assert all(row["trigger"] == "TELEGRAM" for row in rows)
        assert all(row["origin"] == "HUMAN" for row in rows)

        entries = await ledger.list_ledger_entries(thread_id, tags=("FAKTA",))
        assert rows[-1]["result_table"] == "ledger_entries"
        assert rows[-1]["result_id"] == entries[-1]["entry_id"]

    asyncio.run(scenario())


def test_audit_can_be_disabled(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")

        async def worker(prompt):
            return "ok"

        runtime = Runtime(ledger, governor, worker=worker, audit_enabled=False)

        await runtime.handle_mission(thread_id, "halo")

        assert await ledger.list_audit_logs(thread_id) == []

    asyncio.run(scenario())


def test_curiosity_cycle_leaves_an_audit_row(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)

        async def worker(prompt, structured_model=None):
            return _decision()

        runtime = Runtime(ledger, governor, worker=worker)

        await runtime._default_initiative()

        thread = await ledger.get_next_active_thread(origin="AGENT")
        rows = await ledger.list_audit_logs(thread["thread_id"])
        assert len(rows) == 1
        assert rows[0]["trigger"] == "CURIOSITY"
        assert rows[0]["action"] == "CREATE_INITIATIVE"
        assert rows[0]["cot"] == "vision says keep learning"
        assert rows[0]["result_table"] == "ledger_entries"
        assert rows[0]["result_id"] is not None

    asyncio.run(scenario())


def test_resume_mission_is_audited_as_scheduler_trigger(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Crashed", "HUMAN")
        await ledger.update(
            "ledger_threads",
            thread_id,
            last_attempt_at="2000-01-01 00:00:00.000000",
        )

        async def worker(prompt):
            return "lanjut"

        runtime = Runtime(ledger, governor, worker=worker)

        await runtime.resume_next_active()

        rows = await ledger.list_audit_logs(thread_id)
        assert rows[0]["trigger"] == "SCHEDULER_RESUME"

    asyncio.run(scenario())


def test_halt_file_blocks_a_mission_without_calling_the_worker(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        (tmp_path / "HALT").write_text("")
        calls = []

        async def worker(prompt):
            calls.append(prompt)
            return "ok"

        runtime = Runtime(ledger, governor, worker=worker, halt_dir=tmp_path)

        reply = await runtime.handle_mission(thread_id, "halo")

        assert "HALT" in reply
        assert calls == []
        assert (await ledger.read("ledger_threads", thread_id))["status"] == "Active"
        rows = await ledger.list_audit_logs(thread_id)
        assert rows[-1]["action"] == "BLOCKED"
        assert rows[-1]["outcome"] == "BLOCKED"

    asyncio.run(scenario())


def test_halt_file_stops_an_in_flight_mission_before_the_tool_runs(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        plugin = _EchoPlugin()

        async def worker(prompt):
            (tmp_path / "STOP").write_text("")
            return MissionReply(
                reply="mencari",
                plugin="echo",
                plugin_payload={"text": "halo"},
            )

        runtime = Runtime(
            ledger,
            governor,
            plugins={"echo": plugin},
            worker=worker,
            halt_dir=tmp_path,
        )

        reply = await runtime.handle_mission(thread_id, "pakai tool")

        assert "STOP" in reply
        assert plugin.payloads == []
        rows = await ledger.list_audit_logs(thread_id)
        assert rows[-1]["action"] == "BLOCKED"
        assert rows[-1]["action_target"] == "echo"

    asyncio.run(scenario())


def test_removing_the_halt_file_resumes_work(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Mission A", "HUMAN")
        halt = tmp_path / "HALT"
        halt.write_text("")
        calls = []

        async def worker(prompt):
            calls.append(prompt)
            return "ok"

        runtime = Runtime(ledger, governor, worker=worker, halt_dir=tmp_path)

        assert "HALT" in await runtime.handle_mission(thread_id, "halo")

        halt.unlink()
        assert await runtime.handle_mission(thread_id, "halo lagi") == "ok"
        assert len(calls) == 1

    asyncio.run(scenario())


def test_scheduled_tick_skips_resume_and_curiosity_while_halted(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Crashed", "HUMAN")
        await ledger.update(
            "ledger_threads",
            thread_id,
            last_attempt_at="2000-01-01 00:00:00.000000",
        )
        (tmp_path / "HALT").write_text("")
        calls = []

        async def worker(prompt, structured_model=None):
            calls.append(prompt)
            return "ok"

        runtime = Runtime(ledger, governor, worker=worker, halt_dir=tmp_path)

        await runtime.scheduled_tick()

        assert calls == []
        assert (await ledger.read("ledger_threads", thread_id))["status"] == "Active"

    asyncio.run(scenario())


def test_run_plugin_heals_sandbox_assertion_failures(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)

        class Sandbox:
            def __init__(self):
                self.codes = []

            def execute(self, payload):
                self.codes.append(payload["code"])
                passed = payload["code"] == "fixed"
                return PluginOutputSchema(
                    success=passed,
                    data={"code": payload["code"]},
                    error_message=None if passed else "assertion failed",
                    assertions_passed=passed,
                )

        sandbox = Sandbox()

        async def propose_patch(attempt, error):
            return HealerPatchProposal(
                attempt_number=attempt,
                root_cause_analysis="The sandboxed snippet used the wrong guard.",
                patched_code="fixed",
                embedded_assertions=["True"],
            )

        runtime = Runtime(
            ledger,
            governor,
            plugins={"sandbox_executor": sandbox},
            propose_patch=propose_patch,
            worker=None,
        )

        output = await runtime.run_plugin(
            "sandbox_executor", {"code": "broken", "language": "python"}
        )

        assert output.success is True
        assert output.assertions_passed is True
        assert sandbox.codes == ["broken", "fixed"]

    asyncio.run(scenario())


def test_default_propose_patch_falls_back_to_the_worker(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)

        async def worker(prompt, structured_model=None):
            assert structured_model is HealerPatchProposal
            return HealerPatchProposal(
                attempt_number=1,
                root_cause_analysis="Worker diagnosed the failure.",
                patched_code="print('ok')",
                embedded_assertions=["True"],
            )

        runtime = Runtime(ledger, governor, worker=worker)

        proposal = await runtime._default_propose_patch(1, "worker boom")

        assert isinstance(proposal, HealerPatchProposal)
        assert proposal.patched_code == "print('ok')"

    asyncio.run(scenario())


def test_sleep_mode_decays_semantic_memories(tmp_path):
    async def scenario():
        ledger, governor = await _setup(tmp_path)
        decayed = []

        class FakeMemory:
            async def decay_plugins(self, now=None):
                return 0

            async def decay_memories(self, now=None):
                decayed.append(now)
                return 1

            async def vectorize_pending(self, now=None):
                return "report"

        async def initiative():
            return 0

        runtime = Runtime(
            ledger,
            governor,
            worker=None,
            memory=FakeMemory(),
            initiative=initiative,
        )
        night = datetime(2026, 11, 16, 2, 0)

        assert await runtime.sleep_mode_tick(night) == "report"
        assert decayed == [night]

    asyncio.run(scenario())
