"""
Event schema — the normalized contract every event source must speak.
=====================================================================
Regardless of origin (syslog, Windows Event Log, metrics poller, file
watcher, scheduler, NetApp EMS), an event entering IRIS is an Event
dataclass with these fields. Adapters translate source-specific formats
into this one shape so the rules engine and UI never care where an
event came from.

Severity ladder: info < warning < error < critical
Domain: system | storage | network | security | application | schedule

Files that depend on this module:
  - events/bus.py      (events flow through the bus as Event objects)
  - events/rules.py    (matches against severity/domain/source_type)
  - events/engine.py   (enriches and dispatches)
  - events/adapters/*  (every adapter constructs Event)
  - backend/main.py    (serializes to JSON for /api/events)
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional


SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}
DOMAINS = ("system", "storage", "network", "security", "application", "schedule")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Event:
    """One normalized infra event."""

    source: str                    # adapter id, e.g. "metrics", "syslog", "winlog"
    source_type: str               # finer grain, e.g. "cpu", "ssh", "ems"
    domain: str                    # one of DOMAINS
    severity: str                  # info | warning | error | critical
    message: str                   # human-readable summary (redact secrets!)
    timestamp: str = field(default_factory=utcnow_iso)  # ISO-8601 UTC
    entity: Optional[str] = None   # affected host/volume/user/path
    value: Optional[float] = None  # metric value when applicable
    details: Dict[str, Any] = field(default_factory=dict)  # raw payload extras

    def severity_rank(self) -> int:
        return SEVERITY_ORDER.get(self.severity, 0)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Event":
        return Event(
            source=data.get("source", "unknown"),
            source_type=data.get("source_type", "unknown"),
            domain=data.get("domain", "system"),
            severity=data.get("severity", "info"),
            message=data.get("message", ""),
            timestamp=data.get("timestamp", utcnow_iso()),
            entity=data.get("entity"),
            value=data.get("value"),
            details=data.get("details", {}),
        )

    def validate(self) -> bool:
        return (
            bool(self.source)
            and self.severity in SEVERITY_ORDER
            and self.domain in DOMAINS
            and bool(self.message)
        )
