#!/usr/bin/env bash
#
# Add a Cloudflare Tunnel ingress route for the onboarding wizard.
#
# The wizard listens on 127.0.0.1:8888 only. This inserts an ingress rule so
# the customer's *existing* tunnel exposes it at setup.<domain>, then creates
# the DNS route and reloads cloudflared. Idempotent and non-destructive: the
# config is backed up and only touched if the route is missing.
#
# Usage:  cloudflared-route.sh <setup-hostname> [local-port] [tunnel-name]
# Env:    CLOUDFLARED_CONFIG   path to config.yml (auto-detected otherwise)
#         CF_TUNNEL_NAME       tunnel name/UUID for the DNS route
#
set -euo pipefail

SETUP_HOSTNAME="${1:?usage: cloudflared-route.sh <setup-hostname> [local-port] [tunnel-name]}"
LOCAL_PORT="${2:-8888}"
TUNNEL="${3:-${CF_TUNNEL_NAME:-}}"
LOCAL_URL="http://127.0.0.1:${LOCAL_PORT}"

log(){ printf '  %s\n' "$*"; }

# --- locate cloudflared + config ---------------------------------------------
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "ERROR: cloudflared is not installed. Install/configure the tunnel first." >&2
  exit 1
fi

CONFIG="${CLOUDFLARED_CONFIG:-}"
if [ -z "$CONFIG" ]; then
  for c in /etc/cloudflared/config.yml /etc/cloudflared/config.yaml \
           "$HOME/.cloudflared/config.yml" "$HOME/.cloudflared/config.yaml"; do
    [ -f "$c" ] && { CONFIG="$c"; break; }
  done
fi
if [ -z "$CONFIG" ] || [ ! -f "$CONFIG" ]; then
  echo "ERROR: could not find the cloudflared config. Set CLOUDFLARED_CONFIG." >&2
  exit 1
fi
log "Using tunnel config: $CONFIG"

# --- idempotency -------------------------------------------------------------
if grep -qE "hostname:[[:space:]]*${SETUP_HOSTNAME//./\\.}([[:space:]]|$)" "$CONFIG"; then
  log "Ingress route for ${SETUP_HOSTNAME} already present — leaving it as-is."
else
  cp -a "$CONFIG" "${CONFIG}.aw-bak"
  log "Backed up config to ${CONFIG}.aw-bak"

  TMP="$(mktemp)"
  # Insert our rule immediately before the catch-all (service: http_status:*),
  # matching the catch-all's indentation so the YAML stays valid.
  awk -v host="$SETUP_HOSTNAME" -v url="$LOCAL_URL" '
    !done && $0 ~ /^[[:space:]]*-[[:space:]]*service:[[:space:]]*http_status:/ {
      match($0, /^[[:space:]]*/); indent = substr($0, 1, RLENGTH)
      print indent "- hostname: " host
      print indent "  service: " url
      done = 1
    }
    { print }
    END { if (!done) exit 3 }
  ' "$CONFIG" > "$TMP" || {
    rm -f "$TMP"
    echo "ERROR: no catch-all ingress rule (service: http_status:404) found." >&2
    echo "       Add this to the ingress list in $CONFIG, before the catch-all:" >&2
    echo "         - hostname: ${SETUP_HOSTNAME}" >&2
    echo "           service: ${LOCAL_URL}" >&2
    exit 3
  }
  mv "$TMP" "$CONFIG"
  log "Added ingress route: ${SETUP_HOSTNAME} -> ${LOCAL_URL}"

  # Validate; roll back on failure.
  if cloudflared tunnel ingress validate --config "$CONFIG" >/dev/null 2>&1; then
    log "Config validated."
  else
    mv "${CONFIG}.aw-bak" "$CONFIG"
    echo "ERROR: cloudflared rejected the new config — reverted. No changes made." >&2
    exit 1
  fi
fi

# --- DNS route (CNAME to the tunnel) -----------------------------------------
if [ -n "$TUNNEL" ]; then
  if cloudflared tunnel route dns "$TUNNEL" "$SETUP_HOSTNAME" >/dev/null 2>&1; then
    log "DNS route created for ${SETUP_HOSTNAME}."
  else
    log "DNS route already exists (or is managed elsewhere) — continuing."
  fi
else
  log "No tunnel name given (CF_TUNNEL_NAME); skipping DNS route creation."
fi

# --- reload cloudflared ------------------------------------------------------
if systemctl reload cloudflared >/dev/null 2>&1 \
   || systemctl restart cloudflared >/dev/null 2>&1; then
  log "Reloaded cloudflared (systemd)."
elif docker restart cloudflared >/dev/null 2>&1; then
  log "Restarted cloudflared (container)."
elif pkill -HUP -x cloudflared >/dev/null 2>&1; then
  log "Signalled cloudflared to reload (SIGHUP)."
else
  log "Could not auto-reload cloudflared — restart it to apply the new route."
fi

log "Onboarding wizard will be reachable at: https://${SETUP_HOSTNAME}/"
