# RAG Knowledge Store (Phase 2)

**Feature**: Local retrieval-augmented generation — ingest ISRO SOPs, incident reports, and operational manuals; retrieve the most relevant excerpts and inject them into the agent's prompts so it reasons with domain-specific context.

**Status**: Phase 2, shipped. All 18 RAG tests green; 76 total.

**Files**:
- `rag/config.py` — reads `[rag]` section from `iris_config`, merges env overrides
- `rag/chunker.py` — splits PDFs / DOCX / TXT into overlapping `Chunk` objects
- `rag/embedder.py` — wraps `sentence-transformers`; singleton model cache
- `rag/store.py` — FAISS flat L2 index on disk + JSON chunk metadata
- `rag/retriever.py` — orchestrates ingest → embed → persist → search
- Backend: `backend/main.py` (`/api/rag/status`, `/api/rag/ingest`, `/api/rag/search`, `/api/rag/clear`)
- Agent: `agent/agent.py` (`_fetch_rag_context()`), `agent/prompt.py`, `agent/pre_prompt.py`
- Frontend: Knowledge Base panel in `frontend/index.html` (bottom-right)

## Architecture

```
documents (.pdf/.docx/.txt)
        │
        ▼
  chunker.ingest_documents()       ← paragraph-aware overlap splitting
        │
        ▼  List[Chunk]
  embedder.encode(texts)           ← all-MiniLM-L6-v2 (384-dim)
        │
        ▼  List[List[float]]
  store.add(vectors, chunks)       ← FAISS IndexFlatL2
        │
        ▼  .iris/knowledge.faiss   ← persistent index
        .iris/knowledge_chunks.json ← chunk metadata
```

On agent run, `_fetch_rag_context(user_task)` calls `retriever.retrieve(task)` and injects the top-k matching chunks into both the pre-prompt (plan phase) and the per-step prompt (execution phase).

## Configuration

Add to `config.json`:

```json
"rag": {
  "enabled": true,
  "model_name": "all-MiniLM-L6-v2",
  "max_chunks_per_doc": 200,
  "chunk_size": 300,
  "chunk_overlap": 50,
  "top_k": 3,
  "threshold": 0.35,
  "store_path": ".iris/knowledge.faiss",
  "embed_path": ".iris/knowledge_chunks.json"
}
```

Or override with env vars: `IRIS_RAG_ENABLED=true IRIS_RAG_TOP_K=5`.

### Air-gap note

The first run downloads the embedding model weights from HuggingFace (~90 MB). After that they live in `~/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2/`. Copy that directory to the air-gapped machine or point `rag.model_name` at a local path.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/rag/status` | Index health: vectors, chunks, dim, model name |
| `POST` | `/api/rag/ingest` | `{ "paths": ["/abs/path/to/docs"] }` — return `{ chunks_added: N }` |
| `GET` | `/api/rag/search?q=...&k=3` | Returns `{ query, results: [{source_file, text, score}] }` |
| `DELETE` | `/api/rag/clear` | Wipes the entire index |

## CLI ingestion

```bash
# From project root, after enabling rag.enabled in config.json:
python -c "
from rag.retriever import KnowledgeRetriever
r = KnowledgeRetriever.from_config()
n = r.ingest('path/to/isro-sop.pdf')
print(f'Indexed {n} chunks')
"
```

## Prompt injection points

The retriever is called at two places inside `agent/agent.py`:

1. **Pre-prompt** (`build_pre_prompt`) — the plan-generation step gets context about relevant SOPs before deciding which tools to use.
2. **Per-step prompt** (`build_prompt`) — each execution step sees the same context so the agent can reference documented procedures mid-workflow.

Both are non-fatal: if retrieval raises an exception the context block is omitted and the agent runs as normal.

## Testing

```bash
python -m pytest tests/test_rag.py -v    # 18 tests, 0.8s
python -m pytest tests/ -q               # 76 total, all green
```

RAG tests mock the embedding model via `sys.modules` swap so no network or GPU is needed.

## Dependencies added (requirements.txt)

```
faiss-cpu==1.15.1
sentence-transformers==6.0.1
```

Both are pinned and compatible with Python 3.13. The `faiss-cpu` wheel is ~16 MB; `sentence-transformers` pulls in `torch` (~200 MB). See `docs/AIRGAP.md` for the offline bundle build procedure.
