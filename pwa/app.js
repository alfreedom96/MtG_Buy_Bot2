"use strict";
/* Price Watch - app per seguire i prezzi delle carte Magic su CardTrader.
   Tutti i dati passano dal Cloudflare Worker (/api/...), protetto da password. */

const DEF_PERCENTILE = 15;
const DEF_OCCASIONE = 10;
const DEF_RIBASSO = 5;
const DEF_RIALZO = 15;
const SCRYFALL = "https://api.scryfall.com";

const S = {
  pw: leggiLocale("pw"),
  carte: [],
  dati: { generato: null, varianti: {} },
  filtro: "tutte",
  range: {},
  polling: null,
  ordine: leggiLocale("ordine") || "segnalazioni",
  impostazioni: { telegram: true },
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
function toast(msg, ms) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  // i messaggi lunghi (es. errori con istruzioni) restano più a lungo; un tocco li chiude
  toastTimer = setTimeout(() => (t.hidden = true), ms || Math.max(3500, msg.length * 70));
  t.onclick = () => (t.hidden = true);
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

async function caricaCarte() {
  const r = await api("/api/carte");
  S.carte = r.carte || [];
  S.impostazioni = r.impostazioni || { telegram: true };
}
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

/* Tutte le segnalazioni attive di una variante, dalla più importante */
function segnali(c) {
  const v = info(c);
  if (!v || v.prezzo == null) return [];
  const out = [];
  if (v.sotto_obiettivo) out.push({ cls: "mythic", tipo: "obiettivo", testo: `Sotto il tuo obiettivo di ${eur(v.obiettivo)}` });
  if (v.occasione) out.push({ cls: "mythic", tipo: "occasione", testo: `Occasione: ${Math.round(v.dettaglio.sconto)}% sotto la media delle offerte più economiche` });
  if (v.prezzo_basso) out.push({ cls: "gold", tipo: "basso", testo: `Prezzo basso: ${Math.round(v.ribasso)}% sotto la mediana storica, tra i più bassi mai registrati` });
  if (v.rialzo && v.rialzo.attivo) out.push({ cls: "rise", tipo: "rialzo", testo: `In rialzo: +${Math.round(v.rialzo.rialzo)}% rispetto ai 3 giorni precedenti` });
  return out;
}

function stato(c) {
  const v = info(c);
  if (!v || v.prezzo == null) {
    if (v && v.fallimenti) return { cls: "err", testo: "Nessuna offerta trovata: " + (v.ultimo_errore || "motivo sconosciuto") };
    return { cls: "wait", testo: "In attesa del primo controllo" };
  }
  if (v.fallimenti >= 3) return { cls: "err", testo: `Ultimi ${v.fallimenti} controlli falliti: ${v.ultimo_errore || ""}` };
  return segnali(c)[0] || { cls: "", testo: "" };
}

const PESO = { mythic: 0, gold: 1, rise: 2, "": 3, wait: 4, err: 5 };
const ha = (g, tipi) => g.varianti.some((c) => segnali(c).some((x) => tipi.includes(x.tipo)));

function gruppi() {
  const m = new Map();
  for (const c of S.carte) {
    const k = pulisci(c.nome_carta).toLowerCase();
    if (!m.has(k)) m.set(k, { nome: c.nome_carta, k, varianti: [] });
    m.get(k).varianti.push(c);
  }
  const lista = [...m.values()];
  for (const g of lista) {
    g.peso = Math.min(...g.varianti.map((c) => PESO[stato(c).cls]));
    const vv = g.varianti.map(info).filter(Boolean);
    g.sconto = Math.max(-1, ...vv.map((v) => (v.dettaglio && v.dettaglio.sconto != null ? v.dettaglio.sconto : -1)));
    g.var7 = Math.min(Infinity, ...vv.map((v) => (v.var7 != null ? v.var7 : Infinity)));
  }
  const ordini = {
    segnalazioni: (a, b) => a.peso - b.peso || a.k.localeCompare(b.k),
    sconto: (a, b) => b.sconto - a.sconto || a.k.localeCompare(b.k),
    calo: (a, b) => a.var7 - b.var7 || a.k.localeCompare(b.k),
    nome: (a, b) => a.k.localeCompare(b.k),
  };
  lista.sort(ordini[S.ordine] || ordini.segnalazioni);
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
          <div id="ordina-riga"></div>
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
  const filtriDef = {
    occasioni: (g) => ha(g, ["obiettivo", "occasione"]),
    bassi: (g) => ha(g, ["basso"]),
    rialzo: (g) => ha(g, ["rialzo"]),
  };
  const filtri = [["tutte", "Tutte", tutti.length], ["occasioni", "Occasioni", tutti.filter(filtriDef.occasioni).length],
    ["bassi", "Prezzi bassi", tutti.filter(filtriDef.bassi).length], ["rialzo", "In rialzo", tutti.filter(filtriDef.rialzo).length]];
  $("#filters").innerHTML = filtri.map(([k, t, n]) => `<button data-f="${k}" aria-pressed="${S.filtro === k}">${t} (${n})</button>`).join("") +
    "";
  $("#ordina-riga").innerHTML = `<label class="ordina"><span>Ordina per</span><select id="ordina">
      ${[["segnalazioni", "segnalazioni"], ["sconto", "sconto sulle offerte"], ["calo", "calo in 7 giorni"], ["nome", "nome"]]
        .map(([k, t]) => `<option value="${k}"${S.ordine === k ? " selected" : ""}>${t}</option>`).join("")}
    </select></label>`;
  $("#filters").onclick = (e) => {
    const b = e.target.closest("button[data-f]");
    if (b) { S.filtro = b.dataset.f; aggiornaLista(); }
  };
  $("#ordina").onchange = (e) => { S.ordine = e.target.value; scriviLocale("ordine", S.ordine); aggiornaLista(); };

  $("#fresh").textContent = S.dati.generato ? `Prezzi aggiornati ${tempoFa(S.dati.generato)}` : "Nessun controllo prezzi ancora disponibile";

  const lista = filtriDef[S.filtro] ? tutti.filter(filtriDef[S.filtro]) : tutti;

  const main = $("#lista");
  const banner = bannerStato() + bannerNotifiche();
  main.onclick = (e) => {
    if (e.target.closest("#b-attiva")) return attivaDaBanner();
    if (e.target.closest("#b-dopo")) { scriviLocale("banner-notifiche", "no"); return aggiornaLista(); }
    if (e.target.closest("#b-stato")) return apriStato();
    const r = e.target.closest(".card-row");
    if (r) location.hash = "c/" + encodeURIComponent(r.dataset.k);
  };
  if (!S.carte.length) {
    main.innerHTML = banner + `<div class="empty"><strong>Nessuna carta seguita</strong>Cerca una carta qui sopra per aggiungerla, oppure importa la lista dal foglio Google dal menu.</div>`;
    return;
  }
  if (!lista.length) {
    main.innerHTML = banner + `<div class="empty"><strong>Niente da segnalare</strong>Nessuna carta in questa categoria al momento.</div>`;
    return;
  }
  main.innerHTML = banner + lista.map((g) => `
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
            ${v && v.var7 != null && Math.abs(v.var7) >= 1 ? `<span class="var num" title="Variazione in 7 giorni">${v.var7 < 0 ? "▼" : "▲"}${Math.abs(Math.round(v.var7))}%</span>` : ""}
            <span class="price num">${v && v.prezzo != null ? eur(v.prezzo) : "—"}</span>
          </div>`;
        }).join("")}
      </div>
    </button>`).join("");
}

function bannerStato() {
  const m = S.dati.meta || {};
  if (m.errore_globale) {
    return `<div class="banner allarme"><p>${esc(m.errore_globale)}</p><button class="btn" id="b-stato">Stato del sistema</button></div>`;
  }
  if (S.dati.generato && Date.now() - Date.parse(S.dati.generato) > 2 * 3600000) {
    return `<div class="banner allarme"><p>Gli ultimi prezzi sono di ${esc(tempoFa(S.dati.generato))}: il controllo automatico potrebbe essersi fermato.</p><button class="btn" id="b-stato">Stato del sistema</button></div>`;
  }
  return "";
}

/* Scala verticale minima: almeno ±5% attorno al prezzo, così variazioni di
   pochi centesimi restano piatte invece di riempire tutto il grafico. */
function scalaMinima(ymin, ymax) {
  const centro = (ymin + ymax) / 2;
  const meta = Math.max((ymax - ymin) / 2, centro * 0.05, 0.01);
  return [centro - meta, centro + meta];
}

function sparkline(serie) {
  const ultimo = serie[serie.length - 1][0];
  const pts = serie.filter((p) => p[0] >= ultimo - 7 * 86400);
  if (pts.length < 2) return "";
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const [y0, y1] = scalaMinima(Math.min(...ys), Math.max(...ys));
  const W = 56, H = 20;
  const d = pts.map((p) => {
    const x = x1 === x0 ? 0 : ((p[0] - x0) / (x1 - x0)) * W;
    const y = H - 2 - ((p[1] - y0) / (y1 - y0)) * (H - 4);
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

function listaOfferte(d) {
  const offerte = d.offerte || [];
  if (!offerte.length) return "";
  return `<details class="offerte"><summary>Le ${offerte.length} offerte più economiche</summary>
    <ol>${offerte.map((o, i) => `
      <li class="${i === 0 ? "prima" : ""}${o.esclusa ? " esclusa" : ""}">
        <span class="num">${eur(o.prezzo)}</span>
        <span>${esc([o.condizione, o.lingua ? o.lingua.toUpperCase() : "", o.foil ? "foil" : "non foil", o.espansione].filter(Boolean).join(", "))}
          <small>${esc(o.venditore || "?")}${o.quantita ? `, ${o.quantita} disp.` : ""}${o.esclusa ? "<br>Esclusa dalla media perché fuori scala" : ""}</small></span>
      </li>`).join("")}
    </ol></details>`;
}

/* "8% sotto" / "3% sopra" / "in linea" */
function confronto(a, b) {
  if (!b) return "";
  const d = Math.round((100 * (a - b)) / b);
  return d === 0 ? "in linea" : `${Math.abs(d)}% ${d < 0 ? "sotto" : "sopra"}`;
}

function sezioneVariante(c) {
  const v = info(c) || {};
  const st = stato(c);
  const d = v.dettaglio || {};
  const sp = c.percentile_soglia || DEF_PERCENTILE, so = c.soglia_occasione || DEF_OCCASIONE, sr = c.soglia_ribasso || DEF_RIBASSO;
  const sri = c.soglia_rialzo !== undefined && c.soglia_rialzo !== "" ? c.soglia_rialzo : DEF_RIALZO;
  const sg = segnali(c);
  const edizioni = [
    c.edizioni_incluse ? `solo ${c.edizioni_incluse}` : "",
    c.edizioni_escluse ? `escluse ${c.edizioni_escluse}` : "",
  ].filter(Boolean).join("; ") || "tutte";
  let percentileTesto = "";
  if (v.percentile != null) {
    const r = v.ribasso != null ? Math.round(Math.abs(v.ribasso)) : null;
    const rispettoMediana = v.mediana == null ? "" :
      ` Mediana storica ${eur(v.mediana)}: il prezzo attuale è ${r === 0 ? "in linea" : `${r}% ${v.ribasso >= 0 ? "sotto" : "sopra"}`}.`;
    percentileTesto = `<p class="settings-line">Percentile ${Math.round(v.percentile)}: il prezzo attuale è più basso del ${Math.round(100 - v.percentile)}% dei prezzi registrati finora (${v.n} rilevazioni).${rispettoMediana}</p>`;
  } else if (v.n) {
    percentileTesto = `<p class="settings-line">Raccolta dati in corso (${v.n} rilevazioni): il confronto con lo storico parte da 6.</p>`;
  }
  return `
    <section class="vsec">
      <h3><span class="gem ${st.cls}"></span>${esc(etichetta(c))}<span class="sp"></span>
        ${v.ultimo_controllo ? `<small>${esc(tempoFa(v.ultimo_controllo))}</small>` : ""}</h3>
      <div class="big num">${eur(v.prezzo)}</div>
      ${(sg.length ? sg : st.testo ? [st] : []).map((x) => `<p class="status ${x.cls}">${esc(x.testo)}</p>`).join("")}
      ${v.prezzo != null ? `
        <div class="stats num">
          <div><small>Minimo</small><b>${eur(v.min)}</b></div>
          <div><small>Medio</small><b>${eur(v.media)}</b></div>
          <div><small>Massimo</small><b>${eur(v.max)}</b></div>
        </div>
        <div class="stats due num">
          <div><small>Tutti i venditori</small><b>${eur(v.minimo_assoluto)}</b>
            ${d.assoluto && d.assoluto.venditore ? `<small>${esc(d.assoluto.venditore)}, ${d.assoluto.ct_zero ? "con" : "senza"} CardTrader Zero</small>` : ""}</div>
          <div><small>Cardmarket</small><b>${v.cm ? eur(v.cm.prezzo) : "—"}</b>
            ${v.cm ? `<small>${esc(v.cm.set || "")}${v.prezzo ? `, CardTrader ${confronto(v.prezzo, v.cm.prezzo)}` : ""}</small>` : `<small>non disponibile</small>`}</div>
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
          ${d.media20 != null ? `<p class="muted">Media delle ${d.n_riferimento || Math.min(10, d.offerte_valide || 10)} offerte più economiche: ${eur(d.media20)} (minimo ${Math.round(d.sconto)}% sotto)${
            d.escluse ? `. ${d.escluse === 1 ? "Esclusa 1 offerta fuori scala" : `Escluse ${d.escluse} offerte fuori scala`}, sopra ${eur(d.limite_fuori_scala)}` : ""}</p>` : ""}
          ${listaOfferte(d)}
          ${d.link ? `<a class="btn primary block" href="${esc(d.link)}" target="_blank" rel="noopener">Apri su CardTrader</a>` : ""}
        </div>` : ""}
      <p class="settings-line">Avvisi: ${c.prezzo_obiettivo ? `obiettivo ${esc(eur(Number(c.prezzo_obiettivo)))}; ` : ""}prezzo basso se sotto il ${esc(sp)}° percentile e almeno ${esc(sr)}% sotto la mediana storica; occasione da ${esc(so)}% di sconto; rialzo ${Number(sri) > 0 ? `da +${esc(sri)}%` : "disattivato"}. Edizioni: ${esc(edizioni)}.</p>
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
  const v = info(c) || {};
  const serie = v.serie || [];
  const fonti = v.fonti || [];
  const giorni = Number(S.range[c.id] || "30");
  const fine = serie.length ? serie[serie.length - 1][0] : 0;
  const pts = giorni ? serie.filter((p) => p[0] >= fine - giorni * 86400) : serie;
  if (pts.length < 2) {
    box.innerHTML = `<p class="settings-line">Servono almeno due rilevazioni in questo periodo per disegnare il grafico.</p>`;
    return;
  }
  const x0 = pts[0][0], x1 = pts[pts.length - 1][0];

  // Cardmarket: prezzo "a gradini" (cambia al massimo una volta al giorno)
  const cmTutti = v.cm_serie || [];
  const cmPrima = cmTutti.filter((p) => p[0] <= x0).pop();
  const cm = [...(cmPrima ? [[x0, cmPrima[1]]] : []), ...cmTutti.filter((p) => p[0] > x0 && p[0] <= x1)];
  if (cm.length) cm.push([x1, cm[cm.length - 1][1]]);
  const cmAl = (t) => { const q = cmTutti.filter((p) => p[0] <= t).pop(); return q ? q[1] : null; };
  // minimo assoluto (anche venditori senza CardTrader Zero)
  const ass = pts.filter((p) => p[3] != null);

  const W = Math.max(box.clientWidth, 280), H = 200;
  const L = 4, R = 54, T = 30, B = 22;
  const valori = [...pts.map((p) => p[1]), ...ass.map((p) => p[3]), ...cm.map((p) => p[1])];
  let [y0, y1] = scalaMinima(Math.min(...valori), Math.max(...valori));
  const pad = (y1 - y0) * 0.08; y0 -= pad; y1 += pad;
  const X = (t) => L + ((t - x0) / (x1 - x0 || 1)) * (W - L - R);
  const Y = (p) => T + (1 - (p - y0) / (y1 - y0)) * (H - T - B);
  const linea = pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
  const area = `${linea}L${X(x1).toFixed(1)},${H - B}L${X(x0).toFixed(1)},${H - B}Z`;
  let lineaAss = "", prec = false;
  for (const p of pts) {
    if (p[3] == null) { prec = false; continue; }
    lineaAss += `${prec ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[3]).toFixed(1)}`;
    prec = true;
  }
  const lineaCm = cm.map((p, i) => (i ? `H${X(p[0]).toFixed(1)}V${Y(p[1]).toFixed(1)}` : `M${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`)).join("");
  const ys = pts.map((p) => p[1]);
  const iMin = ys.indexOf(Math.min(...ys));
  const ticks = [y0 + pad, (y0 + y1) / 2, y1 - pad];
  const giorno = (t) => new Date(t * 1000).toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit" });
  box.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Andamento del prezzo">
      <defs><linearGradient id="g-${esc(c.id)}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#B7C4C9" stop-opacity=".22"/><stop offset="1" stop-color="#B7C4C9" stop-opacity="0"/>
      </linearGradient></defs>
      ${ticks.map((t) => `<line x1="${L}" x2="${W - R}" y1="${Y(t)}" y2="${Y(t)}" stroke="#1D4740"/>
        <text x="${W - R + 6}" y="${Y(t) + 4}" fill="#93ADA6" font-size="12" class="num">${eur(t)}</text>`).join("")}
      <path d="${area}" fill="url(#g-${esc(c.id)})"/>
      ${lineaCm ? `<path d="${lineaCm}" fill="none" stroke="#7DB8E8" stroke-width="1.6"/>` : ""}
      ${lineaAss ? `<path d="${lineaAss}" fill="none" stroke="#93ADA6" stroke-width="1.4" stroke-dasharray="4 3"/>` : ""}
      <path d="${linea}" fill="none" stroke="#EAF2EE" stroke-width="1.8" stroke-linejoin="round"/>
      <circle cx="${X(pts[iMin][0])}" cy="${Y(pts[iMin][1])}" r="4" fill="#E3B94F"/>
      <text x="${L}" y="${H - 5}" fill="#93ADA6" font-size="12">${giorno(x0)}</text>
      <text x="${W - R}" y="${H - 5}" fill="#93ADA6" font-size="12" text-anchor="end">${giorno(x1)}</text>
      <g visibility="hidden">
        <line y1="${T - 4}" y2="${H - B}" stroke="#93ADA6" stroke-dasharray="3 3"/>
        <circle r="4.5" fill="#EAF2EE"/>
      </g>
    </svg>
    <div class="tip" hidden></div>
    <div class="legenda">
      <span><i class="l-zero"></i>CardTrader Zero</span>
      ${lineaAss ? `<span><i class="l-ass"></i>Tutti i venditori</span>` : ""}
      ${lineaCm ? `<span><i class="l-cm"></i>Cardmarket</span>` : ""}
    </div>`;
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
    const fonte = p[2] != null && fonti[p[2]] ? fonti[p[2]] : null;
    const cmv = cmAl(p[0]);
    const righe = [
      fonte ? esc([fonte[0], fonte[1]].filter(Boolean).join(", ")) : "",
      p[3] != null && p[3] < p[1] - 0.004 ? `Tutti i venditori: ${esc(eur(p[3]))}` : "",
      cmv != null ? `Cardmarket: ${esc(eur(cmv))}` : "",
    ].filter(Boolean);
    tip.hidden = false;
    tip.innerHTML = `${esc(eur(p[1]))}, ${esc(dataOra(p[0]))}` + righe.map((r) => `<small>${r}</small>`).join("");
    const left = (px / W) * rect.width;
    tip.style.left = Math.min(Math.max(left, 95), rect.width - 95) + "px";
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
      <button class="btn" id="m-notifiche">Notifiche</button>
      <button class="btn" id="m-stato">Stato del sistema</button>
      <button class="btn" id="m-import">Importa dal foglio Google</button>
      <button class="btn danger" id="m-exit">Esci</button>
    </div>`);
  $("#m-check", p).onclick = () => { chiudiPannello(); avviaControllo(false); };
  $("#m-import", p).onclick = apriImporta;
  $("#m-exit", p).onclick = () => esci("");
  $("#m-notifiche", p).onclick = apriNotifiche;
  $("#m-stato", p).onclick = apriStato;
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
    soglia_ribasso: c.soglia_ribasso || "",
    soglia_rialzo: c.soglia_rialzo ?? "",
    prezzo_obiettivo: c.prezzo_obiettivo || "",
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
      <div><label for="s-obi">Prezzo obiettivo (€)</label>
        <input class="input num" id="s-obi" inputmode="decimal" placeholder="nessuno" value="${esc(String(f.prezzo_obiettivo).replace(".", ","))}">
        <div class="help">Avvisa quando il prezzo scende fino a questo valore.</div></div>
      <div><label for="s-ria">Avviso rialzo (%)</label>
        <input class="input num" id="s-ria" inputmode="numeric" placeholder="${DEF_RIALZO}" value="${esc(f.soglia_rialzo)}">
        <div class="help">Avvisa se sale di questo % rispetto ai 3 giorni prima. 0 = mai.</div></div>
    </div>
    <div class="field"><span class="lab">Avviso prezzo basso</span>
      <div class="two">
        <div><label for="s-perc" class="help">Percentile massimo</label><input class="input num" id="s-perc" inputmode="numeric" placeholder="${DEF_PERCENTILE}" value="${esc(f.percentile_soglia)}"></div>
        <div><label for="s-rib" class="help">Ribasso minimo (%)</label><input class="input num" id="s-rib" inputmode="numeric" placeholder="${DEF_RIBASSO}" value="${esc(f.soglia_ribasso)}"></div>
      </div>
      <div class="help">Scatta se il prezzo è tra i più bassi mai visti (15 = nel 15% più basso dello storico) <b>e</b> almeno questo % sotto la mediana, cioè il valore centrale dei prezzi registrati.</div>
    </div>
    <div class="field"><label for="s-occ">Avviso occasione: sconto minimo (%)</label>
      <input class="input num" id="s-occ" inputmode="numeric" placeholder="${DEF_OCCASIONE}" value="${esc(f.soglia_occasione)}">
      <div class="help">Scatta se l'offerta più economica è almeno questo % sotto la media delle 10 offerte più economiche.</div>
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
    soglia_ribasso: numero($("#s-rib", p).value),
    soglia_rialzo: numero($("#s-ria", p).value),
    prezzo_obiettivo: numero($("#s-obi", p).value.replace("€", "")),
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

