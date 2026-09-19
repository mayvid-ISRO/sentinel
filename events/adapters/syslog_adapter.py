"""Syslog UDP adapter — receive RFC3164/RFC5424-style messages from network
devices, Linux hosts (rsyslog forwarders), and NetApp EMS syslog mirrors.

Listens on events.syslog.port (default 5514 — use 514 only with adjusted
OS privileges). Publishes domain/security/auth events; auth failures are
detected by keyword so the ssh-brute-force rule can fire.

Files: events/service.py builds this from config; see base.py for the API.
"""

import asyncio
import logging
import re
import socket

from events.adapters.base import EventAdapter
from events.schema import Event

logger = logging.getLogger(__name__)

# severity keyword map — extend as sources are added
_ERROR_WORDS = re.compile(r"\b(error|crit|emerg|fatal|failed|failure|denied)\b", re.I)
_WARN_WORDS = re.compile(r"\b(warn|degrad|near|high|retry)\b", re.I)
_AUTH_FAIL = re.compile(
    r"(failed password|authentication failure|invalid user|login failed|"
    r"failed login|access denied)", re.I)


class SyslogAdapter(EventAdapter):
    name = "syslog"

    def __init__(self, bus, listen: str = "0.0.0.0", port: int = 5514):
        super().__init__(bus)
        self.listen = listen
        self.port = port

    async def run(self) -> None:
        loop = asyncio.get_event_loop()
        transport, _protocol = await loop.create_datagram_endpoint(
            lambda: _SyslogProtocol(self),
            local_addr=(self.listen, self.port),
        )
        logger.info("syslog adapter listening on udp://%s:%d", self.listen, self.port)
        try:
            await asyncio.Event().wait()  # run until cancelled
        finally:
            transport.close()

    def handle_datagram(self, data: bytes, addr) -> None:
        """Called by the UDP protocol; parse + publish."""
        try:
            text = data.decode("utf-8", errors="replace").strip()
        except Exception:
            return
        if not text:
            return

        host = addr[0] if addr else "unknown"
        if _AUTH_FAIL.search(text):
            severity, source_type, domain = "error", "auth_failure", "security"
        elif _ERROR_WORDS.search(text):
            severity, source_type, domain = "error", "syslog_error", "system"
        elif _WARN_WORDS.search(text):
            severity, source_type, domain = "warning", "syslog_warn", "system"
        else:
            severity, source_type, domain = "info", "syslog_info", "system"

        self._publish(Event(
            source="syslog",
            source_type=source_type,
            domain=domain,
            severity=severity,
            message=text[:800],
            entity=host,
            details={"raw": text[:1500]},
        ))


class _SyslogProtocol(asyncio.DatagramProtocol):
    def __init__(self, adapter: SyslogAdapter):
        self.adapter = adapter

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            self.adapter.handle_datagram(data, addr)
        except Exception:
            pass  # one malformed packet must never kill the listener

    def error_received(self, exc) -> None:
        pass
