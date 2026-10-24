/**
 * Cloudflare Worker - Price Watch
 * --------------------------------
 * File GENERATO da build_worker.py a partire da worker/worker_src.js e dalla
 * cartella pwa/. Su Cloudflare si incolla solo cloudflare_worker.js.
 *
 * Fa tre cose:
 *  1. Ospita l'app (PWA) agli indirizzi /, /app.js, /sw.js, ...
 *  2. Espone le API dell'app sotto /api/ (protette da password):
 *       GET  /api/carte      lista carte seguite (salvata in Cloudflare KV)
 *       PUT  /api/carte      salva la lista carte
 *       GET  /api/dati       prezzi e storico compatto (app_data.json dal repo dati privato)
 *       POST /api/controlla  avvia subito il controllo prezzi su GitHub Actions
 *       POST /api/importa    importa le carte da un foglio Google pubblicato come CSV
 *       GET/PUT /api/impostazioni   preferenze (es. avvisi anche su Telegram)
 *       GET  /api/push/chiave       chiave pubblica per le notifiche push
 *       POST /api/push/iscrivi      registra un telefono per le notifiche
 *       POST /api/push/rimuovi      smette di inviare notifiche a un telefono
 *       POST /api/push/prova        manda una notifica di prova
 *       POST /api/notifica          manda una notifica a tutti i telefoni (usato da GitHub Actions)
 *  3. Riceve il webhook di Telegram (POST su qualunque altro indirizzo) e
 *     avvia il workflow dei comandi del bot, come prima.
 *
 * Impostazioni del Worker (Settings -> Variables and Secrets / Bindings):
 *   APP_PASSWORD       (secret) password dell'app; la usa anche GitHub Actions
 *   DB                 binding a un namespace KV (Key-Value) dove si salva la lista carte
 *   DATA_REPO          "utente/nome-repo-dati-privato", dove sta app_data.json
 *   GITHUB_REPO        "utente/nome-repository" del codice (per avviare i workflow)
 *   GITHUB_TOKEN       (secret) token GitHub: Actions read/write sul repo del codice
 *                      e Contents read sul repo dati
 *   DATA_TOKEN         (secret, facoltativo) token separato solo per leggere il repo dati
 *   TELEGRAM_CHAT_ID   la tua chat id (per ignorare messaggi non tuoi)
 *   TELEGRAM_BOT_TOKEN (secret, facoltativo) toglie subito il "caricamento" dai bottoni
 */

const ASSETS = __ASSETS__;

const LINGUE = ["entrambe", "it", "en"];
const FOIL = ["si", "no", "qualsiasi"];

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (url.pathname.startsWith("/api/")) return await gestisciApi(request, env, url);
      if (request.method === "POST") return await gestisciTelegram(request, env);
      if (request.method === "GET" || request.method === "HEAD") return servi(url.pathname);
      return new Response("OK");
    } catch (e) {
      console.log("Errore:", e && e.stack ? e.stack : e);
      if (url.pathname.startsWith("/api/")) return json({ errore: "errore interno del Worker: " + e.message }, 500);
      return new Response("OK");
    }
  },
};

/* ------------------------------------------------------------------ app */

function servi(path) {
  if (path === "/" || path === "/index.html") path = "/index.html";
  const a = ASSETS[path];
  if (!a) return new Response("Pagina non trovata", { status: 404 });
  let corpo = a.body;
  if (a.b64) {
    const bin = atob(a.body);
    corpo = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) corpo[i] = bin.charCodeAt(i);
  }
  return new Response(corpo, {
    headers: {
      "Content-Type": a.type,
      "Cache-Control": a.b64 ? "public, max-age=86400" : "no-cache",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "same-origin",
    },
  });
}

/* ------------------------------------------------------------------ API */

