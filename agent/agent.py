"""
IRIS Agent - agent.py
Core agent loop with improved error handling and structured output.

Key improvements:
- Better error recovery for LLM failures
- Structured step-by-step progress tracking
- Graceful degradation on partial failures
- Detailed logging for debugging

Phase 0 rework:
- Error budget: a failed tool call is fed back as an observation and the
  loop CONTINUES (self-correction) instead of aborting; the run only stops
  early after `error_budget` consecutive failures.
- on_step callback: the backend registers one callback to receive each
  StepResult the moment it happens (WebSocket streaming) without
  duplicating the loop.
- should_cancel callback: polled before every step so the backend can
  stop a runaway task (cancellation is now real, not cosmetic).
- agent/redact.py masks secrets in every recorded result.

Files that depend on this module:
  - main.py (CLI runner)      - backend/main.py (run_task wrapper)
  - events/engine.py          (event-triggered tasks reuse run_agent)
"""

import json
from agent.llm import call_llm
from agent.dispatcher import dispatch
from agent.prompt import build_prompt
from agent.pre_prompt import build_pre_prompt
from agent.redact import redact
import logging
import time
from typing import List, Dict, Any, Callable, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime

logger = logging.getLogger(__name__)

StepCallback = Callable[["StepResult"], None]
CancelCheck = Callable[[], bool]

# Test seam: tests monkeypatch this dict to swap the tool registry without
# touching tools/__init__.py. When non-empty, dispatch goes through it.
TOOLS_REF = {}