/* ------------------------------------------------------------------ stato del sistema */

/* GitHub scrive la scadenza come "2026-12-31 23:59:59 UTC" o "... +0100" */
function dataGitHub(s) {
  if (!s) return null;
  const m = String(s).replace(" UTC", " +0000").match(/^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d) ([+-]\d\d)(\d\d)$/);
  const t = m ? Date.parse(`${m[1]}T${m[2]}${m[3]}:${m[4]}`) : Date.parse(s);
  return Number.isNaN(t) ? null : t;
}

function rigaToken(nome, info, scadenza) {
  if (info && !info.ok && info.codice !== 404) {
    return `<li class="warn"><b>${esc(nome)}</b>: GitHub lo rifiuta (errore ${esc(info.codice)}). Controlla il token e i suoi permessi.</li>`;
  }
  if (!scadenza) return `<li><b>${esc(nome)}</b>: valido${info && info.codice === 404 ? " (file dei dati non ancora creato)" : ""}, nessuna scadenza indicata.</li>`;
  const giorni = Math.floor((scadenza - Date.now()) / 86400000);
  const quando = new Date(scadenza).toLocaleDateString("it-IT");
  if (giorni < 0) return `<li class="warn"><b>${esc(nome)}</b>: scaduto il ${quando}. Va rigenerato.</li>`;
  return `<li class="${giorni < 14 ? "warn" : ""}"><b>${esc(nome)}</b>: scade il ${quando} (tra ${giorni} ${giorni === 1 ? "giorno" : "giorni"}).</li>`;
}

