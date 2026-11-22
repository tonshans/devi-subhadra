"""CLI for OverseerDashboard: metrics, audit, and DB interventions.

Usage (run from the repository root):

    python -m scripts.overseer_cli trajectory
    python -m scripts.overseer_cli audit --limit 20
    python -m scripts.overseer_cli kill
    python -m scripts.overseer_cli resume
    python -m scripts.overseer_cli override THREAD_ID --priority 150 --expected "File ZIP"
    python -m scripts.overseer_cli edit RULE_ID --text "New rule." --confidence 90

Options:
    --config PATH    config.yaml to read (default: repo config.yaml)

Every command is zero-LLM-token. `kill` sets the kill switch and pauses all
active threads; `resume` reverses it. Both mirror /pause-agent and /resume-agent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from core.cognitive_ledger import CognitiveLedger
from core.config_loader import load_config
from core.db.engine import DatabaseUnavailableError, build_database
from scripts.overseer_dashboard import OverseerDashboard

_DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


async def _run(args: argparse.Namespace) -> dict | list | int:
    settings = load_config(args.config)
    database = build_database(settings)
    ledger = CognitiveLedger(engine=database.engine)
    await ledger.initialize()
    try:
        dashboard = OverseerDashboard(ledger)
        if args.command == "trajectory":
            return await dashboard.character_trajectory()
        if args.command == "audit":
            return await dashboard.read_audit_log(limit=args.limit)
        if args.command == "kill":
            return {"paused_threads": await dashboard.kill_switch()}
        if args.command == "resume":
            return {"resumed_threads": await dashboard.resume()}
        if args.command == "override":
            updated = await dashboard.override_priority(
                args.thread_id, args.priority, args.expected
            )
            if updated is None:
                raise ValueError(f"thread not found: {args.thread_id}")
            return updated
        updated = await dashboard.edit_memory(
            args.rule_id, args.text, args.confidence
        )
        if updated is None:
            raise ValueError(f"memory rule not found: {args.rule_id}")
        return updated
    finally:
        await ledger.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Overseer dashboard CLI")
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG))
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit", help="recent activity_logs")
    audit.add_argument("--limit", type=int, default=50)

    sub.add_parser("trajectory", help="semantic memory aggregates")
    sub.add_parser("kill", help="set kill switch and pause all active threads")
    sub.add_parser("resume", help="clear kill switch and unpause threads")

    override = sub.add_parser("override", help="override thread priority")
    override.add_argument("thread_id")
    override.add_argument("--priority", type=int, required=True)
    override.add_argument("--expected", default=None)

    edit = sub.add_parser("edit", help="edit a semantic memory rule")
    edit.add_argument("rule_id")
    edit.add_argument("--text", default=None)
    edit.add_argument("--confidence", type=int, default=None)

    args = parser.parse_args(argv)
    if args.command == "edit" and args.text is None and args.confidence is None:
        parser.error("edit needs --text and/or --confidence")
    if args.command == "audit" and args.limit < 1:
        parser.error("--limit must be at least 1")

    try:
        result = asyncio.run(_run(args))
    except (
        DatabaseUnavailableError,
        FileNotFoundError,
        SQLAlchemyError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
