"""
Rules engine — declarative event-to-task routing.
=================================================
Rules live in events/rules.json (hot-reloadable, no code change to tune
thresholds). Each rule:

    {
      "name": "high-cpu",
      "match": {"source_type": "cpu", "min_severity": "warning"},
      "condition": {"field": "value", "op": ">", "threshold": 90},   # optional
      "task_template": "Investigate high CPU usage on {entity}: currently at {value}%",
      "max_steps": 5,
      "cooldown_seconds": 300,      # don't re-trigger for same entity
      "mode": "auto"                # auto | manual | shadow — overrides global
    }

Modes (mirror the phased rollout in PROJECT_ARCHITECTURE.md):
  shadow  — log only, never create a task
  manual  — create a PENDING event task the user must approve in the UI
  auto    — create and immediately run the agent task

Files that depend on this module:
  - events/engine.py  (loads + evaluates rules per event)
  - events/rules.json (the data this engine reads)
  - backend/main.py    (exposes /api/events/rules for UI editing)
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from events.schema import SEVERITY_ORDER, Event

logger = logging.getLogger(__name__)

DEFAULT_RULES_PATH = Path(__file__).resolve().parent / "rules.json"

_OPS = {
    ">": lambda a, b: a is not None and b is not None and a > b,
    ">=": lambda a, b: a is not None and b is not None and a >= b,
    "<": lambda a, b: a is not None and b is not None and a < b,
    "<=": lambda a, b: a is not None and b is not None and a <= b,
    "==": lambda a, b: a == b,
    "contains": lambda a, b: b in str(a or ""),
    "regex": lambda a, b: __import__("re").search(b, str(a or "")) is not None,
}


class RuleBook:
    """Loads rules.json and evaluates events against it."""

    def __init__(self, path: Path = DEFAULT_RULES_PATH):
        self.path = path
        self.rules: List[Dict[str, Any]] = []
        self._last_cooldown: Dict[str, float] = {}  # rule|entity -> last fire
        self._mtime: float = 0.0
        self.load()

    def load(self) -> None:
        """Load (or hot-reload) rules from disk; keeps old rules on error."""
        if not self.path.exists():
            self.rules = []
            logger.info("No rules file at %s — all events pass through unmatched", self.path)
            return
        try:
            mtime = self.path.stat().st_mtime
            if mtime == self._mtime and self.rules:
                return  # unchanged
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.rules = data.get("rules", [])
            self._mtime = mtime
            logger.info("Loaded %d event rules from %s", len(self.rules), self.path)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load rules (%s): keeping previous rules", exc)

    def reload_if_changed(self) -> None:
        self.load()

    # ── matching ────────────────────────────────────────────────────────
    def _match_section(self, event: Event, match: Dict[str, Any]) -> bool:
        for key, expected in (match or {}).items():
            if key == "min_severity":
                if SEVERITY_ORDER.get(event.severity, 0) < SEVERITY_ORDER.get(expected, 0):
                    return False
            elif key == "domain":
                if event.domain != expected:
                    return False
            elif key == "source":
                if event.source != expected:
                    return False
            elif key == "source_type":
                if event.source_type != expected:
                    return False
            elif key == "entity_regex":
                import re
                if not re.search(expected, event.entity or ""):
                    return False
            else:
                # generic field match against the event dict
                if getattr(event, key, None) != expected:
                    return False
        return True

    def _check_condition(self, event: Event, condition: Dict[str, Any]) -> bool:
        if not condition:
            return True
        field = condition.get("field", "value")
        op = condition.get("op", ">")
        threshold = condition.get("threshold")
        actual = getattr(event, field, None)
        if field == "value" and actual is None:
            actual = event.details.get("value")
        if op not in _OPS:
            logger.warning("Unknown op '%s' in rule condition", op)
            return False
        return bool(_OPS[op](actual, threshold))

    def _in_cooldown(self, rule: Dict[str, Any], event: Event) -> bool:
        cooldown = rule.get("cooldown_seconds", 0)
        if cooldown <= 0:
            return False
        key = f"{rule.get('name', '?')}|{event.entity or '*'}"
        last = self._last_cooldown.get(key, 0.0)
        if time.time() - last < cooldown:
            return True
        return False

    def evaluate(self, event: Event) -> Optional[Dict[str, Any]]:
        """Return the FIRST matching rule (with `task` filled from the
        template), or None. Also marks cooldown when matched."""
        self.reload_if_changed()
        for rule in self.rules:
            if not self._match_section(event, rule.get("match", {})):
                continue
            if not self._check_condition(event, rule.get("condition", {})):
                continue
            if self._in_cooldown(rule, event):
                return None
            task_text = rule.get("task_template", "").format(
                entity=event.entity or "unknown host",
                value=event.value if event.value is not None else "n/a",
                message=event.message,
                source=event.source,
            )
            self._last_cooldown[
                f"{rule.get('name', '?')}|{event.entity or '*'}"
            ] = time.time()
            return {
                "rule": rule.get("name", "unnamed"),
                "mode": rule.get("mode", "manual"),
                "task": task_text,
                "max_steps": rule.get("max_steps", 5),
                "event": event,
            }
        return None
