"""Server-side integrations with the four home services.

Every function returns plain-English results. Technical errors are caught
and never surfaced to the caller in raw form. Friendly names only:
    Photo Vault   -> Immich
    Password Safe -> Vaultwarden
    File Vault    -> Nextcloud
    Smart Home    -> Home Assistant
"""
from __future__ import annotations

import json
import uuid

import httpx

from bw_crypto import build_register_payload

# ---- internal (loopback) endpoints — never exposed to the browser ----
IMMICH = "http://127.0.0.1:2283"
VAULTWARDEN = "http://127.0.0.1:8443"
NEXTCLOUD = "http://127.0.0.1:8080"
HOMEASSISTANT = "http://127.0.0.1:8123"

TIMEOUT = httpx.Timeout(30.0, connect=8.0)

# Friendly display names
FRIENDLY = {
    "immich": "Photo Vault",
    "vaultwarden": "Password Safe",
    "nextcloud": "File Vault",
    "homeassistant": "Smart Home",
}


class SetupError(Exception):
    """Carries a friendly, non-technical message safe to show the user."""

    def __init__(self, friendly: str):
        super().__init__(friendly)
        self.friendly = friendly


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------
async def check_health(nc_admin_user: str, nc_admin_pass: str) -> dict:
    """Return readiness of each service in plain English. Never raises."""
    results = {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=4.0)) as c:
        results["immich"] = await _ok(c, "GET", IMMICH + "/api/server/ping",
                                       expect_json_key=("res", "pong"))
        results["vaultwarden"] = await _ok(c, "GET", VAULTWARDEN + "/alive")
        results["nextcloud"] = await _ok(c, "GET", NEXTCLOUD + "/status.php",
                                          expect_json_key=("maintenance", False))
        results["homeassistant"] = await _ok(c, "GET", HOMEASSISTANT + "/manifest.json")
    ready = all(results.values())
    return {
        "ready": ready,
        "services": [
            {"key": k, "name": FRIENDLY[k], "ready": results[k]}
            for k in ("immich", "vaultwarden", "nextcloud", "homeassistant")
        ],
    }


async def _ok(client, method, url, expect_json_key=None) -> bool:
    try:
        r = await client.request(method, url)
        if r.status_code >= 500 or r.status_code == 404:
            return False
        if r.status_code >= 400:
            return False
        if expect_json_key:
            key, val = expect_json_key
            try:
                return r.json().get(key) == val
            except Exception:
                return False
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# Account creation — the customer becomes owner of Photos & Smart Home,
# and a user of Passwords & Files.
# Returns admin handles (immich api key, HA refresh token) for later use.
# --------------------------------------------------------------------------
async def create_immich(name: str, email: str, password: str) -> dict:
    """Create the Immich owner (first user = admin) and mint an admin API key."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        try:
            cfg = (await c.get(IMMICH + "/api/server/config")).json()
        except Exception:
            raise SetupError("We couldn’t reach your Photo Vault. Let’s try again.")

        if not cfg.get("isInitialized"):
            r = await c.post(IMMICH + "/api/auth/admin-sign-up",
                             json={"email": email, "password": password, "name": name})
            if r.status_code not in (200, 201):
                raise SetupError("We couldn’t set up your Photo Vault. Let’s try again.")

        # Log in to confirm and to obtain a token for minting an API key.
        r = await c.post(IMMICH + "/api/auth/login",
                         json={"email": email, "password": password})
        if r.status_code not in (200, 201) or "accessToken" not in r.text:
            raise SetupError(
                "Your Photo Vault is set up, but we had trouble signing in. Let’s try again.")
        token = r.json()["accessToken"]

        api_key = None
        try:
            k = await c.post(IMMICH + "/api/api-keys",
                             headers={"Authorization": f"Bearer {token}"},
                             json={"name": "August West Onboarding",
                                   "permissions": ["all"]})
            if k.status_code in (200, 201):
                api_key = k.json().get("secret")
        except Exception:
            api_key = None
    return {"immich_api_key": api_key}


async def create_vaultwarden(name: str, email: str, password: str) -> dict:
    """Register the customer in Vaultwarden with a real, unlockable master password."""
    payload = build_register_payload(email, name, password)
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(VAULTWARDEN + "/identity/accounts/register", json=payload)
        if r.status_code in (200, 204):
            return {}
        # Already registered? Verify the password unlocks it, then treat as success.
        if await _vw_can_login(c, email, payload["masterPasswordHash"]):
            return {}
        raise SetupError("We couldn’t set up your Password Safe. Let’s try again.")


async def _vw_can_login(client, email, master_password_hash) -> bool:
    try:
        form = {
            "grant_type": "password",
            "username": email.strip().lower(),
            "password": master_password_hash,
            "scope": "api offline_access",
            "client_id": "web",
            "deviceType": "9",
            "deviceIdentifier": str(uuid.uuid4()),
            "deviceName": "augustwest",
        }
        r = await client.post(VAULTWARDEN + "/identity/connect/token", data=form)
        return r.status_code == 200 and "access_token" in r.text
    except Exception:
        return False


async def create_nextcloud(name: str, email: str, password: str,
                           admin_user: str, admin_pass: str) -> dict:
    """Create the customer's File Vault user via the admin provisioning API."""
    userid = email.strip().lower()
    async with httpx.AsyncClient(timeout=TIMEOUT, auth=(admin_user, admin_pass)) as c:
        r = await c.post(
            NEXTCLOUD + "/ocs/v2.php/cloud/users",
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            data={"userid": userid, "password": password,
                  "displayName": name, "email": email},
        )
        code = _ocs_code(r)
        if code == 200 or code == 102:  # 200 created, 102 already exists
            return {}
        raise SetupError("We couldn’t set up your File Vault. Let’s try again.")


