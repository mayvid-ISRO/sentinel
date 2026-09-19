"""
Secret redaction.
=================
Applies the configured regex patterns to any string before it is written to
logs, the database, or the WebSocket UI. Used by agent step recording and the
credential store.

Files that depend on this module:
  - agent/agent.py        (redacts step results / observations)
  - tools/credentials.py  (redacts before logging)
  - backend/main.py       (redacts task descriptions before DB write)
"""

import re
from typing import List

from iris_config import get

_CACHE: List[re.Pattern] = []


def _patterns() -> List[re.Pattern]:
    """Compile redaction patterns once, reload when config reloads."""
    if not _CACHE:
        raw = get("security", "redact_patterns", []) or []
        _CACHE.extend(re.compile(p, re.IGNORECASE) for p in raw)
    return _CACHE


def clear_cache() -> None:
    """Drop compiled patterns (call after a config reload in tests)."""
    _CACHE.clear()


def redact(text: str) -> str:
    """Replace secret-looking substrings with the configured placeholder."""
    if not text:
        return text
    out = text
    placeholder = get("security", "redact_placeholder", "«REDACTED»")
    for pattern in _patterns():
        # Keep group 1 (the key name), mask group 2 (the value).
        out = pattern.sub(lambda m: f"{m.group(1)}={placeholder}", out)
    return out
