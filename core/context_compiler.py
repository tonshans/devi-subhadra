"""Dynamic Elastic Window, 4-layer context assembly, and local micro-consolidation."""

from __future__ import annotations

from dataclasses import dataclass

import tiktoken

from core.cognitive_ledger import CognitiveLedger


@dataclass(frozen=True)
class CompiledContext:
    thread_id: str
    query_text: str
    directives: list[dict]
    semantic_memories: list[dict]
    thread: dict | None
    entries: list[dict]
    prompt: str
    token_count: int
    micro_consolidated: bool


class ContextCompiler:
    def __init__(
        self,
        ledger: CognitiveLedger,
        max_context_tokens: int = 30000,
    ) -> None:
        self._ledger = ledger
        self.max_context_tokens = max_context_tokens
        self._encoding = tiktoken.get_encoding("cl100k_base")

    def estimate_tokens(self, text: str) -> int:
        return len(self._encoding.encode(text))

    async def _load_layers(
        self, thread_id: str, rpm_pressure_high: bool
    ) -> tuple[list[dict], list[dict], dict | None, list[dict]]:
        directives = await self._ledger.list_core_directives()
        memories = await self._ledger.list_semantic_memories()
        thread = await self._ledger.read("ledger_threads", thread_id)
        if rpm_pressure_high:
            entries = await self._ledger.list_ledger_entries(
                thread_id, limit=5, tags=("FAKTA", "TINDAKAN")
            )
        else:
            entries = await self._ledger.list_ledger_entries(thread_id, limit=20)
        return directives, memories, thread, entries

    @staticmethod
    def _render_prompt(
        directives: list[dict],
        memories: list[dict],
        thread: dict | None,
        entries: list[dict],
        query_text: str,
    ) -> str:
        directive_text = "\n".join(
            f"[{row['directive_type']}] {row['content']}" for row in directives
        )
        memory_text = "\n".join(
            f"[{row['category']}] {row['heuristic_text']}" for row in memories
        )
        if thread:
            thread_text = (
                f"[{thread['thread_id']}] {thread['title']} | "
                f"status={thread['status']} | origin={thread['origin']} | "
                f"priority={thread['priority_score']} | "
                f"endorsed={thread['is_human_endorsed']}"
            )
        else:
            thread_text = "(thread not found)"
        entry_text = "\n".join(f"[{row['tag']}] {row['content']}" for row in entries)

        return "\n\n".join(
            [
                "CORE DIRECTIVES:\n" + (directive_text or "(none)"),
                "SEMANTIC MEMORY:\n" + (memory_text or "(none)"),
                "ACTIVE THREAD:\n" + thread_text,
                "LEDGER ENTRIES:\n" + (entry_text or "(none)"),
                "USER QUERY:\n" + query_text,
            ]
        )

    async def micro_consolidate_entries(self, thread_id: str) -> bool:
        oldest = await self._ledger.list_oldest_ledger_entries(thread_id, limit=20)
        if not oldest:
            return False

        joined = " ".join(" ".join(row["content"].split()) for row in oldest)
        summary = f"Consolidated {len(oldest)} entries: {joined[:400]}"
        return await self._ledger.replace_oldest_entries(
            thread_id,
            limit=20,
            summary=summary,
            token_count=self.estimate_tokens(summary),
        )

    async def compile_context(
        self,
        thread_id: str,
        query_text: str,
        rpm_pressure_high: bool,
    ) -> CompiledContext:
        directives, memories, thread, entries = await self._load_layers(
            thread_id, rpm_pressure_high
        )
        prompt = self._render_prompt(directives, memories, thread, entries, query_text)
        token_count = self.estimate_tokens(prompt)

        micro_consolidated = False
        if token_count > self.max_context_tokens:
            micro_consolidated = await self.micro_consolidate_entries(thread_id)
            if micro_consolidated:
                entries = (
                    await self._ledger.list_ledger_entries(
                        thread_id, limit=5, tags=("FAKTA", "TINDAKAN")
                    )
                    if rpm_pressure_high
                    else await self._ledger.list_ledger_entries(thread_id, limit=20)
                )
                prompt = self._render_prompt(
                    directives, memories, thread, entries, query_text
                )
                token_count = self.estimate_tokens(prompt)

        return CompiledContext(
            thread_id=thread_id,
            query_text=query_text,
            directives=directives,
            semantic_memories=memories,
            thread=thread,
            entries=entries,
            prompt=prompt,
            token_count=token_count,
            micro_consolidated=micro_consolidated,
        )