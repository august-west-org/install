#!/usr/bin/env bash
# Verify that "going dark" is not a one-way door.
#
# Drives the REAL dashboard API through the REAL paths:
#   1. baseline           -- tunnel up, public dashboard answers, mesh up
#   2. go dark            -- toggle offline over the PUBLIC (Cloudflare) URL, the
#                            way the customer's phone does it
#   3. while dark         -- assert the public path is dead AND every part of the
#                            fallback path is untouched and still working
#   4. come back online   -- toggle back on over the FALLBACK path only, with
#                            Cloudflare down the whole time
#   5. confirm            -- aw-cloudflared is running again and the public URL
#                            answers again
#
# Usage:
#   ./test-fallback.sh                     # uses the device's real tailnet IP
#   ./test-fallback.sh --stand-in          # see MECHANISM TEST below
#   AW_TOKEN=<bearer> ./test-fallback.sh   # explicit dashboard session token
#   CUSTOMER_DOMAIN=<customer>.augustwest.org ./test-fallback.sh
#                                          # only if it cannot be read from the
#                                          # device's own cloudflared config
#
# MECHANISM TEST (--stand-in): when the node is not yet approved on the
# coordinator the device has no tailnet IP, so step 4 has no address to use. With
# --stand-in the script creates a dummy interface holding a CGNAT (100.64/10)
# address, points the dashboard's mesh listener at it, and runs the same cycle
# through it. That exercises the listener, the socket proxy, the toggle and the
# recovery for real -- everything EXCEPT the tailnet actually carrying the
# packets. It is NOT proof that the tailnet works; it is labelled as such in the
# output. Run without --stand-in once the node is approved.
set -uo pipefail

# Which public URL should go dark and come back. Read from the device's OWN
# tunnel config -- the same "derive it from where we actually live" rule the
# dashboard front-end uses for its service links -- so this script carries no
# customer name and needs no editing per device. Override with CUSTOMER_DOMAIN
# when the tunnel is configured elsewhere; there is deliberately no default.
CF_CONFIG="${CF_CONFIG:-/etc/cloudflared/config.yml}"
if [ -z "${CUSTOMER_DOMAIN:-}" ] && [ -r "$CF_CONFIG" ]; then
  CUSTOMER_DOMAIN="$(sed -n 's/^[[:space:]]*-*[[:space:]]*hostname:[[:space:]]*dashboard-\([^[:space:]]*\).*/\1/p' \
    "$CF_CONFIG" | head -n1)"
fi
if [ -z "${CUSTOMER_DOMAIN:-}" ]; then
  echo "could not determine the public dashboard hostname from $CF_CONFIG --" >&2
  echo "re-run with CUSTOMER_DOMAIN=<customer>.augustwest.org" >&2
  exit 2
fi
PUBLIC_URL="https://dashboard-${CUSTOMER_DOMAIN}"
DASH_PORT=8889
SESSIONS=/opt/augustwest/dashboard/sessions.json
SOCKET_UNIT=aw-dashboard-mesh.socket
DROPIN=/etc/systemd/system/${SOCKET_UNIT}.d/10-tailnet-address.conf
STANDIN_IF=awmeshtest0
STANDIN_IP=100.64.250.250

STAND_IN=false
[ "${1:-}" = "--stand-in" ] && STAND_IN=true

pass=0; fail=0
ts()   { date -u +%H:%M:%SZ; }
step() { printf '\n\033[1;36m[%s] %s\033[0m\n' "$(ts)" "$*"; }
ok()   { printf '  \033[1;32mPASS\033[0m %s\n' "$*"; pass=$((pass+1)); }
no()   { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; fail=$((fail+1)); }
info() { printf '       %s\n' "$*"; }

check() { # check <description> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1 ($3)"; else no "$1 — expected '$2', got '$3'"; fi
}

# Bearer token: the same thing the phone keeps in localStorage.
TOKEN="${AW_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -r "$SESSIONS" ]; then
  TOKEN="$(python3 - "$SESSIONS" <<'PY'
import json, sys, time
try:
    s = json.load(open(sys.argv[1]))
except Exception:
    sys.exit()
live = [t for t, exp in s.items() if exp > time.time()]
print(live[0] if live else "")
PY
)"
fi
[ -n "$TOKEN" ] || { echo "no dashboard session token (set AW_TOKEN=...)" >&2; exit 2; }

api() { # api <base-url> <method> <path> [json]
  local -a args=(-sS -m 25 -o /tmp/aw-test-body -w '%{http_code}' -X "$2" "$1$3"
                 -H "Authorization: Bearer $TOKEN")
  [ -n "${4:-}" ] && args+=(-H 'Content-Type: application/json' -d "$4")
  curl "${args[@]}" 2>/dev/null
}
tunnel_state() { systemctl is-active aw-cloudflared.service 2>/dev/null; }
mesh_bits() {
  printf 'tailscaled=%s bridge=%s refresh-timer=%s listener=%s' \
    "$(systemctl is-active tailscaled 2>/dev/null)" \
    "$(systemctl is-active aw-mesh-bridge.service 2>/dev/null)" \
    "$(systemctl is-active aw-mesh-refresh.timer 2>/dev/null)" \
    "$(systemctl is-active $SOCKET_UNIT 2>/dev/null)"
}
# Is the coordinator's control plane still usable? Drives the bridge exactly as
# tailscaled does; headscale answers a deliberately short handshake, which proves
# the whole transport (bridge -> websocket -> coordinator) is alive.
control_alive() {
  local out
  out="$(curl -sS -m 20 --http1.1 -H 'Connection: Upgrade' \
    -H 'Upgrade: tailscale-control-protocol' -H 'X-Tailscale-Handshake: cHJvYmU=' \
    "http://127.0.0.1:8990/ts2021" 2>/dev/null | tr -d '\0')"
  case "$out" in *handshake*) return 0 ;; *) return 1 ;; esac
}

