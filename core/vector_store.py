"""Local ChromaDB long-term memory: persistent, dependency-lazy vector search.

Vectors live in ./data/chroma_db with Chroma's built-in all-MiniLM-L6-v2 ONNX
embedding function. No external embedding API is ever called; the model weights
are downloaded once into the Chroma cache on first use, then run locally.

If ./data/chroma_db is lost or moved without its files, ContextCompiler sees
`count_vectorized() > 0` but an empty collection and calls
`reset_vectorized_flags()` so the next Sleep Mode cycle re-embeds everything.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_CHROMA_PATH = "./data/chroma_db"
COLLECTION_NAME = "long_term_memory"
DEFAULT_TOP_K = 5


class VectorStore:
    """Thin wrapper so memory_manager and context_compiler share one collection."""

    def __init__(
        self,
        path: str | Path = DEFAULT_CHROMA_PATH,
        *,
        client=None,
        embedding_function=None,
    ) -> None:
        self.path = str(Path(path).resolve())
        self._client = client
        self._embedding_function = embedding_function
        self._collection = None

    def _collection_handle(self):
        if self._collection is None:
            import chromadb
            from chromadb.utils.embedding_functions import (
                DefaultEmbeddingFunction,
            )

            Path(self.path).mkdir(parents=True, exist_ok=True)
            if self._client is None:
                self._client = chromadb.PersistentClient(path=self.path)
            self._collection = self._client.get_or_create_collection(
                COLLECTION_NAME,
                embedding_function=(
                    self._embedding_function or DefaultEmbeddingFunction()
                ),
            )
        return self._collection

    def upsert(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        if not ids:
            return
        self._collection_handle().upsert(
            ids=ids, documents=documents, metadatas=metadatas
        )

    def query(self, text: str, k: int = DEFAULT_TOP_K) -> list[dict]:
        collection = self._collection_handle()
        if not text.strip() or collection.count() == 0:
            return []
        result = collection.query(
            query_texts=[text],
            n_results=k,
            include=["documents", "metadatas", "distances"],
        )
        ids = result.get("ids") or [[]]
        documents = result.get("documents") or [[]]
        metadatas = result.get("metadatas") or [[]]
        distances = result.get("distances") or [[]]
        return [
            {
                "id": row_id,
                "document": documents[0][index],
                "metadata": metadatas[0][index] or {},
                "distance": distances[0][index],
            }
            for index, row_id in enumerate(ids[0])
        ]

    def count(self) -> int:
        return int(self._collection_handle().count())
