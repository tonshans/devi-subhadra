"""Unit tests for the composition root: mission flow, plugins, and scheduling."""

import asyncio
import contextlib
from pathlib import Path

import pytest

from core.budget_governor import BudgetGovernor
from core.cognitive_ledger import CognitiveLedger
from core.provider_pool import AllProvidersUnavailable
from core.runtime import MissionBlockedError, Runtime, load_plugins, scheduler_loop
from core.schemas import MissionReply


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


def test_load_plugins_initializes_each_with_config(tmp_path):
    repo_plugins = Path(__file__).resolve().parent.parent / "plugins"

    plugins = load_plugins(
        repo_plugins,
        {
            "paths": {
                "workspace_dir": str(tmp_path / "workspace"),
                "atelier_dir": str(tmp_path / "atelier"),
            }
        },
    )

    assert "file_manager" in plugins
    assert (tmp_path / "workspace").is_dir()
    assert (tmp_path / "atelier").is_dir()


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
        assert (await ledger.read("ledger_threads", thread_id))["status"] == "Paused"

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
        assert (await ledger.read("ledger_threads", human_thread))["status"] == "Active"

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
        assert (await ledger.read("ledger_threads", thread_id))["status"] == "Active"

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
