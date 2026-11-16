"""Dynamic Elastic Window, hybrid context assembly, and local micro-consolidation."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import tiktoken

from core.cognitive_ledger import CognitiveLedger
from core.vector_store import DEFAULT_TOP_K, VectorStore

logger = logging.getLogger(__name__)

EPISTEMIC_WARNING = (
    "Informasi berikut adalah opini komunitas internet dan belum terverifikasi "
    "secara teknis. Gunakan dengan skeptis."
)


@dataclass(frozen=True)
class CompiledContext:
    thread_id: str
    query_text: str
    directives: list[dict]
    semantic_memories: list[dict]
    thread: dict | None
    entries: list[dict]
    long_term: list[dict]
    prompt: str
    token_count: int
    micro_consolidated: bool


class ContextCompiler:
    def __init__(
        self,
        ledger: CognitiveLedger,
        max_context_tokens: int = 30000,
        vector_store: VectorStore | None = None,
        long_term_k: int = DEFAULT_TOP_K,
    ) -> None:
        self._ledger = ledger
        self.max_context_tokens = max_context_tokens
        self.long_term_k = long_term_k
        self._vector_store = vector_store
        self._encoding = tiktoken.get_encoding("cl100k_base")

    @property
    def vector_store(self) -> VectorStore:
        if self._vector_store is None:
            self._vector_store = VectorStore()
        return self._vector_store

    def estimate_tokens(self, text: str) -> int:
        return len(self._encoding.encode(text))

    async def _retrieve_long_term(self, query_text: str) -> list[dict]:
        if not query_text.strip():
            return []
        store = self._vector_store
        try:
            if store is None:
                # Do not spin up Chroma (or download the model) before any vector exists.
                if await self._ledger.count_vectorized() == 0:
                    return []
                store = self.vector_store
            return await asyncio.to_thread(store.query, query_text, self.long_term_k)
        except Exception:
            logger.exception("long-term recall unavailable; SQL context only")
            return []

    async def _load_layers(
        self, thread_id: str, rpm_pressure_high: bool
    ) -> tuple[list[dict], list[dict], dict | None, list[dict]]:
        directives = await self._ledger.list_core_directives()
        memories = await self._ledger.list_semantic_memories()
        thread = await self._ledger.read("ledger_threads", thread_id)
        if rpm_pressure_high:
            entries = await self._ledger.list_ledger_entries(
                thread_id,
                limit=5,
                tags=("FAKTA", "TINDAKAN"),
                only_unvectorized=True,
            )
        else:
            entries = await self._ledger.list_ledger_entries(
                thread_id, limit=20, only_unvectorized=True
            )
        return directives, memories, thread, entries

    @staticmethod
    def _render_long_term(long_term: list[dict]) -> str:
        lines = []
        for row in long_term:
            metadata = row.get("metadata") or {}
            tag = metadata.get("epistemic_tag") or metadata.get("tag") or "MEMORY"
            score = metadata.get("credibility_score")
            label = f"[{tag}|{score}]" if score is not None else f"[{tag}]"
            line = f"{label} {row.get('document') or ''}"
            if metadata.get("epistemic_tag") == "COMMUNITY_OPINION" or (
                isinstance(score, (int, float)) and score < 70
            ):
                line = f"{EPISTEMIC_WARNING}\n{line}"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _render_prompt(
        directives: list[dict],
        memories: list[dict],
        thread: dict | None,
        entries: list[dict],
        long_term: list[dict],
        query_text: str,
        extra_tools: str = "",
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
        long_term_text = ContextCompiler._render_long_term(long_term)

        sections = [
            "CORE DIRECTIVES:\n" + (directive_text or "(none)"),
            "SEMANTIC MEMORY:\n" + (memory_text or "(none)"),
            "ACTIVE THREAD:\n" + thread_text,
            "LEDGER ENTRIES:\n" + (entry_text or "(none)"),
            "LONG-TERM SEMANTIC RECALL:\n" + (long_term_text or "(none)"),
        ]
        if extra_tools:
            sections.append("TOOL CATALOG & SOP:\n" + extra_tools)
        sections.append("USER QUERY:\n" + query_text)
        return "\n\n".join(sections)

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
        extra_tools: str = "",
    ) -> CompiledContext:
        directives, memories, thread, entries = await self._load_layers(
            thread_id, rpm_pressure_high
        )
        long_term = await self._retrieve_long_term(query_text)
        prompt = self._render_prompt(
            directives, memories, thread, entries, long_term, query_text, extra_tools
        )
        token_count = self.estimate_tokens(prompt)

        # Protect the active short-term context: drop recall hits from the tail first.
        while token_count > self.max_context_tokens and long_term:
            long_term = long_term[:-1]
            prompt = self._render_prompt(
                directives,
                memories,
                thread,
                entries,
                long_term,
                query_text,
                extra_tools,
            )
            token_count = self.estimate_tokens(prompt)

        micro_consolidated = False
        if token_count > self.max_context_tokens:
            micro_consolidated = await self.micro_consolidate_entries(thread_id)
            if micro_consolidated:
                entries = (
                    await self._ledger.list_ledger_entries(
                        thread_id,
                        limit=5,
                        tags=("FAKTA", "TINDAKAN"),
                        only_unvectorized=True,
                    )
                    if rpm_pressure_high
                    else await self._ledger.list_ledger_entries(
                        thread_id, limit=20, only_unvectorized=True
                    )
                )
                prompt = self._render_prompt(
                    directives,
                    memories,
                    thread,
                    entries,
                    long_term,
                    query_text,
                    extra_tools,
                )
                token_count = self.estimate_tokens(prompt)

        return CompiledContext(
            thread_id=thread_id,
            query_text=query_text,
            directives=directives,
            semantic_memories=memories,
            thread=thread,
            entries=entries,
            long_term=long_term,
            prompt=prompt,
            token_count=token_count,
            micro_consolidated=micro_consolidated,
        )