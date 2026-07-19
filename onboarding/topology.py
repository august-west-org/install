"""Resolves the public (Cloudflare Tunnel) hostname for each service, using
the naming scheme install.sh's tunnel step establishes: photos./vault./
files./home.<customer_domain>. Falls back to None when no tunnel is
configured yet, so callers can show a "not public yet" state instead of a
broken link."""
from services.secrets import SECRETS

_PREFIXES = {
    "immich": "photos",
    "vaultwarden": "vault",
    "nextcloud": "files",
    "homeassistant": "home",
    "setup": "setup",
}


def tunnel_configured() -> bool:
    return SECRETS.get("TUNNEL_CONFIGURED") == "true" and bool(SECRETS.get("CUSTOMER_DOMAIN"))


def public_url(service: str) -> str | None:
    if not tunnel_configured():
        return None
    domain = SECRETS["CUSTOMER_DOMAIN"]
    return f"https://{_PREFIXES[service]}-{domain}"


def setup_base_url() -> str:
    """Best-effort URL for the wizard's own origin, for QR codes that should
    open it on the customer's phone (which isn't on loopback)."""
    tunnel_url = public_url("setup")
    if tunnel_url:
        return tunnel_url
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        lan_ip = s.getsockname()[0]
        s.close()
        return f"http://{lan_ip}:8888"
    except OSError:
        return "http://localhost:8888"
