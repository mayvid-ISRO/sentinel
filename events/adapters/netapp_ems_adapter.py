"""NetApp EMS adapter — polls ONTAP cluster event logs.

Uses the credential store (tools/credentials.py, saved by
netapp_save_credentials) so no secrets live in config. Polls the
/private/cli/event/... REST path via the netapp_ontap SDK. When no
credentials are stored, the adapter no-ops with a single log line.

Mapping:
  - EMS severity EMERGENCY/ALERT -> critical/error, domain=storage
  - ALERT/ERROR EMS events      -> source_type="ems"
  - volume near-full EMS names  -> source_type="volume_capacity" (value=%%)
    so rules.json netapp-volume-capacity can fire on it.

Files: events/service.py builds from config; consumes tools/credentials.
"""

import asyncio
import logging

from events.adapters.base import EventAdapter
from events.schema import Event

log = logging.getLogger(__name__)


class NetAppEmsAdapter(EventAdapter):
    name = "netapp_ems"

    def __init__(self, bus, interval: int = 60):
        super().__init__(bus)
        self.interval = interval
        self._seen: set = set()

    def _credentials(self):
        try:
            import tools.credentials as cred
            data = cred.load("netapp")
            if all(data.get(k) for k in ("cluster", "api_user", "api_pass")):
                return data
        except Exception:
            pass
        return None

    def poll(self) -> None:
        creds = self._credentials()
        if not creds:
            log.debug("netapp ems: no stored credentials — skipping poll")
            return

        try:
            import requests
            from requests.auth import HTTPBasicAuth
            # unrecommended verify=False matches the SDK default the tools use
            r = requests.get(
                f"https://{creds['cluster']}/api/private/cli/event?severity=alert|error|emergency&max_records=20",
                auth=HTTPBasicAuth(creds["api_user"], creds["api_pass"]),
                verify=False, timeout=15,
            )
            if r.status_code != 200:
                log.warning("netapp ems poll returned %s", r.status_code)
                return
            for rec in r.json().get("records", []):
                self._emit(rec)
        except Exception as exc:
            log.warning("netapp ems poll failed: %s", exc)

    def _emit(self, rec: dict) -> None:
        key = (rec.get("index"), rec.get("event"), rec.get("time"))
        if key in self._seen:
            return
        self._seen.add(key)
        if len(self._seen) > 2000:
            self._seen.clear()

        severity_map = {"emergency": "critical", "alert": "error", "error": "error"}
        severity = severity_map.get(str(rec.get("severity", "error")).lower(), "error")

        source_type = "ems"
        value = None
        name = str(rec.get("event", "")).lower()
        entity = rec.get("node") or rec.get("ems") or "cluster"
        if "vol" in name and ("full" in name or "near" in name or "capac" in name):
            source_type = "volume_capacity"
            entity = rec.get("parameters", {}).get("volname") or entity
            # %% full often arrives in parameters; try to parse
            params = rec.get("parameters", {})
            for v in params.values():
                if isinstance(v, (int, float)) and 0 < v < 100:
                    value = float(v)
                    break

        self._publish(Event(
            source="netapp_ems", source_type=source_type, domain="storage",
            severity=severity,
            message=f"ONTAP EMS {rec.get('event')}: {rec.get('description', '')}"[:800],
            entity=str(entity), value=value,
            details={"ems": rec.get("ems"), "parameters": rec.get("parameters")},
        ))

    async def run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.poll)
            except Exception:
                log.exception("netapp ems adapter crashed (restarting next tick)")
            await asyncio.sleep(self.interval)