def _fetch_rag_context(user_task: str) -> str:
    """
    Fetch knowledge-base chunks relevant to *user_task* and return them as
    a plain-text context block, or an empty string when RAG is disabled.

    Returns up to rag.config top_k results with score >= threshold.
    """
    try:
        from rag.retriever import KnowledgeRetriever
        retriever = KnowledgeRetriever.from_config()
        if retriever is None:
            return ""
        refs = retriever.retrieve(user_task)
        if not refs:
            return ""
        lines = []
        for r in refs:
            lines.append(f"[{r.source_file}] (relevance={r.score:.2f}): {r.text}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("RAG retrieval failed (non-fatal): %s", exc)
        return ""


def _dispatch(action: Dict[str, Any]):
    """Dispatch through the test seam when populated, else the real registry."""
    if TOOLS_REF:
        tool_name = action["tool"]
        if tool_name not in TOOLS_REF:
            raise RuntimeError(f"Unknown tool: {tool_name}")
        return TOOLS_REF[tool_name].run(action.get("args", {}))
    return dispatch(action)


@dataclass
class StepResult:
    """Structured result from each agent step."""
    step_number: int
    tool_name: str
    tool_args: Dict[str, Any]
    result: str
    status: str  # 'ok', 'error', 'skip', 'done'
    timestamp: str
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AgentError(Exception):
    """Custom exception for agent errors."""
    pass


class JSONParseError(AgentError):
    """Custom exception for JSON parsing errors."""
    pass


def parse_llm_response(response: str) -> Tuple[Dict[str, Any], str]:
    """
    Parse LLM response and extract JSON action.

    Args:
        response: Raw LLM response string

    Returns:
        Tuple of (action_dict, parsed_response_string)

    Raises:
        JSONParseError: If JSON cannot be extracted
    """
    # Try to extract JSON from various formats
    json_str = None

    # Format 1: ```json ... ```
    if "```json" in response:
        try:
            json_part = response.split("```json")[1].split("```")[0].strip()
            json_str = json_part
        except IndexError:
            pass

    # Format 2: ``` ... ```
    if json_str is None and "```" in response:
        try:
            json_part = response.split("```")[1].split("```")[0].strip()
            # Check if it looks like JSON
            if json_part.startswith("{") or json_part.startswith("["):
                json_str = json_part
        except IndexError:
            pass

    # Format 3: Raw JSON
    if json_str is None:
        # Try to find JSON-like content
        response_stripped = response.strip()
        if response_stripped.startswith("{") or response_stripped.startswith("["):
            json_str = response_stripped

    # Parse the JSON
    if json_str:
        try:
            action = json.loads(json_str)
            return action, json_str
        except json.JSONDecodeError as e:
            raise JSONParseError(f"Failed to parse JSON: {e}\nJSON string: {json_str[:200]}...")

    raise JSONParseError(f"Could not extract JSON from response: {response[:500]}...")


def validate_action(action: Dict[str, Any]) -> bool:
    """
    Validate that the action has the expected structure.

    Args:
        action: Action dictionary from LLM

    Returns:
        True if valid
    """
    if not isinstance(action, dict):
        return False

    if "tool" not in action:
        return False

    tool = action.get("tool", "")
    args = action.get("args", {})

    # Tool name should be a non-empty string
    if not isinstance(tool, str) or not tool.strip():
        return False

    # Args should be a dict
    if not isinstance(args, dict):
        return False

    return True


def run_agent(
    user_task: str,
    max_steps: int = 10,
    on_step: Optional[StepCallback] = None,
    should_cancel: Optional[CancelCheck] = None,
) -> Tuple[List[str], List[StepResult]]:
    """
    Run the agent loop to complete a user task.

    Args:
        user_task: Description of the task to complete
        max_steps: Maximum number of steps to attempt
        on_step: Called with each StepResult as it is produced (used by
                 the backend to stream progress over WebSocket)
        should_cancel: Polled before each step; when it returns True the
                       run stops cleanly (used by the backend cancel API)

    Returns:
        Tuple of (observations_list, step_results_list)
    """
    from iris_config import get as cfg_get

    error_budget = int(cfg_get("agent", "error_budget", 3))
    step_delay = float(cfg_get("agent", "step_delay_seconds", 0.5))

    observations: List[str] = []
    step_results: List[StepResult] = []
    consecutive_errors = 0

    def record(step: StepResult) -> None:
        step_results.append(step)
        if on_step:
            try:
                on_step(step)
            except Exception as cb_error:  # a UI callback must never kill a run
                logger.warning("on_step callback failed: %s", cb_error)

    logger.info("=" * 60)
    logger.info("Starting agent run: %s...", redact(user_task)[:60])
    logger.info("Max steps: %s | Error budget: %s", max_steps, error_budget)
    logger.info("=" * 60)

    # Step 1: Generate plan from pre-prompt (with optional RAG context)
    logger.info("Generating execution plan...")
    try:
        rag_context = _fetch_rag_context(user_task)
        pre_prompt = build_pre_prompt(user_task, knowledge_context=rag_context)
        steps_plan = call_llm(pre_prompt)
        logger.info("Plan generated:\n%s", redact(steps_plan))
    except Exception as e:
        error_msg = f"Failed to generate plan: {e}"
        logger.error(error_msg)
        observations.append(f"Error: {error_msg}")
        return observations, step_results

    # Step 2: Execute steps
    for step_num in range(1, max_steps + 1):
        if should_cancel and should_cancel():
            logger.info("Run cancelled by request before step %d", step_num)
            record(StepResult(
                step_number=step_num,
                tool_name="cancelled",
                tool_args={},
                result="Task cancelled by user",
                status="error",
                timestamp=datetime.utcnow().isoformat(),
                error_message="cancelled",
            ))
            return observations, step_results

        logger.info("-" * 40)
        logger.info("Step %d/%d", step_num, max_steps)

        # Build prompt with history
        prompt = build_prompt(user_task, steps_plan, knowledge_context=rag_context)
        if observations:
            prompt += "\n--- Previous observations ---\n" + "\n".join(map(str, observations))

        logger.debug("Prompt length: %d chars", len(prompt))

        # Get LLM response
        try:
            llm_response = call_llm(prompt)
            logger.debug("LLM response received (%d chars)", len(llm_response))
        except Exception as e:
            error_msg = f"Step {step_num}: LLM call failed - {e}"
            logger.error(error_msg)
            observations.append(error_msg)
            record(StepResult(
                step_number=step_num,
                tool_name="llm_error",
                tool_args={},
                result=redact(error_msg),
                status="error",
                timestamp=datetime.utcnow().isoformat(),
                error_message=str(e),
            ))
            consecutive_errors += 1
            if consecutive_errors >= error_budget:
                logger.error("Error budget exhausted (LLM); stopping run")
                break
            continue

        # The budget counts consecutive FAILED STEPS (LLM, parse, or tool).
        # It is reset ONLY by a step that fully succeeds (tool ran ok),
        # not merely by an LLM response — otherwise a tool that keeps
        # crashing while the LLM keeps "successfully" proposing it would
        # never exhaust the budget.

        # Parse the response
        try:
            action, json_str = parse_llm_response(llm_response)
            logger.info("Parsed action: %s...", redact(json_str)[:100])
        except JSONParseError as e:
            error_msg = f"Step {step_num}: {e}"
            logger.error(error_msg)
            observations.append(error_msg + " — respond with ONE valid JSON action object only.")
            record(StepResult(
                step_number=step_num,
                tool_name="parse_error",
                tool_args={},
                result=redact(str(e)),
                status="error",
                timestamp=datetime.utcnow().isoformat(),
                error_message=str(e),
            ))
            consecutive_errors += 1
            if consecutive_errors >= error_budget:
                logger.error("Error budget exhausted (parse); stopping run")
                break
            continue
        except Exception as e:
            error_msg = f"Step {step_num}: Unexpected parsing error - {e}"
            logger.error(error_msg)
            observations.append(error_msg)
            record(StepResult(
                step_number=step_num,
                tool_name="parse_error",
                tool_args={},
                result=redact(error_msg),
                status="error",
                timestamp=datetime.utcnow().isoformat(),
                error_message=str(e),
            ))
            consecutive_errors += 1
            if consecutive_errors >= error_budget:
                break
            continue

        # Validate action
        if not validate_action(action):
            error_msg = f"Step {step_num}: Invalid action structure"
            logger.error(error_msg)
            observations.append(error_msg)
            record(StepResult(
                step_number=step_num,
                tool_name="validation_error",
                tool_args=action,
                result=error_msg,
                status="error",
                timestamp=datetime.utcnow().isoformat(),
                error_message="Invalid action structure",
            ))
            consecutive_errors += 1
            if consecutive_errors >= error_budget:
                break
            continue

        tool_name = action.get("tool", "")
        tool_args = action.get("args", {})

        logger.info("Tool: %s", tool_name)
        logger.debug("Args: %s", redact(json.dumps(tool_args, default=str)))

        # Check for completion
        if tool_name == "done":
            message = action.get("message", "Task complete")
            logger.info("Task completed: %s", message)
            observations.append(f"✓ Task completed: {message}")
            record(StepResult(
                step_number=step_num,
                tool_name="done",
                tool_args=tool_args,
                result=message,
                status="done",
                timestamp=datetime.utcnow().isoformat(),
            ))
            break

        # Execute the tool
        try:
            result = _dispatch(action)
            result_str = str(result)
            logger.info("Result: %s...", redact(result_str)[:100])
            observations.append(f"Step {step_num}: {result}")
            record(StepResult(
                step_number=step_num,
                tool_name=tool_name,
                tool_args=tool_args,
                result=redact(result_str),
                status="ok",
                timestamp=datetime.utcnow().isoformat(),
            ))
            consecutive_errors = 0
        except Exception as e:
            # Feed the failure back as an observation so the LLM can correct
            # course; only stop when the error budget is exhausted.
            error_msg = f"Step {step_num}: Tool execution failed - {e}"
            logger.error(error_msg)
            observations.append(error_msg)
            record(StepResult(
                step_number=step_num,
                tool_name=tool_name,
                tool_args=tool_args,
                result=redact(error_msg),
                status="error",
                timestamp=datetime.utcnow().isoformat(),
                error_message=str(e),
            ))
            consecutive_errors += 1
            if consecutive_errors >= error_budget:
                logger.error("Error budget exhausted (tool); stopping run")
                break

        # Small delay between steps
        time.sleep(step_delay)

    # Final summary
    logger.info("=" * 60)
    logger.info("Agent run complete")
    logger.info("Total steps: %d", len(step_results))
    logger.info("Observations: %d", len(observations))
    logger.info("=" * 60)

    return observations, step_results


def run_agent_simple(user_task: str, max_steps: int = 10) -> List[str]:
    """
    Simplified agent runner for backward compatibility.
    Returns only observations list.

    Args:
        user_task: Description of the task to complete
        max_steps: Maximum number of steps to attempt

    Returns:
        List of observation strings
    """
    observations, _ = run_agent(user_task, max_steps)
    return observations
