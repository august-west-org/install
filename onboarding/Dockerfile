# August West onboarding wizard.
#
# Slim Python base -- the wizard talks to every service (including Vaultwarden)
# over plain HTTP APIs and does its Bitwarden client-side crypto in pure Python
# (services/bw_crypto.py), so there is no browser, TLS shim, or docker CLI to
# carry. Home Assistant person creation uses a WebSocket (websockets), and
# QR codes use pillow -- both pure-Python wheels.
FROM python:3.12-slim

WORKDIR /opt/augustwest/onboarding

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code. Mutable/host-shared paths (state.json, secrets) come in as
# volumes at runtime -- see docker-compose.yml -- so they are not baked in.
COPY . .

EXPOSE 8888

# Loopback-only, matching the stack's security model: every service binds
# 127.0.0.1 and is reached over the Cloudflare Tunnel / Tailscale, never bound
# to 0.0.0.0. With network_mode: host (see compose) this is the host's loopback.
CMD ["python", "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8888"]
