"""Windows Event Log adapter — security & system channel monitoring.

Polls the Windows Event Log (via win32 API through the `pywin32`-free
`winreg`-free path: we shell out to PowerShell Get-WinEvent, which needs
ZERO extra dependencies — critical for the air-gapped offline bundle).

Detects:
  - Security 4625 (failed logon)          -> security/auth_failure
  - Security 4720 (user created)           -> security/user_created
  - System error-level events              -> system/winlog_error

On non-Windows hosts the adapter no-ops (returns without starting).

Files: events/service.py builds from config; feeds ssh-brute-force-style
rules on Windows hosts.
"""

import asyncio
import platform
import subprocess

from events.adapters.base import EventAdapter
from events.schema import Event

_PS_QUERY = (
    "Get-WinEvent -FilterHashtable @{LogName=@('Security','System'); "
    "StartTime=(Get-Date).AddSeconds(-%d)} -ErrorAction SilentlyContinue | "
    "Select-Object -First 50 TimeCreated, Id, LevelDisplayName, "
    "@{n='Msg';e={$_.Message.Substring(0,[Math]::Min(300,$_.Message.Length))}}, "
    "ProviderName | ConvertTo-Json -Compress"
)

_ID_MAP = {
    4625: ("auth_failure", "security", "error"),
    4720: ("user_created", "security", "warning"),
    4726: ("user_deleted", "security", "warning"),
    1102: ("audit_cleared", "security", "critical"),  # log cleared!
}


class WinLogAdapter(EventAdapter):
    name = "winlog"

    def __init__(self, bus, interval: int = 20, lookback: int = 60):
        super().__init__(bus)
        self.interval = interval
        self.lookback = lookback
        self._seen: set = set()  # (id, timecreated) dedup

    async def run(self) -> None:
        if platform.system() != "Windows":
            return
        loop = asyncio.get_event_loop()
        while True:
            try:
                await asyncio.to_thread(self.poll)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("winlog poll failed")
            await asyncio.sleep(self.interval)

    def poll(self) -> None:
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", _PS_QUERY % self.lookback],
                capture_output=True, text=True, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (subprocess.TimeoutExpired, OSError):
            return
        if result.returncode != 0 or not result.stdout.strip():
            return

        import json
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return
        if isinstance(data, dict):  # single event
            data = [data]

        for rec in data:
            evid = rec.get("Id")
            created = str(rec.get("TimeCreated", ""))
            key = (evid, created, rec.get("Msg", "")[:60])
            if key in self._seen:
                continue
            self._seen.add(key)
            if len(self._seen) > 5000:
                self._seen.clear()

            if evid in _ID_MAP:
                source_type, domain, severity = _ID_MAP[evid]
            elif "error" in str(rec.get("LevelDisplayName", "")).lower():
                source_type, domain, severity = "winlog_error", "system", "error"
            elif "warning" in str(rec.get("LevelDisplayName", "")).lower():
                source_type, domain, severity = "winlog_warn", "system", "warning"
            else:
                continue  # info-level noise: skip

            self._publish(Event(
                source="winlog", source_type=source_type, domain=domain,
                severity=severity,
                message=f"[EventID {evid}] {rec.get('Msg', '(no message)')}",
                entity=platform.node() or "this-pc",
                details={"event_id": evid, "provider": rec.get("ProviderName")},
            ))
