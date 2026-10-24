"use strict";
/* Price Watch - app per seguire i prezzi delle carte Magic su CardTrader.
   Tutti i dati passano dal Cloudflare Worker (/api/...), protetto da password. */

const DEF_PERCENTILE = 15;
const DEF_OCCASIONE = 10;
const SCRYFALL = "https://api.scryfall.com";

const S = {
  pw: leggiLocale("pw"),
  carte: [],
  dati: { generato: null, varianti: {} },
  filtro: "tutte",
  range: {},
  polling: null,
  caricato: false,
};

/* ------------------------------------------------------------------ utilità */

function leggiLocale(k) { try { return localStorage.getItem(k) || ""; } catch (e) { return ""; } }
function scriviLocale(k, v) { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch (e) {} }

const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtEur = new Intl.NumberFormat("it-IT", { style: "currency", currency: "EUR" });
const eur = (n) => (n == null ? "—" : fmtEur.format(n));
const pulisci = (n) => String(n || "").replace(/ /g, " ").replace(/[‘’]/g, "'").replace(/[“”]/g, '"').trim().split(/\s+/).join(" ");
const chiave = (c) => `${pulisci(c.nome_carta).toLowerCase()}|${c.lingua}|${c.foil}`;
const titolo = (n) => n.split(" ").map((p) => (p ? p[0].toUpperCase() + p.slice(1) : p)).join(" ");
const nomeVisibile = (n) => (n === n.toLowerCase() ? titolo(n) : n);
const immagine = (nome, versione) => `${SCRYFALL}/cards/named?exact=${encodeURIComponent(nome)}&format=image&version=${versione}`;

const LINGUE = { entrambe: "IT e EN", it: "Italiano", en: "Inglese" };
const FOIL = { si: "Foil", no: "Non foil", qualsiasi: "Foil o non foil" };
const etichetta = (c) => `${FOIL[c.foil] || c.foil}, ${LINGUE[c.lingua] || c.lingua}`;

function tempoFa(iso) {
  if (!iso) return "";
  const min = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (min < 1) return "adesso";
  if (min < 60) return `${min} min fa`;
  const ore = Math.round(min / 60);
  if (ore < 24) return ore === 1 ? "1 ora fa" : `${ore} ore fa`;
  const g = Math.round(ore / 24);
  return g === 1 ? "ieri" : `${g} giorni fa`;
}

function dataOra(t) {
  const d = new Date(t * 1000);
  return d.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit" }) + " alle " +
    d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
}

let toastTimer;
function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), ms);
}

const ICON = {
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></svg>',
  menu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  back: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m15 18-6-6 6-6"/></svg>',
};

/* ------------------------------------------------------------------ API */

async function api(path, opt = {}) {
  const headers = { Authorization: "Bearer " + S.pw };
  if (opt.body) headers["Content-Type"] = "application/json";
  const r = await fetch(path, { ...opt, headers });
  if (r.status === 401) {
    esci("Password errata.");
    throw new Error("non autorizzato");
  }
  if (!r.ok) {
    let msg = `errore ${r.status}`;
    try { msg = (await r.json()).errore || msg; } catch (e) {}
    throw new Error(msg);
  }
  return r.json();
}

async function caricaCarte() { S.carte = (await api("/api/carte")).carte || []; }
async function caricaDati() { S.dati = await api("/api/dati"); }

async function salvaCarte(nuove) {
  const r = await api("/api/carte", { method: "PUT", body: JSON.stringify({ carte: nuove }) });
  S.carte = r.carte;
}

async function avviaControllo(silenzioso) {
  try {
    await api("/api/controlla", { method: "POST" });
    if (!silenzioso) toast("Controllo avviato: i nuovi prezzi arrivano tra 2 e 5 minuti.");
    attendiNuoviDati();
  } catch (e) {
    toast("Non riesco ad avviare il controllo: " + e.message);
  }
}

function attendiNuoviDati() {
  clearInterval(S.polling);
  const prima = S.dati.generato;
  let giri = 0;
  aggiornaSpinner(true);
  S.polling = setInterval(async () => {
    giri++;
    try { await caricaDati(); } catch (e) {}
    if (S.dati.generato !== prima || giri >= 20) {
      clearInterval(S.polling);
      S.polling = null;
      aggiornaSpinner(false);
      if (S.dati.generato !== prima) toast("Prezzi aggiornati.");
      render();
    }
  }, 30000);
}

