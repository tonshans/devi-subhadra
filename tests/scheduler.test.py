"""Tests for scheduled reminders, process watches, kills, and recurring runs."""

import asyncio
import os
from datetime import datetime, timedelta

from core.scheduled_task_runner import ScheduledTaskRunner
from core.scheduled_task_store import ScheduledTaskStore
from core.schemas import PluginOutputSchema
from plugins.scheduler import SchedulerPlugin


def _plugin(tmp_path) -> SchedulerPlugin:
    plugin = SchedulerPlugin(tasks_path=tmp_path / "tasks.json")
    plugin.initialize({"plugins_dir": tmp_path / "plugins"})
    return plugin


def _payload(**overrides):
    data = {
        "action": "create",
        "kind": "remind",
        "message": "minum kopi",
        "at": "10:30",
    }
    data.update(overrides)
    return data


def test_create_and_list_a_reminder(tmp_path):
    async def scenario():
        plugin = _plugin(tmp_path)

        created = await plugin.execute(_payload())

        assert created.success is True
        assert created.data["task"]["daily_at"] is None
        assert "timezone" in created.data
        listed = await plugin.execute({"action": "list"})
        assert [task["kind"] for task in listed.data["tasks"]] == ["remind"]

    asyncio.run(scenario())


def test_kill_requires_a_human_origin(tmp_path):
    async def scenario():
        plugin = _plugin(tmp_path)

        agent = await plugin.execute(
            _payload(kind="kill_process", pid=1234, origin="AGENT")
        )
        human = await plugin.execute(
            _payload(kind="kill_process", pid=1234, origin="HUMAN")
        )

        assert agent.success is False
        assert "manusia" in agent.error_message
        assert human.success is True

    asyncio.run(scenario())


def test_run_plugin_rejects_an_unknown_plugin(tmp_path):
    async def scenario():
        plugin = _plugin(tmp_path)

        result = await plugin.execute(
            _payload(kind="run_plugin", plugin="tidak_ada", at="08:00")
        )

        assert result.success is False
        assert "tidak ditemukan" in result.error_message

    asyncio.run(scenario())


def test_cancel_removes_a_pending_task(tmp_path):
    async def scenario():
        plugin = _plugin(tmp_path)
        task = (await plugin.execute(_payload())).data["task"]

        cancelled = await plugin.execute(
            {"action": "cancel", "task_id": task["id"]}
        )

        assert cancelled.success is True
        assert (await plugin.execute({"action": "list"})).data["tasks"] == []

    asyncio.run(scenario())


def test_runner_fires_due_reminder_and_marks_it_done(tmp_path):
    async def scenario():
        store = ScheduledTaskStore(tmp_path / "tasks.json")
        sent = []

        async def notify(message):
            sent.append(message)

        async def run_plugin(name, payload):
            return PluginOutputSchema(success=True, data={})

        async def read_row(table, pk):
            return None

        runner = ScheduledTaskRunner(store, notify, run_plugin, read_row)
        now = datetime.now().astimezone()
        await store.add(
            {
                "kind": "remind",
                "message": "waktunya!",
                "run_at": (now - timedelta(minutes=1)).isoformat(),
            }
        )

        fired = await runner.tick(now)

        assert fired == ["waktunya!"]
        assert sent == ["waktunya!"]
        assert (await store.load())[0]["done"] is True
        assert await runner.tick(now + timedelta(minutes=1)) == []

    asyncio.run(scenario())


def test_daily_task_fires_once_per_day(tmp_path):
    async def scenario():
        store = ScheduledTaskStore(tmp_path / "tasks.json")
        sent = []

        async def notify(message):
            sent.append(message)

        async def run_plugin(name, payload):
            return PluginOutputSchema(success=True, data={})

        async def read_row(table, pk):
            return None

        runner = ScheduledTaskRunner(store, notify, run_plugin, read_row)
        today = datetime.now().astimezone().replace(
            hour=8, minute=0, second=0, microsecond=0
        )
        await store.add(
            {"kind": "remind", "message": "pagi", "daily_at": "08:00", "run_at": None}
        )

        assert await runner.tick(today) == ["pagi"]
        assert await runner.tick(today + timedelta(minutes=1)) == []

    asyncio.run(scenario())


def test_runner_fires_when_watched_process_is_gone(tmp_path):
    async def scenario():
        store = ScheduledTaskStore(tmp_path / "tasks.json")
        sent = []

        async def notify(message):
            sent.append(message)

        async def run_plugin(name, payload):
            return PluginOutputSchema(success=True, data={})

        async def read_row(table, pk):
            return None

        runner = ScheduledTaskRunner(store, notify, run_plugin, read_row)
        await store.add({"kind": "watch_process", "pid": 999_999_999})

        fired = await runner.tick(datetime.now().astimezone())

        assert fired == ["Proses 999999999 sudah selesai."]
        assert sent == fired

    asyncio.run(scenario())


def test_runner_kills_a_real_process(tmp_path):
    async def scenario():
        store = ScheduledTaskStore(tmp_path / "tasks.json")
        sent = []
        process = await asyncio.create_subprocess_exec("sleep", "60")

        async def notify(message):
            sent.append(message)

        async def run_plugin(name, payload):
            return PluginOutputSchema(success=True, data={})

        async def read_row(table, pk):
            return None

        runner = ScheduledTaskRunner(store, notify, run_plugin, read_row)
        await store.add(
            {
                "kind": "kill_process",
                "pid": process.pid,
                "run_at": (datetime.now().astimezone() - timedelta(seconds=1)).isoformat(),
            }
        )

        await runner.tick(datetime.now().astimezone())
        await asyncio.wait_for(process.wait(), timeout=5)

        assert process.returncode is not None
        assert sent == [f"Proses {process.pid} dihentikan (SIGTERM)."]
        assert os.getpid() != process.pid

    asyncio.run(scenario())


def test_runtime_stamps_the_mission_origin_for_scheduler(tmp_path):
    from core.budget_governor import BudgetGovernor
    from core.cognitive_ledger import CognitiveLedger
    from core.runtime import Runtime
    from core.schemas import MissionReply

    async def scenario():
        ledger = CognitiveLedger(tmp_path / "ledger.db")
        await ledger.initialize()
        thread_id = await ledger.create_thread("Matikan ffmpeg", "HUMAN")
        plugin = _plugin(tmp_path)
        calls = []

        async def worker(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return MissionReply(
                    reply="siap",
                    plugin="scheduler",
                    plugin_payload={
                        "action": "create",
                        "kind": "kill_process",
                        "pid": 1234,
                        "at": "10:30",
                    },
                )
            return MissionReply(reply="sudah dijadwalkan")

        runtime = Runtime(
            ledger,
            BudgetGovernor(1.00),
            plugins={"scheduler": plugin},
            worker=worker,
        )

        assert await runtime.handle_mission(thread_id, "matikan ffmpeg jam 10:30")

        tasks = await plugin._store.load()
        assert [task["origin"] for task in tasks] == ["HUMAN"]
        await ledger.dispose()

    asyncio.run(scenario())
