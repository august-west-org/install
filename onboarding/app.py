import logging

import dashboard_auth
import topology
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import state as state_store
from provisioning import provision_family_member, provision_primary
from security import require_setup_token
from services import nextcloud, vaultwarden
from services.common import SERVICE_LABELS, check_health

# Configure logging once at import. basicConfig is a no-op if the root logger is
# already configured (e.g. by a custom uvicorn log config), so this only adds a
# stderr handler when nothing else has -- guaranteeing our provisioning error
# logs are actually emitted to the container logs.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("august_west.wizard")

app = FastAPI(title="August West Setup")
app.mount("/static", StaticFiles(directory="static"), name="static")


def _public_state(s: dict) -> dict:
    return {k: v for k, v in s.items() if k != "_internal"}


def _account_failure(email: str, context: str, result: dict) -> HTTPException:
    """Build the 502 raised when one or more services fail to create an account.
    Logs which services failed (with their error detail) and returns an
    HTTPException whose body carries the per-service result so the wizard can
    show the customer exactly what went wrong -- never a silent 200."""
    failed = [name for name, r in result.items() if not r.get("ok")]
    logger.error("%s FAILED for %s; failed services=%s; detail=%s", context, email, failed, result)
    return HTTPException(
        status_code=502,
        detail={
            "status": "error",
            "message": "Some services could not be set up: " + ", ".join(failed),
            "failed": failed,
            "result": result,
        },
    )


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/api/state", dependencies=[Depends(require_setup_token)])
async def get_state():
    s = state_store.load()
    return {"state": _public_state(s), "labels": SERVICE_LABELS}


@app.post("/api/steps/health", dependencies=[Depends(require_setup_token)])
async def run_health_check():
    # Single, immediate probe -- returns right away so the request never blocks
    # long enough to trip Cloudflare's proxy idle timeout. The frontend polls
    # this endpoint every few seconds and advances on its own once `ready` is
    # true. While the stack is still warming up we keep the step "pending" (not
    # "done"), so a page reload mid-warmup resumes correctly on this screen.
    result = await check_health()
    ready = all(r["ok"] for r in result.values())
    s = state_store.load()
    s["steps"]["health"] = {"status": "done" if ready else "pending", "result": result}
    state_store.save(s)
    return {"ready": ready, "status": s["steps"]["health"]["status"], "result": result}


class AccountRequest(BaseModel):
    name: str
    email: str
    password: str
    password_hint: str = ""
    advanced: dict[str, str] = {}


@app.post("/api/steps/account", dependencies=[Depends(require_setup_token)])
async def create_account(body: AccountRequest):
    outcome = await provision_primary(
        body.name, body.email, body.password, body.password_hint, body.advanced
    )
    s = state_store.load()
    s["primary"] = {"name": body.name, "email": body.email, "username": outcome["username"]}
    s["steps"]["account"] = {
        "status": "done" if outcome["ok"] else "error",
        "result": outcome["result"],
    }
    s["quick_access"] = outcome["quick_access"]
    s["_internal"] = outcome["internal"]
    # Persist the (possibly partial) result before deciding the response, so the
    # error detail is available for a later retry/resume.
    state_store.save(s)

    if not outcome["ok"]:
        # Real REST semantics: creation failed, so do NOT return 200. The 502
        # carries the per-service detail and is logged in _account_failure.
        raise _account_failure(body.email, "account creation", outcome["result"])

    # Persist a verifier for the master password so the customer's home-screen
    # dashboard app can authenticate with the same password. Best-effort: never
    # let this break a successful account creation.
    try:
        dashboard_auth.write_master_password_hash(body.password)
    except Exception:  # noqa: BLE001
        logger.exception("could not write dashboard master-password verifier")

    return {"status": "done", "result": outcome["result"]}


# Public app-store landing pages for each companion app. These are plain links
# to the vendors' own download pages (App Store / Google Play) and need NO
# tunnel -- so the download QR codes ALWAYS render, even before the customer's
# home is reachable on the internet. (Password Safe = Bitwarden client; Photo
# Vault = Immich.)
APP_DOWNLOAD_URLS = {
    "immich": "https://immich.app",
    "vaultwarden": "https://bitwarden.com/download",
    "nextcloud": "https://nextcloud.com/install/#install-clients",
    "homeassistant": "https://companion.home-assistant.io",
}