function aggiornaSpinner(on) {
  const b = $("#btn-refresh");
  if (b) b.classList.toggle("spin", on);
}

/* ------------------------------------------------------------------ stato carte */

function info(c) { return S.dati.varianti[chiave(c)]; }

function stato(c) {
  const v = info(c);
  if (!v || v.prezzo == null) {
    if (v && v.fallimenti) return { cls: "err", testo: "Nessuna offerta trovata: " + (v.ultimo_errore || "motivo sconosciuto") };
    return { cls: "wait", testo: "In attesa del primo controllo" };
  }
  if (v.fallimenti >= 3) return { cls: "err", testo: `Ultimi ${v.fallimenti} controlli falliti: ${v.ultimo_errore || ""}` };
  if (v.occasione) {
    const sc = Math.round(v.dettaglio.sconto);
    return { cls: "mythic", testo: `Occasione: ${sc}% sotto la media delle offerte più economiche` };
  }
  if (v.prezzo_basso) return { cls: "gold", testo: "Tra i prezzi più bassi mai registrati" };
  return { cls: "", testo: "" };
}

const PESO = { mythic: 0, gold: 1, "": 2, wait: 3, err: 4 };

function gruppi() {
  const m = new Map();
  for (const c of S.carte) {
    const k = pulisci(c.nome_carta).toLowerCase();
    if (!m.has(k)) m.set(k, { nome: c.nome_carta, k, varianti: [] });
    m.get(k).varianti.push(c);
  }
  const lista = [...m.values()];
  for (const g of lista) g.peso = Math.min(...g.varianti.map((c) => PESO[stato(c).cls]));
  lista.sort((a, b) => a.peso - b.peso || a.k.localeCompare(b.k));
  return lista;
}

/* ------------------------------------------------------------------ accesso */

function renderLogin(errore) {
  $("#app").innerHTML = `
    <div class="login">
      <h1>Price Watch</h1>
      <p>Inserisci la password che hai impostato nel Worker (variabile APP_PASSWORD).</p>
      <form id="f-login">
        <input class="input" id="pw" type="password" autocomplete="current-password" placeholder="Password" required>
        <button class="btn primary block" style="margin-top:12px">Entra</button>
        <div class="err" id="login-err">${esc(errore || "")}</div>
      </form>
    </div>`;
  $("#f-login").onsubmit = async (e) => {
    e.preventDefault();
    S.pw = $("#pw").value.trim();
    try {
      await caricaCarte();
      scriviLocale("pw", S.pw);
      await avvio();
    } catch (err) {
      if (err.message !== "non autorizzato") renderLogin("Impossibile contattare il server: " + err.message);
    }
  };
  $("#pw").focus();
}

function esci(msg) {
  S.pw = "";
  scriviLocale("pw", "");
  chiudiPannello();
  renderLogin(msg);
}

/* ------------------------------------------------------------------ lista */

function renderLista() {
  if (!$("#lista-shell")) {
    $("#app").innerHTML = `
      <div id="lista-shell">
        <header class="top">
          <div class="top-row">
            <h1>Le mie carte</h1>
            <button class="icon-btn${S.polling ? " spin" : ""}" id="btn-refresh" aria-label="Aggiorna prezzi ora">${ICON.refresh}</button>
            <button class="icon-btn" id="btn-menu" aria-label="Menu">${ICON.menu}</button>
          </div>
          <div class="fresh" id="fresh"></div>
          <div class="search">
            ${ICON.search}
            <input id="q" type="search" placeholder="Cerca una carta da aggiungere" autocomplete="off" enterkeyhint="search">
            <div class="suggest" id="suggest" hidden></div>
          </div>
          <div class="filters" id="filters"></div>
        </header>
        <main id="lista"></main>
      </div>`;
    $("#btn-refresh").onclick = () => avviaControllo(false);
    $("#btn-menu").onclick = apriMenu;
    collegaRicerca();
  }
  aggiornaLista();
}