async function apriStato() {
  const p = apriPannello(`<h2>Stato del sistema</h2><div id="st-corpo"><p class="settings-line">Controllo…</p></div>
    <div class="sheet-actions"><button class="btn" id="st-chiudi">Chiudi</button></div>`);
  $("#st-chiudi", p).onclick = chiudiPannello;
  let st = {};
  try { st = await api("/api/stato"); } catch (e) { st = { errore: e.message }; }
  if (!document.body.contains(p)) return;
  const m = S.dati.meta || {};
  const vecchi = S.dati.generato && Date.now() - Date.parse(S.dati.generato) > 2 * 3600000;
  const durata = m.durata_s != null ? `, durata ${Math.floor(m.durata_s / 60)} min ${m.durata_s % 60} s` : "";
  const ESITI = { success: "riuscito", failure: "fallito", cancelled: "annullato", timed_out: "scaduto", skipped: "saltato" };
  const esecuzioni = (st.esecuzioni || []).map((x) => {
    const esito = x.stato !== "completed" ? (x.stato === "queued" ? "in coda" : "in corso") : ESITI[x.esito] || x.esito;
    const cls = x.esito === "failure" ? "warn" : "";
    return `<li class="${cls}">${x.esito === "success" ? "✓" : x.stato !== "completed" ? "…" : "✗"} ${esc(tempoFa(x.inizio))}: ${esc(esito)}</li>`;
  }).join("");
  const problemi = S.carte.map((c) => ({ c, v: info(c) })).filter(({ v }) => v && v.fallimenti)
    .map(({ c, v }) => `<li class="warn"><b>${esc(nomeVisibile(c.nome_carta))}</b> (${esc(etichetta(c))}): ${esc(v.ultimo_errore || "errore")}, ${v.fallimenti} controlli di fila</li>`).join("");
  const tk = st.token || {};
  $("#st-corpo", p).innerHTML = `
    ${m.errore_globale ? `<div class="banner allarme"><p>${esc(m.errore_globale)}</p></div>` : ""}
    <div class="field"><span class="lab">Controllo prezzi</span>
      <ul class="elenco">
        <li class="${vecchi ? "warn" : ""}">Ultimo aggiornamento dei dati: ${S.dati.generato ? esc(tempoFa(S.dati.generato)) : "mai"}${m.n_carte != null ? ` (${m.n_carte} varianti${durata})` : ""}.
          ${vecchi ? "Più vecchio del previsto: controlla cron-job.org e la scheda Actions su GitHub." : ""}</li>
      </ul></div>
    <div class="field"><span class="lab">Ultime esecuzioni</span>
      ${st.errore ? `<p class="settings-line">Non disponibili: ${esc(st.errore)}</p>` : esecuzioni ? `<ul class="elenco">${esecuzioni}</ul>` : `<p class="settings-line">Nessuna esecuzione trovata.</p>`}
    </div>
    <div class="field"><span class="lab">Carte con problemi</span>
      ${problemi ? `<ul class="elenco">${problemi}</ul>` : `<p class="settings-line">Nessuna.</p>`}
    </div>
    <div class="field"><span class="lab">Token</span>
      <ul class="elenco">
        ${m.cardtrader_scadenza !== undefined ? rigaToken("CardTrader", m.errore_globale ? { ok: false, codice: 403 } : { ok: true }, m.cardtrader_scadenza ? Date.parse(m.cardtrader_scadenza) : null) : ""}
        ${tk.dati ? rigaToken(`GitHub, lettura dati (${tk.dati.nome})`, tk.dati, dataGitHub(tk.dati.scadenza)) : ""}
        ${tk.azioni ? rigaToken("GitHub, avvio controlli (GITHUB_TOKEN)", tk.azioni, dataGitHub(tk.azioni.scadenza)) : ""}
      </ul></div>
    <div class="field"><span class="lab">Notifiche</span>
      <p class="settings-line">${st.telefoni != null ? `${st.telefoni} ${st.telefoni === 1 ? "telefono registrato" : "telefoni registrati"}.` : "—"}</p>
    </div>`;
}

