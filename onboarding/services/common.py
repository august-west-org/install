"""Consumer-facing service labels and health checks. Internal service names
never appear in any user-facing text -- only the labels in SERVICE_LABELS do."""
import asyncio

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

# Right after `docker compose up`, services are still booting -- Immich waits on
# Postgres + machine-learning, Nextcloud runs its first-boot install, etc. A
# probe during that window fails with "connection reset" rather than a clean
# HTTP error, and reporting the stack as unhealthy would wrongly block account
# creation. So instead of a single shot we poll: re-check up to
# HEALTH_MAX_RETRIES times, waiting HEALTH_RETRY_DELAY_S seconds between tries
# (100 seconds total by default), and only report the outcome once everything is
# up or we have genuinely run out of patience.
HEALTH_MAX_RETRIES = 10
HEALTH_RETRY_DELAY_S = 10


async def _check_once() -> dict:
    """One pass over every service's health endpoint (no retries)."""
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


def _all_ok(results: dict) -> bool:
    return all(r["ok"] for r in results.values())


async def check_health() -> dict:
    """Poll every service until all report healthy, then return the results.

    Does one immediate check, then retries up to HEALTH_MAX_RETRIES times,
    sleeping HEALTH_RETRY_DELAY_S seconds before each retry (up to 100s of
    waiting by default), so a freshly-started stack has time to finish booting
    before the wizard lets the customer create an account. Returns as soon as
    all services are healthy; if they never all come up within the budget,
    returns the results of the final attempt (some ok:false) so the caller can
    surface a degraded state.
    """
    results = await _check_once()
    if _all_ok(results):
        return results

    for _ in range(HEALTH_MAX_RETRIES):
        # Something isn't up yet -- reassure the customer and try again shortly.
        print(
            f"Still warming up... checking again in {HEALTH_RETRY_DELAY_S} seconds",
            flush=True,
        )
        await asyncio.sleep(HEALTH_RETRY_DELAY_S)
        results = await _check_once()
        if _all_ok(results):
            return results

    return results