function aggiornaLista() {
  const tutti = gruppi();
  const nOcc = tutti.filter((g) => g.peso === 0).length;
  const nBassi = tutti.filter((g) => g.varianti.some((c) => stato(c).cls === "gold")).length;
  const filtri = [["tutte", `Tutte (${tutti.length})`], ["occasioni", `Occasioni (${nOcc})`], ["bassi", `Prezzi bassi (${nBassi})`]];
  $("#filters").innerHTML = filtri.map(([k, t]) => `<button data-f="${k}" aria-pressed="${S.filtro === k}">${t}</button>`).join("");
  $("#filters").onclick = (e) => {
    const b = e.target.closest("button[data-f]");
    if (b) { S.filtro = b.dataset.f; aggiornaLista(); }
  };

  $("#fresh").textContent = S.dati.generato ? `Prezzi aggiornati ${tempoFa(S.dati.generato)}` : "Nessun controllo prezzi ancora disponibile";

  let lista = tutti;
  if (S.filtro === "occasioni") lista = tutti.filter((g) => g.peso === 0);
  if (S.filtro === "bassi") lista = tutti.filter((g) => g.varianti.some((c) => stato(c).cls === "gold"));

  const main = $("#lista");
  if (!S.carte.length) {
    main.innerHTML = `<div class="empty"><strong>Nessuna carta seguita</strong>Cerca una carta qui sopra per aggiungerla, oppure importa la lista dal foglio Google dal menu.</div>`;
    return;
  }
  if (!lista.length) {
    main.innerHTML = `<div class="empty"><strong>Niente da segnalare</strong>Nessuna carta in questa categoria al momento.</div>`;
    return;
  }
  main.innerHTML = lista.map((g) => `
    <button class="card-row" data-k="${esc(g.k)}">
      <img class="thumb" loading="lazy" alt="" src="${esc(immagine(g.nome, "art_crop"))}">
      <div>
        <h2>${esc(nomeVisibile(g.nome))}</h2>
        ${g.varianti.map((c) => {
          const v = info(c), st = stato(c);
          return `<div class="variant">
            <span class="gem ${st.cls}"></span>
            <span class="lbl">${esc(etichetta(c))}</span>
            ${v && v.serie && v.serie.length > 1 ? sparkline(v.serie) : ""}
            <span class="price num">${v && v.prezzo != null ? eur(v.prezzo) : "—"}</span>
          </div>`;
        }).join("")}
      </div>
    </button>`).join("");
  main.onclick = (e) => {
    const r = e.target.closest(".card-row");
    if (r) location.hash = "c/" + encodeURIComponent(r.dataset.k);
  };
}

function sparkline(serie) {
  const ultimo = serie[serie.length - 1][0];
  const pts = serie.filter((p) => p[0] >= ultimo - 7 * 86400);
  if (pts.length < 2) return "";
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const W = 56, H = 20;
  const d = pts.map((p) => {
    const x = x1 === x0 ? 0 : ((p[0] - x0) / (x1 - x0)) * W;
    const y = y1 === y0 ? H / 2 : H - 2 - ((p[1] - y0) / (y1 - y0)) * (H - 4);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" aria-hidden="true"><polyline points="${d}" fill="none" stroke="#93ADA6" stroke-width="1.5"/></svg>`;
}

/* ------------------------------------------------------------------ ricerca */

function collegaRicerca() {
  const q = $("#q"), box = $("#suggest");
  let timer, ultimaQuery = "";
  q.addEventListener("input", () => {
    clearTimeout(timer);
    const testo = q.value.trim();
    if (testo.length < 2) { box.hidden = true; return; }
    timer = setTimeout(async () => {
      ultimaQuery = testo;
      try {
        const r = await fetch(`${SCRYFALL}/cards/autocomplete?q=${encodeURIComponent(testo)}`);
        const j = await r.json();
        if (ultimaQuery !== testo) return;
        const nomi = (j.data || []).slice(0, 8);
        box.innerHTML = nomi.length
          ? nomi.map((n) => `<button data-n="${esc(n)}">${esc(n)}</button>`).join("")
          : `<div class="hint">Nessuna carta trovata. Scrivi il nome in inglese.</div>`;
        box.hidden = false;
      } catch (e) {
        box.innerHTML = `<div class="hint">Ricerca non disponibile: controlla la connessione.</div>`;
        box.hidden = false;
      }
    }, 250);
  });
  box.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-n]");
    if (!b) return;
    box.hidden = true;
    q.value = "";
    q.blur();
    apriModulo({ nome_carta: b.dataset.n });
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".search")) box.hidden = true;
  });
}

/* ------------------------------------------------------------------ dettaglio */

