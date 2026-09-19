"""
Event engine — the brain connecting the bus to the agent.
=========================================================
Subscribes to the EventBus. For every event:
  1. Evaluate against the RuleBook (events/rules.json).
  2. No rule match + severity >= error → optional LLM triage (config:
     events.llm_triage) which asks the model to classify & propose a task.
  3. Decide by MODE (rule mode overrides global events.mode):
       shadow  — record only (no task)
       manual  — create a task with status "awaiting_approval" (UI approve)
       auto    — create and start the task immediately

Task creation goes through a callback injected by events/service.py
(create_event_task), which persists to SQLite and runs the agent via the
same run_task path as user-submitted tasks — one execution engine for
both intent-driven and event-driven flows.

Files that depend on this module:
  - events/service.py (wires engine + bus + adapters + backend)
  - backend/main.py    (provides create_event_task + approve endpoint)
"""

import json
import logging
from typing import Any, Callable, Dict, Optional

from events.bus import EventBus
from events.rules import RuleBook
from events.schema import Event

logger = logging.getLogger(__name__)

# create_event_task(task_text, max_steps, mode, event) -> task_id | None
TaskCreator = Callable[[str, int, str, Event], Any]


class EventEngine:
    def __init__(self, bus: EventBus, rules: RuleBook,
                 create_task: TaskCreator, llm_call=None):
        self.bus = bus
        self.rules = rules
        self.create_task = create_task
        self.llm_call = llm_call  # agent.llm.call_llm by default
        self.processed = 0
        self.matched = 0
        self.tasks_created = 0

    async def handle_event(self, event: Event) -> None:
        """Bus subscriber entry point."""
        self.processed += 1
        logger.debug("event: %s/%s %s", event.source, event.source_type, event.severity)

        try:
            match = self.rules.evaluate(event)
            if match is None:
                if (event.severity in ("error", "critical")
                        and self.llm_call is not None):
                    match = await self._llm_triage(event)
                if match is None:
                    return  # unmatched, below triage threshold — recorded only

            self.matched += 1
            mode = match.get("mode", "manual")
            task_text = match.get("task", "")
            max_steps = int(match.get("max_steps", 5))

            if mode == "shadow":
                logger.info("[shadow] rule=%s would run: %s", match.get("rule"), task_text[:80])
                return

            task_id = self.create_task(task_text, max_steps, mode, event)
            if task_id:
                self.tasks_created += 1
                logger.info("event task created (%s): rule=%s task_id=%s",
                            mode, match.get("rule"), task_id)
        except Exception:
            logger.exception("event handling failed — event dropped, bus continues")

    async def _llm_triage(self, event: Event) -> Optional[Dict[str, Any]]:
        """Ask the LLM to classify an unmatched severe event and propose
        a safe investigation task. Falls back to None on any failure."""
        prompt = f"""You are the triage module of an IT infrastructure agent.
An event occurred that matches no pre-defined rule. Classify it and
propose ONE safe, read-only investigation task for an automation agent.

Event:
  source: {event.source} / {event.source_type}
  domain: {event.domain}
  severity: {event.severity}
  entity: {event.entity}
  value: {event.value}
  message: {event.message}

Respond ONLY with JSON:
{{"task": "<investigation task text, read-only, no destructive actions>",
  "max_steps": 5}}"""
        try:
            import asyncio
            raw = await asyncio.to_thread(self.llm_call, prompt)
            # reuse the agent's tolerant parser
            from agent.agent import parse_llm_response
            action, _ = parse_llm_response(raw)
            if isinstance(action, dict) and action.get("task"):
                return {
                    "rule": "llm-triage",
                    "mode": "manual",   # LLM-proposed tasks are never auto-run
                    "task": str(action["task"])[:500],
                    "max_steps": int(action.get("max_steps", 5) or 5),
                }
        except Exception as exc:
            logger.warning("LLM triage failed for event: %s", exc)
        return None


def attach_engine(bus: EventBus, rules: RuleBook, create_task: TaskCreator,
                  llm_call=None) -> EventEngine:
    """Create the engine and subscribe it to the bus."""
    engine = EventEngine(bus, rules, create_task, llm_call)
    bus.subscribe(engine.handle_event)
    return engine
