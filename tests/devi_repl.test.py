"""Tests for the local REPL loop."""

import asyncio
from pathlib import Path

from core.budget_governor import BudgetGovernor
from core.cognitive_ledger import CognitiveLedger
from core.commands import CommandContext
from core.config_loader import load_config
from core.runtime import Runtime
from scripts.devi_repl import run_session

_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


def test_repl_handles_commands_pause_and_chat(tmp_path):
    async def scenario():
        ledger = CognitiveLedger(tmp_path / "repl.db")
        await ledger.initialize()
        governor = BudgetGovernor(2.50)
        ctx = CommandContext(ledger, governor, load_config(_CONFIG))
        thread_id = await ledger.create_thread("REPL session", "HUMAN")
        calls = []

        async def worker(prompt):
            calls.append(prompt)
            return "jawaban"

        runtime = Runtime(ledger, governor, worker=worker)
        lines = iter(
            [
                "/status",
                "",
                "/pause-agent",
                "halo saat pause",
                "/resume-agent",
                "halo lagi",
                "/quit",
            ]
        )

        async def read(_prompt):
            try:
                return next(lines)
            except StopIteration:
                raise EOFError

        output = []
        await run_session(runtime, ctx, thread_id, read=read, write=output.append)

        text = "\n".join(output)
        assert "state: running" in text
        assert "Agent paused" in text
        assert "Agent is paused. Send /resume-agent." in text
        assert "Agent resumed" in text
        assert "devi> jawaban" in text
        assert len(calls) == 1
        assert "halo lagi" in calls[0]

    asyncio.run(scenario())
