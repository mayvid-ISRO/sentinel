"""
RAG configuration — reads from iris_config under the 'rag' section.

Config schema (mirrors config.example.json [rag]):
  enabled            bool   — flag to activate retrieval (default False)
  model_name         str    — HuggingFace model id for embeddings
  max_chunks_per_doc int    — cap to prevent memory blow-up
  chunk_size         int    — tokens per chunk (soft, ~chars)
  chunk_overlap      int    — overlap between adjacent chunks
  top_k              int    — how many chunks to retrieve per query
  threshold          float  — cosine-similarity cutoff (drop below this)
  store_path         str    — path to FAISS index file (.faiss)
  embed_path         str    — path to embedding cache dir

Precedence (highest wins):
  1. Environment variables   IRIS_RAG_*
  2. config.json             [rag] section
  3. Defaults below
"""

from __future__ import annotations

from typing import Any, Dict

from iris_config import get as cfg_get

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "model_name": "all-MiniLM-L6-v2",
    "max_chunks_per_doc": 200,
    "chunk_size": 300,          # chars — soft boundary at newline
    "chunk_overlap": 50,
    "top_k": 3,
    "threshold": 0.35,          # min cosine similarity to include a chunk
    "store_path": ".iris/knowledge.faiss",
    "embed_path": ".iris/knowledge_embeddings",
}


def get_rag_config() -> Dict[str, Any]:
    """Return the merged RAG configuration dict."""
    raw = cfg_get("rag", default={}) or {}
    merged = _deep_merge(DEFAULTS, raw)
    _apply_env_overrides(merged)
    return merged


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _apply_env_overrides(cfg: Dict[str, Any]) -> None:
    """Override scalar leaf values from IRIS_RAG_* env vars."""
    import os
    prefix = "IRIS_RAG_"
    for env_name, raw in os.environ.items():
        if not env_name.startswith(prefix):
            continue
        key = env_name[len(prefix):].lower()
        if key not in cfg:
            continue
        current = cfg[key]
        try:
            if isinstance(current, bool):
                cfg[key] = raw.lower() in ("1", "true", "yes")
            elif isinstance(current, int):
                cfg[key] = int(raw)
            elif isinstance(current, float):
                cfg[key] = float(raw)
            else:
                cfg[key] = raw
        except (ValueError, TypeError):
            pass