async def create_homeassistant(name: str, email: str, password: str) -> dict:
    """Create the Home Assistant owner and store a refresh token for later use."""
    client_id = HOMEASSISTANT + "/"
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        try:
            steps = (await c.get(HOMEASSISTANT + "/api/onboarding")).json()
        except Exception:
            raise SetupError("We couldn’t reach your Smart Home. Let’s try again.")

        user_done = any(s.get("step") == "user" and s.get("done") for s in steps)
        auth_code = None
        if not user_done:
            r = await c.post(HOMEASSISTANT + "/api/onboarding/users",
                             json={"client_id": client_id, "name": name,
                                   "username": email, "password": password,
                                   "language": "en"})
            if r.status_code != 200 or "auth_code" not in r.text:
                raise SetupError("We couldn’t set up your Smart Home. Let’s try again.")
            auth_code = r.json()["auth_code"]

        refresh_token = None
        if auth_code:
            try:
                t = await c.post(HOMEASSISTANT + "/auth/token",
                                 data={"grant_type": "authorization_code",
                                       "code": auth_code, "client_id": client_id})
                if t.status_code == 200:
                    refresh_token = t.json().get("refresh_token")
            except Exception:
                refresh_token = None
    return {"ha_refresh_token": refresh_token, "ha_client_id": client_id}


# --------------------------------------------------------------------------
# Family members (step 6) — best effort per service.
# --------------------------------------------------------------------------
async def add_family_member(name: str, email: str, password: str,
                            handles: dict, nc_admin_user: str, nc_admin_pass: str) -> dict:
    """Add an extra person to every service. Returns per-service plain results."""
    out = {}

    # Photo Vault — admin creates the user with the stored API key
    out["immich"] = await _family_immich(name, email, password, handles.get("immich_api_key"))
    # Password Safe — self-register (signups allowed)
    out["vaultwarden"] = await _family_vaultwarden(name, email, password)
    # File Vault — admin provisioning API
    out["nextcloud"] = await _family_nextcloud(name, email, password,
                                               nc_admin_user, nc_admin_pass)
    # Smart Home — websocket admin command using stored refresh token
    out["homeassistant"] = await _family_homeassistant(
        name, email, password, handles.get("ha_refresh_token"), handles.get("ha_client_id"))
    return out


async def _family_immich(name, email, password, api_key) -> bool:
    if not api_key:
        return False
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(IMMICH + "/api/admin/users",
                             headers={"x-api-key": api_key},
                             json={"email": email, "password": password, "name": name})
            return r.status_code in (200, 201)
    except Exception:
        return False


async def _family_vaultwarden(name, email, password) -> bool:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(VAULTWARDEN + "/identity/accounts/register",
                             json=build_register_payload(email, name, password))
            return r.status_code in (200, 204)
    except Exception:
        return False


async def _family_nextcloud(name, email, password, admin_user, admin_pass) -> bool:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, auth=(admin_user, admin_pass)) as c:
            r = await c.post(
                NEXTCLOUD + "/ocs/v2.php/cloud/users",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                data={"userid": email.strip().lower(), "password": password,
                      "displayName": name, "email": email})
            return _ocs_code(r) in (200, 102)
    except Exception:
        return False


async def _family_homeassistant(name, email, password, refresh_token, client_id) -> bool:
    if not refresh_token or not client_id:
        return False
    try:
        import websockets  # imported lazily; optional dependency

        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            t = await c.post(HOMEASSISTANT + "/auth/token",
                             data={"grant_type": "refresh_token",
                                   "refresh_token": refresh_token, "client_id": client_id})
            if t.status_code != 200:
                return False
            access = t.json()["access_token"]

        uri = "ws://127.0.0.1:8123/api/websocket"
        async with websockets.connect(uri, open_timeout=8) as ws:
            await ws.recv()  # auth_required
            await ws.send(json.dumps({"type": "auth", "access_token": access}))
            if json.loads(await ws.recv()).get("type") != "auth_ok":
                return False
            await ws.send(json.dumps({"id": 1, "type": "config/auth/create",
                                      "name": name, "group_ids": ["system-users"]}))
            created = json.loads(await ws.recv())
            if not created.get("success"):
                return False
            user_id = created["result"]["user"]["id"]
            await ws.send(json.dumps({
                "id": 2, "type": "config/auth_provider/homeassistant/create",
                "user_id": user_id, "username": email, "password": password}))
            return json.loads(await ws.recv()).get("success", False)
    except Exception:
        return False


# --------------------------------------------------------------------------
def _ocs_code(resp) -> int | None:
    try:
        return resp.json()["ocs"]["meta"]["statuscode"]
    except Exception:
        return None