/* ------------------------------------------------------------------ notifiche */

const pushSupportato = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

function daB64url(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
}
function aB64url(buf) {
  return btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function iscrizioneAttuale() {
  if (!pushSupportato()) return null;
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

function nomeTelefono() {
  const ua = navigator.userAgent;
  const m = ua.match(/Android [\d.]+; ([^;)]+)/);
  return m ? m[1].trim() : (navigator.platform || "dispositivo");
}

async function registraIscrizione(sub) {
  await api("/api/push/iscrivi", { method: "POST", body: JSON.stringify({ iscrizione: sub.toJSON(), nome: nomeTelefono() }) });
  scriviLocale("push-endpoint", sub.endpoint);
  scriviLocale("push-sync", String(Date.now()));
}

async function attivaNotifiche() {
  if (!pushSupportato()) throw new Error("questo browser non supporta le notifiche");
  const permesso = await Notification.requestPermission();
  if (permesso !== "granted") throw new Error(permesso === "denied" ? "le notifiche sono bloccate nelle impostazioni del telefono" : "permesso non concesso");
  const { chiave } = await api("/api/push/chiave");
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  // se la chiave del Worker è cambiata, l'iscrizione vecchia non funziona più
  if (sub && sub.options && sub.options.applicationServerKey && aB64url(sub.options.applicationServerKey) !== chiave) {
    await sub.unsubscribe();
    sub = null;
  }
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: daB64url(chiave) });
  await registraIscrizione(sub);
}

