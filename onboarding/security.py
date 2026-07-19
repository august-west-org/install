"""One-time setup token, gating access to the onboarding wizard the same way
ADMIN_TOKEN gates Vaultwarden's own admin panel elsewhere in this stack."""
import hmac
import os
import secrets as _secrets

from fastapi import Header, HTTPException

TOKEN_PATH = "/etc/augustwest/onboarding_token"


def ensure_token() -> str:
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH) as f:
            return f.read().strip()
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    token = _secrets.token_urlsafe(32)
    fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    return token


async def require_setup_token(x_setup_token: str | None = Header(default=None)) -> None:
    expected = ensure_token()
    if not x_setup_token or not hmac.compare_digest(x_setup_token, expected):
        raise HTTPException(status_code=403, detail="invalid or missing setup token")
