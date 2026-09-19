"""
FAISS-backed vector store — persists and queries embedded chunks.

Index file layout::
  .iris/knowledge.faiss         — FAISS flat L2 index
  .iris/knowledge_chunks.json   — chunk metadata (text, source_file, idx)

On load the store reads both files; if either is missing a fresh empty
index is created.  All mutations are synchronous (single-process RAG).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ChunkRef:
    """Lightweight reference returned by search queries."""
    chunk_index: int
    source_file: str
    text: str
    score: float   # 1 - cosine_sim (FAISS returns distance; we invert)

    def to_dict(self) -> dict:
        return asdict(self)


class VectorStore:
    """FAISS flat index with JSON-serialised chunk metadata."""

    def __init__(
        self,
        index_path: Path,
        meta_path: Path,
        dim: int,
    ) -> None:
        self.index_path = index_path
        self.meta_path = meta_path
        self.dim = dim
        self._index = self._load_index(dim)
        self._chunks: List[dict] = self._load_chunks()

    # ── Persistence ────────────────────────────────────────────────────

    def _load_index(self, dim: int):
        import faiss
        if self.index_path.exists():
            logger.info("Loading FAISS index from %s", self.index_path)
            return faiss.read_index(str(self.index_path))
        logger.info("Creating new FAISS InnerProduct index (dim=%d)", dim)
        # InnerProduct index stores dot-products directly; since we store
        # normalized vectors, the dot-product equals cosine similarity.
        return faiss.IndexFlatIP(dim)

    def _load_chunks(self) -> List[dict]:
        if self.meta_path.exists():
            try:
                data = json.loads(self.meta_path.read_text(encoding="utf-8"))
                return data.get("chunks", [])
            except Exception as exc:
                logger.warning("Could not parse chunk metadata (%s); starting fresh", exc)
        return []

    def save(self) -> None:
        """Persist the current index and metadata to disk."""
        import faiss
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(self.index_path))
        self.meta_path.parent.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(
            json.dumps({"chunks": self._chunks}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(
            "Saved index: %d vectors, dim=%d | chunks: %d",
            self._index.ntotal, self.dim, len(self._chunks),
        )

    # ── Ingestion ──────────────────────────────────────────────────

    def add(self, vectors: List[List[float]], chunks: List[dict]) -> None:
        """
        Append *vectors* and their corresponding *chunk* metadata.

        Vectors are L2-normalised on insertion so the InnerProduct index
        returns true cosine similarity scores.
        """
        if not vectors:
            return
        import faiss
        import numpy as np
        arr = np.asarray(vectors, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        # L2-normalise rows so inner-product == cosine similarity
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        arr = arr / norms
        start_idx = self._index.ntotal
        self._index.add(arr)
        for i, chunk in enumerate(chunks):
            chunk["vector_index"] = start_idx + i
            self._chunks.append(chunk)

    # ── Query ──────────────────────────────────────────────────────

    def search(
        self,
        query_vector: List[float],
        top_k: int = 3,
        threshold: float = 0.35,
    ) -> List[ChunkRef]:
        """
        Search the store for the *top_k* nearest neighbours to *query_vector*.

        Returns ``ChunkRef`` entries sorted by relevance (lowest distance
        first).  Results below *threshold* (converted to FAISS L2 distance)
        are filtered out.
        """
        if self._index.ntotal == 0:
            return []

        import faiss
        import numpy as np
        q = np.asarray([query_vector], dtype=np.float32)
        # Normalise query so dot-product == cosine similarity
        q_norm = np.linalg.norm(q)
        if q_norm > 0:
            q = q / q_norm
        # For InnerProduct index, scores are cosine sims in [−1, 1].
        # Filter below threshold directly.
        scores, indices = self._index.search(q, min(top_k, self._index.ntotal))

        refs: List[ChunkRef] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._chunks):
                continue
            if float(score) < threshold:
                continue
            chunk = self._chunks[idx]
            refs.append(ChunkRef(
                chunk_index=int(chunk.get("chunk_index", idx)),
                source_file=chunk["source_file"],
                text=chunk["text"][:300],          # truncate for UI display
                score=round(float(score), 4),
            ))
        return refs

    # ── Status ─────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "vectors": self._index.ntotal,
            "chunks": len(self._chunks),
            "dim": self.dim,
            "index_path": str(self.index_path),
            "meta_path": str(self.meta_path),
        }

    def clear(self) -> None:
        """Remove all vectors and metadata (keeps the index structure)."""
        import faiss
        self._index = faiss.IndexFlatL2(self.dim)
        self._chunks = []
        self.save()
        logger.info("Vector store cleared")
