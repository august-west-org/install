# August West customer dashboard (PWA backend).
#
# Slim Python base -- the app only talks to the local service ports over HTTP
# (httpx) and drives the tunnel through a shared-file spool, so there is no
# browser, docker CLI, or systemd to carry inside the image.
FROM python:3.12-slim

WORKDIR /opt/augustwest/dashboard

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code + static PWA assets. Mutable/host-shared paths (the auth
# hash, the tunnel spool, sessions.json, the backup log) arrive as volumes at
# runtime -- see docker-compose.yml -- so they are not baked in.
COPY . .

EXPOSE 8889

# Loopback-only, matching the stack's security model: reached from the phone via
# the Cloudflare Tunnel's dashboard-<customer_domain> route, never bound to
# 0.0.0.0. network_mode: host (compose) makes this the host's loopback so the
# local service health checks on 127.0.0.1 keep working.
CMD ["python", "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8889"]
