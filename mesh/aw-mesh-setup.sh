#!/usr/bin/env bash
# August West fallback mesh setup -- Tailscale client -> August West Headscale.
#
# WHY THIS EXISTS
#   The customer dashboard can take the home dark by stopping aw-cloudflared.
#   The Cloudflare Tunnel is also the phone's ONLY route to that dashboard, so
#   without a second path "offline" is a one-way door: nothing can turn the front
#   door back on remotely. This script installs the second path -- a Tailscale
#   client joined to the August West Headscale coordinator -- as an INDEPENDENT
#   always-on service that the offline toggle never touches.
#
#   What it installs:
#     * tailscale + tailscaled (official Tailscale apt repo, static tarball
#       fallback), enabled at boot
#     * registration with $HEADSCALE_URL (default https://headscale.augustwest.org)
#     * aw-mesh-bridge -- the control transport, IF the coordinator needs it (see
#       "CONTROL TRANSPORT" below); probed, not assumed
#     * /usr/local/bin/aw-mesh-ctl + aw-mesh-refresh.timer -- self-heals the link
#       every 60s and publishes /etc/augustwest/mesh/state for the dashboard
#     * aw-dashboard-mesh.socket/.service -- the dashboard on <tailnet-ip>:8889
#       (loopback container untouched; nothing new on the public internet)
#     * UFW: allow in on tailscale0 + 41641/udp (direct WireGuard path)
#
# CONTROL TRANSPORT
#   headscale.augustwest.org is currently published through a Cloudflare Tunnel,
#   and the Cloudflare edge strips the `Upgrade: tailscale-control-protocol`
#   header a stock client needs, so registration fails with a 500 from the
#   coordinator. We probe for that and, when it applies, route the client through
#   aw-mesh-bridge, which carries the identical (already Noise-encrypted) control
#   session over a websocket -- the one upgrade Cloudflare does forward.
#   Publish the coordinator directly and `aw-mesh-ctl reprobe` drops the bridge.
#
# CREDENTIALS
#   HEADSCALE_AUTHKEY is an August West OPERATOR credential (a Headscale pre-auth
#   key). Like CF_API_TOKEN and PROVISION_TOKEN it is read ONLY from the
#   environment and never prompted for. Without it the client is still installed
#   and started, and the registration URL is printed for support to approve --
#   the install does not fail.
#
# Safe to re-run: an already-registered, running node is left alone.
set -euo pipefail

HEADSCALE_URL="${HEADSCALE_URL:-https://headscale.augustwest.org}"
AUTHKEY="${HEADSCALE_AUTHKEY:-}"
CUSTOMER="${CUSTOMER:-}"
MESH_HOSTNAME="${MESH_HOSTNAME:-}"

SPOOL=/etc/augustwest/mesh
HERE="$(cd "$(dirname "$0")" && pwd)"

log()  { printf '[aw-mesh] %s\n' "$*"; }
warn() { printf '[aw-mesh] WARNING: %s\n' "$*" >&2; }

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }

# Tailnet hostname: aw-<customer>-<host> keeps a support engineer's `headscale
# nodes list` readable, and Headscale/Tailscale hostnames must be a DNS label.
if [ -z "$MESH_HOSTNAME" ]; then
  raw="aw-${CUSTOMER:+$CUSTOMER-}$(hostname)"
  MESH_HOSTNAME="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]' \
    | tr -c 'a-z0-9-' '-' | sed 's/-\+/-/g; s/^-//; s/-$//' | cut -c1-63)"
fi

# ---------------------------------------------------------------------------
# 1. Tailscale client
# ---------------------------------------------------------------------------
if command -v tailscale >/dev/null 2>&1; then
  log "tailscale already installed: $(tailscale version | head -n1)"
