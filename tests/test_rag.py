"""
Tests for the RAG (Retrieval-Augmented Generation) knowledge store — Phase 2.

Covers:
  - rag.config.get_rag_config() merges defaults / config.json / env vars correctly
  - rag.chunker.ingest_documents handles .txt, .pdf, .docx; skips unsupported ext
  - rag.chunker.chunk_text respects chunk_size, overlap, max_chunks limits
  - rag.embedder.encode produces vectors of correct dimension
  - rag.store.VectorStore add/search round-trip; empty-store search returns []
  - rag.retriever.KnowledgeRetriever.from_config returns None when disabled
  - agent.agent._fetch_rag_context returns "" when RAG is disabled
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

# ── project root on sys.path ──────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest


# ══ helpers ═══════════════════════════════════════════════════════════════


def _write_txt(dirpath: Path, name: str, text: str) -> Path:
    p = dirpath / name
    p.write_text(text, encoding="utf-8")
    return p


def _patch_rag_cfg(monkeypatch, enabled: bool = True, **overrides):
    """Set a clean, merged RAG cfg on iris_config's DEFAULTS dict."""
    import iris_config
    rag_defaults = dict(iris_config.DEFAULTS.get("rag", {}))
    rag_defaults["enabled"] = enabled
    rag_defaults.update(overrides)
    # Rebuild DEFAULTS so the singleton picks it up on next load
    monkeypatch.setattr(iris_config, "DEFAULTS", {**iris_config.DEFAULTS, "rag": rag_defaults})
    # Force singleton reload so merged config reflects the new defaults
    iris_config._CONFIG._loaded = False


# ══ config ════════════════════════════════════════════════════════════════


class TestRagConfig:
    def test_defaults_are_valid(self):
        from rag.config import DEFAULTS
        assert DEFAULTS["enabled"] is False
        assert DEFAULTS["chunk_size"] == 300
        assert DEFAULTS["top_k"] == 3
        assert DEFAULTS["threshold"] == 0.35

    def test_get_rag_config_merged(self, monkeypatch):
        _patch_rag_cfg(monkeypatch, enabled=True, top_k=5, threshold=0.5)
        from rag.config import get_rag_config
        cfg = get_rag_config()
        assert cfg["enabled"] is True
        assert cfg["top_k"] == 5
        assert cfg["threshold"] == 0.5
        # unspecified keys fall back to defaults
        assert cfg["chunk_size"] == 300

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("IRIS_RAG_ENABLED", "true")
        monkeypatch.setenv("IRIS_RAG_TOP_K", "7")
        _patch_rag_cfg(monkeypatch, enabled=False)  # file says False
        from rag.config import get_rag_config
        cfg = get_rag_config()
        assert cfg["enabled"] is True
        assert cfg["top_k"] == 7
        monkeypatch.delenv("IRIS_RAG_ENABLED", raising=False)
        monkeypatch.delenv("IRIS_RAG_TOP_K", raising=False)


# ══ chunker ═══════════════════════════════════════════════════════════════


class TestChunker:
    def test_chunk_text_basic(self):
        from rag.chunker import chunk_text
        text = "Alpha. Bravo. Charlie. Delta. Echo." * 20
        chunks = chunk_text(text, source_file="test.txt", chunk_size=40, chunk_overlap=10)
        assert len(chunks) > 0
        for c in chunks:
            assert isinstance(c.text, str)
            assert c.source_file == "test.txt"
            assert isinstance(c.chunk_index, int)

    def test_chunk_text_max_limit(self):
        from rag.chunker import chunk_text
        text = "word " * 5000
        chunks = chunk_text(text, source_file="big.txt", chunk_size=50, max_chunks=5)
        assert len(chunks) <= 5

    def test_ingest_txt(self, tmp_path, monkeypatch):
        from rag.chunker import ingest_documents
        p = _write_txt(tmp_path, "doc.txt", "Hello world. This is a test document.")
        chunks = ingest_documents([p], chunk_size=20, chunk_overlap=5)
        assert len(chunks) > 0
        assert all(c.source_file.endswith("doc.txt") for c in chunks)

    def test_ingest_skips_unsupported(self, tmp_path, caplog):
        from rag.chunker import ingest_documents
        bad = tmp_path / "data.csv"
        bad.write_text("a,b,c\n1,2,3")
        chunks = ingest_documents([bad])
        assert chunks == []

    def test_ingest_empty_dir(self, tmp_path):
        from rag.chunker import ingest_documents
        chunks = ingest_documents([tmp_path])
        assert chunks == []


# ══ embedder (mocked) ═════════════════════════════════════════════════════


class TestEmbedder:
    def test_encode_single(self, monkeypatch):
        import sys
        fake_st = SimpleNamespace(
            SentenceTransformer=lambda name: SimpleNamespace(
                get_embedding_dimension=lambda: 3,
                encode=lambda texts, convert_to_numpy=True, show_progress_bar=False: [[0.1, 0.2, 0.3]],
            ),
        )
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
        # force re-import so the monkeypatch takes effect
        if "rag.embedder" in sys.modules:
            del sys.modules["rag.embedder"]
        from rag.embedder import Embedder
        emb = Embedder("fake-model")
        vec = emb.encode_single("hello")
        assert vec == [0.1, 0.2, 0.3]
        assert emb.dim == 3

    def test_init_failure_raises(self, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "sentence_transformers",
                            SimpleNamespace(SentenceTransformer=lambda *_: (_ for _ in ()).throw(ImportError("missing"))))
        if "rag.embedder" in sys.modules:
            del sys.modules["rag.embedder"]
        from rag.embedder import Embedder
        with pytest.raises(RuntimeError, match="Failed to load embedding model"):
            Embedder("missing-model")


