"""Adapter registry — service.py imports BUILDERS to construct adapters
from config. Add new adapters here (and to iris_config DEFAULTS)."""

from typing import Callable, Dict

from events.adapters.base import EventAdapter
from events.adapters.metrics_adapter import MetricsAdapter
from events.adapters.scheduler_adapter import SchedulerAdapter
from events.adapters.syslog_adapter import SyslogAdapter
from events.adapters.watcher_adapter import WatcherAdapter
from events.adapters.winlog_adapter import WinLogAdapter

try:  # netapp_ontap is optional at runtime (guarded import)
    from events.adapters.netapp_ems_adapter import NetAppEmsAdapter
except Exception:  # pragma: no cover
    NetAppEmsAdapter = None  # type: ignore

try:  # anomalies is Phase 4 — may be absent in minimal installs
    from anomalies.adapter import AnomalyAdapter
except Exception:  # pragma: no cover
    AnomalyAdapter = None  # type: ignore

# adapter key -> builder(bus, cfg_section) -> EventAdapter | None
BUILDERS: Dict[str, Callable] = {
    "syslog": lambda bus, cfg: SyslogAdapter(
        bus, listen=cfg.get("listen", "0.0.0.0"), port=cfg.get("port", 5514)),
    "metrics": lambda bus, cfg: MetricsAdapter(
        bus,
        interval=cfg.get("interval_seconds", 30),
        cpu_threshold=cfg.get("cpu_threshold", 90.0),
        mem_threshold=cfg.get("memory_threshold", 90.0),
        disk_threshold=cfg.get("disk_threshold", 90.0)),
    "watcher": lambda bus, cfg: WatcherAdapter(
        bus, paths=cfg.get("paths", []),
        interval=cfg.get("interval_seconds", 10)),
    "scheduler": lambda bus, cfg: SchedulerAdapter(bus, cfg.get("tasks", [])),
    "winlog": lambda bus, cfg: WinLogAdapter(
        bus, interval=cfg.get("interval_seconds", 20),
        lookback=cfg.get("lookback_seconds", 60)),
}

if NetAppEmsAdapter is not None:
    BUILDERS["netapp_ems"] = lambda bus, cfg: NetAppEmsAdapter(
        bus, interval=cfg.get("interval_seconds", 60))

if AnomalyAdapter is not None:
    BUILDERS["anomaly"] = lambda bus, cfg: AnomalyAdapter(bus, cfg)
