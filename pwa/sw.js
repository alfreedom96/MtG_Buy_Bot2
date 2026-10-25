/* Service worker: apre l'app all'istante e anche senza rete.
   - guscio dell'app: dalla cache, aggiornato in background
   - /api/dati e /api/carte: prima dalla rete, dalla cache se offline
   - immagini Scryfall e font: dalla cache dopo il primo download */
const VERSIONE = "pw-v7";
const GUSCIO = ["/", "/app.js", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png", "/badge.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSIONE).then((c) => c.addAll(GUSCIO)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((k) => Promise.all(k.filter((n) => n !== VERSIONE && n !== "runtime").map((n) => caches.delete(n))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  if (url.origin === location.origin && url.pathname.startsWith("/api/")) {
    // rete prima, cache come riserva (la cache tiene solo l'ultima risposta)
    e.respondWith(
      fetch(req).then((r) => {
        if (r.ok) { const copia = r.clone(); caches.open("runtime").then((c) => c.put(req, copia)); }
        return r;
      }).catch(() => caches.match(req).then((r) => r || Response.error()))
    );
    return;
  }

  if (url.origin === location.origin) {
    // guscio: cache subito, aggiornamento in background
    e.respondWith(
      caches.open(VERSIONE).then(async (c) => {
        const inCache = await c.match(req, { ignoreSearch: true });
        const rete = fetch(req).then((r) => { if (r.ok) c.put(req, r.clone()); return r; }).catch(() => null);
        return inCache || (await rete) || Response.error();
      })
    );
    return;
  }

  const statico = url.hostname.endsWith("gstatic.com") || url.hostname === "fonts.googleapis.com" ||
    (url.hostname === "api.scryfall.com" && url.searchParams.get("format") === "image") ||
    url.hostname.endsWith("scryfall.io");
  if (statico) {
    e.respondWith(
      caches.open("runtime").then(async (c) => {
        const inCache = await c.match(req);
        if (inCache) return inCache;
        const r = await fetch(req);
        if (r.ok || r.type === "opaque") c.put(req, r.clone());
        return r;
      })
    );
  }
});

/* ---------------------------------------------------------------- notifiche */

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { testo: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.titolo || "Price Watch", {
    body: d.testo || "",
    icon: "/icon-192.png",
    badge: "/badge.png",
    tag: d.tag || undefined,
    renotify: !!d.tag,
    data: { url: d.url || "/" },
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil((async () => {
    const finestre = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of finestre) {
      if (new URL(c.url).origin === self.location.origin) {
        await c.focus();
        c.postMessage({ vai: url });
        return;
      }
    }
    await self.clients.openWindow(new URL(url, self.location.origin).href);
  })());
});
