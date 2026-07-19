"""Persist a verifier for the customer's master password so the home-screen
dashboard app (august-west-org/install -> dashboard/) can authenticate with the
SAME password chosen here -- WITHOUT ever storing the plaintext.

We write only a salted PBKDF2-HMAC-SHA256 hash to a file on the shared
/etc/augustwest volume that both containers mount. Kept in lock-step with the
dashboard's auth.py (same algo, iterations, JSON shape)."""
import hashlib
import json
import os

AUTH_FILE = os.environ.get("AW_DASHBOARD_AUTH_FILE", "/etc/augustwest/dashboard_auth.json")
PBKDF2_ITERATIONS = 200_000


def write_master_password_hash(password: str) -> None:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    record = {
        "algo": "pbkdf2_sha256",
        "iterations": PBKDF2_ITERATIONS,
        "salt": salt.hex(),
        "hash": dk.hex(),
    }
    os.makedirs(os.path.dirname(AUTH_FILE), exist_ok=True)
    tmp = AUTH_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(record, f)
    os.replace(tmp, AUTH_FILE)
