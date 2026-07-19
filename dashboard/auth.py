"""Master-password auth for the customer dashboard.

The customer's master password (chosen during onboarding) is NEVER stored in
plaintext anywhere in the stack. Onboarding instead writes a salted
PBKDF2-HMAC-SHA256 hash of it to AUTH_FILE (a file on the shared /etc/augustwest
volume) when the primary account is created. This module verifies a login
attempt against that hash and mints bearer session tokens.

Sessions are persisted to SESSIONS_FILE so a container restart doesn't sign the
customer out of their home-screen app. Single-tenant device -> a plain JSON file
is plenty, no database.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

AUTH_FILE = os.environ.get("AW_DASHBOARD_AUTH_FILE", "/etc/augustwest/dashboard_auth.json")
SESSIONS_FILE = os.environ.get(
    "AW_DASHBOARD_SESSIONS_FILE", "/opt/augustwest/dashboard/sessions.json"
)
SESSION_TTL = 60 * 60 * 24 * 30  # 30 days
PBKDF2_ITERATIONS = 200_000

_lock = threading.Lock()


def password_is_set() -> bool:
    """True once onboarding has written the master-password verifier."""
    return os.path.exists(AUTH_FILE)


def _load_auth() -> dict | None:
    try:
        with open(AUTH_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def verify_password(password: str) -> bool:
    rec = _load_auth()
    if not rec:
        return False
    try:
        salt = bytes.fromhex(rec["salt"])
        iterations = int(rec.get("iterations", PBKDF2_ITERATIONS))
        expected = bytes.fromhex(rec["hash"])
    except (KeyError, ValueError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------
def _load_sessions() -> dict:
    try:
        with open(SESSIONS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_sessions(sessions: dict) -> None:
    os.makedirs(os.path.dirname(SESSIONS_FILE), exist_ok=True)
    tmp = SESSIONS_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(sessions, f)
    os.replace(tmp, SESSIONS_FILE)


def create_session() -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with _lock:
        sessions = {t: e for t, e in _load_sessions().items() if e > now}  # prune expired
        sessions[token] = now + SESSION_TTL
        _save_sessions(sessions)
    return token


def session_valid(token: str | None) -> bool:
    if not token:
        return False
    now = int(time.time())
    with _lock:
        exp = _load_sessions().get(token)
    return bool(exp and exp > now)


def destroy_session(token: str) -> None:
    with _lock:
        sessions = _load_sessions()
        if sessions.pop(token, None) is not None:
            _save_sessions(sessions)
