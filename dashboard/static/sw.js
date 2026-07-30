/* August West dashboard service worker.
 *
 * Precaches the app shell so the PWA opens instantly and still loads its UI
 * when the phone is offline (it will show the last data and a reconnect note).
 * API calls (/api/*) and non-GET requests always go to the network -- we never
 * cache status, logins, or toggles. */
// Bump on every shell change: v2 adds the backup-connection (fallback mesh)
// address to the login screen and dashboard, so a phone holding v1 must refetch.
const CACHE = "aw-dashboard-v2";
const SHELL = [
  "/",
  "/static/style.css",
  "/static/app.js",
  "/manifest.json",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return; // logins/toggles must hit the network
  const url = new URL(request.url);
  if (url.pathname.startsWith("/api/")) return; // live data, never cached

  // Cache-first for the shell; fall back to the network and backfill the cache.
  event.respondWith(
    caches.match(request).then(
      (cached) =>
        cached ||
        fetch(request)
          .then((resp) => {
            const copy = resp.clone();
            caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => {});
            return resp;
          })
          .catch(() => cached)
    )
  );
});
