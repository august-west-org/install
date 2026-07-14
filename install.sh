#!/usr/bin/env bash
#
# ============================================================================
#  August West — home server installer
# ============================================================================
#  Brings up the four private services, waits until they are healthy, exposes
#  the onboarding wizard through the customer's Cloudflare tunnel, deploys the
#  wizard, and prints a QR code the customer scans with their phone to begin.
#
#  Services (internal, loopback only):
#     Photo Vault    Immich          127.0.0.1:2283
#     Password Safe  Vaultwarden     127.0.0.1:8443
#     File Vault     Nextcloud       127.0.0.1:8080
#     Smart Home     Home Assistant  127.0.0.1:8123
#     Setup wizard   onboarding      127.0.0.1:8888  (via setup.<domain>)
# ============================================================================
set -euo pipefail

# ----------------------------------------------------------------------------
# Configuration (override via environment)
# ----------------------------------------------------------------------------
AW_ROOT="${AW_ROOT:-/opt/augustwest}"
ONBOARDING_DIR="${ONBOARDING_DIR:-$AW_ROOT/onboarding}"
CREDS_FILE="${CREDS_FILE:-/root/augustwest-credentials.txt}"

# The customer's base domain, e.g. smith.augustwest.org. Required so the phone
# can reach the wizard over the internet through the tunnel.
AW_BASE_DOMAIN="${AW_BASE_DOMAIN:-}"

# Per-service public hostnames (already routed by the customer's tunnel).
AW_SETUP_HOSTNAME="${AW_SETUP_HOSTNAME:-${AW_BASE_DOMAIN:+setup.$AW_BASE_DOMAIN}}"
PHOTOS_HOST="${PHOTOS_HOST:-${AW_BASE_DOMAIN:+photos.$AW_BASE_DOMAIN}}"
PASSWORDS_HOST="${PASSWORDS_HOST:-${AW_BASE_DOMAIN:+passwords.$AW_BASE_DOMAIN}}"
FILES_HOST="${FILES_HOST:-${AW_BASE_DOMAIN:+files.$AW_BASE_DOMAIN}}"
SMARTHOME_HOST="${SMARTHOME_HOST:-${AW_BASE_DOMAIN:+home.$AW_BASE_DOMAIN}}"

# Cloudflare tunnel name/UUID (for creating the DNS route).
CF_TUNNEL_NAME="${CF_TUNNEL_NAME:-augustwest}"

# Set SKIP_CLOUDFLARED=1 on a box without a tunnel (dev/testing).
SKIP_CLOUDFLARED="${SKIP_CLOUDFLARED:-0}"

# ----------------------------------------------------------------------------
# Pretty output
# ----------------------------------------------------------------------------
c_blue="\033[38;5;74m"; c_dim="\033[2m"; c_ok="\033[38;5;42m"; c_warn="\033[38;5;214m"; c_rst="\033[0m"
step(){ printf "\n${c_blue}⚡ %s${c_rst}\n" "$*"; }
ok(){   printf "   ${c_ok}✓${c_rst} %s\n" "$*"; }
info(){ printf "   ${c_dim}%s${c_rst}\n" "$*"; }
warn(){ printf "   ${c_warn}!${c_rst} %s\n" "$*"; }
die(){  printf "\n\033[38;5;203mERROR: %s${c_rst}\n" "$*" >&2; exit 1; }

# ----------------------------------------------------------------------------
# 0. Preflight
# ----------------------------------------------------------------------------
step "Preflight checks"
[ "$(id -u)" -eq 0 ] || die "Run as root."
command -v docker >/dev/null 2>&1 || die "docker is required."
docker compose version >/dev/null 2>&1 || die "docker compose plugin is required."

# Install the tools we need for the finishing steps.
for pkg in qrencode; do
  command -v "$pkg" >/dev/null 2>&1 || { info "Installing $pkg…"; apt-get update -qq && apt-get install -y -qq "$pkg" >/dev/null; }
done
ok "docker, docker compose, qrencode present"

# Nextcloud admin password: env wins, else read from the credentials file.
if [ -z "${NEXTCLOUD_ADMIN_PASSWORD:-}" ] && [ -f "$CREDS_FILE" ]; then
  NEXTCLOUD_ADMIN_PASSWORD="$(grep -oE 'ADMIN_PASSWORD=[^ ]+' "$CREDS_FILE" | head -1 | cut -d= -f2)"
fi
NEXTCLOUD_ADMIN_USER="${NEXTCLOUD_ADMIN_USER:-admin}"
[ -n "${NEXTCLOUD_ADMIN_PASSWORD:-}" ] || die "NEXTCLOUD_ADMIN_PASSWORD not set and not found in $CREDS_FILE."
ok "Loaded service credentials"

# ----------------------------------------------------------------------------
# 1. Bring up the four services
# ----------------------------------------------------------------------------
step "Starting your private services"
for svc in immich vaultwarden nextcloud homeassistant; do
  d="$AW_ROOT/$svc"
  [ -f "$d/docker-compose.yml" ] || die "Missing $d/docker-compose.yml"
  ( cd "$d" && docker compose up -d >/dev/null 2>&1 ) && ok "$svc started" || die "Failed to start $svc"
done

# ----------------------------------------------------------------------------
# 2. Wait until every service is healthy
# ----------------------------------------------------------------------------
step "Waiting for services to warm up"

