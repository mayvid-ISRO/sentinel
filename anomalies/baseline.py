"""
BaselineTracker — per-(source_type, entity, metric) rolling-window statistics.

Pure-Python math (no numpy/sklearn): mean, std, z-score from a capped deque.
Tracks three families:
  - value     : raw numeric sample (e.g. CPU%, memory%)
  - count     : event counts per interval (e.g. auth failures/min)
  - latency   : durations in ms (e.g. SSH connect time)

Usage:
  tracker = get_tracker()
  tracker.observe("metrics", "host-01", "cpu", 87.3)
  result  = tracker.check("metrics", "host-01", "cpu", 99.5)
  # result: {"is_anomaly": True, "z_score": 3.12, "mean": 85.2, "std": 2.1}

Thread-safe via threading.Lock.
Persisted to JSON between cycles for crash recovery.
"""

from __future__ import annotations

import json
import math
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


# ── Internal sample bucket ───────────────────────────────────────────────

class _Bucket:
    """Rolling-window accumulator for one (source_type, entity, metric)."""

    __slots__ = ("values", "count", "sum_sq", "last_anomaly_ts")

    def __init__(self, max_len: int = 100):
        self.values: deque = deque(maxlen=max_len)
        self.count: int = 0
        self.sum_sq: float = 0.0
        self.last_anomaly_ts: float = 0.0

    def observe(self, value: float) -> None:
        if len(self.values) == self.values.maxlen:
            old = self.values[0]
            self.sum_sq -= old * old
        self.values.append(value)
        self.sum_sq += value * value
        self.count += 1

    @property
    def n(self) -> int:
        return len(self.values)

    def mean(self) -> float:
        if not self.values:
            return 0.0
        return sum(self.values) / self.n

    def std(self) -> float:
        if self.n < 2:
            return 0.0
        m = self.mean()
        var_sum = 0.0
        for v in self.values:
            d = v - m
            var_sum += d * d
        return math.sqrt(var_sum / (self.n - 1))

    def z_score(self, value: float) -> float:
        s = self.std()
        if s < 1e-9:
            return float("inf") if value != self.mean() else 0.0
        return (value - self.mean()) / s

    def is_cooldown(self, now_epoch: float, cooldown_secs: int) -> bool:
        if cooldown_secs <= 0:
            return False
        return (now_epoch - self.last_anomaly_ts) < cooldown_secs


# ── Tracker ──────────────────────────────────────────────────────────────

