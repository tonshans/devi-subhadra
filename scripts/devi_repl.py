"""Local REPL for chatting with the agent without Telegram.

Usage (from the repository root):

    python -m scripts.devi_repl
    python scripts/devi_repl.py

Commands (same ones Telegram receives):

    /status        agent, uptime, budget, limits, usage, last activity
    /budget-ratio  show or set the agent share, e.g. /budget-ratio 60 40
    /agent-freetime on|off|status   let initiatives use the full limit
    /pause-agent   stop all agent activity and LLM calls
    /resume-agent  resume agent activity
    /quit, /exit   leave the REPL (Ctrl-D works too)

Add a command by decorating an async handler in core/commands.py:

    @command("/hello", "Say hello.")
    async def hello(ctx: CommandContext, args: str) -> str:
        return "hello"

Telegram and this REPL pick it up automatically; no change needed here.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.telegram_gateway import TelegramGateway  # noqa: E402
from core.cognitive_ledger import CognitiveLedger  # noqa: E402
from core.commands import (  # noqa: E402
    CommandContext,
    dispatch,
    load_governor_overrides,
)
from core.config_loader import load_config  # noqa: E402
from core.db.engine import build_database  # noqa: E402
from core.runtime import MissionBlockedError, Runtime  # noqa: E402
from main import build_governor, build_runtime  # noqa: E402

_DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"
_EXIT_WORDS = {"/quit", "/exit", "quit", "exit"}


async def run_session(
    runtime: Runtime,
    ctx: CommandContext,
    thread_id: str,
    *,
    read: Callable[[str], Awaitable[str]],
    write: Callable[[str], None],
) -> None:
    while True:
        try:
            line = await read("you> ")
        except (EOFError, KeyboardInterrupt):
            break

        text = line.strip()
        if not text:
            continue
        if text in _EXIT_WORDS:
            break

        await runtime.auto_resume()
        reply = await dispatch(text, ctx)
        if reply is None:
            try:
                # shortcut: activity_logs CHECK has no TUI value; reuse the chat label.
                reply = await runtime.handle_mission(
                    thread_id, text, initiator="Telegram"
                )
            except MissionBlockedError:
                notice = await runtime.pause_notice()
                reply = notice or "Agent is paused. Send /resume-agent."
            except Exception as exc:
                reply = f"Error: {exc}"
        write(f"devi> {reply}")


async def _read_line(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)


async def _show_file(path: str) -> None:
    print(f"[file] {path}")


async def main() -> None:
    settings = load_config(_DEFAULT_CONFIG)
    database = build_database(settings)
    ledger = CognitiveLedger(engine=database.engine)
    governor = build_governor(settings, store=database.budget_store())
    await ledger.initialize()
    await load_governor_overrides(ledger, governor)
    runtime = build_runtime(
        settings=settings,
        ledger=ledger,
        governor=governor,
        gateway=TelegramGateway(workspace_dir=settings.paths.workspace_dir),
        deliver_file=_show_file,
    )
    ctx = CommandContext(
        ledger, governor, settings, provider=getattr(runtime, "provider", None)
    )
    thread_id = await ledger.create_thread(
        f"REPL: {datetime.now():%Y-%m-%d %H:%M:%S}", "HUMAN"
    )
    print(
        f"{settings.agent_name} REPL. Commands: /status, /budget-ratio, "
        "/agent-freetime, /pause-agent, /resume-agent, /quit."
    )
    try:
        await run_session(runtime, ctx, thread_id, read=_read_line, write=print)
    finally:
        await ledger.dispose()


if __name__ == "__main__":
    asyncio.run(main())
