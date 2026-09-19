# tools/ssh_manager.py
import paramiko
import logging
from threading import Lock

log = logging.getLogger(__name__)

class SSHManager:
    """
    Simple thread‑safe manager that stores one paramiko.SSHClient per host.
    You can reuse the same connection for multiple commands, or let it
    be closed automatically after a timeout.
    """
    _instance = None
    _lock = Lock()

    def __new__(cls):
        # Classic singleton pattern
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._sessions = {}          # host → SSHClient
        return cls._instance

    def get(self, host: str) -> paramiko.SSHClient | None:
        return self._sessions.get(host)

    def set(self, host: str, client: paramiko.SSHClient):
        self._sessions[host] = client

    def close(self, host: str):
        client = self._sessions.pop(host, None)
        if client:
            client.close()
            log.info("SSH session to %s closed", host)

    def close_all(self):
        for host in list(self._sessions):
            self.close(host)