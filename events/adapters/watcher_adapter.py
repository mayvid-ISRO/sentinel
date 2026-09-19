"""File watcher adapter — monitor config/log files for changes.

Watches events.watcher.paths (poll-based, works on Windows + Linux with
no extra deps). Publishes:
  - file_modified  (warning) when a watched file changes content/size
  - file_created    (info) / file_deleted (warning) in watched directories

Use case: unauthorized config changes (e.g. /etc/ssh/sshd_config,
C:\\Windows\\System32\\drivers\\etc\\hosts) feed the security domain.

Files: events/service.py builds this from config; rules.json can route
file_modified events on sensitive paths to investigation tasks.
"""

import asyncio
import hashlib
import os
from pathlib import Path

from events.adapters.base import EventAdapter
from events.schema import Event


class WatcherAdapter(EventAdapter):
    name = "watcher"

    def __init__(self, bus, paths, interval: int = 10, max_file_mb: float = 5.0):
        super().__init__(bus)
        self.paths = [Path(p) for p in paths]
        self.interval = interval
        self.max_bytes = int(max_file_mb * 1024 * 1024)
        self._state: dict = {}  # path -> (size, mtime, hash)

    def _snapshot_dir(self, directory: Path) -> set:
        try:
            return {str(p): (p.stat().st_size, p.stat().st_mtime)
                    for p in directory.iterdir() if p.is_file()}
        except (PermissionError, OSError):
            return {}

    def _fingerprint(self, path: Path):
        try:
            stat = path.stat()
            if stat.st_size > self.max_bytes:
                return (stat.st_size, stat.st_mtime, None)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            return (stat.st_size, stat.st_mtime, digest)
        except (PermissionError, OSError):
            return None

    def sample(self) -> None:
        for base in self.paths:
            if not base.exists():
                continue
            targets = [base] if base.is_file() else sorted(base.iterdir())
            for target in targets:
                if not target.is_file():
                    continue
                key = str(target)
                current = self._fingerprint(target)
                previous = self._state.get(key)

                if current is None:
                    if previous is not None:
                        self._publish(Event(
                            source="watcher", source_type="file_deleted",
                            domain="security", severity="warning",
                            message=f"Watched file deleted: {key}", entity=key))
                    self._state.pop(key, None)
                    continue

                if previous is None:
                    self._publish(Event(
                        source="watcher", source_type="file_created",
                        domain="security", severity="info",
                        message=f"New file in watched location: {key}", entity=key))
                elif current != previous:
                    content_changed = current[2] is not None and previous[2] is not None \
                        and current[2] != previous[2]
                    self._publish(Event(
                        source="watcher", source_type="file_modified",
                        domain="security", severity="warning",
                        message=f"Watched file modified: {key}"
                                + (" (content changed)" if content_changed else ""),
                        entity=key,
                        details={"old_size": previous[0], "new_size": current[0]}))
                self._state[key] = current

    async def run(self) -> None:
        while True:
            try:
                self.sample()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("watcher sample failed")
            await asyncio.sleep(self.interval)
