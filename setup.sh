#!/usr/bin/env bash
# ── IRIS ── One-command setup + launch ──────────────────────────────────────
# Works on Linux, macOS, and WSL/Git-Bash on Windows.
# Requires: Python 3.12+
#
# Usage:
#   ./setup.sh              # install, then show startup options
#   ./setup.sh start        # install + run the server in foreground
#   ./setup.sh test         # run the full test suite
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()    { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

# ── Checks ───────────────────────────────────────────────────────────────────

check_python() {
  if ! command -v python3 &>/dev/null; then
    warn "python3 not found — trying 'python'"
    if ! command -v python &>/dev/null; then
      fail "Python 3.12+ is required. Install it first:"
      fail "  Ubuntu/Debian: sudo apt install python3.12 python3.12-venv"
      fail "  macOS:         brew install python@3.12"
      fail "  Windows:       https://www.python.org/downloads/"
    fi
    PY=python
  else
    PY=python3
  fi

  ver=$("$PY" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
  major=${ver%.*}; minor=${ver#*.}
  if (( major < 3 || (major == 3 && minor < 12) )); then
    fail "Python 3.12+ required (found $ver)."
  fi
  ok "Python $ver"
}

check_venv() {
  if [[ -d .venv ]]; then
    ok ".venv already exists — skipping creation"
    return 0
  fi
  info "Creating virtual environment..."
  "$PY" -m venv .venv || fail "venv module missing — try: $PY -m ensurepip"
  ok "Virtual environment created at .venv/"
}

activate() {
  # shellcheck disable=SC1091
  source .venv/bin/activate
}

install_deps() {
  if [[ -f .venv/INSTALL_COMPLETE ]]; then
    ok "Dependencies already installed — skipping"
    return 0
  fi
  info "Installing dependencies from requirements.txt..."
  # Upgrade pip inside venv first
  "$PY" -m pip install --upgrade pip --quiet
  "$PY" -m pip install -r requirements.txt --quiet \
    || warn "Some packages may have failed. Check output above."
  touch .venv/INSTALL_COMPLETE
  ok "Dependencies installed"
}

setup_config() {
  if [[ ! -f config.json ]]; then
    if [[ -f config.example.json ]]; then
      info "No config.json found — copying from config.example.json"
      cp config.example.json config.json
      ok "Created config.json — edit it to set your LLM URL etc."
    else
      warn "config.example.json not found — running with built-in defaults"
    fi
  else
    ok "config.json already present"
  fi
}

# ── Actions ──────────────────────────────────────────────────────────────────

do_setup() {
  check_python
  check_venv
  activate
  install_deps
  setup_config
  echo
  ok "Setup complete!"
  echo
  echo "  ${CYAN}To start the server:${NC}"
  echo "    ./setup.sh start"
  echo
  echo "  ${CYAN}To run tests:${NC}"
  echo "    ./setup.sh test"
  echo
  echo "  ${CYAN}Or manually:${NC}"
  echo "    source .venv/bin/activate"
  echo "    uvicorn backend.main:app --host 0.0.0.0 --port 8000"
  echo
}

do_start() {
  check_python
  check_venv
  activate
  install_deps
  setup_config
  echo
  info "Starting IRIS backend on http://localhost:8000 ..."
  echo "Press Ctrl+C to stop."
  echo
  exec uvicorn backend.main:app --host 0.0.0.0 --port 8000
}

do_test() {
  check_python
  check_venv
  activate
  install_deps
  info "Running tests..."
  exec "$PY" -m pytest tests/ -q --tb=short
}

# ── Entry point ──────────────────────────────────────────────────────────────

cmd="${1:-setup}"
case "$cmd" in
  start)  do_start  ;;
  test)   do_test   ;;
  setup|*) do_setup ;;
esac