function renderDettaglio(k) {
  const varianti = S.carte.filter((c) => pulisci(c.nome_carta).toLowerCase() === k);
  if (!varianti.length) { location.hash = ""; return; }
  const nome = varianti[0].nome_carta;
  window.scrollTo(0, 0);
  $("#app").innerHTML = `
    <main>
      <div style="padding-top:calc(env(safe-area-inset-top) + 12px)">
        <button class="back" id="back">${ICON.back} Le mie carte</button>
      </div>
      <div class="detail-head">
        <img alt="" src="${esc(immagine(nome, "normal"))}">
        <div>
          <h2>${esc(nomeVisibile(nome))}</h2>
          <button class="btn" id="add-var">Aggiungi variante</button>
        </div>
      </div>
      ${varianti.map((c) => sezioneVariante(c)).join("")}
    </main>`;
  $("#back").onclick = () => (location.hash = "");
  $("#add-var").onclick = () => apriModulo({ nome_carta: nome });
  $("#app").querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => apriModulo(S.carte.find((c) => c.id === b.dataset.edit))));
  $("#app").querySelectorAll("[data-del]").forEach((b) => (b.onclick = () => rimuovi(b.dataset.del)));
  for (const c of varianti) disegnaGrafico(c);
}

function sezioneVariante(c) {
  const v = info(c) || {};
  const st = stato(c);
  const d = v.dettaglio || {};
  const sp = c.percentile_soglia || DEF_PERCENTILE, so = c.soglia_occasione || DEF_OCCASIONE;
  const edizioni = [
    c.edizioni_incluse ? `solo ${c.edizioni_incluse}` : "",
    c.edizioni_escluse ? `escluse ${c.edizioni_escluse}` : "",
  ].filter(Boolean).join("; ") || "tutte";
  let percentileTesto = "";
  if (v.percentile != null) {
    percentileTesto = `<p class="settings-line">Percentile ${Math.round(v.percentile)}: il prezzo attuale è più basso del ${Math.round(100 - v.percentile)}% dei prezzi registrati finora (${v.n} rilevazioni).</p>`;
  } else if (v.n) {
    percentileTesto = `<p class="settings-line">Raccolta dati in corso (${v.n} rilevazioni): il confronto con lo storico parte da 6.</p>`;
  }
  return `
    <section class="vsec">
      <h3><span class="gem ${st.cls}"></span>${esc(etichetta(c))}<span class="sp"></span>
        ${v.ultimo_controllo ? `<small>${esc(tempoFa(v.ultimo_controllo))}</small>` : ""}</h3>
      <div class="big num">${eur(v.prezzo)}</div>
      ${st.testo ? `<p class="status ${st.cls}">${esc(st.testo)}</p>` : ""}
      ${v.prezzo != null ? `
        <div class="stats num">
          <div><small>Minimo</small><b>${eur(v.min)}</b></div>
          <div><small>Medio</small><b>${eur(v.media)}</b></div>
          <div><small>Massimo</small><b>${eur(v.max)}</b></div>
        </div>` : ""}
      ${percentileTesto}
      ${v.serie && v.serie.length ? `
        <div class="ranges" data-ranges="${esc(c.id)}">
          ${[["7", "7 giorni"], ["30", "30 giorni"], ["0", "Tutto"]].map(([r, t]) =>
            `<button data-r="${r}" aria-pressed="${(S.range[c.id] || "30") === r}">${t}</button>`).join("")}
        </div>
        <div class="chart" id="chart-${esc(c.id)}"></div>` : ""}
      ${d.prezzo != null ? `
        <div class="offer">
          <p><b>Offerta migliore: ${eur(d.prezzo)}</b></p>
          <p class="muted">${esc([d.condizione, d.lingua ? d.lingua.toUpperCase() : "", d.foil ? "foil" : "non foil", d.espansione].filter(Boolean).join(", "))}</p>
          <p class="muted">Venditore ${esc(d.venditore || "?")}${d.quantita ? `, ${d.quantita} disponibili` : ""}</p>
          ${d.media20 != null ? `<p class="muted">Media delle ${Math.min(20, d.offerte_valide || 20)} offerte più economiche: ${eur(d.media20)} (minimo ${Math.round(d.sconto)}% sotto)</p>` : ""}
          ${d.link ? `<a class="btn primary block" href="${esc(d.link)}" target="_blank" rel="noopener">Apri su CardTrader</a>` : ""}
        </div>` : ""}
      <p class="settings-line">Avvisi: prezzo sotto il ${esc(sp)}° percentile, occasione da ${esc(so)}% di sconto. Edizioni: ${esc(edizioni)}.</p>
      <div class="row-btns">
        <button class="btn" data-edit="${esc(c.id)}">Modifica</button>
        <button class="btn danger" data-del="${esc(c.id)}">Rimuovi</button>
      </div>
    </section>`;
}

