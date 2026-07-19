const STEP_ORDER = ["welcome", "health", "account", "qr", "icloud", "family", "completion"];

let TOKEN = null;
let STATE = null;
let LABELS = null;
let currentStep = "welcome";

function resolveToken() {
  const params = new URLSearchParams(location.search);
  const fromUrl = params.get("t");
  if (fromUrl) {
    localStorage.setItem("aw_setup_token", fromUrl);
    history.replaceState({}, "", location.pathname);
  }
  return localStorage.getItem("aw_setup_token");
}

async function api(path, opts) {
  opts = opts || {};
  const headers = Object.assign({}, opts.headers, { "X-Setup-Token": TOKEN });
  if (opts.body) headers["Content-Type"] = "application/json";
  const resp = await fetch(path, {
    method: opts.method || "GET",
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

function resumeStep(state) {
  if (state.completed) return "completion";
  if (state.steps.health.status === "pending") return "welcome";
  if (state.steps.account.status !== "done") return "account";
  if (state.steps.qr.status !== "done") return "qr";
  if (state.steps.icloud.status !== "done") return "icloud";
  if (state.steps.family.status !== "done") return "family";
  return "completion";
}

function renderProgress() {
  const idx = STEP_ORDER.indexOf(currentStep);
  const el = document.getElementById("progress");
  el.innerHTML = STEP_ORDER.map((s, i) => {
    const cls = i < idx ? "done" : i === idx ? "current" : "";
    return `<div class="dot ${cls}"></div>`;
  }).join("");
}

async function goto(step) {
  currentStep = step;
  renderProgress();
  const screen = document.getElementById("screen");
  screen.innerHTML = '<div class="center" style="padding:60px 0;"><span class="spinner"></span></div>';
  try {
    await SCREENS[step]();
  } catch (e) {
    screen.innerHTML = `<div class="card"><div class="error-box">${escapeHtml(e.message)}</div></div>`;
  }
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function card(html) {
  document.getElementById("screen").innerHTML = `<div class="card">${html}</div>`;
}

const SCREENS = {
  async welcome() {
    card(`
      <div class="center">
        <div class="icon-big">🏡</div>
        <h1>Welcome to August West</h1>
        <p class="sub">Let's get your home set up: a private photo vault, password safe,
        file storage, and smart home -- all yours, all in one place.</p>
        <button class="btn btn-primary" id="start">Get started</button>
      </div>
    `);
    document.getElementById("start").onclick = () => goto("health");
  },

  async health() {
    card(`
      <h1>Checking your system</h1>
      <p class="sub">Making sure everything is up and running before we begin.</p>
      <div id="health-body" class="center" style="padding:30px 0;"><span class="spinner"></span> Checking...</div>
    `);

    // Services can take a while to boot after install. Rather than hold one long
    // request open (which Cloudflare's proxy may cut off), poll this quick
    // endpoint every few seconds until every service reports healthy, then
    // advance automatically. Each poll is an independent, fast request.
    const POLL_MS = 5000;

    const serviceRows = (result) =>
      Object.values(result || {})
        .map((r) => {
          const pill = r.ok
            ? '<span class="status-pill ok">Ready</span>'
            : '<span class="status-pill bad">Starting...</span>';
          return `<div class="service-row"><span class="name">${r.label}</span>${pill}</div>`;
        })
        .join("");

    const setBody = (html) => {
      const body = document.getElementById("health-body");
      if (body) body.outerHTML = `<div id="health-body">${html}</div>`;
    };

    const poll = async () => {
      // Stop polling if the customer has navigated away from this screen.
      if (currentStep !== "health") return;

      let data;
      try {
        data = await api("/api/steps/health", { method: "POST" });
      } catch (e) {
        // Transient error while things come up -- reassure and keep trying.
        setBody(
          '<div class="center" style="padding:20px 0;"><span class="spinner"></span> ' +
            "Still warming up... checking again shortly</div>"
        );
        setTimeout(poll, POLL_MS);
        return;
      }

      if (data.ready) {
        // Everything is healthy -- proceed automatically.
        goto("account");
        return;
      }

      setBody(
        serviceRows(data.result) +
          '<div class="center" style="padding:16px 0;"><span class="spinner"></span> ' +
          "Still warming up... checking again shortly</div>"
      );
      setTimeout(poll, POLL_MS);
    };

    poll();
  },

  async account() {
    card(`
      <h1>Create your login</h1>
      <p class="sub">One name, one email, one password. This single login unlocks your
      Photo Vault, Password Safe, File Vault, and Smart Home -- no need to remember four
      different passwords.</p>
      <label>Full name</label>
      <input type="text" id="f-name" autocomplete="name" />
      <label>Email address</label>
      <input type="email" id="f-email" autocomplete="email" />
      <label>Master password</label>
      <input type="password" id="f-password" autocomplete="new-password" />
      <p class="hint">This is also your Password Safe master password -- the one password
      that protects everything else stored inside it. Choose something strong you'll
      remember; it can never be recovered if you forget it.</p>
      <label>Password hint (optional)</label>
      <input type="text" id="f-hint" />
      <button class="btn-link" id="toggle-advanced">Use a different password per service (advanced)</button>
      <div id="advanced-fields" style="display:none;">
        <label>Photo Vault password</label>
        <input type="password" id="adv-immich" />
        <label>Password Safe master password</label>
        <input type="password" id="adv-vaultwarden" />
        <label>File Vault password</label>
        <input type="password" id="adv-nextcloud" />
        <label>Smart Home password</label>
        <input type="password" id="adv-homeassistant" />
      </div>
      <button class="btn btn-primary" id="submit">Create my accounts</button>
      <div id="account-error"></div>
    `);
    document.getElementById("toggle-advanced").onclick = () => {
      const el = document.getElementById("advanced-fields");
      el.style.display = el.style.display === "none" ? "block" : "none";
    };
    document.getElementById("submit").onclick = async () => {
      const btn = document.getElementById("submit");
      btn.disabled = true;
      btn.textContent = "Creating your accounts...";
      const advanced = {};
      ["immich", "vaultwarden", "nextcloud", "homeassistant"].forEach((svc) => {
        const v = document.getElementById(`adv-${svc}`).value.trim();
        if (v) advanced[svc] = v;
      });
      try {
        const result = await api("/api/steps/account", {
          method: "POST",
          body: {
            name: document.getElementById("f-name").value.trim(),
            email: document.getElementById("f-email").value.trim(),
            password: document.getElementById("f-password").value,
            password_hint: document.getElementById("f-hint").value.trim(),
            advanced,
          },
        });
        if (result.status !== "done") {
          throw new Error("One or more services couldn't be set up. Please try again.");
        }
        goto("qr");
      } catch (e) {
        btn.disabled = false;
        btn.textContent = "Create my accounts";
        document.getElementById("account-error").innerHTML =
          `<div class="error-box">${escapeHtml(e.message)}</div>`;
      }
    };
  },

  async qr() {
    card(`
      <h1>Set up your phone</h1>
      <p class="sub">Scan to grab the apps and connect them to your home automatically.</p>
      <div id="qr-body" class="center" style="padding:30px 0;"><span class="spinner"></span></div>
    `);
    const bundle = await api("/api/steps/qr");
    const tile = (key, name) => {
      const item = bundle[key];
      if (!item) return "";
      const body = item.qr
        ? `<img src="${item.qr}" alt="${name} QR code" />`
        : `<div class="unavailable">Available once your home is connected to the internet</div>`;
      return `<div class="qr-tile">${body}<div class="name">${name}</div></div>`;
    };
    document.getElementById("qr-body").outerHTML = `
      <div class="qr-grid">
        ${tile("apps", "Get the apps")}
        ${tile("nextcloud", "File Vault")}
        ${tile("immich", "Photo Vault")}
        ${tile("vaultwarden", "Password Safe")}
        ${tile("homeassistant", "Smart Home")}
      </div>
      <p class="hint">Scanning the File Vault code signs your phone in automatically.
      The others may ask you to sign in with your August West login once -- for the
      Password Safe, that's expected and by design: it's the one thing that should
      always ask.</p>
      <p class="hint">App Store listings show each app's real underlying name -- that's normal,
      it's what quietly powers your setup behind the scenes.</p>
      <button class="btn btn-primary" id="continue">Continue</button>
    `;
    document.getElementById("continue").onclick = async () => {
      await api("/api/steps/qr/advance", { method: "POST" });
      goto("icloud");
    };
  },

  async icloud() {
    const items = [
      ["photos", "Photos", "Move your photos from iCloud Photos or Google Photos into your Photo Vault"],
      ["contacts_calendar", "Contacts & Calendar", "Move your contacts and calendar from iCloud or Google into your August West calendar"],
      ["files_documents", "Files & Documents", "Copy files from iCloud Drive or Google Drive into your File Vault"],
      ["passwords", "Passwords", "Import passwords from iCloud Keychain, Google Passwords, or 1Password into your Password Safe"],
    ];
    card(`
      <h1>Taking Back Your Data</h1>
      <p class="sub">This is your data, and it's coming home. Check each one off as you bring it
      over -- there's no rush, and you can always pick up where you left off.</p>
      ${items.map(([key, label, desc]) => `
        <div class="checklist-item">
          <input type="checkbox" id="chk-${key}" />
          <div><div class="label">${label}</div><div class="desc">${desc}</div></div>
        </div>
      `).join("")}
      <button class="btn btn-primary" id="continue">Continue</button>
    `);
    const state = STATE.state.steps.icloud.items || {};
    items.forEach(([key]) => {
      document.getElementById(`chk-${key}`).checked = !!state[key];
    });
    document.getElementById("continue").onclick = async () => {
      const values = {};
      items.forEach(([key]) => {
        values[key] = document.getElementById(`chk-${key}`).checked;
      });
      await api("/api/steps/icloud", { method: "POST", body: { items: values } });
      await api("/api/steps/icloud/advance", { method: "POST" });
      goto("family");
    };
  },

  async family() {
    const members = STATE.state.steps.family.members || [];
    card(`
      <h1>Add family members</h1>
      <p class="sub">Everyone gets their own private login -- their own photos, their own
      password safe, their own files.</p>
      <div id="member-list">
        ${members.map((m) => `<div class="family-member"><div class="name">${escapeHtml(m.name)}</div><div class="hint">${escapeHtml(m.email)}</div></div>`).join("")}
      </div>
      <label>Name</label>
      <input type="text" id="fm-name" />
      <label>Email</label>
      <input type="email" id="fm-email" />
      <label>Password</label>
      <input type="password" id="fm-password" />
      <button class="btn btn-secondary" id="add-member">Add this person</button>
      <div id="member-error"></div>
      <button class="btn btn-primary" id="continue" style="margin-top:28px;">Continue</button>
    `);
    document.getElementById("add-member").onclick = async () => {
      const btn = document.getElementById("add-member");
      btn.disabled = true;
      try {
        await api("/api/steps/family", {
          method: "POST",
          body: {
            name: document.getElementById("fm-name").value.trim(),
            email: document.getElementById("fm-email").value.trim(),
            password: document.getElementById("fm-password").value,
          },
        });
        const fresh = await api("/api/state");
        STATE = fresh;
        goto("family");
      } catch (e) {
        document.getElementById("member-error").innerHTML =
          `<div class="error-box">${escapeHtml(e.message)}</div>`;
        btn.disabled = false;
      }
    };
    document.getElementById("continue").onclick = async () => {
      await api("/api/steps/family/advance", { method: "POST" });
      goto("completion");
    };
  },

  async completion() {
    if (!STATE.state.completed) {
      await api("/api/steps/complete", { method: "POST" });
      STATE = await api("/api/state");
    }
    card(`
      <div class="center">
        <div class="icon-big">✅</div>
        <h1>You're all set</h1>
        <p class="sub">Your home is ready. Open the August West app any time to reach your
        Photo Vault, Password Safe, File Vault, and Smart Home.</p>
      </div>
    `);
  },
};

async function init() {
  TOKEN = resolveToken();
  if (!TOKEN) {
    card(`<div class="error-box">This setup link is missing its access token. Please use
      the link/QR code provided with your August West device.</div>`);
    return;
  }
  try {
    STATE = await api("/api/state");
    LABELS = STATE.labels;
  } catch (e) {
    card(`<div class="error-box">${escapeHtml(e.message)}</div>`);
    return;
  }
  await goto(resumeStep(STATE.state));
}

init();
