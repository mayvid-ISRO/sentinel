# Air-Gapped Deployment

**Feature**: Everything IRIS needs to run with zero internet access.
**Status**: **DONE** (Phase 6 complete). `scripts/build_offline_bundle.py` produces a
fully verifiable offline bundle with SHA-256 integrity checks and
platform-specific install scripts (`install.bat` / `install.sh`).

## What was fixed in Phase 0

1. **Fonts vendored** (`fonts/*.woff2`, ~60 KB total). The UI used to load
   Share Tech Mono + Syne from fonts.googleapis.com — broken offline, and
   a subtle exfiltration path. `frontend/index.html` now uses local
   `@font-face` with `url('../fonts/…')`. **Serve frontend/ from the repo
   root layout** (e.g. `python -m http.server 3000` at root, open
   `/frontend/`) or the relative font path breaks.
2. **Pinned `requirements.txt`** — every dependency with the exact version
   from the validated build env. The old `requirements` file listed 6 of
   ~18 actual dependencies (fastapi, uvicorn, requests, paramiko,
   netapp_ontap, playwright… all missing).
3. **No hardcoded endpoints** — LLM URL/model via config; defaults are
   `127.0.0.1` (fail-fast rather than phone-home).
4. **Secrets out of source** — commented API keys removed from
   `agent/llm.py` history-in-code; credentials live in
   `.iris/credentials.json` (gitignore it) or env vars.

## Offline install (current procedure)

On the internet-connected build machine:

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt          # verify everything resolves
pip download -r requirements.txt -d wheels/   # cache ALL wheels locally
```

Transfer the repo + `wheels/` to the air-gapped host (same OS/arch!), then:

```bash
python -m venv .venv && .venv\Scripts\activate
pip install --no-index --find-links wheels/ -r requirements.txt
```

Also required on the air-gapped host (not pip-installable):
- **Ollama + model files** — currently `gpt-oss:20b`. Models live under
  `~/.ollama/models` — copy the whole directory, or export via
  `ollama cp` + tar. Set `llm.url` in config.json to the Ollama host.
- **Tesseract binary** — only if you use pytesseract-based vision tools.
- **Playwright browsers** — `playwright install chromium` on the build
  machine, then copy `%LOCALAPPDATA%\ms-playwright\` to the same path on
  the target.

## Phase 6 — DONE ✅ `scripts/build_offline_bundle.py`

One command on the build machine that produces `iris_offline_bundle/`:

```
iris_offline_bundle/
├── wheels/               # pip download -r requirements.txt (skipped if present)
├── repo/                 # this codebase minus caches/db/logs
├── fonts/                # vendored web fonts (~60 KB — no Google Fonts CDN)
├── MANIFEST.json         # sha256 of every artifact + versions
└── install.sh / install.bat   # venv + pip --no-index + integrity check
```

The installer verifies hashes from MANIFEST before installing (defends
against transfer corruption). Design constraints met: single transfer, no
post-install internet, works on Windows + Linux targets, documents OS/arch
matching requirement.

### Build script internals

- `collect_wheels()` — runs `pip download` only if `wheels/` doesn't already exist; idempotent
- `copy_repo()` — `os.walk` with `_should_exclude()` pruning dirs/files at walk time
- `copy_fonts()` — copies `.woff2` files from root-level `fonts/` into bundle `fonts/`
- `build_manifest()` — walks bundle tree, computes SHA-256 per file, writes `MANIFEST.json`
- `verify_bundle()` — reads `MANIFEST.json`, recomputes all hashes, reports MISSING/HASH MISMATCH
- `_should_exclude()` — uses `pathlib.Path.match()` for glob prefixes (`*.egg-info`, `*.db`) and exact-name matching for plain prefixes (`.env`, `config.json`). Iterates all ancestor path parts so directory-name globs work on nested paths.

Excluded paths (from source repo copy):
- Directories: `.git`, `__pycache__`, `.venv`, `venv`, `logs`, `.iris`, `wheels`, `iris_offline_bundle`
- Files/masks: `config.json`, `.env`, `*.db`, `*.egg-info`, `.DS_Store`, `Thumbs.db`

### Tests

### Tests
33 new tests in `tests/test_airgap.py` (174 total), covering exclusion logic, SHA-256 hashing, repo copy, font copy, manifest building, hash verification (pass/fail/corruption/missing-file), and wheel-collection skip behaviour.

Before merging anything, check:
- No CDN/Google Fonts/external URLs in frontend (grep
  `https?://` in index.html — only relative paths + `${API}` allowed).
- No pip-install-time or runtime downloads (model downloads, package
  auto-fetch). Wheels ship in the bundle or the feature doesn't ship.
- Defaults that fail fast locally (127.0.0.1) rather than reaching out.
- New deps must be added to requirements.txt WITH a pinned version and
  re-verified with `pip download`.
