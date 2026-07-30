# Getting a customer back online after they've gone dark

**Audience:** August West support. **Situation:** the customer tapped the offline
toggle, their home is dark, and `dashboard-<customer>.augustwest.org` no longer
answers — by design.

Going offline stops **only** `aw-cloudflared.service`. It never touches the
private August West connection (Tailscale ↔ Headscale), which is exactly the path
you use to get back in.

## 1. Find the device's backup address

Either of these, in order of speed:

- **Ask the customer.** The dashboard prints it on its login screen and under the
  toggle, as `http://100.x.y.z:8889`, labelled "Backup connection". They may have
  written it down when they went dark — the confirmation dialog shows it.
- **Look it up on the coordinator:**

  ```sh
  headscale nodes list | grep aw-<customer>
  ```

  Device names follow `aw-<customer>-<hostname>`.

## 2. Join the same private network

Install Tailscale and point it at our coordinator (not Tailscale's own):

```sh
tailscale up --login-server https://headscale.augustwest.org
```

On phones: Tailscale app → Settings → *Use alternate server* →
`https://headscale.augustwest.org`.

## 3. Open the dashboard and flip the toggle

```
http://100.x.y.z:8889
```

Sign in with the customer's master password (they enter it — we never hold it) and
set the switch to **On**. The dashboard writes the intent, the host applies it
with real `systemctl`, and `aw-cloudflared` comes back within a few seconds. The
public `dashboard-<customer>.augustwest.org` answers again shortly after.

## If the backup address doesn't answer

Check, from a device already on the tailnet:

```sh
tailscale ping <device-name>            # is the peer reachable at all?
curl -sS -m 5 http://100.x.y.z:8889/api/mesh   # is the dashboard listening?
```

On the device itself (console/SSH), everything above is one command:

```sh
aw-mesh-ctl status
```

Common causes:

| Symptom | Cause | Fix |
| --- | --- | --- |
| dashboard reports "waiting to be approved" | node registered but not approved on the coordinator | `headscale nodes register --user <user> --key <key>` |
| `backend: NeedsLogin`, no pre-auth key on disk | installed without `HEADSCALE_AUTHKEY` | approve as above, or re-run setup with a key |
| `listener: inactive` | no tailnet IP yet, or the socket unit was stopped | `aw-mesh-ctl ensure` |
| `bridge: inactive` while transport is `websocket-bridge` | control bridge stopped | `systemctl start aw-mesh-bridge` |
| tailnet fine, dashboard 502 | dashboard container down | `cd /opt/augustwest/dashboard && docker compose up -d` |

## Last resort

If the tailnet path is genuinely gone, the toggle can still be flipped on the
device itself — console, or SSH on port 22, which the offline toggle also does not
touch:

```sh
echo up > /etc/augustwest/tunnel/desired    # the path unit applies it
# or directly:
systemctl start aw-cloudflared.service
```

## Note on what the toggle does and doesn't stop

| Stays up when the home is dark | Stopped |
| --- | --- |
| `tailscaled` + the tailnet path | `aw-cloudflared.service` |
| `aw-mesh-bridge`, `aw-mesh-refresh.timer`, `aw-dashboard-mesh.socket` | (nothing else) |
| every app on loopback, backups, heartbeat, SSH | |

If you ever see the mesh go down together with the tunnel, that is a bug —
`aw-tunnel-ctl` asserts the fallback after each apply and logs
`WARNING: fallback mesh was down after a tunnel change` to the journal
(`journalctl -t aw-tunnel-ctl`).
