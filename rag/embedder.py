"""
Embedding service — wraps sentence-transformers for local vector generation.

The model is loaded once and cached; subsequent calls reuse the same
inference pipeline. On first use the model weights are downloaded from
HuggingFace (requires internet); after that they are served from the
local cache (``~/.cache/huggingface/``).

For air-gapped deployment, copy the downloaded model directory into the
project's ``models/`` folder and set ``rag.model_name`` to the local path
(e.g. ``models/all-MiniLM-L6-v2``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# Singleton pattern — load once per process.
_model_instance = None


def reset_embedder_singleton() -> None:
    """Clear the cached embedder. Intended for tests only."""
    global _model_instance
    _model_instance = None


def get_embedder(model_name: str) -> "Embedder":
    """Return the global Embedder instance (created lazily)."""
    global _model_instance
    if _model_instance is None:
        _model_instance = Embedder(model_name)
    return _model_instance


class Embedder:
    """
    Local embedding pipeline backed by sentence-transformers.

    Attributes:
        model_name: HuggingFace model id or local filesystem path.
        model: The loaded SentenceTransformer instance.
        dim: Embedding dimension (used when building the FAISS index).
    """

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        logger.info("Loading embedding model: %s", model_name)
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(model_name)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load embedding model '{model_name}'. "
                f"Check that the model exists locally or that network access "
                f"is available. Error: {exc}"
            ) from exc
        self.dim = self._model.get_embedding_dimension()
        logger.info("Model loaded — embedding dim = %d", self.dim)

    @property
    def model(self):
        return self._model

    def encode(self, texts: List[str]) -> List[List[float]]:
        """
        Encode a batch of texts into float32 embedding vectors.

        Returns a list of lists (one per input text).
        """
        import numpy as np
        vectors = self._model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        # Ensure we always return Python lists (JSON-safe for persistence)
        if isinstance(vectors, np.ndarray):
            return vectors.tolist()
        return [v.tolist() if hasattr(v, "tolist") else list(v) for v in vectors]

    def encode_single(self, text: str) -> List[float]:
        """Encode a single text string; convenience wrapper."""
        vecs = self.encode([text])
        return vecs[0]
