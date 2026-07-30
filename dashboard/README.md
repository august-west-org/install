# August West customer dashboard (PWA)

An installable Progressive Web App the customer adds to their phone home screen.
It shows their home's status in plain English and lets them take it offline
("go dark") with one tap.

Lives at `dashboard-<customer>.augustwest.org`, served by a container on the
customer's device at `127.0.0.1:8889` (reached over the Cloudflare Tunnel).

## What it does

- **Installable** on iOS + Android home screens: `manifest.json`, a service
  worker (`/sw.js`, root scope), maskable + `apple-touch-icon` icons.
- **Service status** in plain English — Photo Vault / Password Safe / File Vault
  / Smart Home Online·Offline, plus "Last backup: X ago".
- **Online/Offline toggle** — a big switch. OFF stops `aw-cloudflared` (the whole
  home goes dark); ON starts it. Going offline asks for confirmation and then
  shows *"Your data is dark — no one can reach it."* It stops **only** the
  Cloudflare tunnel — never the fallback mesh below.
- **Backup connection** — the tailnet address of this dashboard
  (`http://<tailnet-ip>:8889`), shown on the login screen, under the toggle, and
  in the go-dark confirmation. It reaches the dashboard while the tunnel is off,
  so the toggle is not a one-way door. Served by `/api/mesh` (no session needed —
  the customer must be able to read it *before* going dark) from
  `/etc/augustwest/mesh/state`, which the host publishes; see `mesh.py` and
  `../mesh/README.md`. Support runbook: `SUPPORT-OFFLINE.md`.
- **Quick links** to each service (derived from the dashboard's own hostname).
- **Support** button → email to hello@augustwest.org.
- **Auth** with the onboarding master password (never stored in plaintext — the
  wizard writes a PBKDF2 hash to `/etc/augustwest/dashboard_auth.json`). The
  session token is kept in `localStorage`.

## How tunnel control works (container has NO host root/systemd)

The container only writes a one-word intent to `/etc/augustwest/tunnel/desired`
(`up`/`down`). Host-side systemd units apply it with real `systemctl` and write
the observed state back to `/etc/augustwest/tunnel/state`, which the dashboard
reads for status. Install those units once on the host:

```sh
/opt/augustwest/dashboard/host/install.sh
```

Scope is enforced on both sides: `tunnel.ALLOWED_SERVICES` in the app and a guard
in `host/aw-tunnel-ctl` allow `aw-cloudflared.service` and nothing else, and
`aw-tunnel-ctl` re-checks the fallback mesh after every apply. The mesh path
(`../mesh/`) is read-only to this app — it can report the fallback but cannot
take it down.

## Deploy (added to install.sh alongside the onboarding step)

```sh
# 1. source is pulled with the rest of the install repo (Step 6-pre clone)
cp -r <repo>/dashboard /opt/augustwest/dashboard

# 2. host-side tunnel control units
bash /opt/augustwest/dashboard/host/install.sh

# 3. bring up the container (loopback :8889)
cd /opt/augustwest/dashboard && docker compose up -d --build

# 4. add the Cloudflare Tunnel route
#    dashboard-<customer_domain>  ->  http://127.0.0.1:8889
```

The master-password verifier is created by the onboarding wizard when the
customer creates their account, so sign-in works as soon as onboarding is done.