# ══ vector store ══════════════════════════════════════════════════════════


class TestVectorStore:
    def test_round_trip(self, tmp_path, monkeypatch):
        import faiss
        import numpy as np
        from rag.store import VectorStore
        idx_path = tmp_path / "k.faiss"
        meta_path = tmp_path / "k_meta.json"
        store = VectorStore(idx_path, meta_path, dim=4)
        assert store.status()["vectors"] == 0

        chunks = [
            {"text": "a b c", "source_file": "f1.txt", "chunk_index": 0},
            {"text": "d e f", "source_file": "f1.txt", "chunk_index": 1},
        ]
        vectors = [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]]
        store.add(vectors, chunks)
        store.save()

        # reload
        store2 = VectorStore(idx_path, meta_path, dim=4)
        assert store2.status()["vectors"] == 2
        assert len(store2._chunks) == 2

    def test_empty_search_returns_none(self, tmp_path):
        from rag.store import VectorStore
        store = VectorStore(tmp_path / "i.faiss", tmp_path / "m.json", dim=3)
        refs = store.search([0.1, 0.2, 0.3], top_k=3, threshold=0.5)
        assert refs == []

    def test_search_filters_by_threshold(self, tmp_path):
        import numpy as np
        from rag.store import VectorStore
        idx_path = tmp_path / "i2.faiss"
        meta_path = tmp_path / "m2.json"
        store = VectorStore(idx_path, meta_path, dim=2)
        chunks = [{"text": "same", "source_file": "x.txt", "chunk_index": 0}]
        store.add([[1.0, 0.0]], chunks)
        store.save()
        # very similar vector → should pass low threshold
        refs = store.search([1.0, 0.01], top_k=1, threshold=0.0)
        assert len(refs) == 1
        # very different vector → should be filtered out by high threshold
        refs2 = store.search([0.0, 1.0], top_k=1, threshold=0.99)
        assert len(refs2) == 0

    def test_clear_resets(self, tmp_path):
        from rag.store import VectorStore
        store = VectorStore(tmp_path / "i3.faiss", tmp_path / "m3.json", dim=2)
        store.add([[1.0, 0.0]], [{"text": "t", "source_file": "x", "chunk_index": 0}])
        store.clear()
        assert store.status()["vectors"] == 0
        assert store._chunks == []


# ══ retriever ═════════════════════════════════════════════════════════════


class TestKnowledgeRetriever:
    def test_disabled_returns_none(self, monkeypatch):
        _patch_rag_cfg(monkeypatch, enabled=False)
        # also clear any IRIS_RAG_ENABLED env var that might flip it back on
        monkeypatch.delenv("IRIS_RAG_ENABLED", raising=False)
        from rag.retriever import KnowledgeRetriever
        r = KnowledgeRetriever.from_config()
        assert r is None

    def test_enabled_returns_instance(self, monkeypatch, tmp_path):
        import iris_config
        from rag.retriever import KnowledgeRetriever
        cfg = dict(iris_config.DEFAULTS.get("rag", {}))
        cfg["enabled"] = True
        cfg["store_path"] = str(tmp_path / "r.faiss")
        cfg["embed_path"] = str(tmp_path / "r_meta")
        monkeypatch.setattr(iris_config, "DEFAULTS", {**iris_config.DEFAULTS, "rag": cfg})
        iris_config._CONFIG._loaded = False
        monkeypatch.delenv("IRIS_RAG_ENABLED", raising=False)
        fake_emb = SimpleNamespace(
            dim=3,
            encode_single=lambda x: [0.1, 0.2, 0.3],
            get_embedding_dimension=lambda: 3,
        )
        import sys as _sys
        _fake_st = SimpleNamespace(
            SentenceTransformer=lambda name: fake_emb,
        )
        monkeypatch.setitem(_sys.modules, "sentence_transformers", _fake_st)
        # clear singleton so from_config re-creates with our fake
        import rag.embedder
        rag.embedder.reset_embedder_singleton()
        r = KnowledgeRetriever.from_config()
        assert isinstance(r, KnowledgeRetriever)
        assert r.retrieve("anything") == []  # empty store


# ══ agent integration ═════════════════════════════════════════════════════


class TestAgentRagIntegration:
    def test_fetch_rag_context_disabled(self, monkeypatch):
        _patch_rag_cfg(monkeypatch, enabled=False)
        from agent.agent import _fetch_rag_context
        result = _fetch_rag_context("some task")
        assert result == ""

    def test_fetch_rag_context_error_handled(self, monkeypatch):
        _patch_rag_cfg(monkeypatch, enabled=True)
        import rag.retriever as mod
        monkeypatch.setattr(mod, "KnowledgeRetriever",
                            SimpleNamespace(from_config=lambda: (_ for _ in ()).throw(RuntimeError("boom"))))
        from agent.agent import _fetch_rag_context
        result = _fetch_rag_context("test")
        assert result == ""
