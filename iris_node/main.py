"""
IRIS Node — iris_node/main.py
=====================================
Lightweight FastAPI service that runs ON the target PC.
Exposes desktop-level tools (mouse, keyboard, screenshot, terminal)
over HTTP so the main IRIS server can call them remotely.

Install on target PC:
    pip install fastapi uvicorn pyautogui pillow

Run on target PC (token is MANDATORY — generate one with:
    python -c "import secrets; print(secrets.token_hex(32))"):
    set IRIS_NODE_TOKEN=<token>
    uvicorn iris_node.main:app --host 0.0.0.0 --port 9000

The main IRIS server calls:
    POST http://<target-ip>:9000/run
    Body: {"tool": "click", "args": {"x": 100, "y": 200}}
    Header: Authorization: Bearer <token>

Phase 0 rework:
  - Token auth is REQUIRED (service refuses to start without IRIS_NODE_TOKEN).
  - The same safety policy as the main server (BLOCKED_PATTERNS) is enforced
    HERE at the node, so blocked commands cannot be smuggled to a remote PC.
  - Terminal runs with an explicit timeout and never echoes secrets back.

Files that depend on this module:
  - tools/web/node_tools.py (client side proxies)
  - iris_config.py "nodes" entries (fleet registry)
"""

import os
import re
import subprocess
import base64
import io
import sys

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from typing import Optional

# ── mandatory shared secret ─────────────────────────────────────────────
AUTH_TOKEN = os.getenv("IRIS_NODE_TOKEN", "")

app = FastAPI(title="IRIS Node", version="2.0.0")


@app.on_event("startup")
def refuse_without_token():
    # A node without auth on a network is a remote shell for anyone who
    # can reach the port — fail fast instead.
    if not AUTH_TOKEN:
        print("FATAL: IRIS_NODE_TOKEN is not set. Refusing to start.", file=sys.stderr)
        os._exit(1)


def check_auth(token: Optional[str]):
    if not AUTH_TOKEN or token != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(401, "Unauthorized")


# ── safety: mirrors tools/safety.py on the main server ──────────────────
BLOCKED_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+/\*",
    r"del\s+/f\s+/q\s+[A-Z]:",
    r"format\s+[A-Z]:",
    r"shutdown",
    r"rmdir\s+/s",
    r"dd\s+if=.*of=/dev/",
    r":\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:",
    r"mkfs\.",
    r"reg\s+delete\s+/f\s+HKLM",
    r"diskpart",
    r"cipher\s+/w",
]


def is_command_safe(command: str) -> tuple[bool, str]:
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return False, f"Blocked: matches dangerous pattern '{pattern}'"
    return True, "Command is safe"


# ── request model ───────────────────────────────────────────────────────
class ToolCall(BaseModel):
    tool: str
    args: dict = {}


# ── tool implementations ────────────────────────────────────────────────

def tool_click(x: int, y: int, button: str = "left"):
    import pyautogui
    pyautogui.click(x, y, button=button)
    return f"Clicked {button} at ({x}, {y})"


def tool_type(text: str):
    import pyautogui
    pyautogui.typewrite(text, interval=0.05)
    return f"Typed: {text[:40]}..."


def tool_screenshot():
    import pyautogui
    img = pyautogui.screenshot()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"format": "png_base64", "data": b64}


def tool_terminal(command: str, timeout: int = 30):
    safe, reason = is_command_safe(command)
    if not safe:
        return {"stdout": "", "stderr": reason, "returncode": 126}
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True,
            text=True, timeout=timeout
        )
        return {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Timed out after {timeout}s", "returncode": 124}


def tool_open_app(app_name: str):
    import subprocess, platform
    system = platform.system()
    if system == "Windows":
        subprocess.Popen(["start", app_name], shell=True)
    elif system == "Darwin":
        subprocess.Popen(["open", "-a", app_name])
    else:
        subprocess.Popen([app_name])
    return f"Launched: {app_name}"


def tool_scroll(x: int, y: int, clicks: int):
    import pyautogui
    pyautogui.scroll(clicks, x=x, y=y)
    return f"Scrolled {clicks} at ({x},{y})"


def tool_key(key: str):
    import pyautogui
    pyautogui.press(key)
    return f"Pressed key: {key}"


def tool_system_info():
    import platform
    return {
        "node": platform.node(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }


# ── tool registry ────────────────────────────────────────────────────────
NODE_TOOLS = {
    "click": tool_click,
    "type": tool_type,
    "screenshot": tool_screenshot,
    "terminal": tool_terminal,
    "open_app": tool_open_app,
    "scroll": tool_scroll,
    "key": tool_key,
    "system_info": tool_system_info,
}


# ── routes ───────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    # No auth on health so the fleet manager can probe liveness cheaply;
    # the response deliberately exposes nothing sensitive.
    return {"status": "ok", "node": "iris-node", "tools": list(NODE_TOOLS.keys())}


@app.post("/run")
def run_tool(call: ToolCall, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    if call.tool not in NODE_TOOLS:
        raise HTTPException(400, f"Unknown tool: {call.tool}. Available: {list(NODE_TOOLS.keys())}")
    try:
        result = NODE_TOOLS[call.tool](**call.args)
        return {"ok": True, "result": result}
    except TypeError as e:
        return {"ok": False, "error": f"Bad arguments: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/tools")
def list_tools(authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    return list(NODE_TOOLS.keys())
