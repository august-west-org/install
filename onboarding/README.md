# August West — Onboarding Wizard

The first-run setup experience shown on a customer's home server right after
install. It creates their account across all four private services and guides
them through leaving iCloud/Google behind — without ever exposing a service
name, IP address, port, or technical error.

## What it does (7 steps)

1. **Health check** — waits until all four services are up ("Still warming up…"
   every 10s), never shows raw errors.
2. **Welcome form** — full name, email, password (+ confirm). One button:
   *Set up my August West*.
3. **Account creation** — creates accounts across all services with live
   progress ("Setting up Photo Vault ✓"). Friendly retry on any failure.
4. **Phone setup** — QR codes for the Photos & Passwords apps (iPhone +
   Android) plus "connect to your home" codes.
5. **Bring your data home** — expandable checklist: turn off iCloud Photos,
   iCloud Keychain, and move passwords into the Password Safe.
6. **Add a family member** *(optional)* — creates an extra account on every
   service. Skippable.
7. **Done** — "Your data is home." with links to each service and app.

## Friendly names (never shown as the real product)

| Shown to customer | Real service   | Internal endpoint        |
|-------------------|----------------|--------------------------|
| Photo Vault       | Immich         | http://127.0.0.1:2283    |
| Password Safe     | Vaultwarden    | http://127.0.0.1:8443    |
| File Vault        | Nextcloud      | http://127.0.0.1:8080    |
| Smart Home        | Home Assistant | http://127.0.0.1:8123    |

## How accounts are created

- **Photo Vault** — customer becomes the owner via `admin-sign-up`. An admin
  API key is minted and stored server-side for adding family members later.
- **Password Safe** — full Bitwarden client-side crypto (PBKDF2 master key,
  protected symmetric key, RSA keypair) so the master password truly unlocks
  the vault. See `app/bw_crypto.py`.
- **File Vault** — created via the admin provisioning (OCS) API.
- **Smart Home** — customer becomes the owner via the onboarding API; a refresh
  token is stored for adding family members over the WebSocket admin API.

The login identifier everywhere is the customer's **email**.

## Security & operational notes

- Binds **127.0.0.1:8888 only** (uvicorn `--host 127.0.0.1`, `network_mode:
  host`). Never exposed to the network. Verified unreachable externally.
- All service calls happen **server-side**; internal ports never reach the
  browser.
- **One-time setup token** generated at install (`data/setup_token`), required
  on every API call via `X-Setup-Token`. After completion, setup is locked
  (HTTP 409).
- **Completion state** persists in `data/state.json` (root-only), so refreshing
  never restarts the wizard. Admin handles (Immich API key, HA refresh token,
  NC admin creds) live only in this file / the container env — never sent to
  the browser.

## Layout

```
onboarding/
├── app/
│   ├── main.py            FastAPI app: routes, token, state, QR
│   ├── services.py        Server-side integrations + plain-English errors
│   ├── bw_crypto.py       Bitwarden/Vaultwarden account crypto
│   └── static/index.html  Single-page wizard (no framework)
├── Dockerfile
├── docker-compose.yml     network_mode: host, restart: unless-stopped
├── requirements.txt
├── install.sh             Generates token + .env, builds & starts, prints link
├── cloudflared-route.sh   Adds the setup.<domain> ingress route to the tunnel
└── data/                  Runtime state (token, state.json) — persisted volume
```

## Access from the customer's phone (Cloudflare tunnel)

The wizard binds to loopback, so it is reached from a phone through the
customer's existing Cloudflare tunnel. `cloudflared-route.sh` adds an ingress
rule to the tunnel config — `setup.<domain> → http://127.0.0.1:8888` — creates
the DNS route, validates, and reloads cloudflared. It is idempotent, backs up
the config, and rolls back if cloudflared rejects the change.

```bash
CF_TUNNEL_NAME=<tunnel> ./cloudflared-route.sh setup.<domain> 8888 <tunnel>
```

The main installer (`/root/augustwest-install.sh`) runs this automatically after
the services are healthy, deploys the wizard, and prints a terminal QR code
(via `qrencode`) pointing at `https://setup.<domain>/?token=<token>` so the
customer can scan it immediately. On a box without a tunnel, run the installer
with `SKIP_CLOUDFLARED=1` for local/dev testing.

## Install / run

```bash
NEXTCLOUD_ADMIN_PASSWORD=... AW_PUBLIC_HOST=<domain-or-ip> ./install.sh
```

Prints the one-time setup link:
`http://127.0.0.1:8888/?token=<token>`

Configuration (env / `.env`): `SETUP_TOKEN`, `NEXTCLOUD_ADMIN_USER`,
`NEXTCLOUD_ADMIN_PASSWORD`, `AW_PUBLIC_HOST`, `AW_PUBLIC_SCHEME`, and optional
per-service public URL overrides `AW_PHOTOS_URL` / `AW_PASSWORDS_URL` /
`AW_FILES_URL` / `AW_SMARTHOME_URL` (use these to show pretty domains instead
of an IP on the final screen).
