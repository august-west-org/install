"""August West customer dashboard -- a small FastAPI backend behind the PWA.

Runs in its own container on the customer's device, loopback-only on :8889 (like
every other service), and is reached from the phone over the Cloudflare Tunnel's
dashboard-<customer_domain> route. It:

  * authenticates with the customer's onboarding master password (auth.py),
  * reports each service's health + last-backup age (services.py),
  * and toggles the public tunnel on/off (tunnel.py) -- "going dark".
"""
import asyncio
import logging
import os

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
import services
import tunnel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("august_west.dashboard")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

OFFLINE_MESSAGE = "Your data is dark — no one can reach it."

app = FastAPI(title="August West Dashboard")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def require_session(authorization: str | None = Header(default=None)) -> str:
    token = _bearer(authorization)
    if not auth.session_valid(token):
        raise HTTPException(status_code=401, detail="Please sign in.")
    return token


# --------------------------------------------------------------------------
# PWA shell + installability assets
# --------------------------------------------------------------------------
def _static(name: str, media_type: str | None = None, headers: dict | None = None):
    return FileResponse(os.path.join(STATIC_DIR, name), media_type=media_type, headers=headers)


@app.get("/")
async def index():
    return _static("index.html", "text/html")


@app.get("/manifest.json")
async def manifest():
    return _static("manifest.webmanifest", "application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    # Served from the ROOT so the worker's scope covers the whole app; the extra
    # header lets a root scope be claimed even though the file could be elsewhere.
    return _static(
        "sw.js",
        "text/javascript",
        {"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/apple-touch-icon.png")
async def apple_touch_icon():
    return _static("apple-touch-icon.png", "image/png")


@app.get("/apple-touch-icon-precomposed.png")
async def apple_touch_icon_precomposed():
    return _static("apple-touch-icon.png", "image/png")


@app.get("/favicon.ico")
async def favicon():
    return _static("icon-192.png", "image/png")


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
class LoginBody(BaseModel):
    password: str


@app.post("/api/login")
async def login(body: LoginBody):
    if not auth.password_is_set():
        raise HTTPException(
            status_code=503,
            detail="Your dashboard isn't ready yet — finish setting up your home first.",
        )
    if not auth.verify_password(body.password):
        raise HTTPException(
            status_code=401,
            detail="That password doesn't match. Use your August West master password.",
        )
    return {"token": auth.create_session()}


@app.post("/api/logout")
async def logout(token: str = Depends(require_session)):
    auth.destroy_session(token)
    return {"ok": True}


# --------------------------------------------------------------------------
# Status + control
# --------------------------------------------------------------------------
def _status_payload(svc: list[dict]) -> dict:
    state = tunnel.current_state()
    # Fail visible: an UNKNOWN state (e.g. host control not wired yet) reads as
    # online rather than scaring the customer with a false "dark".
    online = state != tunnel.DOWN
    return {
        "online": online,
        "tunnel_state": state,
        "control_available": tunnel.control_available(),
        "services": svc,
        "backup": services.last_backup(),
        "offline_message": OFFLINE_MESSAGE,
    }


@app.get("/api/status")
async def status(_: str = Depends(require_session)):
    return _status_payload(await services.service_status())


@app.get("/api/tunnel/state")
async def tunnel_state(_: str = Depends(require_session)):
    """Lightweight poll target for the phone UI.

    Reads /etc/augustwest/tunnel/state and reports {"state": "up"|"down"} so the
    dashboard can reflect "going dark" within a few seconds without the heavier
    per-service health sweep that /api/status runs.
    """
    return {"state": tunnel.read_state_file()}


class ToggleBody(BaseModel):
    online: bool


@app.post("/api/toggle")
async def toggle(body: ToggleBody, _: str = Depends(require_session)):
    try:
        tunnel.set_online(body.online)
    except Exception as e:  # noqa: BLE001 -- report, don't 500 opaquely
        logger.exception("tunnel toggle to online=%s failed", body.online)
        raise HTTPException(status_code=502, detail=f"Couldn't change your connection: {e}")

    # Best-effort: give the host control unit a moment to apply and report back,
    # so the response reflects the real new state (up to ~10s).
    target = tunnel.UP if body.online else tunnel.DOWN
    for _ in range(20):
        if tunnel.current_state() == target:
            break
        await asyncio.sleep(0.5)

    logger.info("tunnel toggled to online=%s (state now %s)", body.online, tunnel.current_state())
    return _status_payload(await services.service_status())
