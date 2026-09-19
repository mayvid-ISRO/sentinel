"""
Credential store.
=================
Persists per-domain credentials (e.g. NetApp cluster login) to
.iris/credentials.json inside the project so the user never re-enters them
across runs or restarts. Values are stored in plain text by design — the
store lives on the already-trusted, air-gapped host and is protected by
filesystem permissions; it is never logged (use agent/redact.py helpers).

Public API:
    save(domain, data)      — merge credentials under a domain key
    load(domain)            — get a domain dict ({} if missing)
    clear(domain=None)       — wipe one or all domains
    path                    — location of the store file

Files that depend on this module:
  - tools/netapp/netapp.py   (auto-fills cluster credentials when omitted)
  - tools/system/ssh_connect.py (could adopt the same pattern)
  - backend/main.py          (exposes /api/credentials status only, never values)
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from iris_config import get, PROJECT_ROOT

path: Path = PROJECT_ROOT / get("credentials", "store_path", ".iris/credentials.json")


def _read_all() -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_all(data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Restrict to current user on POSIX; Windows inherits ACLs from profile dir.
    previous = None
    if path.exists():
        previous = path.read_text(encoding="utf-8")
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if os.name == "posix":
            os.chmod(path, 0o600)
    except OSError:
        if previous is not None:
            path.write_text(previous, encoding="utf-8")
        raise


def save(domain: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Merge `data` under `domain` (existing keys win per-key merge)."""
    all_data = _read_all()
    merged = {**all_data.get(domain, {}), **data}
    all_data[domain] = merged
    _write_all(all_data)
    return merged


def load(domain: str) -> Dict[str, Any]:
    """Return the credentials dict for a domain ({} if absent)."""
    return _read_all().get(domain, {})


def clear(domain: Optional[str] = None) -> None:
    """Remove one domain, or the entire store when domain is None."""
    if domain is None:
        if path.exists():
            path.unlink()
        return
    all_data = _read_all()
    if domain in all_data:
        del all_data[domain]
        _write_all(all_data)