function json(dati, status = 200) {
  return new Response(JSON.stringify(dati), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

async function gestisciApi(request, env, url) {
  if (!env.APP_PASSWORD) return json({ errore: "APP_PASSWORD non impostata nel Worker" }, 500);
  const auth = request.headers.get("Authorization") || "";
  if (!confrontoSicuro(auth, `Bearer ${env.APP_PASSWORD}`)) return json({ errore: "non autorizzato" }, 401);

  const p = url.pathname, m = request.method;

  if (p === "/api/carte" && m === "GET") {
    return json({ carte: await leggiCarte(env), impostazioni: await leggiImpostazioni(env) });
  }

  if (p === "/api/impostazioni" && m === "GET") return json(await leggiImpostazioni(env));
  if (p === "/api/impostazioni" && m === "PUT") {
    const body = await request.json().catch(() => ({}));
    const imp = { ...(await leggiImpostazioni(env)) };
    if (typeof body.telegram === "boolean") imp.telegram = body.telegram;
    await kv(env).put("impostazioni", JSON.stringify(imp));
    return json(imp);
  }

  if (p === "/api/push/chiave" && m === "GET") return json({ chiave: (await chiaviVapid(env)).pubblica });

  if (p === "/api/push/iscrivi" && m === "POST") {
    const body = await request.json().catch(() => ({}));
    const i = body.iscrizione || {};
    if (!i.endpoint || !i.keys || !i.keys.p256dh || !i.keys.auth || !/^https:\/\//.test(i.endpoint)) {
      return json({ errore: "iscrizione non valida" }, 400);
    }
    const lista = (await leggiIscrizioni(env)).filter((x) => x.endpoint !== i.endpoint);
    lista.push({ endpoint: i.endpoint, keys: { p256dh: i.keys.p256dh, auth: i.keys.auth }, nome: testo(body.nome, 60), creata: new Date().toISOString() });
    await kv(env).put("iscrizioni", JSON.stringify(lista.slice(-10)));
    return json({ ok: true, telefoni: lista.length });
  }

  if (p === "/api/push/rimuovi" && m === "POST") {
    const body = await request.json().catch(() => ({}));
    const lista = (await leggiIscrizioni(env)).filter((x) => x.endpoint !== body.endpoint);
    await kv(env).put("iscrizioni", JSON.stringify(lista));
    return json({ ok: true, telefoni: lista.length });
  }

  if (p === "/api/push/prova" && m === "POST") {
    return json(await notificaTutti(env, {
      titolo: "Notifiche attive",
      testo: "Da ora gli avvisi su occasioni e prezzi bassi arrivano qui.",
      url: "/",
      tag: "prova",
    }, request.url));
  }

  if (p === "/api/notifica" && m === "POST") {
    const body = await request.json().catch(() => null);
    if (!body || !body.titolo) return json({ errore: "manca il titolo" }, 400);
    return json(await notificaTutti(env, body, request.url));
  }

  if (p === "/api/carte" && m === "PUT") {
    const body = await request.json().catch(() => null);
    if (!body || !Array.isArray(body.carte)) return json({ errore: "formato non valido" }, 400);
    const carte = valida(body.carte);
    await scriviCarte(env, carte);
    return json({ carte });
  }

  if (p === "/api/dati" && m === "GET") return await leggiDati(env);

  if (p === "/api/controlla" && m === "POST") {
    const r = await avviaWorkflow(env, "check_prices.yml", null);
    return r.ok ? json({ ok: true }) : json({ errore: `GitHub ha risposto ${r.status}: controlla GITHUB_REPO e GITHUB_TOKEN` }, 502);
  }

  if (p === "/api/importa" && m === "POST") {
    const body = await request.json().catch(() => ({}));
    return await importaFoglio(env, String(body.url || ""));
  }

  return json({ errore: "indirizzo sconosciuto" }, 404);
}

function confrontoSicuro(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function kv(env) {
  if (!env.DB) throw new Error("archivio KV non collegato: aggiungi al Worker il binding KV chiamato DB");
  return env.DB;
}

async function leggiCarte(env) {
  const testo = await kv(env).get("carte");
  if (!testo) return [];
  try { return JSON.parse(testo); } catch (e) { return []; }
}

async function scriviCarte(env, carte) {
  await kv(env).put("carte", JSON.stringify(carte));
}

function testo(v, max) {
  return String(v == null ? "" : v).replace(/[\u0000-\u001f]/g, " ").trim().slice(0, max);
}

function valida(lista) {
  const out = [];
  for (const c of lista.slice(0, 500)) {
    if (!c || typeof c !== "object") continue;
    const nome = testo(c.nome_carta, 200);
    if (!nome) continue;
    out.push({
      id: testo(c.id, 64) || crypto.randomUUID(),
      nome_carta: nome,
      lingua: LINGUE.includes(c.lingua) ? c.lingua : "entrambe",
      foil: FOIL.includes(c.foil) ? c.foil : "si",
      percentile_soglia: testo(c.percentile_soglia, 6),
      soglia_occasione: testo(c.soglia_occasione, 6),
      soglia_ribasso: testo(c.soglia_ribasso, 6),
      edizioni_incluse: testo(c.edizioni_incluse, 2000),
      edizioni_escluse: testo(c.edizioni_escluse, 2000),
    });
  }
  return out;
}

function intestazioniGitHub(token) {
  return {
    Authorization: `Bearer ${token}`,
    "User-Agent": "price-watch-worker",
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

async function leggiDati(env) {
  if (!env.DATA_REPO) return json({ errore: "DATA_REPO non impostata nel Worker" }, 500);
  const token = env.DATA_TOKEN || env.GITHUB_TOKEN;
  const r = await fetch(`https://api.github.com/repos/${env.DATA_REPO}/contents/app_data.json`, {
    headers: { ...intestazioniGitHub(token), Accept: "application/vnd.github.raw+json" },
  });
  if (r.status === 404) {
    // Il file non esiste ancora (nessun controllo dopo l'aggiornamento) oppure
    // il token non vede il repository: GitHub risponde 404 in entrambi i casi.
    const repo = await fetch(`https://api.github.com/repos/${env.DATA_REPO}`, { headers: intestazioniGitHub(token) });
    if (!repo.ok) {
      return json({ errore: `il token non riesce a leggere il repository ${env.DATA_REPO}: controlla DATA_REPO e il permesso Contents del token` }, 502);
    }
    return json({ generato: null, varianti: {} });
  }
  if (!r.ok) {
    let dettaglio = "";
    try { dettaglio = (await r.json()).message || ""; } catch (e) {}
    const quale = env.DATA_TOKEN ? "DATA_TOKEN" : "GITHUB_TOKEN (DATA_TOKEN non è impostato)";
    const consiglio = r.status === 403 || r.status === 401
      ? ` Il token usato è ${quale}: deve includere il repository ${env.DATA_REPO} con il permesso Contents: Read-only.`
      : "";
    return json({ errore: `GitHub ha risposto ${r.status} leggendo i dati${dettaglio ? ` ("${dettaglio}")` : ""}.${consiglio}` }, 502);
  }
  return new Response(r.body, {
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

async function avviaWorkflow(env, file, inputs) {
  const corpo = { ref: "main" };
  if (inputs) corpo.inputs = inputs;
  const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${file}/dispatches`, {
    method: "POST",
    headers: { ...intestazioniGitHub(env.GITHUB_TOKEN), Accept: "application/vnd.github+json", "Content-Type": "application/json" },
    body: JSON.stringify(corpo),
  });
  if (!r.ok) console.log(`Errore avviando ${file}:`, r.status, await r.text());
  return r;
}

/* Importazione una tantum dal vecchio foglio Google (CSV pubblicato) */
async function importaFoglio(env, link) {
  let u;
  try { u = new URL(link); } catch (e) { return json({ errore: "link non valido" }, 400); }
  if (u.protocol !== "https:") return json({ errore: "serve un link https" }, 400);
  const r = await fetch(u.toString(), { redirect: "follow" });
  if (!r.ok) return json({ errore: `il foglio ha risposto ${r.status}: è pubblicato sul web come CSV?` }, 502);
  const testoCsv = await r.text();
  const tipo = r.headers.get("Content-Type") || "";
  if (tipo.includes("text/html") || /^\s*<(!doctype|html)/i.test(testoCsv)) {
    return json({ errore: "il link apre una pagina web, non un file CSV. In Pubblica sul web scegli il formato \"Valori separati da virgola (.csv)\": il link deve finire con output=csv" }, 400);
  }
  const righe = leggiCsv(testoCsv);
  if (righe.length < 2) return json({ errore: "il foglio sembra vuoto" }, 400);
  const intest = righe[0].map((h) => h.trim().toLowerCase());
  if (!["nome_carta", "nome", "carta"].some((n) => intest.includes(n))) {
    return json({ errore: `non trovo la colonna nome_carta nella prima riga (ho trovato: ${intest.slice(0, 7).join(", ") || "niente"})` }, 400);
  }
  const col = (riga, ...nomi) => {
    for (const n of nomi) { const i = intest.indexOf(n); if (i >= 0 && riga[i] != null) return riga[i].trim(); }
    return "";
  };
  const attuali = await leggiCarte(env);
  const chiave = (c) => `${c.nome_carta.trim().toLowerCase().split(/\s+/).join(" ")}|${c.lingua}|${c.foil}`;
  const presenti = new Set(attuali.map(chiave));
  let aggiunte = 0, gia = 0;
  const nuove = [];
  for (const riga of righe.slice(1)) {
    const nome = col(riga, "nome_carta", "nome", "carta");
    if (!nome) continue;
    let foil = (col(riga, "foil") || "si").toLowerCase();
    if (foil === "sì" || foil === "yes" || foil === "true") foil = "si";
    const c = valida([{
      nome_carta: nome,
      lingua: (col(riga, "lingua") || "entrambe").toLowerCase(),
      foil,
      percentile_soglia: col(riga, "percentile_soglia"),
      soglia_occasione: col(riga, "soglia_occasione").replace("%", ""),
      soglia_ribasso: col(riga, "soglia_ribasso").replace("%", ""),
      edizioni_incluse: col(riga, "edizioni_incluse"),
      edizioni_escluse: col(riga, "edizioni_escluse"),
    }])[0];
    if (!c) continue;
    if (presenti.has(chiave(c))) { gia++; continue; }
    presenti.add(chiave(c));
    nuove.push(c);
    aggiunte++;
  }
  const carte = valida([...attuali, ...nuove]);
  await scriviCarte(env, carte);
  return json({ aggiunte, gia_presenti: gia, carte });
}

function leggiCsv(t) {
  const righe = [];
  let riga = [], campo = "", virgolette = false;
  for (let i = 0; i < t.length; i++) {
    const ch = t[i];
    if (virgolette) {
      if (ch === '"' && t[i + 1] === '"') { campo += '"'; i++; }
      else if (ch === '"') virgolette = false;
      else campo += ch;
    } else if (ch === '"') virgolette = true;
    else if (ch === ",") { riga.push(campo); campo = ""; }
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && t[i + 1] === "\n") i++;
      riga.push(campo); righe.push(riga); riga = []; campo = "";
    } else campo += ch;
  }
  if (campo || riga.length) { riga.push(campo); righe.push(riga); }
  return righe.filter((r) => r.some((c) => c.trim()));
}

/* ------------------------------------------------------------------ impostazioni */

async function leggiImpostazioni(env) {
  const base = { telegram: true };
  const t = await kv(env).get("impostazioni");
  if (!t) return base;
  try { return { ...base, ...JSON.parse(t) }; } catch (e) { return base; }
}

/* ------------------------------------------------------------------ notifiche push (Web Push) */
/* Standard usati: RFC 8030 (Web Push), RFC 8291 (cifratura dei messaggi,
   "aes128gcm"), RFC 8292 (VAPID, l'identificazione di chi invia). */

const enc = new TextEncoder();

function b64url(bytes) {
  let s = "";
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function daB64url(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function unisci(...parti) {
  const tot = parti.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(tot);
  let i = 0;
  for (const p of parti) { out.set(p, i); i += p.length; }
  return out;
}

async function leggiIscrizioni(env) {
  const t = await kv(env).get("iscrizioni");
  if (!t) return [];
  try { return JSON.parse(t); } catch (e) { return []; }
}

/* Le chiavi VAPID vengono create alla prima richiesta e salvate nel KV:
   non serve configurare niente. Se si cancellano, i telefoni vanno
   riattivati dall'app. */
async function chiaviVapid(env) {
  const salvate = await kv(env).get("vapid");
  if (salvate) {
    try { return JSON.parse(salvate); } catch (e) { /* rigenero */ }
  }
  const coppia = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  const chiavi = {
    pubblica: b64url(await crypto.subtle.exportKey("raw", coppia.publicKey)),
    privata: await crypto.subtle.exportKey("jwk", coppia.privateKey),
  };
  await kv(env).put("vapid", JSON.stringify(chiavi));
  return chiavi;
}

async function intestazioneVapid(chiavi, endpoint, contatto) {
  const aud = new URL(endpoint).origin;
  const head = b64url(enc.encode(JSON.stringify({ typ: "JWT", alg: "ES256" })));
  const body = b64url(enc.encode(JSON.stringify({ aud, exp: Math.floor(Date.now() / 1000) + 12 * 3600, sub: contatto })));
  const chiave = await crypto.subtle.importKey("jwk", chiavi.privata, { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
  const firma = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, chiave, enc.encode(`${head}.${body}`));
  return `vapid t=${head}.${body}.${b64url(firma)}, k=${chiavi.pubblica}`;
}

async function hkdf(salt, ikm, info, lunghezza) {
  const k = await crypto.subtle.importKey("raw", ikm, "HKDF", false, ["deriveBits"]);
  return new Uint8Array(await crypto.subtle.deriveBits({ name: "HKDF", hash: "SHA-256", salt, info }, k, lunghezza * 8));
}

/* Cifra il messaggio per un telefono (RFC 8291) */
async function cifra(iscrizione, testoChiaro) {
  const uaPub = daB64url(iscrizione.keys.p256dh);
  const auth = daB64url(iscrizione.keys.auth);
  const effimera = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
  const asPub = new Uint8Array(await crypto.subtle.exportKey("raw", effimera.publicKey));
  const uaKey = await crypto.subtle.importKey("raw", uaPub, { name: "ECDH", namedCurve: "P-256" }, false, []);
  const segreto = new Uint8Array(await crypto.subtle.deriveBits({ name: "ECDH", public: uaKey }, effimera.privateKey, 256));
  const ikm = await hkdf(auth, segreto, unisci(enc.encode("WebPush: info\0"), uaPub, asPub), 32);
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const cek = await hkdf(salt, ikm, enc.encode("Content-Encoding: aes128gcm\0"), 16);
  const nonce = await hkdf(salt, ikm, enc.encode("Content-Encoding: nonce\0"), 12);
  const chiaveAes = await crypto.subtle.importKey("raw", cek, "AES-GCM", false, ["encrypt"]);
  const chiaro = unisci(enc.encode(testoChiaro), new Uint8Array([2])); // 2 = ultimo blocco
  const cifrato = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce }, chiaveAes, chiaro));
  const rs = new Uint8Array([0, 0, 16, 0]); // dimensione blocco 4096
  return unisci(salt, rs, new Uint8Array([asPub.length]), asPub, cifrato);
}

async function inviaPush(env, chiavi, iscrizione, messaggio, contatto) {
  const corpo = await cifra(iscrizione, JSON.stringify(messaggio));
  return fetch(iscrizione.endpoint, {
    method: "POST",
    headers: {
      Authorization: await intestazioneVapid(chiavi, iscrizione.endpoint, contatto),
      "Content-Encoding": "aes128gcm",
      "Content-Type": "application/octet-stream",
      TTL: "86400",
      Urgency: "high",
    },
    body: corpo,
  });
}

async function notificaTutti(env, dati, urlRichiesta) {
  const messaggio = {
    titolo: testo(dati.titolo, 120),
    testo: testo(dati.testo, 600),
    url: testo(dati.url, 300) || "/",
    tag: testo(dati.tag, 120),
  };
  const iscrizioni = await leggiIscrizioni(env);
  if (!iscrizioni.length) return { inviate: 0, rimosse: 0, errori: [], telefoni: 0 };
  const chiavi = await chiaviVapid(env);
  const contatto = new URL(urlRichiesta).origin; // "sub" del VAPID: un indirizzo di contatto
  const esiti = await Promise.all(iscrizioni.map((i) =>
    inviaPush(env, chiavi, i, messaggio, contatto).then((r) => r.status, (e) => "errore: " + e.message)));
  // 404/410: il telefono ha disattivato le notifiche o reinstallato l'app
  const valide = iscrizioni.filter((_, n) => esiti[n] !== 404 && esiti[n] !== 410);
  if (valide.length !== iscrizioni.length) await kv(env).put("iscrizioni", JSON.stringify(valide));
  return {
    inviate: esiti.filter((x) => typeof x === "number" && x >= 200 && x < 300).length,
    rimosse: iscrizioni.length - valide.length,
    errori: esiti.filter((x) => !(typeof x === "number" && x >= 200 && x < 300) && x !== 404 && x !== 410).map(String),
    telefoni: valide.length,
  };
}

/* ------------------------------------------------------------------ Telegram */

async function gestisciTelegram(request, env) {
  const update = await request.json().catch(() => null);
  if (!update) return new Response("OK");

  let comando, chatId;
  const cb = update.callback_query;
  if (cb) {
    // Click su un bottone inline
    chatId = String(cb.message && cb.message.chat && cb.message.chat.id);
    comando = "cb:" + (cb.data || "menu");
    if (chatId === env.TELEGRAM_CHAT_ID && env.TELEGRAM_BOT_TOKEN) {
      await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/answerCallbackQuery`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ callback_query_id: cb.id }),
      });
    }
  } else {
    const messaggio = update.message || update.edited_message;
    if (!messaggio || !messaggio.text) return new Response("OK");
    chatId = String(messaggio.chat && messaggio.chat.id);
    comando = messaggio.text.trim();
  }

  if (chatId !== env.TELEGRAM_CHAT_ID) return new Response("OK"); // chat non autorizzata

  await avviaWorkflow(env, "bot_commands.yml", { comando });
  return new Response("OK");
}
