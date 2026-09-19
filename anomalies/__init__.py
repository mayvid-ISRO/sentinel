"""
Anomaly Detection Subsystem — Phase 4
======================================
Statistical baselining over event metric streams. Zero external deps
(psutil-only; pure-python math).

Public API:
  from anomalies.baseline import BaselineTracker, get_tracker
  from anomalies.config  import get_anomaly_config
  from anomalies.adapter import AnomalyAdapter  # wires into EventBus
"""

from anomalies.baseline import BaselineTracker, get_tracker, reset_tracker_singleton
from anomalies.config   import get_anomaly_config, ANOMALY_DEFAULTS
from anomalies.adapter  import AnomalyAdapter

__all__ = [
    "BaselineTracker", "get_tracker", "reset_tracker_singleton",
    "get_anomaly_config", "ANOMALY_DEFAULTS",
    "AnomalyAdapter",
]
