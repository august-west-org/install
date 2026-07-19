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
    </div>`;
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
  const online = status ? status.online : true;
  const services = (status && status.services) || [];
  const controlAvailable = status ? status.control_available : true;

  const svcRows = services
    .map(
      (s) => `<div class="svc-row">
        <span class="name">${escapeHtml(s.label)}</span>
        <span class="pill ${s.online ? "online" : "offline"}"><span class="dot"></span>${s.online ? "Online" : "Offline"}</span>
      </div>`
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
  const online = status.online;
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
        (s) => `<div class="svc-row"><span class="name">${escapeHtml(s.label)}</span>
          <span class="pill ${s.online ? "online" : "offline"}"><span class="dot"></span>${s.online ? "Online" : "Offline"}</span></div>`
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

async function onToggle(currentlyOnline, controlAvailable) {
  if (!controlAvailable) return;
  const goingOffline = currentlyOnline; // toggling from online -> offline

  if (goingOffline) {
    const ok = confirm(
      "Take your home offline?\n\nYour data goes dark — no one, anywhere, will be able to " +
        "reach your photos, passwords, files, or smart home until you turn it back on."
    );
    if (!ok) return;
  }

  const sw = el("switch");
  const headline = el("toggle-headline");
  if (sw) sw.setAttribute("aria-disabled", "true");
  if (headline) headline.textContent = goingOffline ? "Going dark…" : "Reconnecting…";

  try {
    const status = await api("/api/toggle", { method: "POST", body: { online: !currentlyOnline } });
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
  if (token) renderDashboard();
  else renderLogin();
  // Periodic refresh while the app is open.
  setInterval(() => { if (token) refreshStatus(); }, 20000);
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

start();