# wait_http <name> <url> <grep-pattern|-> <timeout-seconds>
wait_http(){
  local name="$1" url="$2" want="$3" timeout="${4:-180}" waited=0
  while true; do
    local body http
    body="$(curl -fs --max-time 5 "$url" 2>/dev/null || true)"
    http=$?
    if [ -n "$body" ] || [ "$http" -eq 0 ]; then
      if [ "$want" = "-" ] || printf '%s' "$body" | grep -q "$want"; then
        ok "$name is ready"; return 0
      fi
    fi
    waited=$((waited+5)); [ "$waited" -ge "$timeout" ] && die "$name did not become healthy in ${timeout}s."
    info "$name still warming up… (${waited}s)"; sleep 5
  done
}
wait_http "Photo Vault"   "http://127.0.0.1:2283/api/server/ping" '"res":"pong"' 240
wait_http "Password Safe" "http://127.0.0.1:8443/alive"           '-'            180
wait_http "File Vault"    "http://127.0.0.1:8080/status.php"      '"installed":true' 240
wait_http "Smart Home"    "http://127.0.0.1:8123/manifest.json"   '-'            240
ok "All services healthy"

# ----------------------------------------------------------------------------
# 3. One-time setup token
# ----------------------------------------------------------------------------
step "Preparing the onboarding wizard"
mkdir -p "$ONBOARDING_DIR/data"
TOKEN_FILE="$ONBOARDING_DIR/data/setup_token"
if [ -f "$TOKEN_FILE" ]; then
  SETUP_TOKEN="$(cat "$TOKEN_FILE")"
  info "Reusing existing one-time setup token"
else
  SETUP_TOKEN="$(head -c 18 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=')"
  printf '%s' "$SETUP_TOKEN" > "$TOKEN_FILE"; chmod 600 "$TOKEN_FILE"
  ok "Generated one-time setup token"
fi

# ----------------------------------------------------------------------------
# 4. Route the wizard through the Cloudflare tunnel
# ----------------------------------------------------------------------------
step "Publishing the wizard via Cloudflare tunnel"
if [ "$SKIP_CLOUDFLARED" = "1" ]; then
  warn "SKIP_CLOUDFLARED=1 — not touching the tunnel (dev mode)."
  [ -n "$AW_SETUP_HOSTNAME" ] || AW_SETUP_HOSTNAME="127.0.0.1:8888"
else
  [ -n "$AW_SETUP_HOSTNAME" ] || die "AW_BASE_DOMAIN (or AW_SETUP_HOSTNAME) is required to publish the wizard."
  CF_TUNNEL_NAME="$CF_TUNNEL_NAME" bash "$ONBOARDING_DIR/cloudflared-route.sh" \
      "$AW_SETUP_HOSTNAME" 8888 "$CF_TUNNEL_NAME" \
    || die "Could not add the onboarding route to the Cloudflare tunnel."
  ok "Wizard routed at https://$AW_SETUP_HOSTNAME/"
fi

# ----------------------------------------------------------------------------
# 5. Deploy the onboarding container
# ----------------------------------------------------------------------------
step "Deploying the onboarding wizard"
# Public URLs shown on the final screen / used in phone-connect QR codes.
# Behind the tunnel these are pretty https hostnames; fall back to the host IP.
PUBLIC_SCHEME="https"; PUBLIC_FALLBACK_HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat > "$ONBOARDING_DIR/.env" <<EOF
SETUP_TOKEN=${SETUP_TOKEN}
NEXTCLOUD_ADMIN_USER=${NEXTCLOUD_ADMIN_USER}
NEXTCLOUD_ADMIN_PASSWORD=${NEXTCLOUD_ADMIN_PASSWORD}
AW_PUBLIC_SCHEME=${PUBLIC_SCHEME}
AW_PUBLIC_HOST=${AW_BASE_DOMAIN:-$PUBLIC_FALLBACK_HOST}
$([ -n "$PHOTOS_HOST" ]    && echo "AW_PHOTOS_URL=https://$PHOTOS_HOST")
$([ -n "$PASSWORDS_HOST" ] && echo "AW_PASSWORDS_URL=https://$PASSWORDS_HOST")
$([ -n "$FILES_HOST" ]     && echo "AW_FILES_URL=https://$FILES_HOST")
$([ -n "$SMARTHOME_HOST" ] && echo "AW_SMARTHOME_URL=https://$SMARTHOME_HOST")
EOF
chmod 600 "$ONBOARDING_DIR/.env"
( cd "$ONBOARDING_DIR" && docker compose up -d --build >/dev/null 2>&1 ) || die "Failed to deploy onboarding wizard."

# Confirm the wizard is answering on loopback.
wait_http "Setup wizard" "http://127.0.0.1:8888/api/health" '"ready"' 60
ok "Onboarding wizard deployed"

# ----------------------------------------------------------------------------
# 6. Show the customer their setup QR code
# ----------------------------------------------------------------------------
if [ "$SKIP_CLOUDFLARED" = "1" ] && [ "$AW_SETUP_HOSTNAME" = "127.0.0.1:8888" ]; then
  SETUP_URL="http://127.0.0.1:8888/?token=${SETUP_TOKEN}"
else
  SETUP_URL="https://${AW_SETUP_HOSTNAME}/?token=${SETUP_TOKEN}"
fi

printf "\n${c_blue}"
printf '╔══════════════════════════════════════════════════════════════╗\n'
printf '║              August West is ready to set up                  ║\n'
printf '╚══════════════════════════════════════════════════════════════╝'
printf "${c_rst}\n\n"
printf "   Scan this with your phone's camera to begin:\n\n"
qrencode -m 2 -t ANSIUTF8 "$SETUP_URL"
printf "\n   Or open the link directly:\n"
printf "   ${c_blue}%s${c_rst}\n\n" "$SETUP_URL"
info "This is a one-time setup link — keep it private."
