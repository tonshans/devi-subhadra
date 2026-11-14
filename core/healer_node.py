"""Bounded recovery loop: validate patches, retry at most three times, escalate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from core.schemas import HealerPatchProposal, PluginOutputSchema

MAX_HEALER_RETRIES = 3

ProposePatch = Callable[[int, str], Awaitable[HealerPatchProposal | dict]]
ExecutePatch = Callable[[str], Awaitable[PluginOutputSchema | dict]]


@dataclass(frozen=True)
class HealResult:
    success: bool
    attempts: int
    report: str


class HealerNode:
    async def heal(
        self,
        error: str,
        execute_patch: ExecutePatch,
        propose_patch: ProposePatch,
    ) -> HealResult:
        last_error = error

        for attempt in range(1, MAX_HEALER_RETRIES + 1):
            try:
                proposal = HealerPatchProposal.model_validate(
                    await propose_patch(attempt, last_error)
                )
            except Exception as exc:
                last_error = f"attempt {attempt}: invalid patch proposal: {exc}"
                continue

            try:
                output = PluginOutputSchema.model_validate(
                    await execute_patch(proposal.patched_code)
                )
            except Exception as exc:
                last_error = f"attempt {attempt}: patch execution failed: {exc}"
                continue

            if output.success and output.assertions_passed:
                return HealResult(
                    success=True,
                    attempts=attempt,
                    report=f"Recovered after {attempt} attempt(s)",
                )

            last_error = (
                f"attempt {attempt}: success={output.success}, "
                f"assertions_passed={output.assertions_passed}, "
                f"error={output.error_message}"
            )

        return HealResult(
            success=False,
            attempts=MAX_HEALER_RETRIES,
            report=(
                "HEALER ESCALATION: failed "
                f"{MAX_HEALER_RETRIES} attempts. Send to Telegram. "
                f"Last error: {last_error}"
            ),
        )