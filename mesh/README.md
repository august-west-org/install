# August West fallback mesh (Tailscale → Headscale)

The permanent second way into a customer's device, independent of Cloudflare.

## Why

The dashboard's offline toggle stops `aw-cloudflared.service` so the home goes
dark. That tunnel is also the phone's *only* route to the dashboard, so on its
own the toggle is a one-way door: nothing can turn the front door back on
remotely. This component is the way back — a Tailscale client joined to the
August West Headscale coordinator, running as its own always-on service that the
toggle never touches.

```
                    ┌──────────────── customer device ────────────────┐
  phone ──public───▶│ aw-cloudflared ──▶ 127.0.0.1:8889 dashboard     │
   │   (Cloudflare) │        ▲                  ▲                     │
   │                │        │ stopped by       │ aw-dashboard-mesh   │
   │                │        │ the toggle       │ (tailnet IP :8889)  │
   └───tailnet─────▶│ tailscaled ──────────────┘                      │
       (Headscale)  └─────────────────────────────────────────────────┘
```

Going dark stops only the top path. The bottom path stays up, so the customer
(or August West support) can sign in and flip the toggle back to online.

## Install

```sh
# Unattended (preferred) — HEADSCALE_AUTHKEY is an operator credential, env only:
HEADSCALE_AUTHKEY='<headscale preauthkeys create ...>' CUSTOMER=<slug> \
  bash /opt/augustwest/mesh/aw-mesh-setup.sh
```

Without a key the client is still installed and started, and the registration URL
is printed for support to approve with `headscale nodes register`. The 60s
self-heal timer keeps retrying, so the path completes the moment the node is
approved — no re-install.

Called automatically by the client install script (Step 6d).

## What gets installed

| Unit / file | Purpose |
| --- | --- |
| `tailscaled.service` | the Tailscale client, enabled at boot (stock unit) |
| `aw-mesh-bridge.service` | control transport, only when needed — see below |
| `aw-mesh-refresh.timer` → `aw-mesh-ctl ensure` | every 60s: self-heal the link, publish state |
| `aw-dashboard-mesh.socket` / `.service` | dashboard on `<tailnet-ip>:8889` via `systemd-socket-proxyd` |
| `/etc/augustwest/mesh/mesh.env` | coordinator, tailnet hostname, pre-auth key (0600) |
| `/etc/augustwest/mesh/state` | observed state, read by the dashboard (0644) |
| UFW | `allow in on tailscale0`, `41641/udp` |

The dashboard container never runs any of this — it only *reads* `state`, exactly
like the tunnel spool. Host root and systemd stay out of the container.

### Why the dashboard needs a socket unit

The dashboard binds `127.0.0.1:8889` only (the stack's safe-by-default rule:
Docker's published ports bypass UFW, so nothing binds a routable address).
`aw-dashboard-mesh.socket` binds *this device's tailnet IP* — never `0.0.0.0` —
and splices connections to that loopback port with stock
`systemd-socket-proxyd`. `aw-mesh-ctl` rewrites the address drop-in whenever the
tailnet IP changes.

## The control transport (`aw-mesh-bridge`)

`headscale.augustwest.org` is currently published **through a Cloudflare Tunnel**
(`CNAME → a6fc19d7-….cfargotunnel.com`). A stock Tailscale client opens its
control session with `Upgrade: tailscale-control-protocol`, and the Cloudflare
edge does not forward that header. Measured on this device:

| Request to the coordinator | Result |
| --- | --- |
| `Upgrade: tailscale-control-protocol` | header **stripped** → headscale sees no upgrade → `500 Internal error` |
| `Upgrade: websocket` | forwarded → `101 Switching Protocols` |

So `tailscale up` can never register while the coordinator is published this way:
`register request: … unexpected HTTP response: 500 Internal Server Error`.

Tailscale's control server — which headscale embeds — speaks the *same* protocol
over a websocket (that is the transport its wasm client uses), but the Linux
client has no flag to select it. `aw-mesh-bridge` supplies it locally:

```
tailscaled ──http──▶ 127.0.0.1:8990 (aw-mesh-bridge) ──wss──▶ headscale
```

Only the framing changes. The session inside is the client's own Noise (ts2021)
session, encrypted end-to-end between `tailscaled` and headscale: the bridge
cannot read or alter it and holds no keys. It is Python-stdlib only — the fallback
path must not depend on a package index.

**This is a workaround for how the coordinator is published, not the design.**
The proper fix is on the monitor server: publish headscale directly (DNS-only
record, TLS on the origin, `443/tcp` + `3478/udp`) instead of through a
Cloudflare Tunnel. Then:

```sh
aw-mesh-ctl reprobe   # finds the working direct upgrade, points the client at
                      # the coordinator, disables the bridge
```

`reprobe` is how the bridge retires itself: it tests for a `101` and only keeps
the bridge when the coordinator still needs it.

## Operating it

```sh
aw-mesh-ctl status     # coordinator, transport, backend, tailnet IP, listener
aw-mesh-ctl ensure     # self-heal now (what the 60s timer runs)
aw-mesh-ctl reprobe    # re-select the control transport
aw-mesh-ctl refresh    # republish state for the dashboard
```

`status` also prints `cloudflared` state, to make the independence visible: the
tunnel can be `inactive` while everything above it is `active`.

## Reaching the dashboard over the mesh

1. Install Tailscale on the phone/laptop and log in against
   `https://headscale.augustwest.org` (Tailscale app → custom login server).
2. Open `http://<tailnet-ip>:8889` — the address the dashboard shows on its login
   screen and under the toggle.
3. Sign in with the master password and flip the toggle back to **On**.

Plain HTTP is intentional: the tailnet leg is already WireGuard-encrypted and the
listener is bound to the tailnet address only, so nothing crosses the public
internet.
