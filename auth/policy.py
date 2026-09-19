"""
Tool Risk Policy — Phase 3 RBAC
================================
Maps every registered tool name to a risk tier and the number of human
approvals required before it can execute.

Risk tiers:
  read      — safe to run automatically (list, query, view)
  write     — requires 1 approver (modify state)
  destructive — requires 2 approvers (irreversible actions)

Usage in middleware:
  from auth.policy import tool_risk, approvals_required
  risk = tool_risk("browser_dom")          # → "read"
  n    = approvals_required(risk)          # → 0
"""

from __future__ import annotations

import logging
from typing import Dict, Literal

logger = logging.getLogger(__name__)

RiskTier = Literal["read", "write", "destructive"]
APPROVALS_REQUIRED = {"read": 0, "write": 1, "destructive": 2}

# ── Default tool → risk mapping ──────────────────────────────────────────
# Keys MUST match the 'name' field in tools/__init__.py registrations.
# Any name not listed here falls through to "write" (fail-closed).

_DEFAULT_TOOL_RISK: Dict[str, RiskTier] = {
    # Read-only tools — safe to auto-run (query/list/view)
    "run_terminal_command_tool": "read",       # local; safety gate in tools/system/
    "ssh_connect_tool":          "read",        # establish connection only
    "netapp_status":             "read",
    "netapp_list_volumes":       "read",
    "netapp_list_qtrees":        "read",
    "take_screenshot_tool":      "read",
    "browser_dom":               "read",        # read page HTML
    "browser_extract":           "read",        # extract text from page
    "workstation_01_system_info":"read",        # read-only remote info
    "workstation_01_screenshot": "read",

    # Write tools — require single approval (modify state)
    "browser_open":              "write",
    "browser_close":             "write",
    "browser_click":             "write",
    "browser_scroll":            "write",
    "browser_type":              "write",
    "click_tool":                "write",
    "type_tool":                 "write",
    "open_app_tool":             "write",
    "netapp_create_volume":      "write",
    "netapp_patch_volume":       "write",
    "netapp_delete_volume":      "write",       # risky but needs 1 approval
    "netapp_delete_qtree":       "write",
    "netapp_create_qtree":       "write",
    "netapp_create_quota":       "write",
    "netapp_create_cifs_share":  "write",
    "netapp_save_credentials":   "write",
    "workstation_01_click":      "write",
    "workstation_01_key":        "write",
    "workstation_01_type":       "write",
    "workstation_01_scroll":     "write",
    "workstation_01_terminal":   "write",
    "workstation_01_open_app":   "write",
    "ssh_disconnect_tool":       "write",

    # Destructive tools — require dual approval (irreversible / full-wipe)
    "delete_task_tool":          "destructive",
    "ssh_disconnect_all_tool":   "destructive",
    "browser_close_all_tool":    "destructive",
}


def tool_risk(tool_name: str) -> RiskTier:
    """Return the risk tier for *tool_name*. Falls back to 'write' (fail-closed)."""
    return _DEFAULT_TOOL_RISK.get(tool_name, "write")


def approvals_required(tool_name: str) -> int:
    """Number of distinct human approvers needed before execution."""
    return APPROVALS_REQUIRED[tool_risk(tool_name)]


def classify_action(action: dict) -> tuple[RiskTier, int]:
    """Given an action dict (from the agent), return (risk_tier, approvals_needed)."""
    name = action.get("tool", "")
    risk = tool_risk(name)
    return risk, APPROVALS_REQUIRED[risk]
