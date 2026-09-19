"""LLM response parsing tests — agent/agent.py parse_llm_response and
validate_action. These are the most regression-prone pieces of the loop
(every LLM quirk surfaces here)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.agent import JSONParseError, parse_llm_response, validate_action


class TestParseLLMResponse:
    def test_plain_json(self):
        action, _ = parse_llm_response('{"tool": "click_tool", "args": {"x": 1}}')
        assert action["tool"] == "click_tool"

    def test_json_in_markdown_block(self):
        raw = 'Here is my action:\n```json\n{"tool": "done", "args": {}}\n```\nDone!'
        action, _ = parse_llm_response(raw)
        assert action["tool"] == "done"

    def test_json_in_bare_block(self):
        raw = "```\n{\"tool\": \"type_tool\", \"args\": {\"text\": \"hi\"}}\n```"
        action, _ = parse_llm_response(raw)
        assert action["tool"] == "type_tool"

    def test_json_with_leading_prose(self):
        raw = 'I will open notepad. {"tool": "open_app_tool", "args": {"app_name": "notepad"}}'
        # Leading prose without a code block: current parser refuses — the
        # prompt forbids it. This documents that contract.
        with pytest.raises(JSONParseError):
            parse_llm_response(raw)

    def test_garbage_raises(self):
        with pytest.raises(JSONParseError):
            parse_llm_response("no json here at all")


class TestValidateAction:
    def test_valid(self):
        assert validate_action({"tool": "x", "args": {}})

    def test_missing_tool(self):
        assert not validate_action({"args": {}})

    def test_missing_args_defaults_ok(self):
        # args is optional in practice (get default {})
        assert validate_action({"tool": "x"})

    def test_non_dict(self):
        assert not validate_action("tool: x")
        assert not validate_action(["tool", "x"])

    def test_empty_tool_name(self):
        assert not validate_action({"tool": "   ", "args": {}})

    def test_args_not_dict(self):
        assert not validate_action({"tool": "x", "args": ["not", "dict"]})
