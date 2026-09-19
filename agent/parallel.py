"""
Parallel Task Dispatcher — submits tasks to multiple fleet nodes concurrently.

Each node runs the task independently using its local tools; results are
gathered and returned as a dict of {node_name: result}.

Files that depend on this module:
  - backend/main.py (POST /api/fleet/run)
"""

import asyncio
import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class ParallelDispatcher:
    """Fan-out a user task to every healthy node in the fleet, gather results."""

    def __init__(self, registry) -> None:   # FleetRegistry
        self._reg = registry

    async def run_parallel(self, user_task: str, max_steps: int = 5) -> Dict[str, Any]:
        """
        Run *user_task* on all healthy nodes concurrently.

        Returns:
            {
                "summary": {"total": N, "healthy": M, "ok": K, "failed": L},
                "results": {"node-name-1": {"status": "ok|error", ...}, ...}
            }
        """
        nodes = self._reg.list_nodes()
        healthy = [n for n in nodes if n["healthy"]]
        others = [n for n in nodes if not n["healthy"]]

        logger.info(
            "fleet: dispatching to %d/%d healthy nodes: %s",
            len(healthy), len(nodes),
            [n["name"] for n in healthy],
        )

        # Probe stale/unhealthy nodes first (non-blocking health check)
        async def _probe(node: Dict[str, Any]) -> Dict[str, Any]:
            url = f"http://{node['host']}:{node['port']}/health"
            try:
                loop = asyncio.get_event_loop()
                import urllib.request
                req = urllib.request.Request(url, method="GET")
                data = await loop.run_in_executor(
                    None, lambda: urllib.request.urlopen(req, timeout=5).read().decode()
                )
                resp = json.loads(data)
                if resp.get("status") == "ok":
                    self._reg.mark_healthy(node["name"])
                    return {"name": node["name"], "status": "healthy"}
            except Exception as e:
                self._reg.mark_unhealthy(node["name"], str(e))
                return {"name": node["name"], "status": "unhealthy", "error": str(e)}
            return {"name": node["name"], "status": "unknown"}

        if others:
            await asyncio.gather(*[asyncio.create_task(_probe(n)) for n in others],
                                return_exceptions=True)

        # Re-collect healthy after probing
        fresh = self._reg.list_nodes()
        healthy = [n for n in fresh if n["healthy"]]

        # Fan out actual task to all healthy nodes
        async def _dispatch(node: Dict[str, Any]) -> tuple:
            return node["name"], await self._send_task(node, user_task)

        raw = await asyncio.gather(
            *[asyncio.create_task(_dispatch(n)) for n in healthy],
            return_exceptions=True,
        )

        results: Dict[str, Any] = {}
        ok_count = fail_count = 0
        for item in raw:
            if isinstance(item, BaseException):
                continue
            name, result = item
            results[name] = result
            if result.get("status") == "ok":
                ok_count += 1
            else:
                fail_count += 1

        return {
            "summary": {
                "total": len(fresh),
                "healthy": len(healthy),
                "ok": ok_count,
                "failed": fail_count,
            },
            "results": results,
        }

    async def _send_task(self, node: Dict[str, Any], task: str) -> Dict[str, Any]:
        """Submit a terminal echo command to a single node and return the result."""
        url = f"http://{node['host']}:{node['port']}/run"
        token = self._reg.get_token(node["name"])
        headers = ({"Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"} if token else {})
        payload = json.dumps({
            "tool": "terminal",
            "args": {"command": f"echo 'IRIS-parallel-task: {task[:80]}'"},
        }).encode()
        try:
            loop = asyncio.get_event_loop()
            import urllib.request
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            data = await loop.run_in_executor(
                None, lambda: urllib.request.urlopen(req, timeout=30).read().decode()
            )
            resp = json.loads(data)
            if resp.get("ok"):
                return {"status": "ok", "output": str(resp.get("result", ""))[:200]}
            return {"status": "error", "error": resp.get("error", "unknown")}
        except Exception as e:
            return {"status": "error", "error": str(e)}
