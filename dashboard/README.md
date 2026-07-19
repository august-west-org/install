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
  shows *"Your data is dark — no one can reach it."*
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