else
  log "installing tailscale"
  . /etc/os-release
  codename="${VERSION_CODENAME:-noble}"
  install -d -m 755 /usr/share/keyrings
  if curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${codename}.noarmor.gpg" \
       -o /usr/share/keyrings/tailscale-archive-keyring.gpg \
     && curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${codename}.tailscale-keyring.list" \
       -o /etc/apt/sources.list.d/tailscale.list \
     && apt-get update -qq \
     && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tailscale; then
    log "installed from the Tailscale apt repo (${codename})"
  else
    # Fallback: static tarball. Keeps a device on an unsupported/unpublished
    # Ubuntu codename from ending up with no fallback path at all.
    warn "apt install failed -- falling back to the static tarball"
    rm -f /etc/apt/sources.list.d/tailscale.list
    arch="$(dpkg --print-architecture)"
    ver="$(curl -fsSL https://pkgs.tailscale.com/stable/?mode=json | sed -n 's/.*"TarballsVersion": *"\([^"]*\)".*/\1/p' | head -n1)"
    [ -n "$ver" ] || { echo "could not determine the latest tailscale version" >&2; exit 1; }
    tmp="$(mktemp -d)"
    curl -fsSL -o "$tmp/ts.tgz" "https://pkgs.tailscale.com/stable/tailscale_${ver}_${arch}.tgz"
    tar -xzf "$tmp/ts.tgz" -C "$tmp"
    src="$tmp/tailscale_${ver}_${arch}"
    install -m 755 "$src/tailscale" "$src/tailscaled" /usr/local/bin/ 2>/dev/null \
      || install -m 755 "$src/tailscale" "$src/tailscaled" /usr/sbin/
    install -m 644 "$src/systemd/tailscaled.service" /etc/systemd/system/tailscaled.service
    install -m 644 "$src/systemd/tailscaled.defaults" /etc/default/tailscaled
    systemctl daemon-reload
    rm -rf "$tmp"
    log "installed tailscale ${ver} from the static tarball"
  fi
fi

systemctl enable --now tailscaled
log "tailscaled: $(systemctl is-active tailscaled) / $(systemctl is-enabled tailscaled)"

# ---------------------------------------------------------------------------
# 2. Host-side control: aw-mesh-ctl + its self-heal timer + the tailnet listener
# ---------------------------------------------------------------------------
install -d -m 755 "$SPOOL"
install -m 755 "$HERE/aw-mesh-ctl"                  /usr/local/bin/aw-mesh-ctl
install -m 755 "$HERE/aw-mesh-bridge"               /usr/local/bin/aw-mesh-bridge
install -m 644 "$HERE/aw-mesh-bridge.service"       /etc/systemd/system/aw-mesh-bridge.service
install -m 644 "$HERE/aw-mesh-refresh.service"      /etc/systemd/system/aw-mesh-refresh.service
install -m 644 "$HERE/aw-mesh-refresh.timer"        /etc/systemd/system/aw-mesh-refresh.timer
install -m 644 "$HERE/aw-dashboard-mesh.socket"     /etc/systemd/system/aw-dashboard-mesh.socket
install -m 644 "$HERE/aw-dashboard-mesh.service"    /etc/systemd/system/aw-dashboard-mesh.service

# mesh.env carries the coordinator + hostname, and the pre-auth key when one was
# supplied, so the 60s self-heal can re-register unattended after a wipe/expiry.
# 0600: it can hold an operator credential. LOGIN_SERVER/USE_BRIDGE are decided
# by the transport probe below (aw-mesh-ctl reprobe), not hardcoded here.
umask 077
{
  printf 'HEADSCALE_URL=%s\n' "$HEADSCALE_URL"
  printf 'MESH_HOSTNAME=%s\n' "$MESH_HOSTNAME"
  [ -n "$AUTHKEY" ] && printf 'AUTHKEY=%s\n' "$AUTHKEY"
} > "$SPOOL/mesh.env"
chmod 600 "$SPOOL/mesh.env"
printf 'AW_MESH_UPSTREAM=%s\n' "$HEADSCALE_URL" > "$SPOOL/bridge.env"
chmod 644 "$SPOOL/bridge.env"
umask 022

systemctl daemon-reload
systemctl enable --now aw-mesh-refresh.timer
systemctl enable aw-dashboard-mesh.socket   # started by aw-mesh-ctl once we hold a tailnet IP

# Pick the control transport (direct if the coordinator accepts the upgrade,
# otherwise the local websocket bridge) before trying to register.
/usr/local/bin/aw-mesh-ctl reprobe

# ---------------------------------------------------------------------------
# 3. Firewall: the tailnet interface is trusted; 41641/udp lets peers find a
#    direct WireGuard path instead of relaying through DERP.
# ---------------------------------------------------------------------------
if command -v ufw >/dev/null 2>&1; then
  ufw allow in on tailscale0 comment 'August West fallback mesh (tailnet)' >/dev/null 2>&1 || true
  ufw allow 41641/udp comment 'tailscale direct (WireGuard)' >/dev/null 2>&1 || true
  log "ufw: tailscale0 trusted, 41641/udp open"
fi

# ---------------------------------------------------------------------------
# 4. Register with Headscale (idempotent)
# ---------------------------------------------------------------------------
state="$(tailscale status --json 2>/dev/null | sed -n 's/.*"BackendState": *"\([^"]*\)".*/\1/p' | head -n1)"
if [ "$state" = Running ]; then
  log "already registered with $HEADSCALE_URL"
  /usr/local/bin/aw-mesh-ctl ensure
else
  /usr/local/bin/aw-mesh-ctl join || true
fi
/usr/local/bin/aw-mesh-ctl refresh

# ---------------------------------------------------------------------------
# 5. Report
# ---------------------------------------------------------------------------
ip4="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
if [ -n "$ip4" ]; then
  log "fallback mesh is UP."
  log "  dashboard over the mesh: http://${ip4}:8889"
  log "  this path stays up when the Cloudflare tunnel is off."
else
  warn "the device is not registered on the tailnet yet."
  if [ -z "$AUTHKEY" ]; then
    warn "no HEADSCALE_AUTHKEY was provided. Approve the node on the coordinator:"
    warn "  headscale nodes list        # find ${MESH_HOSTNAME}"
    warn "  headscale nodes register --user <user> --key <nodekey from the URL above>"
    warn "or re-run with HEADSCALE_AUTHKEY=<pre-auth key> for unattended setup."
  fi
  warn "aw-mesh-refresh.timer keeps retrying every 60s, so the path comes up as"
  warn "soon as the node is approved -- no re-install needed."
fi
