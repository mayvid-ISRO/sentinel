"""
KnowledgeRetriever — orchestrates chunking, embedding, storage and search.

This is the main entry point used by the agent and the backend API.
It encapsulates the full RAG lifecycle:

  1. ingest(paths)   — read docs, chunk, embed, persist
  2. retrieve(query, k) — embed query, FAISS search, return relevant chunks
  3. status()        — index health report
  4. clear()         — wipe the index

Usage in backend::

    retriever = KnowledgeRetriever.from_config()
    ...
    chunks = retriever.retrieve(user_task, k=3)
    context = "\\n---\\n".join(c.text for c in chunks)
    prompt += f\"\\nRelevant knowledge:\\n{context}\"

Usage as CLI::

    python -m rag.retriever ingest path/to/docs/
    python -m rag.retriever search "How do I reset the ONTAP volume?"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from rag.config import get_rag_config
from rag.embedder import Embedder, get_embedder
from rag.store import VectorStore, ChunkRef
from rag.chunker import ingest_documents, Chunk

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Aggregated result returned to the caller."""
    chunks: List[ChunkRef]
    num_indexed: int
    query: str


class KnowledgeRetriever:
    """
    Main RAG orchestration class.

    Attributes:
        embedder: Loaded sentence-transformers model.
        store:    FAISS-backed vector store.
        config:   Merged RAG configuration dict.
    """

    def __init__(
        self,
        embedder: Embedder,
        store: VectorStore,
        config: dict,
    ) -> None:
        self.embedder = embedder
        self.store = store
        self.config = config

    @classmethod
    def from_config(cls) -> "KnowledgeRetriever":
        """
        Factory: reads ``iris_config["rag"]`` and builds the retriever.

        Returns ``None`` (silently) if RAG is disabled in config.
        Callers should check ``isinstance(retriever, KnowledgeRetriever)``.
        """
        cfg = get_rag_config()
        if not cfg.get("enabled", False):
            logger.debug("RAG disabled — KnowledgeRetriever not initialised")
            return None
        embedder = get_embedder(cfg["model_name"])
        store = VectorStore(
            index_path=Path(cfg["store_path"]),
            meta_path=Path(cfg["embed_path"]).with_suffix(".json"),
            dim=embedder.dim,
        )
        return cls(embedder=embedder, store=store, config=cfg)

    # ── Ingestion ──────────────────────────────────────────────────

    def ingest(self, paths) -> int:
        """
        Index documents found at *paths* (a path or iterable of paths).

        Returns the number of chunks added to the store.
        """
        from pathlib import Path as P
        paths_list = [P(p) for p in paths] if not hasattr(paths, "__iter__") else list(paths)

        # Resolve globs and collect supported files
        files: List[Path] = []
        for p in paths_list:
            p = P(p)
            if p.is_file():
                files.append(p)
            elif p.is_dir():
                for ext in (".pdf", ".docx", ".txt", ".md", ".rst"):
                    files.extend(p.rglob(f"*{ext}"))
            # else: skip silently

        if not files:
            logger.warning("No supported documents found at %s", paths)
            return 0

        logger.info("Ingesting %d document(s)...", len(files))
        chunks = ingest_documents(
            files,
            chunk_size=self.config["chunk_size"],
            chunk_overlap=self.config["chunk_overlap"],
            max_chunks_per_doc=self.config["max_chunks_per_doc"],
        )
        if not chunks:
            return 0

        texts = [c.text for c in chunks]
        vectors = self.embedder.encode(texts)
        self.store.add(vectors, [c.to_dict() for c in chunks])
        self.store.save()
        logger.info("Ingested %d chunks from %d file(s)", len(chunks), len(files))
        return len(chunks)

    # ── Retrieval ──────────────────────────────────────────────────

    def retrieve(self, query: str, k: Optional[int] = None) -> List[ChunkRef]:
        """
        Embed *query* and return the top-k most relevant stored chunks.

        Uses ``config.top_k`` and ``config.threshold`` unless overridden.
        """
        if self.store._index.ntotal == 0:
            return []
        qk = k or self.config["top_k"]
        qth = self.config["threshold"]
        vec = self.embedder.encode_single(query)
        return self.store.search(vec, top_k=qk, threshold=qth)

    # ── Status & control ───────────────────────────────────────────

    def status(self) -> dict:
        s = self.store.status()
        s["enabled"] = True
        s["model_name"] = self.embedder.model_name
        return s

    def clear(self) -> None:
        self.store.clear()
