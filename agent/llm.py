"""
LLM interface.
==============
Provider-agnostic wrapper supporting both Ollama (/api/generate) and
OpenAI-compatible (/v1/chat/completions) endpoints.  All settings come
from iris_config.py — nothing is hardcoded here.

Config keys (all optional):
  llm.url        — endpoint URL
  llm.model      — model name
  llm.api_key    — Bearer token for OpenAI-compatible providers
  llm.timeout_seconds
  llm.max_retries
  llm.retry_backoff_seconds

Files that depend on this module:
  - agent/agent.py, agent/pre_prompt.py  (direct calls)
  - backend/main.py                      (via run_agent)
  - events/engine.py                     (LLM triage of unmatched events)
"""

import logging
import time

import requests

from iris_config import get

logger = logging.getLogger(__name__)


def call_llm(prompt: str) -> str:
    """Send one completion request; retry with backoff on transient errors.

    Auto-detects provider from URL:
      - URL contains '/chat/completions' or '/v1/'  → OpenAI format
      - everything else                               → Ollama format
    Raises requests.RequestException on final failure.
    """
    url    = get("llm", "url")
    model  = get("llm", "model")
    api_key = get("llm", "api_key", "")
    timeout    = get("llm", "timeout_seconds", 180)
    max_retries = get("llm", "max_retries", 2)
    backoff    = get("llm", "retry_backoff_seconds", 2.0)

    is_openai = ("/chat/completions" in url) or ("/v1/" in url)

    if is_openai:
        payload = {
            "model":  model,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    else:
        payload = {"model": model, "prompt": prompt, "stream": False}
        headers = {"Content-Type": "application/json"}

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            if is_openai:
                content = data["choices"][0]["message"]["content"]
            else:
                content = data["response"]
            return content or "[No content returned from LLM]"
        except (requests.RequestException, KeyError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries:
                wait = backoff * (2 ** attempt)
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s — retrying in %.1fs",
                    attempt + 1, max_retries + 1, exc, wait,
                )
                time.sleep(wait)

    raise last_error
