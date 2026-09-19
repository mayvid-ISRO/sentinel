"""
tools/web/node_tools.py
=======================
Remote desktop tool proxies.

These proxy the IRIS Node service (iris_node/main.py) running on a target
PC. Registered automatically by tools/tools/__init__.py for every entry in
config.json "nodes" — tool names become node_<name>_<action> (e.g.
node_workstation_01_terminal) so the LLM can target a specific machine.
Node auth tokens come from the token_env variable of each config entry,
never from the config file itself.
"""

import requests
from tools.base import Tool


def make_node_tools(host: str, port: int = 9000, token: str = "", prefix: str = "node") -> dict:
    """
    Dynamically creates Tool objects that forward calls to an IRIS Node.
    Call once per target machine; returns a dict ready to merge into TOOLS.
    """
    base_url = f"http://{host}:{port}"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    prefix = prefix.replace("-", "_").replace(" ", "_").lower()

    def _call(tool_name: str, **kwargs):
        try:
            r = requests.post(
                f"{base_url}/run",
                json={"tool": tool_name, "args": kwargs},
                headers=headers,
                timeout=30
            )
            r.raise_for_status()
            data = r.json()
            if not data.get("ok"):
                return f"[Node error: {data.get('error', 'unknown')}]"
            return data.get("result", "no response")
        except requests.exceptions.ConnectionError:
            return f"[IRIS Node unreachable at {host}:{port}]"
        except Exception as e:
            return f"[Node error: {e}]"

    def _mk(action: str, description: str, schema: dict):
        name = f"{prefix}_{action}"
        return name, Tool(
            name=name,
            description=f"{description} (remote PC: {host})",
            args_schema=schema,
            func=lambda a=action, **kw: _call(a, **kw),
        )

    tools = dict([
        _mk("click", "Click mouse at (x, y) on remote PC", {"x": int, "y": int, "button": str}),
        _mk("type", "Type text on remote PC keyboard", {"text": str}),
        _mk("screenshot", "Take screenshot of remote PC, returns base64 PNG", {}),
        _mk("terminal", "Run a shell command on remote PC (safety-checked at the node)", {"command": str, "timeout": int}),
        _mk("open_app", "Open an application on remote PC", {"app_name": str}),
        _mk("scroll", "Scroll at (x,y) on remote PC", {"x": int, "y": int, "clicks": int}),
        _mk("key", "Press a single key on remote PC", {"key": str}),
        _mk("system_info", "Get OS/platform info of remote PC", {}),
    ])
    return tools