class BaselineTracker:
    """
    Per-key rolling statistics engine.

    Key = (source_type, entity, metric) — for metrics adapter these are
    e.g. ("metrics", "host-01", "cpu") or ("syslog", "firewall-01",
    "auth_failures"). For continuous streams a synthetic metric name must
    be chosen by the caller.
    """

    def __init__(self, window_size: int = 100, min_samples: int = 10,
                 z_threshold: float = 2.5, cooldown_seconds: int = 300,
                 store_path: str = ".iris/baselines.json"):
        self.window_size = max(5, window_size)
        self.min_samples = max(2, min_samples)
        self.z_threshold = max(1.0, z_threshold)
        self.cooldown_seconds = max(0, cooldown_seconds)
        self.store_path = Path(store_path)
        self._buckets: Dict[str, _Bucket] = {}
        self._lock = threading.Lock()
        self._load()

    def observe(self, source_type: str, entity: str, metric: str,
                value: float) -> None:
        """Record one sample; evicts oldest if window full."""
        key = f"{source_type}|{entity}|{metric}"
        with self._lock:
            self._get_or_create(key).observe(value)
            self._maybe_save()

    def check(self, source_type: str, entity: str, metric: str,
              value: float) -> Dict[str, Any]:
        """
        Check whether *value* is anomalous for this key.
        Returns dict with: is_anomaly, z_score, mean, std, samples, note.
        """
        key = f"{source_type}|{entity}|{metric}"
        now = datetime.now(timezone.utc).timestamp()
        with self._lock:
            bucket = self._get_or_create(key)
            bucket.observe(value)

            z = bucket.z_score(value)
            mean = bucket.mean()
            std = bucket.std()
            n = bucket.n
            cd = bucket.is_cooldown(now, self.cooldown_seconds)

            is_anomaly = (
                n >= self.min_samples
                and abs(z) >= self.z_threshold
                and not cd
            )

            if is_anomaly:
                bucket.last_anomaly_ts = now

            # Persist after every check so baselines survive restarts
            self._maybe_save()

            note_parts = []
            if cd:
                note_parts.append("in cooldown")
            elif n < self.min_samples:
                note_parts.append(f"insufficient samples ({n}<{self.min_samples})")

            return {
                "is_anomaly": is_anomaly,
                "z_score": round(z, 3) if math.isfinite(z) else None,
                "mean": round(mean, 4),
                "std": round(std, 4),
                "samples": n,
                "value": round(value, 4),
                "cooldown": cd,
                "note": ", ".join(note_parts) if note_parts else "",
            }

    def get_baseline(self, source_type: str, entity: str, metric: str
                     ) -> Optional[Dict[str, Any]]:
        """Return current baseline stats without observing a new value."""
        key = f"{source_type}|{entity}|{metric}"
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None or bucket.n < self.min_samples:
                return None
            return {
                "source_type": source_type,
                "entity": entity,
                "metric": metric,
                "mean": round(bucket.mean(), 4),
                "std": round(bucket.std(), 4),
                "samples": bucket.n,
                "z_threshold": self.z_threshold,
                "window_size": self.window_size,
            }

    def list_baselines(self) -> list:
        """Return baseline dicts for every key that has enough samples."""
        with self._lock:
            out = []
            for key, bucket in self._buckets.items():
                if bucket.n >= self.min_samples:
                    src, ent, met = key.split("|", 2)
                    out.append({
                        "source_type": src, "entity": ent, "metric": met,
                        "mean": round(bucket.mean(), 4),
                        "std": round(bucket.std(), 4),
                        "samples": bucket.n,
                        "z_threshold": self.z_threshold,
                        "window_size": self.window_size,
                    })
            return out

    def clear(self) -> int:
        """Remove all tracked baselines. Returns count cleared."""
        with self._lock:
            n = len(self._buckets)
            self._buckets.clear()
            self._save_empty()
            return n

    def clear_key(self, source_type: str, entity: str, metric: str) -> bool:
        """Remove one specific key. Returns True if it existed."""
        key = f"{source_type}|{entity}|{metric}"
        with self._lock:
            removed = key in self._buckets
            if removed:
                del self._buckets[key]
                self._maybe_save()
            return removed

    # ── persistence ────────────────────────────────────────────────────

    def _load(self) -> None:
        """Restore buckets from JSON store on disk."""
        if not self.store_path.exists():
            return
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, bd in data.items():
                b = _Bucket(max_len=self.window_size)
                b.values = deque(bd["values"], maxlen=self.window_size)
                b.count = bd.get("count", 0)
                b.sum_sq = bd.get("sum_sq", 0.0)
                b.last_anomaly_ts = bd.get("last_anomaly_ts", 0.0)
                self._buckets[key] = b
        except Exception:
            pass

    def _save(self) -> None:
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.store_path, "w", encoding="utf-8") as f:
                dump = {}
                for key, b in self._buckets.items():
                    dump[key] = {
                        "values": list(b.values),
                        "count": b.count,
                        "sum_sq": b.sum_sq,
                        "last_anomaly_ts": b.last_anomaly_ts,
                    }
                json.dump(dump, f)
        except Exception:
            pass

    def _save_empty(self) -> None:
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.store_path, "w", encoding="utf-8") as f:
                json.dump({}, f)
        except Exception:
            pass

    def _maybe_save(self) -> None:
        self._save()

    def _get_or_create(self, key: str) -> _Bucket:
        if key not in self._buckets:
            self._buckets[key] = _Bucket(max_len=self.window_size)
        return self._buckets[key]


# ── Module-level singleton ───────────────────────────────────────────────

_tracker_instance: Optional[BaselineTracker] = None


def get_tracker(window_size: int = 100, min_samples: int = 10,
                z_threshold: float = 2.5, cooldown_seconds: int = 300,
                store_path: str = ".iris/baselines.json") -> BaselineTracker:
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = BaselineTracker(
            window_size=window_size, min_samples=min_samples,
            z_threshold=z_threshold, cooldown_seconds=cooldown_seconds,
            store_path=store_path,
        )
    return _tracker_instance


def reset_tracker_singleton() -> None:
    """For tests only — tear down the module-level singleton."""
    global _tracker_instance
    _tracker_instance = None
