"""Unit tests for the local ChromaDB wrapper (deterministic stub embeddings)."""

import pytest

chromadb = pytest.importorskip("chromadb")
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings  # noqa: E402

from core.vector_store import VectorStore  # noqa: E402


class HashEmbedding(EmbeddingFunction[Documents]):
    """Tiny deterministic embedding so tests never download all-MiniLM."""

    def __init__(self) -> None:
        pass

    def __call__(self, input: Documents) -> Embeddings:
        return [
            [float((ord(text[0]) + index) % 7 + 1) for index in range(3)]
            if text
            else [1.0, 1.0, 1.0]
            for text in input
        ]

    @staticmethod
    def name() -> str:
        return "hash-embedding"

    @staticmethod
    def build_from_config(config) -> "HashEmbedding":
        return HashEmbedding()

    def get_config(self) -> dict:
        return {}


def test_upsert_and_query_roundtrip_keeps_sql_id_and_metadata(tmp_path):
    store = VectorStore(tmp_path / "chroma", embedding_function=HashEmbedding())
    metadata = {
        "source": "atelier",
        "epistemic_tag": "VERIFIED_FACT",
        "credibility_score": 95,
    }

    store.upsert(
        ids=["sql-uuid-1"],
        documents=["Python asyncio patterns"],
        metadatas=[metadata],
    )

    hits = store.query("Python asyncio", k=3)

    assert [hit["id"] for hit in hits] == ["sql-uuid-1"]
    assert hits[0]["metadata"] == metadata
    assert hits[0]["document"] == "Python asyncio patterns"
    assert store.count() == 1


def test_empty_store_and_blank_query_do_no_work(tmp_path):
    store = VectorStore(tmp_path / "chroma", embedding_function=HashEmbedding())

    assert store.query("anything") == []
    store.upsert(ids=[], documents=[], metadatas=[])
    store.upsert(
        ids=["id-1"], documents=["hello"], metadatas=[{"source": "ledger"}]
    )
    assert store.query("   ") == []


def test_upsert_is_idempotent_on_repeated_ids(tmp_path):
    store = VectorStore(tmp_path / "chroma", embedding_function=HashEmbedding())
    store.upsert(ids=["id-1"], documents=["first"], metadatas=[{"source": "ledger"}])
    store.upsert(
        ids=["id-1"],
        documents=["second version"],
        metadatas=[{"source": "ledger"}],
    )

    assert store.count() == 1
    assert store.query("second version", k=1)[0]["document"] == "second version"


def test_default_store_path_is_local_data_directory():
    store = VectorStore()

    assert store.path.endswith("/data/chroma_db")
