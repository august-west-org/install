#!/usr/bin/env bash
# Installs the host-side tunnel-control units so the dashboard container can
# start/stop aw-cloudflared WITHOUT holding host root or systemd access itself.
# Run once on the host (install.sh calls this when deploying the dashboard).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

install -m 755 "$HERE/aw-tunnel-ctl"            /usr/local/bin/aw-tunnel-ctl
install -m 644 "$HERE/aw-tunnel-apply.path"     /etc/systemd/system/aw-tunnel-apply.path
install -m 644 "$HERE/aw-tunnel-apply.service"  /etc/systemd/system/aw-tunnel-apply.service
install -m 644 "$HERE/aw-tunnel-refresh.service" /etc/systemd/system/aw-tunnel-refresh.service
install -m 644 "$HERE/aw-tunnel-refresh.timer"  /etc/systemd/system/aw-tunnel-refresh.timer

mkdir -p /etc/augustwest/tunnel
systemctl daemon-reload
systemctl enable --now aw-tunnel-apply.path aw-tunnel-refresh.timer
/usr/local/bin/aw-tunnel-ctl refresh   # publish the current state immediately

echo "August West tunnel control units installed and active."
