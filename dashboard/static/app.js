/* August West customer dashboard (PWA). */

const TOKEN_KEY = "aw_dashboard_token";
const SUPPORT_EMAIL = "hello@augustwest.org";
const LIGHTNING = `<svg viewBox="0 0 24 36" fill="none" aria-hidden="true"><path d="M14 0L2 20h8L4 36l18-22h-9L14 0z" fill="#dfeaf9"/></svg>`;

// Service label -> public subdomain prefix (matches the tunnel naming scheme:
// photos./vault./files./home.<customer_domain>). The dashboard itself lives at
// dashboard-<customer_domain>, so we derive each service URL from our own host.
const LINK_PREFIX = {
  photo_vault: "photos",
  password_safe: "vault",
  file_vault: "files",
  smart_home: "home",
};

let token = localStorage.getItem(TOKEN_KEY);

// Last known fallback ("backup connection") details from /api/mesh or
// /api/status. The whole point of the toggle NOT being a one-way door: this
// address reaches the dashboard over the private mesh while the Cloudflare
// tunnel is off, so we surface it before, during and after going dark.
let mesh = null;

// True when this very page was loaded over the fallback path rather than the
// public tunnel -- i.e. the customer is already using the way back in.
function onMeshNow() {
  const h = location.hostname;
  if (!h) return false;
  if (mesh && mesh.address && mesh.address.split(":")[0] === h) return true;
  // 100.64.0.0/10 is the tailnet (CGNAT) range; recognise it even before
  // /api/mesh has answered.
  const m = h.match(/^100\.(\d+)\./);
  return !!m && +m[1] >= 64 && +m[1] <= 127;
}

// Authoritative online/offline state, driven by the 5s /api/tunnel/state poll.
// When the tunnel is down the home is dark: the toggle reads off, the dark
// banner shows, and EVERY service tile reads Offline (they're only reachable
// through the tunnel, even though their loopback health checks still pass).
let tunnelOnline = true;
// Last /api/status payload (services + backup), so a tunnel-state flip can
// repaint without waiting for the slower status poll.
let lastStatus = null;

function el(id) { return document.getElementById(id); }
function escapeHtml(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }

