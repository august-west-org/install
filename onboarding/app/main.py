"""August West onboarding wizard — FastAPI backend.

Runs on the customer's home server right after install. Guides them through
setting up their private cloud. All service calls happen server-side; the
browser never sees internal ports, IPs, or technical errors.
"""
from __future__ import annotations

import hmac
import io
import json
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

import services

APP_DIR = Path(__file__).resolve().parent
STATIC = APP_DIR / "static"
DATA_DIR = Path(os.environ.get("AW_DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"
TOKEN_FILE = DATA_DIR / "setup_token"

# ---- configuration (provided by the install script via environment) ----
NC_ADMIN_USER = os.environ.get("NEXTCLOUD_ADMIN_USER", "admin")
NC_ADMIN_PASS = os.environ.get("NEXTCLOUD_ADMIN_PASSWORD", "")

PUBLIC_SCHEME = os.environ.get("AW_PUBLIC_SCHEME", "http")
PUBLIC_HOST = os.environ.get("AW_PUBLIC_HOST", "65.21.246.9")


def _public_url(port: int, override_env: str) -> str:
    v = os.environ.get(override_env)
    return v if v else f"{PUBLIC_SCHEME}://{PUBLIC_HOST}:{port}"


PUBLIC_URLS = {
    "photos": _public_url(2283, "AW_PHOTOS_URL"),
    "passwords": _public_url(8443, "AW_PASSWORDS_URL"),
    "files": _public_url(8080, "AW_FILES_URL"),
    "smarthome": _public_url(8123, "AW_SMARTHOME_URL"),
}

APP_LINKS = {
    "photos_ios": "https://apps.apple.com/app/immich/id1613945652",
    "photos_android": "https://play.google.com/store/apps/details?id=app.alextran.immich",
    "passwords_ios": "https://apps.apple.com/app/bitwarden-password-manager/id1137397744",
    "passwords_android": "https://play.google.com/store/apps/details?id=com.x8bit.bitwarden",
}

# QR payloads: app downloads + server "configuration" links.
QR_CONTENT = {
    "photos_ios": APP_LINKS["photos_ios"],
    "photos_android": APP_LINKS["photos_android"],
    "photos_config": PUBLIC_URLS["photos"],
    "passwords_ios": APP_LINKS["passwords_ios"],
    "passwords_android": APP_LINKS["passwords_android"],
    "passwords_config": PUBLIC_URLS["passwords"],
}

app = FastAPI(title="August West Onboarding", docs_url=None, redoc_url=None)


# --------------------------------------------------------------------------
# Setup token (one-time, generated at install)
# --------------------------------------------------------------------------
def _load_token() -> str:
    t = os.environ.get("SETUP_TOKEN")
    if t:
        return t.strip()
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    # Fallback: mint one and persist it (dev / manual installs).
    t = secrets.token_urlsafe(24)
    TOKEN_FILE.write_text(t)
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass
    return t


SETUP_TOKEN = _load_token()


def _check_token(token: str | None):
    if not token or not hmac.compare_digest(token, SETUP_TOKEN):
        raise HTTPException(status_code=401,
                            detail="This setup link isn’t valid. Please use the "
                                   "link shown when your August West was installed.")


# --------------------------------------------------------------------------
# Persistent state (so a refresh never restarts the wizard)
# --------------------------------------------------------------------------
def _default_state() -> dict:
    return {
        "version": 1,
        "accounts_created": False,
        "completed": False,
        "account": None,          # {name, email}
        "created": {k: False for k in ("immich", "vaultwarden", "nextcloud", "homeassistant")},
        "handles": {},            # server-side admin handles (never sent to browser)
    }


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            s = json.loads(STATE_FILE.read_text())
            base = _default_state()
            base.update(s)
            return base
        except Exception:
            pass
    return _default_state()


def save_state(state: dict):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)
    try:
        os.chmod(STATE_FILE, 0o600)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
async def health():
    """Public: which services are ready. Used by step 1 polling."""
    return await services.check_health(NC_ADMIN_USER, NC_ADMIN_PASS)


@app.get("/api/state")
async def get_state(x_setup_token: str | None = Header(default=None)):
    _check_token(x_setup_token)
    s = load_state()
    return {
        "completed": s["completed"],
        "accounts_created": s["accounts_created"],
        "account": s["account"],
        "created": s["created"],
        "public_urls": PUBLIC_URLS,
        "app_links": APP_LINKS,
    }


@app.get("/api/config")
async def config():
    """Public, non-sensitive display data (friendly URLs & app links)."""
    return {"public_urls": PUBLIC_URLS, "app_links": APP_LINKS}


def _sse(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode()


@app.post("/api/setup")
async def setup(request: Request, x_setup_token: str | None = Header(default=None)):
    _check_token(x_setup_token)
    state = load_state()
    if state["completed"]:
        raise HTTPException(status_code=409,
                            detail="Your August West is already set up.")

    body = await request.json()
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    confirm = body.get("confirm") or ""

    problem = _validate_signup(name, email, password, confirm)
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    steps = [
        ("immich", "Photo Vault", lambda: services.create_immich(name, email, password)),
        ("vaultwarden", "Password Safe", lambda: services.create_vaultwarden(name, email, password)),
        ("nextcloud", "File Vault",
         lambda: services.create_nextcloud(name, email, password, NC_ADMIN_USER, NC_ADMIN_PASS)),
        ("homeassistant", "Smart Home", lambda: services.create_homeassistant(name, email, password)),
    ]

    async def stream():
        state["account"] = {"name": name, "email": email}
        any_error = False
        for key, label, fn in steps:
            if state["created"].get(key):
                yield _sse({"event": "done", "key": key, "name": label, "skipped": True})
                continue
            yield _sse({"event": "start", "key": key, "name": label})
            try:
                handles = await fn()
                if handles:
                    state["handles"].update(handles)
                state["created"][key] = True
                save_state(state)
                yield _sse({"event": "done", "key": key, "name": label})
            except services.SetupError as e:
                any_error = True
                yield _sse({"event": "error", "key": key, "name": label,
                            "message": e.friendly})
                break
            except Exception:
                any_error = True
                yield _sse({"event": "error", "key": key, "name": label,
                            "message": f"We couldn’t set up your {label}. Let’s try again."})
                break

        if not any_error and all(state["created"].values()):
            state["accounts_created"] = True
            save_state(state)
            yield _sse({"event": "complete"})
        else:
            yield _sse({"event": "failed"})

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.post("/api/family")
async def family(request: Request, x_setup_token: str | None = Header(default=None)):
    _check_token(x_setup_token)
    state = load_state()
    if not state["accounts_created"]:
        raise HTTPException(status_code=409, detail="Please finish your own setup first.")

    body = await request.json()
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    confirm = body.get("confirm") or ""

    problem = _validate_signup(name, email, password, confirm)
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    results = await services.add_family_member(
        name, email, password, state["handles"], NC_ADMIN_USER, NC_ADMIN_PASS)

    added = [services.FRIENDLY[k] for k, ok in results.items() if ok]
    missed = [services.FRIENDLY[k] for k, ok in results.items() if not ok]
    return {
        "added": added,
        "missed": missed,
        "ok": len(added) > 0,
        "message": (
            f"{name.split()[0] if name else 'They'} can now sign in to "
            + _join(added) + "." if added
            else "We couldn’t add them just now — you can try again anytime."
        ),
    }


@app.post("/api/complete")
async def complete(x_setup_token: str | None = Header(default=None)):
    _check_token(x_setup_token)
    state = load_state()
    if not state["accounts_created"]:
        raise HTTPException(status_code=409, detail="Please finish your own setup first.")
    state["completed"] = True
    save_state(state)
    return {"completed": True}


@app.get("/api/qr/{kind}")
async def qr(kind: str):
    content = QR_CONTENT.get(kind)
    if not content:
        raise HTTPException(status_code=404, detail="Unknown code")
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(content, image_factory=qrcode.image.svg.SvgPathImage,
                      box_size=11, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------
def _validate_signup(name, email, password, confirm) -> str | None:
    if not name:
        return "Please enter your full name."
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return "Please enter a valid email address."
    if len(password) < 8:
        return "Please choose a password with at least 8 characters."
    if password != confirm:
        return "The two passwords don’t match. Please re-enter them."
    return None


def _join(items) -> str:
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " and " + items[-1]


@app.exception_handler(HTTPException)
async def friendly_http_exc(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
