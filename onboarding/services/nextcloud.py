"""Nextcloud (File Vault) integration via the OCS provisioning API."""
import httpx

from .secrets import NEXTCLOUD_ADMIN_PASSWORD

BASE = "http://127.0.0.1:8080"
_HEADERS = {"OCS-APIRequest": "true", "Accept": "application/json"}


async def create_user(username: str, password: str, email: str, display_name: str) -> dict:
    """Creates a real Nextcloud user. Returns {"ok": True} or {"ok": False, "error": ...}.
    Idempotent: if the user already exists, treats it as success (re-running the
    wizard after a partial failure shouldn't hard-fail on this step)."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{BASE}/ocs/v1.php/cloud/users",
            auth=("admin", NEXTCLOUD_ADMIN_PASSWORD),
            headers=_HEADERS,
            data={
                "userid": username,
                "password": password,
                "email": email,
                "displayName": display_name,
            },
        )
        data = resp.json()
        meta = data.get("ocs", {}).get("meta", {})
        if meta.get("statuscode") in (100, 200):
            return {"ok": True, "username": username}
        if "already exists" in meta.get("message", "").lower():
            return {"ok": True, "username": username, "already_existed": True}
        return {"ok": False, "error": meta.get("message", "unknown error")}


async def start_login_flow() -> dict:
    """Nextcloud's "Login Flow v2" -- the same one-tap QR pairing real
    Nextcloud desktop/mobile clients use. The customer opens the returned
    login URL once (scanning it is the normal path, but any browser works
    too), logs in, and poll_login_flow() then receives a device-specific app
    password -- no master password ever touches the companion app."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{BASE}/index.php/login/v2")
        data = resp.json()
        return {
            "login_url": data["login"],
            "poll_token": data["poll"]["token"],
            "poll_endpoint": data["poll"]["endpoint"],
        }


async def poll_login_flow(poll_token: str, poll_endpoint: str) -> dict | None:
    """Returns None while the customer hasn't finished logging in yet, or the
    resulting {server, loginName, appPassword} once they have."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(poll_endpoint, data={"token": poll_token})
        if resp.status_code != 200:
            return None
        return resp.json()


async def get_app_password(username: str, password: str) -> dict:
    """Exchanges the user's own credentials for a device-specific app password,
    so the companion app/webview never needs to hold the master password."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{BASE}/ocs/v2.php/core/getapppassword",
            auth=(username, password),
            headers=_HEADERS,
        )
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}"}
        data = resp.json()
        app_password = data.get("ocs", {}).get("data", {}).get("apppassword")
        if not app_password:
            return {"ok": False, "error": "no apppassword in response"}
        return {"ok": True, "app_password": app_password}
