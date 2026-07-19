"""Immich (Photo Vault) integration."""
import httpx

BASE = "http://127.0.0.1:2283"


async def create_account(email: str, password: str, name: str) -> dict:
    """Creates the primary Immich account. On a fresh instance this is always
    the very first account, which must go through admin-sign-up (Immich has
    no separate "create user" call until an admin exists). If the wizard is
    re-run after this step already succeeded, admin-sign-up 400s "already has
    an admin" -- in that case, confirm the given credentials still work and
    treat it as an idempotent success rather than a failure."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{BASE}/api/auth/admin-sign-up",
            json={"email": email, "password": password, "name": name},
        )
        if resp.status_code in (200, 201):
            return {"ok": True, "email": email, "role": "admin"}

        login_resp = await client.post(
            f"{BASE}/api/auth/login", json={"email": email, "password": password}
        )
        if login_resp.status_code in (200, 201):
            return {"ok": True, "email": email, "role": "admin", "already_existed": True}
        return {"ok": False, "error": resp.text}


async def login(email: str, password: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{BASE}/api/auth/login", json={"email": email, "password": password}
        )
        if resp.status_code not in (200, 201):
            return {"ok": False, "error": resp.text}
        return {"ok": True, **resp.json()}


async def create_member_account(admin_access_token: str, email: str, password: str, name: str) -> dict:
    """Creates an additional (non-admin) account -- used for family members,
    authenticated as the already-provisioned primary account."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{BASE}/api/admin/users",
            headers={"Authorization": f"Bearer {admin_access_token}"},
            json={
                "email": email,
                "password": password,
                "name": name,
                "notify": False,
                "shouldChangePassword": False,
            },
        )
        if resp.status_code in (200, 201):
            return {"ok": True, "email": email}
        if resp.status_code == 400 and "exist" in resp.text.lower():
            return {"ok": True, "email": email, "already_existed": True}
        return {"ok": False, "error": resp.text}


async def create_api_key(access_token: str, name: str) -> dict:
    """A long-lived API key for one-tap access, so the companion app never
    has to retain the account's actual password."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{BASE}/api/api-keys",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"name": name, "permissions": ["all"]},
        )
        if resp.status_code not in (200, 201):
            return {"ok": False, "error": resp.text}
        data = resp.json()
        return {"ok": True, "api_key": data["secret"]}
