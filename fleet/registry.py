"""
Fleet Registry — manages known iris_node instances.

Each node has a name, host, port, and optional token (loaded from env).
The registry periodically pings each node's /health endpoint; stale nodes
(expired health check) are auto-removed after HEALTH_TTL seconds.

Thread-safe via threading.Lock. Intended to be used from asyncio event-loop
threads; the sync lock + async call pattern is safe because the critical
section is tiny (dict read/write only).

Files that depend on this module:
  - agent/parallel.py (dispatches tasks to live nodes)
  - backend/main.py (fleet API endpoints)
  - frontend/index.html (Fleet status tab)
"""

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

HEALTH_TTL = 120          # seconds — node removed if no health ping within this window
POLL_INTERVAL = 15        # seconds between background health checks
DEFAULT_REGISTRY_PATH = ".iris/nodes.json"


@dataclass
class NodeEntry:
    """A registered iris_node in the fleet."""
    name: str
    host: str
    port: int
    token_env: str = ""          # env var holding the auth token
    last_health_ts: float = 0.0  # epoch seconds of last successful ping
    healthy: bool = False
    status: str = "unknown"      # "healthy", "unhealthy", "unknown"


class FleetRegistry:
    """In-memory registry of managed nodes with periodic health polling."""

    def __init__(self, registry_path: str = DEFAULT_REGISTRY_PATH) -> None:
        self._path = Path(registry_path)
        self._nodes: Dict[str, NodeEntry] = {}
        self._lock = threading.RLock()  # RLock for recursive acquisition (save_nodes called inside locked sections)
        self._poll_task: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.load_initial_nodes()

    # ── persistence ────────────────────────────────────────────────────

    def load_initial_nodes(self) -> int:
        """Load node definitions from JSON file and return count loaded."""
        if not self._path.exists():
            return 0
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            logger.warning("fleet: could not read %s", self._path)
            return 0
        count = 0
        for entry in raw:
            name = entry.get("name", "")
            if not name:
                continue
            self._nodes[name] = NodeEntry(
                name=name,
                host=entry.get("host", ""),
                port=int(entry.get("port", 9000)),
                token_env=entry.get("token_env", ""),
            )
            count += 1
        logger.info("fleet: loaded %d node(s) from %s", count, self._path)
        return count

    def save_nodes(self) -> None:
        """Persist current node list (without health state) to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = []
        with self._lock:
            for n in self._nodes.values():
                data.append({
                    "name": n.name,
                    "host": n.host,
                    "port": n.port,
                    "token_env": n.token_env,
                })
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.debug("fleet: saved %d node(s) to %s", len(data), self._path)

    # ── node CRUD ──────────────────────────────────────────────────────

    def add_node(self, name: str, host: str, port: int, token_env: str = "") -> bool:
        """Register a new node. Returns True if added or updated."""
        with self._lock:
            existing = self._nodes.get(name)
            if existing:
                # Preserve health state on update
                self._nodes[name] = NodeEntry(
                    name=name, host=host, port=port,
                    token_env=token_env,
                    last_health_ts=existing.last_health_ts,
                    healthy=existing.healthy,
                    status=existing.status,
                )
            else:
                self._nodes[name] = NodeEntry(name=name, host=host, port=port,
                                               token_env=token_env)
            logger.info("fleet: node '%s' registered at %s:%d", name, host, port)
            self.save_nodes()
            return True

    def remove_node(self, name: str) -> bool:
        """Remove a node by name. Returns True if it existed."""
        with self._lock:
            removed = name in self._nodes
            self._nodes.pop(name, None)
        if removed:
            self.save_nodes()
            logger.info("fleet: node '%s' removed", name)
        return removed

    def get_node(self, name: str) -> Optional[NodeEntry]:
        with self._lock:
            return self._nodes.get(name)

    def list_nodes(self) -> List[Dict[str, Any]]:
        """Return snapshot of all nodes as dicts."""
        with self._lock:
            return [asdict(n) for n in self._nodes.values()]

    # ── health polling ─────────────────────────────────────────────────

    def _ping_node(self, node: NodeEntry) -> None:
        """Probe a single node's /health endpoint (runs in poll thread)."""
        url = f"http://{node.host}:{node.port}/health"
        try:
            import urllib.request
            req = urllib.request.Request(url, method="GET")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    with self._lock:
                        n = self._nodes.get(node.name)
                        if n:
                            n.last_health_ts = time.time()
                            n.healthy = True
                            n.status = "healthy"
                    logger.debug("fleet: node '%s' healthy", node.name)
                else:
                    self._mark_unhealthy(node)
        except Exception as e:
            self._mark_unhealthy(node, str(e))

    def _mark_unhealthy(self, node: NodeEntry, reason: str = "") -> None:
        with self._lock:
            n = self._nodes.get(node.name)
            if n:
                n.healthy = False
                n.status = "unhealthy"
                logger.debug("fleet: node '%s' unhealthy: %s", node.name, reason)

    def mark_healthy(self, name: str) -> None:
        """Public helper: set a node's health state to healthy."""
        with self._lock:
            n = self._nodes.get(name)
            if n:
                n.healthy = True
                n.status = "healthy"
                n.last_health_ts = time.time()

    def mark_unhealthy(self, name: str, reason: str = "") -> None:
        """Public helper: set a node's health state to unhealthy."""
        with self._lock:
            n = self._nodes.get(name)
            if n:
                n.healthy = False
                n.status = "unhealthy"

    def _cleanup_stale(self) -> int:
        """Remove nodes whose last health ping exceeds HEALTH_TTL. Return count."""
        now = time.time()
        stale = []
        with self._lock:
            for name, n in self._nodes.items():
                if n.last_health_ts > 0 and (now - n.last_health_ts) > HEALTH_TTL:
                    stale.append(name)
            for name in stale:
                del self._nodes[name]
        if stale:
            self.save_nodes()
            logger.info("fleet: removed %d stale node(s): %s", len(stale), stale)
        return len(stale)

    def start_polling(self) -> None:
        """Start background health-check thread. Idempotent."""
        if self._poll_task and self._poll_task.is_alive():
            return
        self._stop_event.clear()

        def _poll_loop() -> None:
            while not self._stop_event.wait(timeout=POLL_INTERVAL):
                with self._lock:
                    nodes_snapshot = list(self._nodes.values())
                for node in nodes_snapshot:
                    self._ping_node(node)
                self._cleanup_stale()

        self._poll_task = threading.Thread(target=_poll_loop, daemon=True,
                                           name="fleet-poller")
        self._poll_task.start()
        logger.info("fleet: health poller started (interval=%ds)", POLL_INTERVAL)

    def stop_polling(self) -> None:
        self._stop_event.set()
        if self._poll_task:
            self._poll_task.join(timeout=5)
            self._poll_task = None
        logger.info("fleet: health poller stopped")

    # ── dispatch helper ────────────────────────────────────────────────

    def get_token(self, name: str) -> str:
        """Resolve auth token from env var for the given node name."""
        node = self.get_node(name)
        if not node or not node.token_env:
            return ""
        return os.getenv(node.token_env, "")