async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers);
  if (token) headers["Authorization"] = "Bearer " + token;
  if (opts.body) headers["Content-Type"] = "application/json";
  const resp = await fetch(path, {
    method: opts.method || "GET",
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (resp.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    token = null;
    const e = new Error("Please sign in again.");
    e.unauthorized = true;
    throw e;
  }
  if (!resp.ok) {
    let message = `Something went wrong (HTTP ${resp.status}).`;
    try {
      const j = await resp.json();
      const d = j.detail;
      if (typeof d === "string") message = d;
      else if (d && d.message) message = d.message;
    } catch (_) {}
    throw new Error(message);
  }
  return resp.json();
}

function brand() {
  return `<div class="brand">
    <svg class="logo" viewBox="0 0 24 36" fill="none" aria-hidden="true"><path d="M14 0L2 20h8L4 36l18-22h-9L14 0z" fill="#dfeaf9"/></svg>
    <div class="wordmark">August <span class="west">West</span></div>
  </div>`;
}

/* -------------------------------------------------------- backup address ---- */
// Shared copy for the login screen and the dashboard. Kept plain: the customer
// reads this out to support, or types it into a browser, on their worst day.
function meshBlock(m, opts = {}) {
  if (!m || !m.configured) return "";
  if (!m.available) {
    return `<div class="mesh-box warn">
      <div class="mesh-title">Backup connection</div>
      <p class="hint">${escapeHtml(m.reason || "Not available right now.")}</p>
    </div>`;
  }
  return `<div class="mesh-box">
    <div class="mesh-title">Backup connection${opts.here ? " — you're on it" : ""}</div>
    <p class="hint">${opts.here
      ? "This page came in over your private August West connection, so it keeps working while your home is dark."
      : "Works even when your home is offline. Connect with the Tailscale app, then open:"}</p>
    <div class="mesh-addr" id="mesh-addr">${escapeHtml(m.url || ("http://" + m.address))}</div>
    ${m.hostname ? `<p class="hint">Device name: <b>${escapeHtml(m.hostname)}</b></p>` : ""}
  </div>`;
}

async function loadMesh() {
  try {
    const res = await fetch("/api/mesh");
    if (res.ok) mesh = await res.json();
  } catch (_) { /* offline / unreachable: leave the last known value */ }
  return mesh;
}

/* ---------------------------------------------------------------- login ---- */
function renderLogin(errMsg) {
  el("app").innerHTML = `
    ${brand()}
    <div class="card">
      <h1>Welcome home</h1>
      <p class="sub">Sign in with your August West master password — the one you created
      when you set up your home.</p>
      <label for="pw">Master password</label>
      <input type="password" id="pw" autocomplete="current-password" />
      <div id="login-error">${errMsg ? `<div class="error-box">${escapeHtml(errMsg)}</div>` : ""}</div>
      <button class="btn btn-primary" id="signin" style="margin-top:18px;">Sign in</button>
      <div id="mesh-hint">${meshBlock(mesh, { here: onMeshNow() })}</div>
    </div>`;

  // The address that still works when the tunnel is down belongs HERE, on the
  // screen the customer can reach while everything is fine -- that is the only
  // moment they can write it down. It needs no session to fetch.
  loadMesh().then((m) => {
    const slot = el("mesh-hint");
    if (slot) slot.innerHTML = meshBlock(m, { here: onMeshNow() });
  });
  const submit = async () => {
    const btn = el("signin");
    const pw = el("pw").value;
    if (!pw) return;
    btn.disabled = true; btn.textContent = "Signing in...";
    try {
      const res = await api("/api/login", { method: "POST", body: { password: pw } });
      token = res.token;
      localStorage.setItem(TOKEN_KEY, token);
      renderDashboard();
    } catch (e) {
      btn.disabled = false; btn.textContent = "Sign in";
      el("login-error").innerHTML = `<div class="error-box">${escapeHtml(e.message)}</div>`;
    }
  };
  el("signin").onclick = submit;
  el("pw").addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
}

/* ------------------------------------------------------------ dashboard ---- */
function serviceLink(key) {
  const prefix = LINK_PREFIX[key];
  const host = location.hostname; // dashboard-<customer>.augustwest.org
  if (!prefix || !host.startsWith("dashboard-")) return null;
  return `https://${prefix}-${host.slice("dashboard-".length)}`;
}

function backupText(backup) {
  if (!backup || !backup.configured) return "Automatic backups aren't set up on this device.";
  if (backup.hours_ago == null) return "No backup has run yet.";
  const h = backup.hours_ago;
  let ago;
  if (h < 1) ago = `${Math.max(1, Math.round(h * 60))} minutes ago`;
  else if (h < 48) ago = `${Math.round(h)} hour${Math.round(h) === 1 ? "" : "s"} ago`;
  else ago = `${Math.round(h / 24)} days ago`;
  return `Last backup: <b>${ago}</b>`;
}

function renderDashboard(status) {
  if (status) lastStatus = status;
  status = status || lastStatus;
  if (status && status.mesh) mesh = status.mesh;
  // The tunnel poll is the single source of truth for online/offline.
  const online = tunnelOnline;
  const services = (status && status.services) || [];
  const controlAvailable = status ? status.control_available : true;

  const svcRows = services
    .map(
      (s) => {
        const svcOnline = online && s.online; // dark tunnel => every tile Offline
        return `<div class="svc-row">
        <span class="name">${escapeHtml(s.label)}</span>
        <span class="pill ${svcOnline ? "online" : "offline"}"><span class="dot"></span>${svcOnline ? "Online" : "Offline"}</span>
      </div>`;
      }
    )
    .join("");

  const linkTiles = (services.length ? services : [])
    .map((s) => {
      const url = serviceLink(s.key);
      if (!url) return "";
      return `<a class="link-tile" href="${url}" target="_blank" rel="noopener">
        ${escapeHtml(s.label)}<span class="arrow">Open ↗</span></a>`;
    })
    .join("");

  const darkBanner = !online
    ? `<div class="dark-banner">${escapeHtml((status && status.offline_message) || "Your data is dark — no one can reach it.")}</div>`
    : "";

  const controlNote = controlAvailable
    ? ""
    : `<p class="hint">Connection control isn't available on this device yet.</p>`;

  // While dark, the backup address is the only way back in -- put it directly
  // under the switch instead of making the customer hunt for it. Warn about a
  // missing fallback BEFORE they go dark, not after.
  const meshCard = (mesh && mesh.configured)
    ? `<div class="card">${meshBlock(mesh, { here: onMeshNow() })}
        ${online && mesh.available
          ? `<p class="hint">Keep this address: it's how you turn your home back on while it's dark.</p>`
          : ""}</div>`
    : "";

  el("app").innerHTML = `
    ${brand()}

    <div class="card toggle-card">
      <div class="toggle-state">Your home</div>
      <p class="toggle-headline ${online ? "on" : "off"}" id="toggle-headline">
        ${online ? "Online — reachable" : "Offline — dark"}
      </p>
      <div class="switch ${online ? "on" : "off"}" id="switch" role="switch"
           aria-checked="${online}" ${controlAvailable ? "" : 'aria-disabled="true"'}>
        <span class="label-on">On</span>
        <span class="label-off">Off</span>
        <span class="knob">${LIGHTNING}</span>
      </div>
      ${darkBanner}
      ${controlNote}
    </div>

    ${meshCard}

    <div class="card">
      <div class="section-title">Service status</div>
      <div id="svc-list">${svcRows || '<p class="hint">Checking…</p>'}</div>
      <div class="backup-line">${backupText(status && status.backup)}</div>
    </div>

    ${linkTiles
      ? `<div class="card"><div class="section-title">Open your apps</div><div class="links-grid">${linkTiles}</div></div>`
      : ""}

    <div class="card footer-actions">
      <a class="btn btn-support" id="support" href="mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent("August West — I need a hand")}">Contact support</a>
      <button class="btn btn-secondary" id="signout">Sign out</button>
    </div>
    <div class="stamp" id="stamp"></div>`;

  el("switch").onclick = () => onToggle(online, controlAvailable);
  el("signout").onclick = async () => {
    try { await api("/api/logout", { method: "POST" }); } catch (_) {}
    localStorage.removeItem(TOKEN_KEY);
    token = null;
    renderLogin();
  };

  // Refresh live data.
  refreshStatus();
  pollTunnelState();
}

async function refreshStatus() {
  try {
    const status = await api("/api/status");
    paintStatus(status);
  } catch (e) {
    if (e.unauthorized) { renderLogin("Please sign in again."); return; }
    const stamp = el("stamp");
    if (stamp) stamp.textContent = "Couldn't refresh — showing last known state.";
  }
}

// Update the already-rendered dashboard in place (avoids flicker / re-binding).
function paintStatus(status) {
  lastStatus = status;
  // online/offline is owned by the tunnel poll, not the per-service status.
  const online = tunnelOnline;
  const headline = el("toggle-headline");
  const sw = el("switch");
  if (headline) {
    headline.textContent = online ? "Online — reachable" : "Offline — dark";
    headline.className = `toggle-headline ${online ? "on" : "off"}`;
  }
  if (sw) {
    sw.className = `switch ${online ? "on" : "off"}`;
    sw.setAttribute("aria-checked", String(online));
    sw.onclick = () => onToggle(online, status.control_available);
  }
  // rebuild the parts that depend on `online` by re-rendering fully only when
  // the online state flips; otherwise just patch rows.
  const list = el("svc-list");
  if (list && status.services) {
    list.innerHTML = status.services
      .map(
        (s) => {
          const svcOnline = online && s.online; // dark tunnel => every tile Offline
          return `<div class="svc-row"><span class="name">${escapeHtml(s.label)}</span>
          <span class="pill ${svcOnline ? "online" : "offline"}"><span class="dot"></span>${svcOnline ? "Online" : "Offline"}</span></div>`;
        }
      )
      .join("");
  }
  const backup = document.querySelector(".backup-line");
  if (backup) backup.innerHTML = backupText(status.backup);

  // The dark banner appears/disappears with the online state -> full re-render
  // keeps the layout correct without hand-patching every dependent node.
  const bannerShown = !!document.querySelector(".dark-banner");
  if (bannerShown !== !online) { renderDashboard(status); return; }

  const stamp = el("stamp");
  if (stamp) stamp.textContent = "Updated just now";
}

// Poll the tunnel state file and flip the whole UI online<->offline. This is the
// authoritative online/offline signal; it runs every 5s so "going dark" (or
// coming back) shows on the phone within seconds.
async function pollTunnelState() {
  try {
    const res = await api("/api/tunnel/state");
    applyTunnelState(res && res.state);
  } catch (e) {
    if (e.unauthorized) { renderLogin("Please sign in again."); return; }
    // transient network error: keep the last known state on screen
  }
}

function applyTunnelState(state) {
  const online = state !== "down";
  if (online === tunnelOnline) return; // no change -> nothing to repaint
  tunnelOnline = online;
  // Re-render fully so the dark banner and every service tile pick up the flip.
  renderDashboard(lastStatus);
}

async function onToggle(currentlyOnline, controlAvailable) {
  if (!controlAvailable) return;
  const goingOffline = currentlyOnline; // toggling from online -> offline

  if (goingOffline) {
    // Refresh the fallback status first: the customer is about to depend on it,
    // and a stale "available" here is what a one-way door looks like.
    await loadMesh();
    let closing = mesh && mesh.available
      ? `\n\nTo turn it back on you'll use your backup connection:\n${mesh.url || "http://" + mesh.address}` +
        `\n\n(Open the Tailscale app on your phone first.)`
      : `\n\nHEADS UP: your backup connection isn't working right now` +
        `${mesh && mesh.reason ? " — " + mesh.reason : ""}\nUntil it is, the only way back on is from this device itself,` +
        ` or by calling support at ${SUPPORT_EMAIL}.`;
    const ok = confirm(
      "Take your home offline?\n\nYour data goes dark — no one, anywhere, will be able to " +
        "reach your photos, passwords, files, or smart home until you turn it back on." + closing
    );
    if (!ok) return;
  }

  const sw = el("switch");
  const headline = el("toggle-headline");
  if (sw) sw.setAttribute("aria-disabled", "true");
  if (headline) headline.textContent = goingOffline ? "Going dark…" : "Reconnecting…";

  try {
    const status = await api("/api/toggle", { method: "POST", body: { online: !currentlyOnline } });
    tunnelOnline = status.online; // reflect the confirmed new state immediately
    renderDashboard(status);
  } catch (e) {
    if (e.unauthorized) { renderLogin("Please sign in again."); return; }
    if (sw) sw.removeAttribute("aria-disabled");
    const stamp = el("stamp");
    if (stamp) stamp.textContent = e.message;
    refreshStatus();
  }
}

/* --------------------------------------------------------------- bootstrap - */
function start() {
  if (token) { loadMesh(); renderDashboard(); }
  else renderLogin();
  // Periodic refresh while the app is open: services/backup every 20s, and the
  // tunnel online/offline state every 5s so "going dark" shows up promptly.
  setInterval(() => { if (token) refreshStatus(); }, 20000);
  setInterval(() => { if (token) pollTunnelState(); }, 5000);
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

start();
