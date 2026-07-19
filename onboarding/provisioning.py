"""Ties the four service modules together into the "one August West login"
flow: a single name/email/password creates matching real accounts on every
service. An advanced override lets a per-service password diverge after the
fact, but the default -- and the whole point of the wizard -- is one shared
credential everywhere.

Nothing here persists the plaintext password. Only the derived, scoped,
revocable tokens (Immich API access token, HA access/refresh token) get
stored, for reuse when provisioning family members later."""
import asyncio
import logging
import re

from services import homeassistant, immich, nextcloud, vaultwarden

logger = logging.getLogger("august_west.provisioning")


async def _safe(coro, service: str) -> dict:
    """Await a provisioning coroutine, converting ANY unexpected exception into a
    logged ``{"ok": False, "error": ...}``.

    Without this, one service raising (a non-JSON response -> JSONDecodeError, a
    missing response key -> KeyError, a dropped connection, ...) would abort the
    whole ``asyncio.gather``, cancel the sibling services, and surface as an
    opaque 500 with NO record of which service actually failed. Catching here
    keeps every service's outcome independent and always logged."""
    try:
        return await coro
    except Exception as e:  # noqa: BLE001 -- surface EVERY failure, never swallow
        logger.exception("provisioning step for '%s' raised an exception", service)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def slugify_username(email: str) -> str:
    local = email.split("@")[0]
    slug = re.sub(r"[^a-z0-9._-]", "", local.lower())
    return slug or "member"


async def provision_primary(name: str, email: str, password: str, hint: str, advanced: dict) -> dict:
    username = slugify_username(email)
    advanced = advanced or {}

    nc_pw = advanced.get("nextcloud") or password
    immich_pw = advanced.get("immich") or password
    ha_pw = advanced.get("homeassistant") or password
    vw_pw = advanced.get("vaultwarden") or password

    immich_result, nc_result, ha_result, vw_result = await asyncio.gather(
        _safe(_provision_immich_primary(email, immich_pw, name), "immich"),
        _safe(_provision_nextcloud_primary(username, nc_pw, email, name), "nextcloud"),
        _safe(homeassistant.create_owner(username, ha_pw, name), "homeassistant"),
        _safe(vaultwarden.register(email, name, vw_pw, hint), "vaultwarden"),
    )

    # immich/nextcloud wrap their raw service result under "service_result"; a
    # _safe exception fallback is already a flat {"ok": False, ...}, so .get()
    # falls back to the fallback dict itself.
    result = {
        "immich": immich_result.get("service_result", immich_result),
        "nextcloud": nc_result.get("service_result", nc_result),
        "homeassistant": ha_result,
        "vaultwarden": vw_result,
    }
    quick_access = {
        "immich": immich_result.get("quick_access", {}),
        "nextcloud": nc_result.get("quick_access", {}),
        "vaultwarden": {"email": email},
        "homeassistant": {},
    }
    internal = {
        "immich_access_token": immich_result.get("access_token"),
        "homeassistant_access_token": ha_result.get("access_token"),
        "homeassistant_refresh_token": ha_result.get("refresh_token"),
    }
    # Log the specific reason for every service that did not succeed, so a
    # partial failure is always traceable in the wizard logs (not just an opaque
    # "one or more services failed").
    for service, r in result.items():
        if not r.get("ok"):
            logger.error("primary account: '%s' failed: %s", service, r.get("error", r))
    ok = all(r.get("ok") for r in result.values())
    return {
        "ok": ok,
        "username": username,
        "result": result,
        "quick_access": quick_access,
        "internal": internal,
    }


async def _provision_immich_primary(email: str, password: str, name: str) -> dict:
    create = await immich.create_account(email, password, name)
    if not create.get("ok"):
        return {"service_result": create, "quick_access": {}, "access_token": None}
    login = await immich.login(email, password)
    if not login.get("ok"):
        return {"service_result": create, "quick_access": {}, "access_token": None}
    api_key = await immich.create_api_key(login["accessToken"], "august-west-app")
    return {
        "service_result": create,
        "quick_access": {"api_key": api_key.get("api_key")} if api_key.get("ok") else {},
        "access_token": login["accessToken"],
    }


async def _provision_nextcloud_primary(username: str, password: str, email: str, name: str) -> dict:
    create = await nextcloud.create_user(username, password, email, name)
    if not create.get("ok"):
        return {"service_result": create, "quick_access": {}}
    app_pw = await nextcloud.get_app_password(username, password)
    quick_access = (
        {"username": username, "app_password": app_pw["app_password"]}
        if app_pw.get("ok")
        else {"username": username}
    )
    return {"service_result": create, "quick_access": quick_access}


async def provision_family_member(
    name: str, email: str, password: str, hint: str, internal: dict
) -> dict:
    username = slugify_username(email)

    nc_result = await _safe(nextcloud.create_user(username, password, email, name), "nextcloud")

    immich_token = internal.get("immich_access_token")
    if immich_token:
        immich_result = await _safe(
            immich.create_member_account(immich_token, email, password, name), "immich"
        )
    else:
        immich_result = {"ok": False, "error": "primary Photo Vault account not yet set up"}

    # HA access tokens expire after 30 min, so refresh from the stored
    # refresh token before use -- the family step often runs well after the
    # account step that first minted it.
    ha_refresh = internal.get("homeassistant_refresh_token")
    ha_token = internal.get("homeassistant_access_token")
    if ha_refresh:
        try:
            refreshed = await homeassistant.refresh_access_token(ha_refresh)
        except Exception:  # noqa: BLE001 -- a refresh error must not abort the step
            logger.exception("family member %s: HA token refresh raised", email)
            refreshed = None
        ha_token = refreshed or ha_token
    if ha_token:
        ha_result = await _safe(homeassistant.create_person(ha_token, name), "homeassistant")
    else:
        ha_result = {"ok": False, "error": "Smart Home not yet set up"}

    vw_result = await _safe(vaultwarden.register(email, name, password, hint), "vaultwarden")

    result = {
        "immich": immich_result,
        "nextcloud": nc_result,
        "homeassistant": ha_result,
        "vaultwarden": vw_result,
    }
    for service, r in result.items():
        if not r.get("ok"):
            logger.error("family member %s: '%s' failed: %s", email, service, r.get("error", r))
    ok = all(r.get("ok") for r in result.values())
    return {
        "ok": ok,
        "username": username,
        "result": result,
    }