/* ------------------------------------------------------------------ grafico */

function disegnaGrafico(c) {
  const box = document.getElementById("chart-" + c.id);
  if (!box) return;
  const ranges = document.querySelector(`[data-ranges="${CSS.escape(c.id)}"]`);
  ranges.onclick = (e) => {
    const b = e.target.closest("button[data-r]");
    if (!b) return;
    S.range[c.id] = b.dataset.r;
    ranges.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    disegnaGrafico(c);
  };
  const serie = (info(c) || {}).serie || [];
  const giorni = Number(S.range[c.id] || "30");
  const fine = serie.length ? serie[serie.length - 1][0] : 0;
  const pts = giorni ? serie.filter((p) => p[0] >= fine - giorni * 86400) : serie;
  if (pts.length < 2) {
    box.innerHTML = `<p class="settings-line">Servono almeno due rilevazioni in questo periodo per disegnare il grafico.</p>`;
    return;
  }
  const W = Math.max(box.clientWidth, 280), H = 190;
  const L = 4, R = 54, T = 26, B = 22;
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (y1 - y0 < 0.02) { y0 -= 0.5; y1 += 0.5; }
  const pad = (y1 - y0) * 0.08; y0 -= pad; y1 += pad;
  const X = (t) => L + ((t - x0) / (x1 - x0 || 1)) * (W - L - R);
  const Y = (p) => T + (1 - (p - y0) / (y1 - y0)) * (H - T - B);
  const linea = pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
  const area = `${linea}L${X(x1).toFixed(1)},${H - B}L${X(x0).toFixed(1)},${H - B}Z`;
  const iMin = ys.indexOf(Math.min(...ys));
  const ticks = [y0 + pad, (y0 + y1) / 2, y1 - pad];
  const giorno = (t) => new Date(t * 1000).toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit" });
  box.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Andamento del prezzo">
      <defs><linearGradient id="g-${esc(c.id)}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#B7C4C9" stop-opacity=".22"/><stop offset="1" stop-color="#B7C4C9" stop-opacity="0"/>
      </linearGradient></defs>
      ${ticks.map((v) => `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="#1D4740"/>
        <text x="${W - R + 6}" y="${Y(v) + 4}" fill="#93ADA6" font-size="12" class="num">${eur(v)}</text>`).join("")}
      <path d="${area}" fill="url(#g-${esc(c.id)})"/>
      <path d="${linea}" fill="none" stroke="#EAF2EE" stroke-width="1.8" stroke-linejoin="round"/>
      <circle cx="${X(pts[iMin][0])}" cy="${Y(pts[iMin][1])}" r="4" fill="#E3B94F"/>
      <text x="${L}" y="${H - 5}" fill="#93ADA6" font-size="12">${giorno(x0)}</text>
      <text x="${W - R}" y="${H - 5}" fill="#93ADA6" font-size="12" text-anchor="end">${giorno(x1)}</text>
      <g id="cur-${esc(c.id)}" visibility="hidden">
        <line y1="${T - 4}" y2="${H - B}" stroke="#93ADA6" stroke-dasharray="3 3"/>
        <circle r="4.5" fill="#EAF2EE"/>
      </g>
    </svg>
    <div class="tip" hidden></div>`;
  const svg = box.querySelector("svg"), cur = box.querySelector("g"), tip = box.querySelector(".tip");
  const mostra = (ev) => {
    const rect = svg.getBoundingClientRect();
    const x = ((ev.clientX - rect.left) / rect.width) * W;
    let best = 0;
    for (let i = 1; i < pts.length; i++) if (Math.abs(X(pts[i][0]) - x) < Math.abs(X(pts[best][0]) - x)) best = i;
    const p = pts[best], px = X(p[0]), py = Y(p[1]);
    cur.setAttribute("visibility", "visible");
    cur.querySelector("line").setAttribute("x1", px);
    cur.querySelector("line").setAttribute("x2", px);
    cur.querySelector("circle").setAttribute("cx", px);
    cur.querySelector("circle").setAttribute("cy", py);
    tip.hidden = false;
    tip.textContent = `${eur(p[1])}, ${dataOra(p[0])}`;
    const left = (px / W) * rect.width;
    tip.style.left = Math.min(Math.max(left, 80), rect.width - 80) + "px";
  };
  svg.addEventListener("pointerdown", mostra);
  svg.addEventListener("pointermove", (e) => { if (e.pointerType === "mouse" || e.buttons) mostra(e); });
  svg.addEventListener("pointerleave", () => { cur.setAttribute("visibility", "hidden"); tip.hidden = true; });
}

/* ------------------------------------------------------------------ pannelli */

function apriPannello(html) {
  $("#layer").innerHTML = `<div class="scrim" id="scrim"></div><div class="sheet" role="dialog" aria-modal="true">${html}</div>`;
  $("#scrim").onclick = chiudiPannello;
  return $("#layer .sheet");
}
function chiudiPannello() { $("#layer").innerHTML = ""; }

function apriMenu() {
  const p = apriPannello(`
    <h2>Menu</h2>
    <div class="menu-list" style="margin-top:14px">
      <button class="btn" id="m-check">Aggiorna prezzi ora</button>
      <button class="btn" id="m-import">Importa dal foglio Google</button>
      <button class="btn danger" id="m-exit">Esci</button>
    </div>`);
  $("#m-check", p).onclick = () => { chiudiPannello(); avviaControllo(false); };
  $("#m-import", p).onclick = apriImporta;
  $("#m-exit", p).onclick = () => esci("");
}

function apriImporta() {
  const p = apriPannello(`
    <h2>Importa dal foglio Google</h2>
    <p class="sub">Incolla il link CSV pubblicato del foglio (lo stesso salvato nel secret SHEET_CSV_URL). Le carte già presenti non vengono duplicate.</p>
    <input class="input" id="imp-url" type="url" placeholder="https://docs.google.com/...output=csv">
    <div class="sheet-actions">
      <button class="btn" id="imp-annulla">Annulla</button>
      <button class="btn primary" id="imp-ok">Importa</button>
    </div>`);
  $("#imp-annulla", p).onclick = chiudiPannello;
  $("#imp-ok", p).onclick = async () => {
    const url = $("#imp-url", p).value.trim();
    if (!url) return;
    $("#imp-ok", p).disabled = true;
    try {
      const r = await api("/api/importa", { method: "POST", body: JSON.stringify({ url }) });
      S.carte = r.carte;
      chiudiPannello();
      toast(`Importate ${r.aggiunte} carte (${r.gia_presenti} erano già presenti).`);
      render();
    } catch (e) {
      $("#imp-ok", p).disabled = false;
      toast("Importazione non riuscita: " + e.message);
    }
  };
}

/* Modulo aggiungi / modifica una variante */
function apriModulo(c) {
  const nuovo = !c.id;
  const f = {
    id: c.id,
    nome_carta: c.nome_carta,
    lingua: c.lingua || "entrambe",
    foil: c.foil || "si",
    percentile_soglia: c.percentile_soglia || "",
    soglia_occasione: c.soglia_occasione || "",
    incl: new Set(), escl: new Set(),
    extraIncl: c.edizioni_incluse || "", extraEscl: c.edizioni_escluse || "",
  };
  const p = apriPannello(`
    <h2>${esc(nomeVisibile(f.nome_carta))}</h2>
    <p class="sub">${nuovo ? "Scegli quale versione seguire." : "Modifica le impostazioni di questa variante."}</p>
    <div class="field"><span class="lab">Lingua</span>
      <div class="seg" data-campo="lingua">
        ${Object.entries(LINGUE).map(([k, t]) => `<button type="button" data-v="${k}" aria-pressed="${f.lingua === k}">${t}</button>`).join("")}
      </div></div>
    <div class="field"><span class="lab">Finitura</span>
      <div class="seg" data-campo="foil">
        ${Object.entries(FOIL).map(([k, t]) => `<button type="button" data-v="${k}" aria-pressed="${f.foil === k}">${t}</button>`).join("")}
      </div>
      ${nuovo ? "" : `<div class="help">Cambiando lingua o finitura, lo storico di questa variante riparte da zero.</div>`}
    </div>
    <div class="field"><span class="lab">Edizioni</span>
      <div class="chips" id="chips"><span class="help">Carico le edizioni…</span></div>
      <div class="help">Tocca un'edizione una volta per seguire solo quella, due volte per escluderla, tre per tornare neutra.</div>
      <details ${f.extraIncl || f.extraEscl ? "open" : ""}><summary>Altre edizioni (solo CardTrader)</summary>
        <div class="two">
          <div><label for="x-in" class="help">Solo queste</label><input class="input" id="x-in" placeholder="es. the-hobbit-collectors"></div>
          <div><label for="x-out" class="help">Escludi</label><input class="input" id="x-out" placeholder="es. pspm"></div>
        </div>
        <div class="help">Codici, nomi o link delle pagine edizione di CardTrader, separati da punto e virgola.</div>
      </details>
    </div>
    <div class="field two">
      <div><label for="s-perc">Soglia prezzo basso</label><input class="input num" id="s-perc" inputmode="numeric" placeholder="${DEF_PERCENTILE}" value="${esc(f.percentile_soglia)}">
        <div class="help">Percentile: 15 = avvisa se il prezzo è nel 15% più basso dello storico.</div></div>
      <div><label for="s-occ">Soglia occasione (%)</label><input class="input num" id="s-occ" inputmode="numeric" placeholder="${DEF_OCCASIONE}" value="${esc(f.soglia_occasione)}">
        <div class="help">Avvisa se il minimo è almeno questo % sotto la media delle 20 offerte più economiche.</div></div>
    </div>
    <div class="sheet-actions">
      <button class="btn" id="f-annulla">Annulla</button>
      <button class="btn primary" id="f-salva">${nuovo ? "Aggiungi" : "Salva"}</button>
    </div>`);

  p.querySelectorAll(".seg").forEach((seg) => {
    seg.onclick = (e) => {
      const b = e.target.closest("button[data-v]");
      if (!b) return;
      f[seg.dataset.campo] = b.dataset.v;
      seg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    };
  });
  $("#x-in", p).value = f.extraIncl;
  $("#x-out", p).value = f.extraEscl;
  $("#f-annulla", p).onclick = chiudiPannello;
  $("#f-salva", p).onclick = () => salvaModulo(f, p, nuovo);
  caricaEdizioni(f, p);
}

const tokens = (s) => String(s || "").split(/[;|]/).map((t) => t.trim()).filter(Boolean);

async function caricaEdizioni(f, p) {
  const box = $("#chips", p);
  let stampe = [];
  try {
    let url = `${SCRYFALL}/cards/search?q=${encodeURIComponent(`!"${pulisci(f.nome_carta)}"`)}&unique=prints&order=released`;
    const r = await fetch(url);
    const j = await r.json();
    const visti = new Set();
    for (const s of j.data || []) {
      if (!visti.has(s.set)) { visti.add(s.set); stampe.push({ code: s.set, nome: s.set_name }); }
    }
  } catch (e) { /* senza rete restano i campi di testo */ }
  if (!document.body.contains(box)) return;
  if (!stampe.length) {
    box.innerHTML = `<span class="help">Elenco edizioni non disponibile: usa i campi qui sotto.</span>`;
    return;
  }
  // sposta nei chip i codici già presenti nei campi di testo
  const codici = new Set(stampe.map((s) => s.code));
  const restoIn = [], restoOut = [];
  for (const t of tokens($("#x-in", p).value)) codici.has(t.toLowerCase()) ? f.incl.add(t.toLowerCase()) : restoIn.push(t);
  for (const t of tokens($("#x-out", p).value)) codici.has(t.toLowerCase()) ? f.escl.add(t.toLowerCase()) : restoOut.push(t);
  $("#x-in", p).value = restoIn.join("; ");
  $("#x-out", p).value = restoOut.join("; ");
  const statoChip = (code) => (f.incl.has(code) ? "in" : f.escl.has(code) ? "out" : "");
  box.innerHTML = stampe.map((s) =>
    `<button type="button" class="chip" data-code="${esc(s.code)}" data-state="${statoChip(s.code)}" title="${esc(s.code.toUpperCase())}">${esc(s.nome)}</button>`).join("");
  box.onclick = (e) => {
    const b = e.target.closest(".chip");
    if (!b) return;
    const code = b.dataset.code;
    const ora = statoChip(code);
    f.incl.delete(code); f.escl.delete(code);
    if (ora === "") f.incl.add(code);
    else if (ora === "in") f.escl.add(code);
    b.dataset.state = statoChip(code);
  };
}

async function salvaModulo(f, p, nuovo) {
  const incl = [...f.incl, ...tokens($("#x-in", p).value)];
  const escl = [...f.escl, ...tokens($("#x-out", p).value)];
  const numero = (v) => String(v || "").replace("%", "").replace(",", ".").trim();
  const voce = {
    id: f.id || (crypto.randomUUID ? crypto.randomUUID() : String(Date.now())),
    nome_carta: f.nome_carta,
    lingua: f.lingua,
    foil: f.foil,
    percentile_soglia: numero($("#s-perc", p).value),
    soglia_occasione: numero($("#s-occ", p).value),
    edizioni_incluse: incl.join("; "),
    edizioni_escluse: escl.join("; "),
  };
  if (S.carte.some((c) => c.id !== voce.id && chiave(c) === chiave(voce))) {
    toast("Questa variante è già nella lista.");
    return;
  }
  const nuove = nuovo ? [...S.carte, voce] : S.carte.map((c) => (c.id === voce.id ? { ...c, ...voce } : c));
  $("#f-salva", p).disabled = true;
  try {
    await salvaCarte(nuove);
    chiudiPannello();
    render();
    if (nuovo) {
      toast("Carta aggiunta. Il primo prezzo arriva tra qualche minuto.");
      avviaControllo(true);
    } else {
      toast("Impostazioni salvate.");
    }
  } catch (e) {
    $("#f-salva", p).disabled = false;
    toast("Salvataggio non riuscito: " + e.message);
  }
}

async function rimuovi(id) {
  const c = S.carte.find((x) => x.id === id);
  if (!c) return;
  if (!confirm(`Rimuovere ${nomeVisibile(c.nome_carta)} (${etichetta(c)})? Il suo storico prezzi verrà cancellato al prossimo controllo.`)) return;
  try {
    await salvaCarte(S.carte.filter((x) => x.id !== id));
    toast("Variante rimossa.");
    render();
  } catch (e) {
    toast("Rimozione non riuscita: " + e.message);
  }
}

/* ------------------------------------------------------------------ avvio */

function render() {
  if (!S.pw) return renderLogin();
  const h = location.hash.replace(/^#/, "");
  if (h.startsWith("c/")) {
    renderDettaglio(decodeURIComponent(h.slice(2)));
  } else {
    if (!$("#lista-shell")) $("#app").innerHTML = "";
    renderLista();
  }
}

async function avvio() {
  try {
    await Promise.all([caricaCarte(), caricaDati()]);
  } catch (e) {
    if (e.message === "non autorizzato") return;
    toast("Dati non aggiornati: " + e.message);
  }
  S.caricato = true;
  render();
  sistemaNomi();
}

/* I nomi importati dal foglio sono spesso in minuscolo: li sostituisco una
   volta sola con il nome ufficiale di Scryfall. Lo storico non cambia,
   perché la chiave dello storico usa comunque il nome in minuscolo. */
async function sistemaNomi() {
  const daSistemare = [...new Set(S.carte.map((c) => c.nome_carta).filter((n) => n === n.toLowerCase()))];
  if (!daSistemare.length) return;
  const mappa = {};
  for (const n of daSistemare) {
    try {
      const r = await fetch(`${SCRYFALL}/cards/named?exact=${encodeURIComponent(pulisci(n))}`);
      if (r.ok) {
        const j = await r.json();
        if (j.name && j.name.toLowerCase() === pulisci(n).toLowerCase()) mappa[n] = j.name;
      }
    } catch (e) { /* riprova al prossimo avvio */ }
    await new Promise((ok) => setTimeout(ok, 120)); // rispetto dei limiti di Scryfall
  }
  if (!Object.keys(mappa).length) return;
  try {
    await salvaCarte(S.carte.map((c) => (mappa[c.nome_carta] ? { ...c, nome_carta: mappa[c.nome_carta] } : c)));
    render();
  } catch (e) {}
}

window.addEventListener("hashchange", () => { chiudiPannello(); render(); });
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState === "visible" && S.pw && S.caricato) {
    try { await caricaDati(); render(); } catch (e) {}
  }
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

if (S.pw) avvio(); else renderLogin();
