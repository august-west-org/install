#!/usr/bin/env bash
#
# August West onboarding — install-time setup.
# Generates the one-time setup token, writes .env, builds and starts the
# wizard, then prints the private setup link to hand to the customer.
#
set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE=".env"
DATA_DIR="./data"
mkdir -p "$DATA_DIR"

# --- one-time setup token (generated at install) ---
if [ -f "$DATA_DIR/setup_token" ]; then
  SETUP_TOKEN="$(cat "$DATA_DIR/setup_token")"
else
  SETUP_TOKEN="$(head -c 18 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=')"
  printf '%s' "$SETUP_TOKEN" > "$DATA_DIR/setup_token"
  chmod 600 "$DATA_DIR/setup_token"
fi

# --- Nextcloud admin credentials (created by the main install) ---
: "${NEXTCLOUD_ADMIN_USER:=admin}"
: "${NEXTCLOUD_ADMIN_PASSWORD:?Set NEXTCLOUD_ADMIN_PASSWORD before running}"

# --- public address customers reach their services on (domain or IP) ---
: "${AW_PUBLIC_HOST:=$(hostname -I 2>/dev/null | awk '{print $1}')}"

cat > "$ENV_FILE" <<EOF
SETUP_TOKEN=${SETUP_TOKEN}
NEXTCLOUD_ADMIN_USER=${NEXTCLOUD_ADMIN_USER}
NEXTCLOUD_ADMIN_PASSWORD=${NEXTCLOUD_ADMIN_PASSWORD}
AW_PUBLIC_HOST=${AW_PUBLIC_HOST}
AW_PUBLIC_SCHEME=${AW_PUBLIC_SCHEME:-http}
EOF
chmod 600 "$ENV_FILE"

docker compose up -d --build

echo
echo "======================================================================"
echo " August West is ready to set up."
echo " Open this one-time link on the home network:"
echo
echo "   http://127.0.0.1:8888/?token=${SETUP_TOKEN}"
echo "======================================================================"
