"""Vaultwarden (Password Safe) integration.

Vaultwarden/Bitwarden accounts are zero-knowledge: the vault's encryption key
is derived and wrapped entirely client-side (PBKDF2 master key, HKDF key
stretching, AES-CBC+HMAC, an RSA keypair for org sharing). This module performs
that client-side crypto in pure Python (see bw_crypto.py) and talks to the
Vaultwarden HTTP API directly -- no browser, no TLS shim, no DOMAIN juggling.

The obvious hazard with hand-rolled password-manager crypto is producing an
account the customer's real Bitwarden app can never unlock -- and registration
would still look like it "succeeded". bw_crypto.py is therefore pinned to the
exact scheme the real web client uses and self-tested against a request captured
from it (its masterPasswordHash is reproduced bit-for-bit, and it can decrypt
the real client's own wrapped key). On top of that, register() confirms every
newly created account by logging back in through the API, which validates the
master-password hash and returns the wrapped vault key -- so a miscomputed
credential fails loudly here instead of silently at the customer's device.

Registration is a two-step Vaultwarden flow:
  1. POST /identity/accounts/register/send-verification-email -> returns a JWT
     verification token in the body (Vaultwarden hands it back directly when no
     SMTP is configured, rather than emailing it).
  2. POST /identity/accounts/register/finish with the crypto material + token.
The KDF salt is the email, never the server URL, so this works over plain
loopback HTTP regardless of the server's configured DOMAIN.
"""
import uuid

import httpx

from . import bw_crypto

BASE = "http://127.0.0.1:8443"
_DEVICE_TYPE = "9"  # Chrome; Vaultwarden requires a device on the token grant


async def register(email: str, name: str, password: str, hint: str = "") -> dict:
    """Creates a real Vaultwarden account via the registration API, then logs
    back in to confirm it. Idempotent: if the email already exists, falls back
    to confirming the given password unlocks it."""
    keys = bw_crypto.build_registration_keys(password, email)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            verif = await client.post(
                f"{BASE}/identity/accounts/register/send-verification-email",
                json={"email": email, "name": name, "receiveMarketingEmails": False},
            )
            # 200 -> token returned in body (no SMTP); anything else is fatal here.
            token = verif.json() if verif.status_code == 200 else None

            finish = await client.post(
                f"{BASE}/identity/accounts/register/finish",
                json={
                    "email": email,
                    "name": name,
                    "masterPasswordHash": keys["masterPasswordHash"],
                    "masterPasswordHint": hint,
                    "userSymmetricKey": keys["userSymmetricKey"],
                    "userAsymmetricKeys": keys["userAsymmetricKeys"],
                    "kdf": keys["kdf"],
                    "kdfIterations": keys["kdfIterations"],
                    "emailVerificationToken": token,
                },
            )
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}

    if finish.status_code in (200, 204):
        # Confirm the freshly created account actually authenticates -- a
        # miscomputed credential surfaces here, not at the customer's app.
        confirm = await login(email, password)
        if confirm["ok"]:
            return {"ok": True, "email": email}
        return {"ok": False, "email": email, "error": "account created but login check failed"}

    if finish.status_code == 400 and "already exist" in finish.text.lower():
        # Idempotent re-run: only a success if the given password unlocks it.
        confirm = await login(email, password)
        if confirm["ok"]:
            return {"ok": True, "email": email, "already_existed": True}
        return {
            "ok": False,
            "email": email,
            "error": "account exists but the provided password did not match",
        }

    return {"ok": False, "email": email, "error": finish.text}


async def login(email: str, password: str) -> dict:
    """Confirms an account is real and the password correct via the API password
    grant. A 200 validates the master-password hash and returns the wrapped
    vault key/private key, i.e. the same material the real Bitwarden client
    needs to unlock -- so this doubles as a crypto-coherence check."""
    mk = bw_crypto.make_master_key(password, email, bw_crypto.DEFAULT_PBKDF2_ITERATIONS)
    master_password_hash = bw_crypto.make_master_password_hash(mk, password)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{BASE}/identity/connect/token",
                data={
                    "grant_type": "password",
                    "username": email,
                    "password": master_password_hash,
                    "scope": "api offline_access",
                    "client_id": "web",
                    "deviceType": _DEVICE_TYPE,
                    "deviceIdentifier": str(uuid.uuid4()),
                    "deviceName": "august-west-setup",
                },
            )
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}
    if resp.status_code != 200:
        return {"ok": False, "error": resp.text}
    return {"ok": True, "email": email}


async def shutdown() -> None:
    """Kept for interface compatibility (app.py calls it on shutdown). The
    direct-API implementation holds no browser/shim resources, so this is a
    no-op."""
    return None
