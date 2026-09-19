"""Agent error-recovery tests — the Phase 0 behaviour change.

The old loop broke out on the FIRST tool failure. The new loop feeds the
failure back as an observation and continues, stopping only after
`error_budget` consecutive failures. These tests run the loop against a
stub LLM (no network) and a throwing tool.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.agent as agent_mod
from tools.base import Tool


def _stub_llm(responses):
    """Return a call_llm stub that plays back `responses` in order."""
    calls = {"n": 0}

    def fake_llm(prompt):
        i = calls["n"]
        calls["n"] += 1
        if i < len(responses):
            return responses[i]
        return '{"tool": "done", "args": {}}'

    return fake_llm, calls


def _boom(**kwargs):
    raise RuntimeError("simulated tool crash")


def _ok(**kwargs):
    return "all good"


class TestErrorRecovery:
    def test_tool_failure_does_not_abort(self, monkeypatch):
        """Tool fails once, then the agent recovers and finishes."""
        boom_tool = Tool("boom_tool", "always fails", {}, _boom)
        ok_tool = Tool("ok_tool", "always works", {}, _ok)
        monkeypatch.setitem(agent_mod.TOOLS_REF, "boom_tool", boom_tool)
        monkeypatch.setitem(agent_mod.TOOLS_REF, "ok_tool", ok_tool)

        responses = [
            "plan: [step1, step2]",  # pre-prompt plan
            '{"tool": "boom_tool", "args": {}}',  # fails -> observation
            '{"tool": "ok_tool", "args": {}}',    # recovers
            '{"tool": "done", "args": {}}',
        ]
        fake_llm, _ = _stub_llm(responses)
        monkeypatch.setattr(agent_mod, "call_llm", fake_llm)
        monkeypatch.setattr(agent_mod.time, "sleep", lambda s: None)

        observations, steps = agent_mod.run_agent("test recovery", max_steps=6)

        statuses = [s.status for s in steps]
        assert "error" in statuses, "first tool failure must be recorded"
        assert steps[-1].status == "done", "agent must finish after recovery, not abort"
        assert any("simulated tool crash" in o for o in observations), (
            "failure must be fed back to the LLM as an observation"
        )

    def test_error_budget_stops_run(self, monkeypatch):
        """After error_budget consecutive failures the loop gives up."""
        boom_tool = Tool("boom_tool", "always fails", {}, _boom)
        monkeypatch.setitem(agent_mod.TOOLS_REF, "boom_tool", boom_tool)

        responses = ["plan"] + ['{"tool": "boom_tool", "args": {}}'] * 10
        fake_llm, _ = _stub_llm(responses)
        monkeypatch.setattr(agent_mod, "call_llm", fake_llm)
        monkeypatch.setattr(agent_mod.time, "sleep", lambda s: None)

        _obs, steps = agent_mod.run_agent("test budget", max_steps=10)

        errors = [s for s in steps if s.status == "error"]
        assert len(errors) == 3, "default error_budget is 3 consecutive failures"
        assert steps[-1].status == "error"

    def test_on_step_callback_fires(self, monkeypatch):
        ok_tool = Tool("ok_tool", "works", {}, _ok)
        monkeypatch.setitem(agent_mod.TOOLS_REF, "ok_tool", ok_tool)
        fake_llm, _ = _stub_llm([
            "plan",
            '{"tool": "ok_tool", "args": {}}',
            '{"tool": "done", "args": {}}',
        ])
        monkeypatch.setattr(agent_mod, "call_llm", fake_llm)
        monkeypatch.setattr(agent_mod.time, "sleep", lambda s: None)

        seen = []
        agent_mod.run_agent("callback test", max_steps=4, on_step=seen.append)
        names = [s.tool_name for s in seen]
        assert "ok_tool" in names and "done" in names

    def test_should_cancel_stops_loop(self, monkeypatch):
        ok_tool = Tool("ok_tool", "works", {}, _ok)
        monkeypatch.setitem(agent_mod.TOOLS_REF, "ok_tool", ok_tool)
        fake_llm, _ = _stub_llm([
            "plan",
            '{"tool": "ok_tool", "args": {}}',
            '{"tool": "done", "args": {}}',
        ])
        monkeypatch.setattr(agent_mod, "call_llm", fake_llm)
        monkeypatch.setattr(agent_mod.time, "sleep", lambda s: None)

        _obs, steps = agent_mod.run_agent(
            "cancel test", max_steps=5, should_cancel=lambda: True
        )
        assert all(s.tool_name == "cancelled" for s in steps)
        assert len(steps) == 1
