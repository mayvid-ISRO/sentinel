"""
Anomaly subsystem configuration — reads [events.anomaly] from iris_config,
with IRIS_ANOMALY_* env-var overrides.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict

# Default values for [events.anomaly] section
ANOMALY_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "window_size": 100,
    "z_threshold": 2.5,
    "min_samples": 10,
    "cooldown_seconds": 300,
    "store_path": ".iris/baselines.json",
}


@dataclass(frozen=True)
class AnomalyConfig:
    enabled: bool
    window_size: int
    z_threshold: float
    min_samples: int
    cooldown_seconds: int
    store_path: str


def _env_int(key: str, default: int) -> int:
    v = os.environ.get(key)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    v = os.environ.get(key)
    if v is None:
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")


def get_anomaly_config(raw_section: Dict[str, Any]) -> AnomalyConfig:
    """Merge defaults → raw_section → env vars. Returns immutable config."""
    merged: Dict[str, Any] = {**ANOMALY_DEFAULTS, **raw_section}

    merged["enabled"]         = _env_bool("IRIS_ANOMALY_ENABLED",      merged["enabled"])
    merged["window_size"]     = _env_int("IRIS_ANOMALY_WINDOW",        merged["window_size"])
    merged["z_threshold"]     = _env_float("IRIS_ANOMALY_Z_THRESH",   merged["z_threshold"])
    merged["min_samples"]     = _env_int("IRIS_ANOMALY_MIN_SAMPLES",  merged["min_samples"])
    merged["cooldown_seconds"] = _env_int("IRIS_ANOMALY_COOLDOWN",    merged["cooldown_seconds"])
    sp = os.environ.get("IRIS_ANOMALY_STORE")
    if sp:
        merged["store_path"] = sp

    return AnomalyConfig(
        enabled=merged["enabled"],
        window_size=max(5, merged["window_size"]),
        z_threshold=max(1.0, merged["z_threshold"]),
        min_samples=max(2, merged["min_samples"]),
        cooldown_seconds=max(0, merged["cooldown_seconds"]),
        store_path=merged["store_path"],
    )
