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

  if (p === "/api/carte" && m === "GET") return json({ carte: await leggiCarte(env) });

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
  if (!r.ok) return json({ errore: `GitHub ha risposto ${r.status} leggendo i dati` }, 502);
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
  const righe = leggiCsv(await r.text());
  if (righe.length < 2) return json({ errore: "il foglio sembra vuoto" }, 400);
  const intest = righe[0].map((h) => h.trim().toLowerCase());
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
