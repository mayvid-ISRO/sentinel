"""
IRIS RAG (Retrieval-Augmented Generation) Knowledge Store
=========================================================
Phase 2 feature — see docs/RAG.md for full architecture.

Local embeddings powered by sentence-transformers + FAISS vector store.
All weights are loaded from the local model cache; no internet required
after the initial model download.

Public API:
  from rag.retriever import KnowledgeRetriever
  retriever = KnowledgeRetriever.from_config()   # reads iris_config["rag"]
  results = retriever.retrieve(query, k=3)       # → list[ChunkRef]
  retriever.ingest(filepath_or_dir)              # index new documents
  retriever.status()                             # → dict with counts
"""

from rag.config import get_rag_config
from rag.embedder import Embedder
from rag.store import VectorStore
from rag.retriever import KnowledgeRetriever
from rag.chunker import ingest_documents, Chunk

__all__ = [
    "KnowledgeRetriever",
    "Embedder",
    "VectorStore",
    "ingest_documents",
    "Chunk",
    "get_rag_config",
]