cleanup_standin() {
  [ "$STAND_IN" = true ] || return 0
  ip link del "$STANDIN_IF" 2>/dev/null
  rm -f "$DROPIN"
  systemctl daemon-reload 2>/dev/null
  # Stop, don't restart: with the stand-in address gone there is nothing to bind,
  # and FreeBind would happily keep a listener on an address we no longer hold.
  systemctl stop "$SOCKET_UNIT" >/dev/null 2>&1
  /usr/local/bin/aw-mesh-ctl ensure >/dev/null 2>&1
}
trap cleanup_standin EXIT

# ---------------------------------------------------------------------------
step "1. Baseline"
# ---------------------------------------------------------------------------
check "aw-cloudflared running" active "$(tunnel_state)"
check "public dashboard answers" 200 "$(api "$PUBLIC_URL" GET /api/status)"
info "$(mesh_bits)"
if control_alive; then ok "coordinator control plane reachable through the bridge"
else no "coordinator control plane NOT reachable"; fi

MESH_IP="$(tailscale ip -4 2>/dev/null | head -n1)"
if [ -n "$MESH_IP" ]; then
  info "tailnet IP: $MESH_IP (real tailnet path)"
elif [ "$STAND_IN" = true ]; then
  printf '\n\033[1;33m  MECHANISM TEST: no tailnet IP (node not approved yet).\033[0m\n'
  printf '\033[1;33m  Using a stand-in CGNAT address on a dummy interface. This exercises the\n'
  printf '  listener, socket proxy, toggle and recovery for real, but does NOT prove the\n'
  printf '  tailnet carries the packets.\033[0m\n'
  ip link add "$STANDIN_IF" type dummy 2>/dev/null
  ip addr add "$STANDIN_IP/32" dev "$STANDIN_IF" 2>/dev/null
  ip link set "$STANDIN_IF" up
  mkdir -p "$(dirname "$DROPIN")"
  printf '[Socket]\nListenStream=%s:%s\n' "$STANDIN_IP" "$DASH_PORT" > "$DROPIN"
  systemctl daemon-reload
  systemctl restart "$SOCKET_UNIT"
  MESH_IP="$STANDIN_IP"
  info "stand-in address: $MESH_IP"
else
  no "no tailnet IP — the fallback path cannot be tested (approve the node, or use --stand-in)"
  echo; echo "RESULT: $pass passed, $fail failed"; exit 1
fi

MESH_URL="http://${MESH_IP}:${DASH_PORT}"
check "dashboard answers over the fallback address" 200 "$(api "$MESH_URL" GET /api/status)"

# ---------------------------------------------------------------------------
step "2. Go dark — toggle offline over the PUBLIC (Cloudflare) path"
# ---------------------------------------------------------------------------
code="$(api "$PUBLIC_URL" POST /api/toggle '{"online":false}')"
check "toggle-off accepted" 200 "$code"
python3 -c "
import json;d=json.load(open('/tmp/aw-test-body'));print('       dashboard reports online=%s tunnel_state=%s' % (d['online'], d['tunnel_state']))" 2>/dev/null

for _ in $(seq 1 20); do [ "$(tunnel_state)" = active ] || break; sleep 1; done
check "aw-cloudflared stopped" inactive "$(tunnel_state)"

# ---------------------------------------------------------------------------
step "3. While dark — public path dead, fallback path untouched"
# ---------------------------------------------------------------------------
pub="$(api "$PUBLIC_URL" GET /api/status)"
if [ "$pub" = 200 ]; then no "public dashboard STILL answers (http $pub) — not actually dark"
else ok "public dashboard unreachable (http $pub)"; fi

info "$(mesh_bits)"
check "tailscaled still active"        active "$(systemctl is-active tailscaled 2>/dev/null)"
check "tailscaled still enabled"      enabled "$(systemctl is-enabled tailscaled 2>/dev/null)"
check "control bridge still active"    active "$(systemctl is-active aw-mesh-bridge.service 2>/dev/null)"
check "mesh self-heal timer active"    active "$(systemctl is-active aw-mesh-refresh.timer 2>/dev/null)"
check "fallback listener still active" active "$(systemctl is-active $SOCKET_UNIT 2>/dev/null)"
if control_alive; then ok "coordinator control plane STILL reachable with Cloudflare down"
else no "coordinator control plane lost when the tunnel went down"; fi
check "dashboard reachable over the fallback address" 200 "$(api "$MESH_URL" GET /api/status)"
python3 -c "
import json;d=json.load(open('/tmp/aw-test-body'))
print('       over the fallback: online=%s tunnel_state=%s' % (d['online'], d['tunnel_state']))" 2>/dev/null

# ---------------------------------------------------------------------------
step "4. Come back online — using the FALLBACK path only"
# ---------------------------------------------------------------------------
check "tunnel still down before we flip it" inactive "$(tunnel_state)"
code="$(api "$MESH_URL" POST /api/toggle '{"online":true}')"
check "toggle-on accepted over the fallback path" 200 "$code"

# ---------------------------------------------------------------------------
step "5. Confirm Cloudflare came back as a result"
# ---------------------------------------------------------------------------
for _ in $(seq 1 30); do [ "$(tunnel_state)" = active ] && break; sleep 1; done
check "aw-cloudflared running again" active "$(tunnel_state)"
for _ in $(seq 1 30); do
  code="$(api "$PUBLIC_URL" GET /api/status)"; [ "$code" = 200 ] && break; sleep 2
done
check "public dashboard answers again" 200 "$code"

printf '\n\033[1m RESULT: %d passed, %d failed\033[0m\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
