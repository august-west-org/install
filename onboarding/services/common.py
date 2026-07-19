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
    """One immediate pass over every service's health endpoint.

    Returns right away with the current per-service status -- it deliberately
    does NOT block waiting for services to finish booting. The wizard's health
    endpoint is polled by the frontend every few seconds until everything
    reports healthy. Polling is more robust than one long-held request: a
    ~100s blocking call risks being cut by Cloudflare's proxy idle timeout,
    whereas each poll is a quick, independent request that sails through.

    While a service is still starting, its probe typically fails with a
    connection-reset (surfaced here as ok:false with an ``error``) rather than a
    clean HTTP status; the caller treats any non-ok service as "not ready yet".
    """
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