async function disattivaNotifiche() {
  const sub = await iscrizioneAttuale();
  if (!sub) return;
  await api("/api/push/rimuovi", { method: "POST", body: JSON.stringify({ endpoint: sub.endpoint }) });
  await sub.unsubscribe();
  scriviLocale("push-endpoint", "");
}

/* All'apertura: se il telefono ha cambiato iscrizione (succede di rado),
   la comunico al Worker; altrimenti la riconfermo una volta al giorno. */
async function sincronizzaIscrizione() {
  try {
    if (!pushSupportato() || Notification.permission !== "granted") return;
    const sub = await iscrizioneAttuale();
    if (!sub) return;
    const cambiata = sub.endpoint !== leggiLocale("push-endpoint");
    const vecchia = Date.now() - Number(leggiLocale("push-sync") || 0) > 86400000;
    if (cambiata || vecchia) await registraIscrizione(sub);
  } catch (e) { /* riprovo alla prossima apertura */ }
}

function bannerNotifiche() {
  if (!pushSupportato() || Notification.permission !== "default" || leggiLocale("banner-notifiche") === "no") return "";
  return `<div class="banner">
    <p>Attiva le notifiche per sapere subito quando una carta è in occasione, anche ad app chiusa.</p>
    <div class="row-btns"><button class="btn primary" id="b-attiva">Attiva notifiche</button><button class="btn" id="b-dopo">Non ora</button></div>
  </div>`;
}

