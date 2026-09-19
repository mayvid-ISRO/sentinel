"""
Phase 5 — Fleet Registry & Parallel Dispatch Tests
====================================================
Covers FleetRegistry (CRUD, persistence, health polling, stale cleanup)
and ParallelDispatcher (fan-out, result aggregation).

Run: python -m pytest tests/test_fleet.py -v
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch


# ══ helpers ──────────────────────────────────────────────────────────────

def _write_nodes(path: Path, entries: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")


# ══ FleetRegistry ═══════════════════════════════════════════════════════

class TestFleetRegistryBasics:
    def test_empty_registry(self):
        from fleet.registry import FleetRegistry
        r = FleetRegistry()
        assert r.list_nodes() == []

    def test_add_and_list_node(self):
        from fleet.registry import FleetRegistry
        r = FleetRegistry()
        r.add_node("w01", "192.168.1.1", 9000)
        nodes = r.list_nodes()
        assert len(nodes) == 1
        assert nodes[0]["name"] == "w01"
        assert nodes[0]["host"] == "192.168.1.1"
        assert nodes[0]["port"] == 9000

    def test_update_existing_node_preserves_health(self):
        from fleet.registry import FleetRegistry
        r = FleetRegistry()
        r.add_node("w01", "10.0.0.1", 9000)
        r.mark_healthy("w01")
        # Update with new host
        r.add_node("w01", "10.0.0.2", 9001)
        node = r.get_node("w01")
        assert node is not None
        assert node.host == "10.0.0.2"
        assert node.port == 9001
        assert node.healthy is True  # preserved

    def test_remove_node(self):
        from fleet.registry import FleetRegistry
        r = FleetRegistry()
        assert r.remove_node("ghost") is False
        r.add_node("w01", "1.2.3.4", 9000)
        assert r.remove_node("w01") is True
        assert len(r.list_nodes()) == 0

    def test_get_node_returns_none_missing(self):
        from fleet.registry import FleetRegistry
        r = FleetRegistry()
        assert r.get_node("nope") is None


class TestFleetPersistence:
    def test_save_and_reload(self, tmp_path: Path):
        from fleet.registry import FleetRegistry
        store = tmp_path / "nodes.json"
        r = FleetRegistry(registry_path=str(store))
        r.add_node("w01", "10.0.0.1", 9000, "TOKEN_ENV_W01")
        r.add_node("w02", "10.0.0.2", 9001)
        # Verify file written
        assert store.exists()
        data = json.loads(store.read_text())
        assert len(data) == 2
        assert data[0]["name"] == "w01"
        # Reload from disk
        r2 = FleetRegistry(registry_path=str(store))
        nodes = r2.list_nodes()
        assert len(nodes) == 2
        names = {n["name"] for n in nodes}
        assert names == {"w01", "w02"}

    def test_clear_persists_empty(self, tmp_path: Path):
        from fleet.registry import FleetRegistry
        store = tmp_path / "nodes.json"
        r = FleetRegistry(registry_path=str(store))
        r.add_node("w01", "1.1.1.1", 9000)
        r.remove_node("w01")
        r2 = FleetRegistry(registry_path=str(store))
        assert r2.list_nodes() == []


class TestFleetHealth:
    def test_mark_healthy_updates_state(self):
        from fleet.registry import FleetRegistry
        r = FleetRegistry()
        r.add_node("w01", "1.2.3.4", 9000)
        r.mark_healthy("w01")
        node = r.get_node("w01")
        assert node.healthy is True
        assert node.status == "healthy"
        assert node.last_health_ts > 0

    def test_mark_unhealthy(self):
        from fleet.registry import FleetRegistry
        r = FleetRegistry()
        r.add_node("w01", "1.2.3.4", 9000)
        r.mark_healthy("w01")
        r.mark_unhealthy("w01")
        node = r.get_node("w01")
        assert node.healthy is False
        assert node.status == "unhealthy"

    def test_stale_cleanup(self, tmp_path: Path):
        """Nodes older than HEALTH_TTL should be auto-removed."""
        from fleet.registry import FleetRegistry, HEALTH_TTL
        store = tmp_path / "nodes.json"
        r = FleetRegistry(registry_path=str(store))
        r.add_node("old-node", "1.2.3.4", 9000)
        # Manually age the node past TTL
        node = r.get_node("old-node")
        assert node is not None
        node.last_health_ts = time.time() - HEALTH_TTL - 10
        # Poll would normally clean, but we can verify directly
        removed = r._cleanup_stale()
        assert removed == 1
        assert len(r.list_nodes()) == 0

    def test_get_token_from_env(self, monkeypatch):
        from fleet.registry import FleetRegistry
        monkeypatch.setenv("MY_TOKEN_VAR", "secret123")
        r = FleetRegistry()
        r.add_node("w01", "1.2.3.4", 9000, "MY_TOKEN_VAR")
        token = r.get_token("w01")
        assert token == "secret123"

    def test_get_token_empty_when_no_env(self, monkeypatch):
        from fleet.registry import FleetRegistry
        monkeypatch.delenv("NONEXISTENT_TOKEN_XYZ", raising=False)
        r = FleetRegistry()
        r.add_node("w01", "1.2.3.4", 9000, "NONEXISTENT_TOKEN_XYZ")
        assert r.get_token("w01") == ""


# ══ ParallelDispatcher ══════════════════════════════════════════════════

class TestParallelDispatcher:
    def test_runs_on_empty_fleet(self, tmp_path: Path):
        import asyncio
        from fleet.registry import FleetRegistry
        from agent.parallel import ParallelDispatcher
        reg = FleetRegistry(registry_path=str(tmp_path / "nodes.json"))
        disp = ParallelDispatcher(reg)
        summary = asyncio.run(disp.run_parallel("restart all services"))
        assert summary["summary"]["total"] == 0
        assert summary["results"] == {}

    def test_dispatch_to_mock_healthy_node(self, tmp_path: Path):
        import asyncio
        from fleet.registry import FleetRegistry
        from agent.parallel import ParallelDispatcher
        reg = FleetRegistry(registry_path=str(tmp_path / "nodes.json"))
        reg.add_node("mock-node", "127.0.0.1", 19999)
        reg.mark_healthy("mock-node")
        disp = ParallelDispatcher(reg)

        # Mock urllib.request to avoid actual network call
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"ok": true, "result": "echo done"}'
        mock_response.status = 200

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = asyncio.run(disp.run_parallel("echo hello"))
        assert result["summary"]["total"] == 1
        assert "mock-node" in result["results"]
        assert result["results"]["mock-node"]["status"] == "ok"

    def test_failed_node_returns_error(self, tmp_path: Path):
        import asyncio
        from fleet.registry import FleetRegistry
        from agent.parallel import ParallelDispatcher
        reg = FleetRegistry(registry_path=str(tmp_path / "nodes.json"))
        reg.add_node("broken-node", "127.0.0.1", 19998)
        reg.mark_healthy("broken-node")
        disp = ParallelDispatcher(reg)

        with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
            result = asyncio.run(disp.run_parallel("test task"))
        assert result["summary"]["total"] == 1
        assert result["summary"]["failed"] == 1
        assert result["results"]["broken-node"]["status"] == "error"


# ══ Fleet API Endpoints ═════════════════════════════════════════════════

class TestFleetEndpoints:
    def setup_method(self):
        from backend.main import _get_fleet_registry, _fleet_lock
        # Reset singleton for test isolation
        with _fleet_lock:
            from backend.main import _fleet_registry as fr
            if fr is not None:
                # Can't reset easily; use fresh registry via temp path
                pass
        from fastapi.testclient import TestClient
        from backend.main import app
        self.client = TestClient(app)
        # Clean up any pre-existing test nodes
        for node in self.client.get("/api/fleet/nodes").json()["nodes"]:
            self.client.delete(f"/api/fleet/nodes/{node['name']}")

    def test_list_nodes_empty(self):
        r = self.client.get("/api/fleet/nodes")
        assert r.status_code == 200
        data = r.json()
        assert "nodes" in data
        assert "count" in data

    def test_add_and_list_node(self):
        r = self.client.post("/api/fleet/nodes", json={
            "name": "test-node", "host": "127.0.0.1", "port": 9999
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True
        r2 = self.client.get("/api/fleet/nodes")
        names = [n["name"] for n in r2.json()["nodes"]]
        assert "test-node" in names

    def test_add_node_missing_fields_raises_400(self):
        r = self.client.post("/api/fleet/nodes", json={"name": "x"})
        assert r.status_code == 400

    def test_add_node_invalid_port_raises_400(self):
        r = self.client.post("/api/fleet/nodes", json={
            "name": "x", "host": "1.2.3.4", "port": "not-a-number"
        })
        assert r.status_code == 400

    def test_delete_node(self):
        self.client.post("/api/fleet/nodes", json={
            "name": "to-delete", "host": "1.2.3.4", "port": 9000
        })
        r = self.client.delete("/api/fleet/nodes/to-delete")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_fleet_health(self):
        r = self.client.get("/api/fleet/health")
        assert r.status_code == 200
        data = r.json()
        assert "total" in data
        assert "healthy" in data
        assert "unknown" in data

    def test_fleet_run_empty(self):
        r = self.client.post("/api/fleet/run", json={"task": "hello"})
        assert r.status_code == 200
        data = r.json()
        assert data["summary"]["total"] == 0

    def test_fleet_run_missing_task(self):
        r = self.client.post("/api/fleet/run", json={})
        assert r.status_code == 400