@app.get("/api/steps/qr", dependencies=[Depends(require_setup_token)])
async def get_qr_bundle():
    from services.qr import make_qr_data_uri

    s = state_store.load()
    s["steps"]["qr"] = s["steps"].get("qr") or {}
    tunnel_up = topology.tunnel_configured()

    # Every service gets an always-available app-download QR. The server-connect
    # QR (which signs the app into the customer's home) is only meaningful once
    # the phone can reach the home over the internet, so it is filled in below
    # ONLY when the tunnel is configured.
    services_out = {}
    for key in ("nextcloud", "immich", "vaultwarden", "homeassistant"):
        services_out[key] = {
            "label": SERVICE_LABELS[key],
            "download": {
                "url": APP_DOWNLOAD_URLS[key],
                "qr": make_qr_data_uri(APP_DOWNLOAD_URLS[key]),
            },
            "server_url": topology.public_url(key),
            "connect": {"available": False, "qr": None, "url": None, "mode": None},
        }

    if tunnel_up:
        # Nextcloud: real one-tap Login Flow v2 pairing. Best-effort -- if the
        # server isn't answering the flow yet, leave its connect QR unavailable
        # rather than failing the whole phone step.
        try:
            flow = await nextcloud.start_login_flow()
            services_out["nextcloud"]["connect"] = {
                "available": True,
                "qr": make_qr_data_uri(flow["login_url"]),
                "url": flow["login_url"],
                "mode": "pairing",
            }
            s["steps"]["qr"]["pending_nextcloud_poll"] = {
                "poll_token": flow["poll_token"],
                "poll_endpoint": flow["poll_endpoint"],
            }
        except Exception:  # noqa: BLE001 -- never block the phone step on this
            logger.exception("qr: nextcloud login flow failed")

        # Others: prefill the server URL so the companion app knows where to
        # connect (it still asks for a one-time login -- always, for the
        # Password Safe, by design).
        for key in ("immich", "vaultwarden", "homeassistant"):
            url = topology.public_url(key)
            if url:
                services_out[key]["connect"] = {
                    "available": True,
                    "qr": make_qr_data_uri(url),
                    "url": url,
                    "mode": "prefill",
                }

    s["steps"]["qr"]["status"] = "pending"
    state_store.save(s)
    return {"tunnel_configured": tunnel_up, "services": services_out}


@app.post("/api/steps/qr/check", dependencies=[Depends(require_setup_token)])
async def check_qr_pairing():
    s = state_store.load()
    pending = s["steps"].get("qr", {}).get("pending_nextcloud_poll")
    if not pending:
        return {"nextcloud_paired": False}
    result = await nextcloud.poll_login_flow(pending["poll_token"], pending["poll_endpoint"])
    if result is None:
        return {"nextcloud_paired": False}
    s["quick_access"]["nextcloud"] = {
        **s["quick_access"].get("nextcloud", {}),
        "app_password": result["appPassword"],
        "login_name": result["loginName"],
    }
    s["steps"]["qr"]["status"] = "done"
    state_store.save(s)
    return {"nextcloud_paired": True}


class IcloudChecklistRequest(BaseModel):
    items: dict[str, bool]


@app.post("/api/steps/icloud", dependencies=[Depends(require_setup_token)])
async def update_icloud_checklist(body: IcloudChecklistRequest):
    s = state_store.load()
    s["steps"]["icloud"]["items"].update(body.items)
    if all(s["steps"]["icloud"]["items"].values()) and body.items:
        s["steps"]["icloud"]["status"] = "done"
    state_store.save(s)
    return s["steps"]["icloud"]


class FamilyMemberRequest(BaseModel):
    name: str
    email: str
    password: str
    password_hint: str = ""


@app.post("/api/steps/family", dependencies=[Depends(require_setup_token)])
async def add_family_member(body: FamilyMemberRequest):
    s = state_store.load()
    internal = s.get("_internal", {})
    outcome = await provision_family_member(
        body.name, body.email, body.password, body.password_hint, internal
    )
    member_record = {
        "name": body.name,
        "email": body.email,
        "username": outcome["username"],
        "result": outcome["result"],
    }

    if not outcome["ok"]:
        # Same as the primary account: a failed family member must not report
        # success. Don't record the member or mark the step done.
        raise _account_failure(body.email, "family member creation", outcome["result"])

    s["steps"]["family"]["members"].append(member_record)
    s["steps"]["family"]["status"] = "done"
    state_store.save(s)
    return member_record


@app.post("/api/steps/{step_name}/advance", dependencies=[Depends(require_setup_token)])
async def advance_step(step_name: str):
    from fastapi import HTTPException

    s = state_store.load()
    if step_name not in s["steps"]:
        raise HTTPException(status_code=404, detail="unknown step")
    s["steps"][step_name]["status"] = "done"
    state_store.save(s)
    return {"status": "done"}


@app.post("/api/steps/complete", dependencies=[Depends(require_setup_token)])
async def complete_setup():
    s = state_store.load()
    s["completed"] = True
    s["steps"]["completion"]["status"] = "done"
    state_store.save(s)
    return {"completed": True, "quick_access": s["quick_access"]}


@app.on_event("shutdown")
async def on_shutdown():
    await vaultwarden.shutdown()
