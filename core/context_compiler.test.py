"""Unit tests for 4-layer context assembly, elastic window, and consolidation."""

import asyncio

import pytest

from core.cognitive_ledger import CognitiveLedger
from core.context_compiler import CompiledContext, ContextCompiler


async def _setup(tmp_path, max_context_tokens=30000):
    ledger = CognitiveLedger(tmp_path / "ledger.db")
    await ledger.initialize()
    compiler = ContextCompiler(ledger, max_context_tokens=max_context_tokens)
    return ledger, compiler


def test_preflight_token_estimator_counts_with_tiktoken(tmp_path):
    async def scenario():
        ledger, compiler = await _setup(tmp_path)

        assert compiler.max_context_tokens == 30000
        assert compiler.estimate_tokens("") == 0
        assert compiler.estimate_tokens("hello world") == 2
        assert compiler.estimate_tokens("hello world " * 100) > compiler.estimate_tokens(
            "hello world"
        )

    asyncio.run(scenario())


def test_compiles_all_four_memory_layers(tmp_path):
    async def scenario():
        ledger, compiler = await _setup(tmp_path)
        await ledger.create(
            "core_directives",
            directive_type="VISION",
            content="Be a transparent digital coworker.",
        )
        await ledger.create(
            "core_directives",
            directive_type="IMMUTABLE_PERSONA",
            content="Calm and precise.",
        )
        await ledger.create(
            "semantic_memory",
            category="TEKNIS",
            heuristic_text="Prefer Python for data scripts.",
            confidence_score=80,
        )
        await ledger.create(
            "semantic_memory",
            category="KOMUNIKASI",
            heuristic_text="Old archived rule.",
            confidence_score=10,
            is_archived=1,
        )
        thread_id = await ledger.create_thread("Thread A", "HUMAN")
        await ledger.create(
            "ledger_entries",
            thread_id=thread_id,
            tag="FAKTA",
            content="Ledger fact content.",
        )

        context = await compiler.compile_context(
            thread_id, "What is the plan?", rpm_pressure_high=False
        )

        assert isinstance(context, CompiledContext)
        assert context.thread_id == thread_id
        assert context.query_text == "What is the plan?"
        assert {row["directive_type"] for row in context.directives} == {
            "VISION",
            "IMMUTABLE_PERSONA",
        }
        assert [row["heuristic_text"] for row in context.semantic_memories] == [
            "Prefer Python for data scripts."
        ]
        assert context.thread["thread_id"] == thread_id
        assert [row["content"] for row in context.entries] == ["Ledger fact content."]

        assert "Be a transparent digital coworker." in context.prompt
        assert "Calm and precise." in context.prompt
        assert "Prefer Python for data scripts." in context.prompt
        assert "Thread A" in context.prompt
        assert "Ledger fact content." in context.prompt
        assert "What is the plan?" in context.prompt
        assert context.token_count == compiler.estimate_tokens(context.prompt)
        assert context.micro_consolidated is False

    asyncio.run(scenario())


def test_normal_window_loads_last_20_entries_including_thoughts(tmp_path):
    async def scenario():
        ledger, compiler = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Thread A", "HUMAN")
        tags = ["PEMIKIRAN", "FAKTA", "TINDAKAN"]
        for index in range(25):
            await ledger.create(
                "ledger_entries",
                thread_id=thread_id,
                tag=tags[index % len(tags)],
                content=f"entry-{index}",
            )

        context = await compiler.compile_context(
            thread_id, "status?", rpm_pressure_high=False
        )

        assert len(context.entries) == 20
        assert context.entries[0]["content"] == "entry-5"
        assert context.entries[-1]["content"] == "entry-24"
        assert "PEMIKIRAN" in {entry["tag"] for entry in context.entries}

    asyncio.run(scenario())


def test_pressure_window_keeps_last_5_facts_actions_and_drops_thoughts(tmp_path):
    async def scenario():
        ledger, compiler = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Thread A", "HUMAN")
        tags = ["PEMIKIRAN", "FAKTA", "TINDAKAN"]
        for index in range(25):
            await ledger.create(
                "ledger_entries",
                thread_id=thread_id,
                tag=tags[index % len(tags)],
                content=f"entry-{index}",
            )

        context = await compiler.compile_context(
            thread_id, "status?", rpm_pressure_high=True
        )

        assert [entry["content"] for entry in context.entries] == [
            "entry-17",
            "entry-19",
            "entry-20",
            "entry-22",
            "entry-23",
        ]
        assert all(entry["tag"] in {"FAKTA", "TINDAKAN"} for entry in context.entries)
        assert "PEMIKIRAN" not in {entry["tag"] for entry in context.entries}

    asyncio.run(scenario())


def test_micro_consolidation_runs_before_prompt_exceeds_safe_limit(tmp_path):
    async def scenario():
        ledger, compiler = await _setup(tmp_path, max_context_tokens=50)
        thread_id = await ledger.create_thread("Thread A", "HUMAN")
        for index in range(25):
            await ledger.create(
                "ledger_entries",
                thread_id=thread_id,
                tag="PEMIKIRAN",
                content=f"entry-{index} " + ("long memory " * 60),
            )

        calls = []
        original = compiler.micro_consolidate_entries

        async def spy(target_thread_id):
            calls.append(target_thread_id)
            return await original(target_thread_id)

        compiler.micro_consolidate_entries = spy

        context = await compiler.compile_context(
            thread_id, "summarize", rpm_pressure_high=False
        )

        assert calls == [thread_id]
        assert context.micro_consolidated is True
        assert context.token_count == compiler.estimate_tokens(context.prompt)

        remaining = await ledger.list_ledger_entries(thread_id, limit=100)
        assert len(remaining) == 6
        assert remaining[-1]["content"].startswith("Consolidated 20 entries:")
        assert [row["content"] for row in remaining[:5]] == [
            f"entry-{index} " + ("long memory " * 60) for index in range(20, 25)
        ]

    asyncio.run(scenario())


def test_micro_consolidation_is_skipped_when_under_safe_limit(tmp_path):
    async def scenario():
        ledger, compiler = await _setup(tmp_path)
        thread_id = await ledger.create_thread("Thread A", "HUMAN")
        await ledger.create(
            "ledger_entries",
            thread_id=thread_id,
            tag="FAKTA",
            content="short fact",
        )

        calls = []
        original = compiler.micro_consolidate_entries

        async def spy(target_thread_id):
            calls.append(target_thread_id)
            return await original(target_thread_id)

        compiler.micro_consolidate_entries = spy

        context = await compiler.compile_context(
            thread_id, "status?", rpm_pressure_high=False
        )

        assert calls == []
        assert context.micro_consolidated is False
        assert len(await ledger.list_ledger_entries(thread_id, limit=100)) == 1

    asyncio.run(scenario())


def test_compiler_default_limit_matches_config_spec(tmp_path):
    async def scenario():
        ledger, _ = await _setup(tmp_path)
        compiler = ContextCompiler(ledger, max_context_tokens=30000)

        assert compiler.max_context_tokens == 30000

    asyncio.run(scenario())