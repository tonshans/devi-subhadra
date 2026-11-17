"""Unit tests for Overseer metrics, audit reads, kill-switch, and DB injectors."""

import asyncio

import pytest

from core.cognitive_ledger import CognitiveLedger
from scripts.overseer_dashboard import OverseerDashboard


async def _setup(tmp_path):
    ledger = CognitiveLedger(tmp_path / "ledger.db")
    await ledger.initialize()
    return ledger, OverseerDashboard(ledger)


def test_character_trajectory_aggregates_semantic_memory(tmp_path):
    async def scenario():
        ledger, dashboard = await _setup(tmp_path)
        await ledger.create(
            "semantic_memory",
            category="TEKNIS",
            heuristic_text="Prefer Python.",
            confidence_score=60,
        )
        await ledger.create(
            "semantic_memory",
            category="KOMUNIKASI",
            heuristic_text="Reply formally.",
            confidence_score=40,
        )
        await ledger.create(
            "semantic_memory",
            category="TEKNIS",
            heuristic_text="Old archived rule.",
            confidence_score=10,
            is_archived=1,
        )

        trajectory = await dashboard.character_trajectory()

        assert trajectory == {
            "total": 3,
            "active": 2,
            "archived": 1,
            "average_confidence": 50.0,
            "categories": {
                "KOMUNIKASI": {"count": 1, "average_confidence": 40.0},
                "TEKNIS": {"count": 1, "average_confidence": 60.0},
            },
        }

    asyncio.run(scenario())


def test_read_audit_log_returns_newest_rows_without_llm_cost(tmp_path):
    async def scenario():
        ledger, dashboard = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Thread A", "HUMAN")
        for index in range(3):
            await ledger.create(
                "activity_logs",
                thread_id=thread_id,
                source="AGENT",
                initiator="Inner_Monologue",
                reasoning=f"reason-{index}",
                budget_bucket="ATELIER_30",
                tokens_used=index * 100,
            )

        before = await ledger.list_activity_logs(limit=100)
        rows = await dashboard.read_audit_log(limit=2)
        after = await ledger.list_activity_logs(limit=100)

        assert [row["reasoning"] for row in rows] == ["reason-2", "reason-1"]
        assert [row["tokens_used"] for row in rows] == [200, 100]
        assert before == after
        assert len(rows) == 2

    asyncio.run(scenario())


def test_kill_switch_pauses_every_active_thread(tmp_path):
    async def scenario():
        ledger, dashboard = await _setup(tmp_path)
        first = await ledger.create_thread("Active A", "HUMAN")
        second = await ledger.create_thread("Active B", "AGENT")
        completed = await ledger.create(
            "ledger_threads",
            title="Completed C",
            status="Completed",
            origin="HUMAN",
            priority_score=100,
        )

        paused = await dashboard.kill_switch()

        assert paused == 2
        assert (await ledger.read("ledger_threads", first))["status"] == "Paused"
        assert (await ledger.read("ledger_threads", second))["status"] == "Paused"
        assert (await ledger.read("ledger_threads", completed))["status"] == "Completed"
        assert await ledger.get_next_active_thread() is None

    asyncio.run(scenario())


def test_kill_switch_flag_persists_and_can_be_resumed(tmp_path):
    async def scenario():
        ledger, dashboard = await _setup(tmp_path)

        await dashboard.kill_switch()

        assert await ledger.get_system_state("kill_switch") == "1"
        reopened = CognitiveLedger(tmp_path / "ledger.db")
        assert await reopened.get_system_state("kill_switch") == "1"

        await dashboard.resume()

        assert await ledger.get_system_state("kill_switch") == "0"

    asyncio.run(scenario())


def test_resume_unpauses_threads_paused_by_kill_switch(tmp_path):
    async def scenario():
        ledger, dashboard = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Active A", "HUMAN")

        await dashboard.kill_switch()
        assert (await ledger.read("ledger_threads", thread_id))["status"] == "Paused"

        resumed = await dashboard.resume()

        assert resumed == 1
        assert (await ledger.read("ledger_threads", thread_id))["status"] == "Active"
        assert (await ledger.get_next_active_thread())["thread_id"] == thread_id
        assert await ledger.get_system_state("kill_switch") == "0"

    asyncio.run(scenario())


def test_edit_memory_revives_archived_rule_when_score_recovers(tmp_path):
    async def scenario():
        ledger, dashboard = await _setup(tmp_path)
        rule_id = await ledger.create(
            "semantic_memory",
            category="TEKNIS",
            heuristic_text="Fading rule.",
            confidence_score=50,
        )

        archived = await dashboard.edit_memory(rule_id, confidence_score=10)
        assert archived["is_archived"] == 1

        revived = await dashboard.edit_memory(rule_id, confidence_score=90)
        assert revived["is_archived"] == 0

    asyncio.run(scenario())


def test_override_priority_marks_thread_endorsed(tmp_path):
    async def scenario():
        ledger, dashboard = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Agent initiative", "AGENT")

        updated = await dashboard.override_priority(
            thread_id, priority_score=150, expected_output="File ZIP"
        )

        assert updated["priority_score"] == 150
        assert updated["is_human_endorsed"] == 1
        assert updated["expected_output"] == "File ZIP"
        assert (await ledger.get_next_active_thread())["thread_id"] == thread_id
        assert await dashboard.override_priority("missing", 150) is None

    asyncio.run(scenario())


def test_edit_memory_updates_text_and_archives_low_scores(tmp_path):
    async def scenario():
        ledger, dashboard = await _setup(tmp_path)
        rule_id = await ledger.create(
            "semantic_memory",
            category="TEKNIS",
            heuristic_text="Old heuristic.",
            confidence_score=50,
        )

        updated = await dashboard.edit_memory(
            rule_id, heuristic_text="New heuristic.", confidence_score=90
        )
        assert updated["heuristic_text"] == "New heuristic."
        assert updated["confidence_score"] == 90
        assert updated["is_archived"] == 0

        archived = await dashboard.edit_memory(rule_id, confidence_score=10)
        assert archived["confidence_score"] == 10
        assert archived["is_archived"] == 1

        with pytest.raises(ValueError):
            await dashboard.edit_memory(rule_id)

    asyncio.run(scenario())