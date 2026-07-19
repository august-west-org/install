"""Home Assistant (Smart Home) integration via its onboarding + auth APIs.

HA only supports ONE owner account created through /api/onboarding/users --
there is no multi-user "create additional account" concept the way the other
three services have. Family members instead get their own Person entries
inside the single HA instance (everyone shares the one smart-home brain;
that's the normal, correct HA multi-user model, not a limitation of this
integration)."""
import json

import httpx
import websockets

BASE = "http://127.0.0.1:8123"
_WS_URL = "ws://127.0.0.1:8123/api/websocket"
_CLIENT_ID = f"{BASE}/"


async def onboarding_status() -> list[dict]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{BASE}/api/onboarding")
        return resp.json()


async def login(username: str, password: str) -> dict:
    """Logs in an existing HA user through the standard login flow and returns
    a fresh access/refresh token pair. Used when onboarding has already been
    completed (wizard re-run) so create_owner can still hand back real tokens
    for downstream family-member provisioning."""
    async with httpx.AsyncClient(timeout=15) as client:
        flow_resp = await client.post(
            f"{BASE}/auth/login_flow",
            json={
                "client_id": _CLIENT_ID,
                "handler": ["homeassistant", None],
                "redirect_uri": _CLIENT_ID,
            },
        )
        if flow_resp.status_code not in (200, 201):
            return {"ok": False, "error": flow_resp.text}
        flow_id = flow_resp.json()["flow_id"]

        step_resp = await client.post(
            f"{BASE}/auth/login_flow/{flow_id}",
            json={"client_id": _CLIENT_ID, "username": username, "password": password},
        )
        if step_resp.status_code not in (200, 201):
            return {"ok": False, "error": step_resp.text}
        step = step_resp.json()
        if step.get("type") != "create_entry" or not step.get("result"):
            return {"ok": False, "error": "HA login rejected the credentials"}

        token_resp = await client.post(
            f"{BASE}/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": step["result"],
                "client_id": _CLIENT_ID,
            },
        )
        if token_resp.status_code != 200:
            return {"ok": False, "error": token_resp.text}
        tokens = token_resp.json()
        return {
            "ok": True,
            "username": username,
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
        }


async def refresh_access_token(refresh_token: str) -> str | None:
    """Exchanges a long-lived refresh token for a fresh access token. HA access
    tokens expire after 30 minutes, so the family step (which can run well after
    the account step) refreshes before using the stored token."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{BASE}/auth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": _CLIENT_ID,
            },
        )
        if resp.status_code != 200:
            return None
        return resp.json().get("access_token")


async def create_owner(username: str, password: str, name: str) -> dict:
    """Creates the HA owner account and exchanges the resulting auth code for
    a real access/refresh token pair. Idempotent: if onboarding's "user" step
    is already done (wizard re-run), logs in with the given credentials so a
    fresh token pair is still returned for downstream family provisioning."""
    status = await onboarding_status()
    user_step = next((s for s in status if s["step"] == "user"), None)
    if user_step and user_step["done"]:
        result = await login(username, password)
        result["already_existed"] = True
        return result

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{BASE}/api/onboarding/users",
            json={
                "client_id": _CLIENT_ID,
                "name": name,
                "username": username,
                "password": password,
                "language": "en",
            },
        )
        if resp.status_code not in (200, 201):
            return {"ok": False, "error": resp.text}
        auth_code = resp.json()["auth_code"]

        token_resp = await client.post(
            f"{BASE}/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "client_id": _CLIENT_ID,
            },
        )
        if token_resp.status_code != 200:
            return {"ok": False, "error": token_resp.text}
        tokens = token_resp.json()

        # Best-effort: finish the remaining onboarding steps so the customer
        # never sees HA's own setup wizard. Non-fatal if either fails --
        # HA just shows that screen once, which is harmless.
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        try:
            await client.post(f"{BASE}/api/onboarding/core_config", headers=headers)
            await client.post(
                f"{BASE}/api/onboarding/analytics",
                headers=headers,
                json={"analytics_reporting": False},
            )
        except httpx.HTTPError:
            pass

        return {
            "ok": True,
            "username": username,
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
        }


async def create_person(access_token: str, name: str) -> dict:
    """Adds a Person entity for a family member (HA's normal multi-user model:
    everyone shares the one instance, distinguished by Person + optionally
    their own companion-app login later via a long-lived token, not a
    separate account). There's no REST route for this -- HA only exposes
    person management over its WebSocket API."""
    async with websockets.connect(_WS_URL) as ws:
        await ws.recv()  # auth_required
        await ws.send(json.dumps({"type": "auth", "access_token": access_token}))
        auth_result = json.loads(await ws.recv())
        if auth_result.get("type") != "auth_ok":
            return {"ok": False, "error": "websocket auth failed"}

        await ws.send(json.dumps({"id": 1, "type": "person/create", "name": name}))
        result = json.loads(await ws.recv())
        if result.get("success"):
            return {"ok": True, "name": name, "person_id": result["result"]["id"]}
        return {"ok": False, "error": result.get("error")}
