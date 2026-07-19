"""Controls the Cloudflare Tunnel (aw-cloudflared.service) that exposes the
customer's home to the internet, and reports whether it is currently up.

The tunnel is the customer's "front door". Turning it OFF makes the home go
dark: no request from the outside can reach any service. The services themselves
keep running on loopback -- only the public path is severed.

Two backends, auto-selected:

* Direct  -- used when `systemctl` is available to this process (the app running
  on the host, or a container granted host systemd access). Runs
  `systemctl start/stop/is-active aw-cloudflared` directly.

* Spool   -- used inside the loopback container (python:slim has no systemctl).
  The dashboard writes the desired state ("up"/"down") to a file on the shared
  /etc/augustwest volume; a tiny host-side systemd path unit (see host/) applies
  it with real systemctl and writes the observed state back. This keeps host
  root/systemd OUT of the container -- it can only drop a one-word intent.

Force a backend with AW_TUNNEL_MODE=direct|spool (default: auto-detect).
"""
import os
import shutil
import subprocess

SERVICE = "aw-cloudflared.service"
SPOOL_DIR = os.environ.get("AW_TUNNEL_SPOOL_DIR", "/etc/augustwest/tunnel")
DESIRED_PATH = os.path.join(SPOOL_DIR, "desired")
STATE_PATH = os.path.join(SPOOL_DIR, "state")

UP, DOWN, UNKNOWN = "up", "down", "unknown"


def _mode() -> str:
    forced = os.environ.get("AW_TUNNEL_MODE")
    if forced in ("direct", "spool"):
        return forced
    return "direct" if shutil.which("systemctl") else "spool"


# ---- direct backend -------------------------------------------------------
def _direct_state() -> str:
    try:
        r = subprocess.run(
            ["systemctl", "is-active", SERVICE], capture_output=True, text=True, timeout=5
        )
        return UP if r.stdout.strip() == "active" else DOWN
    except (subprocess.SubprocessError, OSError):
        return UNKNOWN


def _direct_set(up: bool) -> None:
    subprocess.run(
        ["systemctl", "start" if up else "stop", SERVICE],
        capture_output=True, text=True, timeout=30, check=True,
    )


# ---- spool backend --------------------------------------------------------
def _spool_write_desired(up: bool) -> None:
    os.makedirs(SPOOL_DIR, exist_ok=True)
    tmp = DESIRED_PATH + ".tmp"
    with open(tmp, "w") as f:
        f.write(UP if up else DOWN)
    os.replace(tmp, DESIRED_PATH)  # atomic -> the path unit sees one clean change


def _spool_state() -> str:
    try:
        with open(STATE_PATH) as f:
            v = f.read().strip()
        return v if v in (UP, DOWN) else UNKNOWN
    except FileNotFoundError:
        return UNKNOWN


# ---- public API -----------------------------------------------------------
def current_state() -> str:
    return _direct_state() if _mode() == "direct" else _spool_state()


def set_online(up: bool) -> None:
    if _mode() == "direct":
        _direct_set(up)
    else:
        _spool_write_desired(up)


def control_available() -> bool:
    """Whether we can actually change the tunnel state from here."""
    if _mode() == "direct":
        return shutil.which("systemctl") is not None
    # spool: we need to be able to write the intent file
    parent = os.path.dirname(SPOOL_DIR) or "/"
    return os.path.isdir(SPOOL_DIR) or os.access(parent, os.W_OK)