async function attivaDaBanner() {
  try {
    await attivaNotifiche();
    toast("Notifiche attive su questo telefono.");
  } catch (e) {
    toast("Notifiche non attivate: " + e.message);
  }
  aggiornaLista();
}

async function apriNotifiche() {
  const p = apriPannello(`
    <h2>Notifiche</h2>
    <p class="sub">Un avviso quando una carta è in occasione, tra i prezzi più bassi mai visti, o quando il controllo di una carta continua a fallire.</p>
    <div class="field"><span class="lab">Su questo telefono</span>
      <p id="n-stato" class="settings-line">Controllo…</p>
      <div class="row-btns">
        <button class="btn primary" id="n-toggle" hidden></button>
        <button class="btn" id="n-prova">Invia una prova</button>
      </div>
    </div>
    <div class="field"><span class="lab">Avvisi anche su Telegram</span>
      <div class="seg" id="n-tg">
        <button type="button" data-v="si" aria-pressed="${S.impostazioni.telegram !== false}">Sì</button>
        <button type="button" data-v="no" aria-pressed="${S.impostazioni.telegram === false}">No</button>
      </div>
      <div class="help">Con "No" gli avvisi arrivano solo qui. I comandi del bot Telegram e il riepilogo settimanale restano attivi comunque.</div>
    </div>
    <div class="sheet-actions"><button class="btn" id="n-chiudi">Chiudi</button></div>`);
  $("#n-chiudi", p).onclick = chiudiPannello;

  const aggiornaStato = async () => {
    const stato = $("#n-stato", p), bottone = $("#n-toggle", p);
    if (!pushSupportato()) {
      stato.textContent = "Questo browser non supporta le notifiche. Apri l'app installata da Chrome.";
      bottone.hidden = true;
      return;
    }
    if (Notification.permission === "denied") {
      stato.textContent = "Bloccate. Per sbloccarle: Impostazioni di Android → App → Price Watch (o Chrome) → Notifiche → consenti, poi torna qui.";
      bottone.hidden = true;
      return;
    }
    const sub = await iscrizioneAttuale();
    const attive = Notification.permission === "granted" && !!sub;
    stato.textContent = attive ? "Attive." : "Non attive.";
    bottone.textContent = attive ? "Disattiva" : "Attiva";
    bottone.className = attive ? "btn danger" : "btn primary";
    bottone.hidden = false;
    bottone.onclick = async () => {
      bottone.disabled = true;
      try {
        if (attive) { await disattivaNotifiche(); toast("Notifiche disattivate su questo telefono."); }
        else { await attivaNotifiche(); toast("Notifiche attive su questo telefono."); }
      } catch (e) {
        toast("Operazione non riuscita: " + e.message);
      }
      bottone.disabled = false;
      aggiornaStato();
    };
  };
  aggiornaStato();

  $("#n-prova", p).onclick = async () => {
    try {
      const r = await api("/api/push/prova", { method: "POST" });
      if (!r.telefoni) toast("Nessun telefono registrato: premi prima Attiva.");
      else if (r.inviate) toast(`Notifica di prova inviata a ${r.inviate} ${r.inviate === 1 ? "telefono" : "telefoni"}.`);
      else toast("Invio non riuscito: " + (r.errori || []).join(", "));
    } catch (e) {
      toast("Invio non riuscito: " + e.message);
    }
  };

  $("#n-tg", p).onclick = async (e) => {
    const b = e.target.closest("button[data-v]");
    if (!b) return;
    const telegram = b.dataset.v === "si";
    try {
      S.impostazioni = await api("/api/impostazioni", { method: "PUT", body: JSON.stringify({ telegram }) });
      $("#n-tg", p).querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
      toast(telegram ? "Gli avvisi arriveranno anche su Telegram." : "Gli avvisi arriveranno solo nell'app.");
    } catch (err) {
      toast("Impostazione non salvata: " + err.message);
    }
  };
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
  sincronizzaIscrizione();
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
  // il service worker chiede di aprire una scheda carta (tocco su una notifica)
  navigator.serviceWorker.addEventListener("message", (e) => {
    if (e.data && typeof e.data.vai === "string") location.hash = e.data.vai.replace(/^\/?#?/, "");
  });
}

if (S.pw) avvio(); else renderLogin();
