"""Reports the device's FALLBACK path -- the Tailscale/Headscale mesh -- so the
customer can still reach this dashboard when the Cloudflare Tunnel is off.

The offline toggle stops aw-cloudflared, which is also the phone's only route to
this app: without a second address, "offline" is a one-way door. The host keeps a
Tailscale client joined to the August West Headscale coordinator as an
independent always-on service and publishes its state to
/etc/augustwest/mesh/state (see mesh/aw-mesh-ctl on the host).

This module only READS that file. The dashboard container holds no host root or
systemd access, exactly like the tunnel spool in tunnel.py -- it cannot bring the
mesh up, and nothing here can take it down.
"""
import json
import os
import time
from datetime import datetime, timezone

SPOOL_DIR = os.environ.get("AW_MESH_SPOOL_DIR", "/etc/augustwest/mesh")
STATE_PATH = os.path.join(SPOOL_DIR, "state")

# The host republishes every 60s; past this we stop trusting the file's contents
# rather than showing the customer a fallback address that may be long gone.
STALE_AFTER_SECONDS = 300

DASHBOARD_PORT = 8889


def read_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _age_seconds(state: dict) -> float | None:
    stamp = state.get("updated")
    if not stamp:
        return None
    try:
        when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0.0, time.time() - when.timestamp())


def address() -> str | None:
    """host:port the dashboard answers on over the mesh, or None."""
    state = read_state()
    ip = (state.get("ipv4") or "").strip()
    return f"{ip}:{DASHBOARD_PORT}" if ip else None


def status() -> dict:
    """Full mesh status for a signed-in customer / support.

    `available` is deliberately strict: the link is only advertised as a way back
    in when the client is registered AND holds an address AND the tailnet-side
    listener is up AND the host is still publishing. Anything less is reported as
    unavailable with a reason, because a fallback address that quietly does not
    work is worse than none.
    """
    state = read_state()
    if not state:
        return {
            "available": False,
            "reason": "The backup connection isn't set up on this device yet.",
            "configured": False,
        }

    age = _age_seconds(state)
    stale = age is not None and age > STALE_AFTER_SECONDS
    ip = (state.get("ipv4") or "").strip()
    backend = state.get("backend") or "unknown"
    listener = bool(state.get("listener_active"))
    running = backend == "Running"

    if stale:
        reason = "The backup connection hasn't reported in — it may not be working."
    elif not running:
        reason = (
            "The backup connection is waiting to be approved by August West support."
            if backend in ("NeedsLogin", "NoState")
            else "The backup connection is starting up."
        )
    elif not ip:
        reason = "The backup connection has no address yet."
    elif not listener:
        reason = "The backup connection is up, but this dashboard isn't listening on it yet."
    else:
        reason = None

    return {
        "available": reason is None,
        "reason": reason,
        "configured": True,
        "address": f"{ip}:{DASHBOARD_PORT}" if ip else None,
        "url": state.get("dashboard_url") or (f"http://{ip}:{DASHBOARD_PORT}" if ip else None),
        "hostname": state.get("hostname") or None,
        "dns_name": state.get("dns_name") or None,
        "coordinator": state.get("coordinator") or None,
        "backend": backend,
        "control_transport": state.get("control_transport") or None,
        "service_active": bool(state.get("service_active")),
        "listener_active": listener,
        "stale": stale,
        "updated": state.get("updated"),
    }


def public_status() -> dict:
    """The subset shown on the login screen, BEFORE sign-in.

    Deliberately minimal: the address, the device's tailnet name, and whether it
    is usable. It is published unauthenticated on purpose -- the customer needs to
    be able to write this address down (or read it to support) while the tunnel is
    still up, which is exactly when they cannot yet sign in over the mesh. What
    leaks is a CGNAT address that is unroutable from the public internet and
    grants nothing without both tailnet membership and the master password.
    """
    s = status()
    return {
        "available": s["available"],
        "configured": s["configured"],
        "reason": s.get("reason"),
        "address": s.get("address"),
        "url": s.get("url"),
        "hostname": s.get("hostname"),
        "coordinator": s.get("coordinator"),
    }
