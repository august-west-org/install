"""Plain-English service status: are the four apps answering on their local
ports, and how long since the last successful backup.

All four services bind 127.0.0.1 (the stack's loopback-only model), so these
checks work even when the tunnel is OFF -- going dark severs only the public
path, not the services themselves.
"""
import re
from datetime import datetime, timezone

import httpx

# (key, customer-facing label, local health URL)
SERVICES = [
    ("photo_vault", "Photo Vault", "http://127.0.0.1:2283/api/server/ping"),
    ("password_safe", "Password Safe", "http://127.0.0.1:8443/alive"),
    ("file_vault", "File Vault", "http://127.0.0.1:8080/status.php"),
    ("smart_home", "Smart Home", "http://127.0.0.1:8123/manifest.json"),
]

BACKUP_LOG = "/var/log/augustwest-backup.log"
# install.sh's backup runner writes "=== <date -Is> augustwest backup done ==="
_DONE_RE = re.compile(r"===\s*(\S+)\s+augustwest backup done ===")


async def service_status() -> list[dict]:
    results = []
    async with httpx.AsyncClient(timeout=4) as client:
        for key, label, url in SERVICES:
            online = False
            try:
                resp = await client.get(url)
                online = resp.status_code < 400
            except httpx.HTTPError:
                online = False
            results.append({"key": key, "label": label, "online": online})
    return results


def last_backup() -> dict:
    """{'configured': bool, 'iso': str|None, 'hours_ago': float|None}.

    - configured False -> backups aren't set up on this device (no log file).
    - configured True, iso None -> configured but no backup has completed yet.
    """
    last_ts = None
    try:
        with open(BACKUP_LOG) as f:
            for line in f:
                m = _DONE_RE.search(line)
                if m:
                    last_ts = m.group(1)
    except FileNotFoundError:
        return {"configured": False, "iso": None, "hours_ago": None}

    if not last_ts:
        return {"configured": True, "iso": None, "hours_ago": None}
    try:
        dt = datetime.fromisoformat(last_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        return {"configured": True, "iso": last_ts, "hours_ago": round(max(hours, 0.0), 1)}
    except ValueError:
        return {"configured": True, "iso": last_ts, "hours_ago": None}
