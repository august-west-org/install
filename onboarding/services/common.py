"""Consumer-facing service labels and health checks. Internal service names
never appear in any user-facing text -- only the labels in SERVICE_LABELS do."""
import httpx

SERVICE_LABELS = {
    "immich": "Photo Vault",
    "vaultwarden": "Password Safe",
    "nextcloud": "File Vault",
    "homeassistant": "Smart Home",
}

SERVICE_ORDER = ["immich", "vaultwarden", "nextcloud", "homeassistant"]

_HEALTH_URLS = {
    "immich": "http://127.0.0.1:2283/api/server/ping",
    "vaultwarden": "http://127.0.0.1:8443/alive",
    "nextcloud": "http://127.0.0.1:8080/status.php",
    "homeassistant": "http://127.0.0.1:8123/manifest.json",
}


async def check_health() -> dict:
    results = {}
    async with httpx.AsyncClient(timeout=5) as client:
        for key, url in _HEALTH_URLS.items():
            try:
                resp = await client.get(url)
                results[key] = {
                    "label": SERVICE_LABELS[key],
                    "ok": resp.status_code < 400,
                    "status_code": resp.status_code,
                }
            except httpx.HTTPError as e:
                results[key] = {"label": SERVICE_LABELS[key], "ok": False, "error": str(e)}
    return results
