# Sentinel — Agentic IT Infrastructure Management

An agentic, event-driven system for managing air-gapped IT infrastructure.  
Built for ISRO environments where internet access is unavailable.

**One command to run:** `./setup.sh start` (Linux/macOS/WSL) or `setup.bat start` (Windows)

---

## Quick Start

```bash
# Clone
git clone https://github.com/mayvid-ISRO/sentinel.git
cd sentinel

# One-command setup + launch (auto-installs everything)
./setup.sh start          # Linux / macOS / WSL
setup.bat start           # Windows (double-click or CMD)

# Open in browser
open http://localhost:8000
```

That's it. The server starts on port 8000 with the web UI, agent engine, and event subsystem all running.

---

## What Sentinel Does

| Capability | Description |
|---|---|
| **Agentic Task Execution** | LLM-planned multi-step automation via a tool registry (40+ tools) |
| **Air-Gapped by Design** | Works fully offline — no internet, no external APIs required at runtime |
| **RBAC & Approval Gates** | Destructive tools require human sign-off before execution |
| **Live Streaming UI** | WebSocket-powered real-time step-by-step progress in the browser |
| **Event-Driven Subsystem** | Syslog ingester + metric watcher + anomaly detector + rule engine |
| **Local RAG Knowledge Base** | Ingest documents, embed locally with FAISS, retrieve context for agents |
| **Fleet Management** | Remote node control via SSH with security policies |
| **Offline Bundle** | `scripts/build_offline_bundle.py` produces a self-contained distributable |

---

## Prerequisites

- **Python 3.12+** (3.13 recommended)
- A local LLM endpoint — Ollama is the default (`http://127.0.0.1:11434`)

No other dependencies are needed before running `setup.sh`.

---

## Configuration

```bash
cp config.example.json config.json
# Edit config.json — set llm.url to your Ollama (or any OpenAI-compatible) endpoint
```

Secrets go in environment variables or `tools/credentials.py` — **never** in `config.json`.

---

## Project Structure

```
sentinel/
├── agent/              # Agent loop, LLM wrapper, prompts, redaction
│   ├── agent.py        # Main run_agent() entry point
│   ├── dispatcher.py   # Tool dispatch with RBAC gate
│   ├── llm.py          # LLM API client (Ollama / OpenAI-compatible)
│   └── prompt.py       # System / task prompts
├── backend/
│   └── main.py         # FastAPI app: REST API + WebSocket + static UI
├── tools/              # Tool registry
│   ├── base.py         # Tool base class + run(args) contract
│   ├── browser/        # Playwright browser automation
│   ├── input/          # Keyboard / mouse simulation (PyAutoGUI)
│   ├── system/         # OS-level tools (disk, memory, process, package)
│   ├── vision/         # Screenshots, OCR (Tesseract)
│   ├── netapp/         # NetApp ONTAP storage management
│   ├── web/            # HTTP/API tools
│   └── credentials.py  # Secure credential store
├── events/             # Event subsystem
│   ├── adapters/       # Syslog UDP, metrics polling
│   ├── rules.json      # Alert rules (fire-and-forget or trigger tasks)
│   └── engine.py       # Rule evaluation engine
├── auth/               # RBAC, approvals, middleware
├── anomalies/          # Statistical anomaly detection (z-score baselines)
├── fleet/              # Remote node management
├── rag/                # Local RAG (FAISS + sentence-transformers)
├── frontend/
│   └── index.html      # Single-file Vue-free SPA (no build step)
├── scripts/
│   └── build_offline_bundle.py  # Produces self-contained deployable zip
├── tests/              # 174 pytest tests, 0 network/LLM dependencies
├── setup.sh            # Cross-platform setup (Linux/macOS/WSL)
├── setup.bat           # Windows setup launcher
└── requirements.txt    # All pinned dependencies
```

---

## Available Commands

```bash
# Setup (creates venv, installs deps, copies config template)
./setup.sh
setup.bat

# Start the server (default: localhost:8000)
./setup.sh start
setup.bat start

# Run tests
./setup.sh test
setup.bat test

# CLI agent mode (no UI)
python main.py "open notepad and type hello"

# Docker
docker compose up --build
```

---

## Docker

```bash
docker compose up --build
# → http://localhost:8000
```

The Dockerfile uses a multi-stage build so the final image is small and contains no build tools.

---

## Air-Gapped Deployment

For completely offline environments, generate a self-contained bundle from an internet-connected machine:

```bash
python scripts/build_offline_bundle.py --out dist/sentinel-offline.zip
```

This produces a ZIP containing:
- Full Python source tree
- All wheel files (no pip download needed)
- Pre-installed virtualenv
- MANIFEST.json with SHA-256 verification hashes

Transfer via USB and run `setup.bat` / `setup.sh` inside the extracted directory. No internet required.

---

## Testing

```bash
python -m pytest tests/ -q
# 174 passed — all tests are offline and deterministic
```

---

## Architecture

See [PROJECT_ARCHITECTURE.md](PROJECT_ARCHITECTURE.md) for the full design document, or per-feature docs in [docs/](docs/):

| Doc | Topic |
|-----|-------|
| [docs/AGENT_LOOP.md](docs/AGENT_LOOP.md) | Two-phase plan → act loop |
| [docs/AIRGAP.md](docs/AIRGAP.md) | Air-gap strategy & offline bundle |
| [docs/ANOMALIES.md](docs/ANOMALIES.md) | Z-score anomaly detection |
| [docs/EVENTS.md](docs/EVENTS.md) | Event subsystem architecture |
| [docs/RAG.md](docs/RAG.md) | Local knowledge retrieval |
| [docs/RBAC.md](docs/RBAC.md) | Role-based access control |
| [docs/SAFETY.md](docs/SAFETY.md) | Safety policies & redaction |
| [docs/TESTING.md](docs/TESTING.md) | Test suite guide |

---

## License

MIT — see [LICENSE](LICENSE) for details.
