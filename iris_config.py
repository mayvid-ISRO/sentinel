"""
IRIS configuration loader.
==========================
Single source of truth for all runtime settings. Every module reads config
through get_config() — no hardcoded URLs, models, or ports in code.

Precedence (highest wins):
  1. Environment variables        (IRIS_LLM_URL, IRIS_LLM_MODEL, ...)
  2. config.json in project root  (copy config.example.json to start)
  3. Built-in defaults below      (always safe offline values)

Secrets (tokens, passwords) are NEVER stored in config.json — they come
from environment variables or the credential store (tools/credentials.py).
See docs/CONFIG.md for the full schema.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config.json"

# Defaults — mirror config.example.json. Keep these valid for a fully
# offline environment.
DEFAULTS: Dict[str, Any] = {
    "llm": {
        "url": "http://127.0.0.1:11434/api/generate",
        "model": "gpt-oss:20b",
        "timeout_seconds": 180,
        "max_retries": 2,
        "retry_backoff_seconds": 2.0,
    },
    "agent": {
        "max_steps": 10,
        "error_budget": 3,          # failed tool calls tolerated before abort
        "step_delay_seconds": 0.5,
    },
    "backend": {
        "host": "127.0.0.1",
        "port": 8000,
        "cors_origins": ["http://localhost:3000", "http://127.0.0.1:3000"],
    },
    "security": {
        # Regex patterns whose matches are replaced in logs / DB / UI.
        "redact_patterns": [
            r"(password|passwd|api_pass|secret|token)\s*[:=]\s*(\S+)",
        ],
        "redact_placeholder": "«REDACTED»",
    },
    "credentials": {
        # Written by tools/credentials.py. NOT a config-file setting.
        "store_path": ".iris/credentials.json",
    },
    "nodes": [],                     # [{name, host, port, token_env}]
    "auth": {
        "enabled": False,              # set true to enforce JWT auth
        "secret": "",                  # auto-generated on first run if empty
        "token_expire_minutes": 480,
    },
    "rbac": {
        # Override tool risk tiers here; keys are tool names
        "tool_risk_overrides": {},
    },
    "rag": {
        "enabled": False,
        "model_name": "all-MiniLM-L6-v2",
        "max_chunks_per_doc": 200,
        "chunk_size": 300,
        "chunk_overlap": 50,
        "top_k": 3,
        "threshold": 0.35,
        "store_path": ".iris/knowledge.faiss",
        "embed_path": ".iris/knowledge_chunks.json",
    },
    "events": {
        "enabled": False,            # flip to true after Phase 1 review
        "mode": "shadow",            # shadow | manual | auto
        "bus_buffer": 1000,
        "syslog": {"enabled": False, "listen": "0.0.0.0", "port": 5514},
        "metrics": {
            "enabled": False,
            "interval_seconds": 30,
            "cpu_threshold": 90.0,
            "memory_threshold": 90.0,
            "disk_threshold": 90.0,
        },
        "watcher": {"enabled": False, "paths": []},
        "scheduler": {"enabled": False, "tasks": []},
        "rules_file": "events/rules.json",
        "anomaly": {
            "enabled": False,
            "window_size": 100,
            "z_threshold": 2.5,
            "min_samples": 10,
            "cooldown_seconds": 300,
            "store_path": ".iris/baselines.json",
        },
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge override into base (override wins)."""
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _apply_env(config: Dict[str, Any]) -> None:
    """Override selected values from IRIS_* environment variables.

    Supports scalar leaves only, addressed as IRIS_<SECTION>_<KEY> in caps,
    e.g. IRIS_LLM_URL / IRIS_LLM_MODEL / IRIS_BACKEND_PORT.
    """
    prefix = "IRIS_"
    for env_name, raw in os.environ.items():
        if not env_name.startswith(prefix) or "_" not in env_name[len(prefix):]:
            continue
        body = env_name[len(prefix):]
        # Longest split that lands on an existing section wins.
        parts = body.lower().split("_")
        for i in range(1, len(parts)):
            section, key = "_".join(parts[:i]), "_".join(parts[i:])
            if section in config and not isinstance(config[section], dict):
                continue
            if isinstance(config.get(section), dict) and key in config[section]:
                current = config[section][key]
                try:
                    if isinstance(current, bool):
                        config[section][key] = raw.lower() in ("1", "true", "yes")
                    elif isinstance(current, int):
                        config[section][key] = int(raw)
                    elif isinstance(current, float):
                        config[section][key] = float(raw)
                    else:
                        config[section][key] = raw
                except ValueError:
                    continue  # keep file value on malformed env override
                break


class _Config:
    """Lazy singleton wrapper around the merged configuration."""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}
        self._loaded = False

    def load(self, path: Path = CONFIG_PATH) -> Dict[str, Any]:
        if not self._loaded or path != CONFIG_PATH:
            file_data: Dict[str, Any] = {}
            if path.exists():
                try:
                    file_data = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    # A broken config file must not take the whole system down;
                    # fall back to defaults and let health checks surface it.
                    file_data = {}
            merged = _deep_merge(DEFAULTS, file_data)
            _apply_env(merged)
            self._data = merged
            self._loaded = True
        return self._data

    def reload(self) -> Dict[str, Any]:
        self._loaded = False
        return self.load()


def get_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    """Return the merged configuration (cached after first call)."""
    return _CONFIG.load(path)




_CONFIG = _Config()


def get(section: str, key: str = None, default: Any = None) -> Any:
    """Shorthand access: get('llm', 'model') or get('llm')."""
    data = get_config()
    if key is None:
        return data.get(section, default)
    return data.get(section, {}).get(key, default)
