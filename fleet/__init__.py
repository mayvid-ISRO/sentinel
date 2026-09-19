"""
Fleet package — multi-node orchestration for IRIS.

Sub-modules:
  registry   FleetRegistry — in-memory node store with health polling
"""

from fleet.registry import FleetRegistry, NodeEntry, HEALTH_TTL, POLL_INTERVAL

__all__ = ["FleetRegistry", "NodeEntry", "HEALTH_TTL", "POLL_INTERVAL"]
