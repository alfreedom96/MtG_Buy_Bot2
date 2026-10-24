#!/usr/bin/env python3
"""
Modulo condiviso da check_prices.py, send_digest.py e bot_commands.py.
Contiene: accesso alle API di CardTrader e Scryfall, gestione dello
storico prezzi, calcoli statistici e invio messaggi/foto Telegram.
"""

import json
import os
import re
import sys
import time

import requests

CARDTRADER_BASE = "https://api.cardtrader.com/api/v2"
SCRYFALL_BASE = "https://api.scryfall.com"
SCRYFALL_HEADERS = {
    "User-Agent": "CardTraderPriceWatch/1.0 (script personale per monitoraggio prezzi)",
    "Accept": "application/json;q=0.9,*/*;q=0.8",
}

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
HISTORY_FILE = os.path.join(DATA_DIR, "price_history.json")
TELEGRAM_OFFSET_FILE = os.path.join(DATA_DIR, "telegram_offset.json")
# Versione compatta dello storico letta dall'app (tramite il Cloudflare Worker)
APP_DATA_FILE = os.path.join(DATA_DIR, "app_data.json")

CONDIZIONI_ACCETTATE = {"Mint", "Near Mint", "Slightly Played"}
MINIMO_PUNTI_PER_STATISTICA = 5
MAX_PUNTI_STORICO = 5000  # circa 70 giorni con un controllo ogni 20 minuti
SOGLIA_FALLIMENTI_CONSECUTIVI = 10  # dopo quanti fallimenti di fila avvisare
N_OFFERTE_RIFERIMENTO = 10  # occasione: confronto con la media delle N offerte più economiche


# ---------------------------------------------------------------------------
# Configurazione / utilità generiche
# ---------------------------------------------------------------------------

def env(name, default=None, required=False):
    val = os.environ.get(name, default)
    if required and not val:
        print(f"ERRORE: variabile d'ambiente mancante: {name}", file=sys.stderr)
        sys.exit(1)
    return val


# ---------------------------------------------------------------------------
# Storico prezzi (data/price_history.json)
# ---------------------------------------------------------------------------

def carica_storico():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def salva_storico(storico):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(storico, f, ensure_ascii=False, indent=2)


def nome_da_chiave(chiave):
    """Estrae il nome carta leggibile da una chiave 'nome|lingua|foil'."""
    return chiave.split("|")[0]


# ---------------------------------------------------------------------------
# Google Sheet
# ---------------------------------------------------------------------------

def _norm_edizione(token):
    """Normalizza codice/nome/URL di un'edizione in uno slug confrontabile.
    'Marvel's Spider-Man Promos' -> 'marvels-spider-man-promos'
    '.../expansions/pspm/categories' -> 'pspm'"""
    token = token.strip().lower()
    m = re.search(r"/expansions/([^/?#]+)", token)
    if m:
        token = m.group(1)
    token = token.replace("'", "").replace("\u2019", "")
    return re.sub(r"[^a-z0-9]+", "-", token).strip("-")


def _lista_edizioni(testo):
    """'pspm; The Hobbit Collectors' -> ['pspm', 'the-hobbit-collectors'].
    Accetta codici, nomi o link alla pagina dell'edizione su CardTrader."""
    if not testo:
        return []
    return [_norm_edizione(t) for t in re.split(r"[;|]", testo) if t.strip()]


def _normalizza_riga(row):
    """Trasforma una riga (dal foglio o dall'app) nel formato usato dal
    controllo prezzi. Ritorna None se manca il nome della carta."""
    row = {str(k).strip().lower(): ("" if v is None else str(v)).strip() for k, v in row.items() if k}
    nome = row.get("nome_carta") or row.get("nome") or row.get("carta")
    if not nome:
        return None
    foil = (row.get("foil") or "si").lower()
    if foil in ("sì", "yes", "true"):
        foil = "si"
    return {
        "nome_carta": pulisci_nome(nome),
        "lingua": (row.get("lingua") or "entrambe").lower(),
        "foil": foil,
        "percentile_soglia": row.get("percentile_soglia") or "",
        "soglia_occasione": (row.get("soglia_occasione") or "").replace("%", "").replace(",", ".").strip(),
        "edizioni_escluse": _lista_edizioni(row.get("edizioni_escluse")),
        "edizioni_incluse": _lista_edizioni(row.get("edizioni_incluse")),
    }


def chiave_carta(riga):
    """Chiave dello storico: 'nome in minuscolo|lingua|foil'."""
    return f"{riga['nome_carta'].strip().lower()}|{riga['lingua']}|{riga['foil']}"


def leggi_lista_carte(sheet_csv_url):
    """Scarica il Google Sheet pubblicato come CSV e restituisce le righe.

    Colonne attese (intestazioni, case-insensitive):
      - nome_carta          (obbligatoria)
      - lingua              (opzionale: it / en / entrambe - default entrambe)
      - foil                (opzionale: si / no / qualsiasi - default si)
      - percentile_soglia   (opzionale: numero 1-100 - default DEFAULT_PERCENTILE_SOGLIA)
      - soglia_occasione    (opzionale: % di sconto del minimo rispetto alla media
                             delle 10 offerte più economiche - default DEFAULT_SOGLIA_OCCASIONE)
      - edizioni_escluse    (opzionale: codici o nomi edizione da ignorare, separati da ; )
      - edizioni_incluse    (opzionale: se compilata, considera SOLO queste edizioni)
    """
    import csv
    import io

    resp = requests.get(sheet_csv_url, timeout=30)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    return [r for r in (_normalizza_riga(raw) for raw in reader) if r]


def leggi_lista_carte_worker(worker_url, password):
    """Legge la lista carte salvata dall'app (Cloudflare Worker + KV).
    Solleva un'eccezione se il Worker non risponde: meglio un controllo
    fallito (che healthchecks segnala) di uno fatto su una lista sbagliata."""
    url = worker_url.rstrip("/") + "/api/carte"
    r = requests.get(url, headers={"Authorization": f"Bearer {password}"}, timeout=30)
    r.raise_for_status()
    carte = r.json().get("carte", [])
    return [r for r in (_normalizza_riga(c) for c in carte) if r]


def leggi_lista_carte_configurata():
    """Usa l'app se configurata (WORKER_URL + APP_PASSWORD), altrimenti il
    foglio Google (SHEET_CSV_URL). Ritorna (righe, descrizione_fonte)."""
    worker_url = os.environ.get("WORKER_URL", "").strip()
    password = os.environ.get("APP_PASSWORD", "").strip()
    if worker_url and password:
        return leggi_lista_carte_worker(worker_url, password), "app"
    sheet_url = env("SHEET_CSV_URL", required=True)
    return leggi_lista_carte(sheet_url), "foglio Google"


def migra_chiavi_minuscole(storico):
    """Porta le chiavi dello storico al formato 'nome in minuscolo|...'
    (l'app salva i nomi con le maiuscole, il foglio spesso in minuscolo:
    così lo storico resta lo stesso qualunque sia la fonte)."""
    for chiave in list(storico.keys()):
        nuova = chiave.lower()
        if nuova == chiave:
            continue
        if nuova in storico:
            # tengo la versione con più dati
            if len(storico[chiave].get("rilevazioni", [])) > len(storico[nuova].get("rilevazioni", [])):
                storico[nuova] = storico[chiave]
            del storico[chiave]
        else:
            storico[nuova] = storico.pop(chiave)
    return storico


# ---------------------------------------------------------------------------
# Client CardTrader (con retry a backoff crescente sui rate limit)
# ---------------------------------------------------------------------------

class CardTraderClient:
    def __init__(self, token):
        self.headers = {"Authorization": f"Bearer {token}"}
        self._expansions = None
        self._nomi_espansioni = {}

    def _get(self, path, params=None, tentativi=4):
        url = f"{CARDTRADER_BASE}{path}"
        attese = [2, 5, 10, 20]
        ultimo_errore = None
        for tentativo in range(tentativi):
            try:
                r = requests.get(url, headers=self.headers, params=params, timeout=30)
            except requests.RequestException as e:
                ultimo_errore = e
                time.sleep(attese[min(tentativo, len(attese) - 1)])
                continue
            if r.status_code == 429 or r.status_code >= 500:
                ultimo_errore = requests.HTTPError(f"status {r.status_code}", response=r)
                time.sleep(attese[min(tentativo, len(attese) - 1)])
                continue
            r.raise_for_status()
            return r.json()
        # esauriti i tentativi
        if ultimo_errore:
            raise ultimo_errore
        raise RuntimeError("richiesta fallita per motivo sconosciuto")

    def espansioni_magic(self):
        """Ritorna {codice_espansione_lower: expansion_id} solo per Magic (game_id=1)."""
        if self._expansions is None:
            data = self._get("/expansions")
            magic = [e for e in data if e.get("game_id") == 1 and e.get("code")]
            self._expansions = {e["code"].lower(): e["id"] for e in magic}
            self._nomi_espansioni = {
                e["code"].lower(): _norm_edizione(e.get("name") or "") for e in magic
            }
        return self._expansions

    def nome_espansione(self, code):
        self.espansioni_magic()
        return self._nomi_espansioni.get(code, "")

    def blueprints_per_espansione(self, expansion_id):
        return self._get("/blueprints/export", params={"expansion_id": expansion_id})

    def marketplace_products(self, blueprint_id, language=None, foil=None):
        params = {"blueprint_id": blueprint_id}
        if language:
            params["language"] = language
        if foil is not None:
            params["foil"] = str(foil).lower()
        data = self._get("/marketplace/products", params=params)
        # la risposta è {blueprint_id: [prodotti]}
        prodotti = []
        for lista in data.values():
            prodotti.extend(lista)
        return prodotti


# ---------------------------------------------------------------------------
# Scryfall: ricerca edizioni di una carta
# ---------------------------------------------------------------------------

def pulisci_nome(nome_carta):
    """Rimuove spazi ai bordi e normalizza spazi/apostrofi 'strani' che a
    volte finiscono nei fogli Google copiando/incollando testo (spazi non
    interrompibili, apici tipografici, ecc.), causa comune di errori 400
    nelle ricerche su Scryfall."""
    sostituzioni = {
        "\u00a0": " ",  # spazio non interrompibile
        "\u2019": "'",  # apostrofo tipografico
        "\u2018": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
    pulito = nome_carta.strip()
    for vecchio, nuovo in sostituzioni.items():
        pulito = pulito.replace(vecchio, nuovo)
    return " ".join(pulito.split())  # comprime spazi multipli


def _scryfall_search(query, nome_carta):
    url = f"{SCRYFALL_BASE}/cards/search"
    try:
        r = requests.get(url, params={"q": query, "unique": "prints"}, headers=SCRYFALL_HEADERS, timeout=30)
    except requests.RequestException as e:
        print(f"   [avviso] errore di rete verso Scryfall per '{nome_carta}': {e}")
        return None
    if r.status_code in (400, 404):
        return None
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        print(f"   [avviso] errore Scryfall per '{nome_carta}': {e}")
        return None
    return r.json().get("data", [])


def stampe_scryfall(nome_carta):
    """Ritorna la lista dei set_code (edizioni) in cui la carta è stata stampata.
    Non solleva mai eccezioni: in caso di problemi ritorna semplicemente
    una lista vuota, così una singola carta problematica non blocca le altre.

    Prova prima una ricerca esatta (operatore '!'); se non trova nulla
    (ma non per un errore), fa un secondo tentativo con una ricerca più
    permissiva, utile per carte doppio-fronte o split card il cui nome
    esatto su Scryfall può differire leggermente."""
    nome_pulito = pulisci_nome(nome_carta)

    dati = _scryfall_search(f'!"{nome_pulito}"', nome_carta)
    if dati:
        return [c["set"].lower() for c in dati]

    # fallback: ricerca meno rigida, poi filtriamo noi i risultati per nome
    dati = _scryfall_search(nome_pulito, nome_carta)
    if not dati:
        return []
    nome_lower = nome_pulito.lower()
    corrispondenti = [
        c for c in dati
        if c.get("name", "").lower() == nome_lower
        or nome_lower in c.get("name", "").lower().split(" // ")
    ]
    if corrispondenti:
        print(f"   [nota] '{nome_carta}' trovata su Scryfall con ricerca approssimata")
        return [c["set"].lower() for c in corrispondenti]
    return []


def trova_blueprint_ids(ct_client, nome_carta, escluse=None, incluse=None):
    """Trova tutti i blueprint_id di CardTrader corrispondenti a tutte le
    edizioni della carta, incrociando Scryfall (per sapere in quali set
    è stata stampata) con l'elenco espansioni/blueprint di CardTrader."""
    set_codes = stampe_scryfall(nome_carta)
    espansioni = ct_client.espansioni_magic()
    blueprint_ids = set()
    nome_lower = pulisci_nome(nome_carta).lower()

    expansion_ids_da_controllare = set()
    escluse = escluse or []
    incluse = incluse or []

    def corrisponde(code, elenco):
        return _norm_edizione(code) in elenco or ct_client.nome_espansione(code) in elenco

    # Partiamo dai set di Scryfall, ma aggiungiamo anche le edizioni indicate
    # in "edizioni_incluse" che esistono solo su CardTrader (es. collector
    # edition speciali, promo) e che Scryfall non elenca per questa carta.
    codici = set(set_codes)
    if incluse:
        for code in espansioni:
            if corrisponde(code, incluse):
                codici.add(code)

    for code in codici:
        if code not in espansioni:
            continue
        if corrisponde(code, escluse):
            continue
        if incluse and not corrisponde(code, incluse):
            continue
        expansion_ids_da_controllare.add(espansioni[code])

    for expansion_id in expansion_ids_da_controllare:
        try:
            blueprints = ct_client.blueprints_per_espansione(expansion_id)
        except requests.HTTPError:
            continue
        for bp in blueprints:
            if bp.get("name", "").strip().lower() == nome_lower:
                blueprint_ids.add(bp["id"])
        time.sleep(0.4)  # rispetto dei rate limit

    return blueprint_ids


def lingue_da_controllare(lingua_richiesta):
    if lingua_richiesta == "it":
        return ["it"]
    if lingua_richiesta == "en":
        return ["en"]
    return ["it", "en"]  # default: entrambe


def foil_da_controllare(foil_richiesto):
    if foil_richiesto in ("si", "sì", "yes", "true"):
        return [True]
    if foil_richiesto == "no":
        return [False]
    return [True, False]  # qualsiasi


def _slugify(testo):
    testo = testo.lower()
    testo = testo.replace("'", "").replace("’", "")
    testo = re.sub(r"[^a-z0-9]+", "-", testo)
    return testo.strip("-")


def link_pagina_carta(nome_carta, nome_espansione):
    """Costruisce il link diretto alla pagina della carta su CardTrader
    (es. cardtrader.com/en/cards/nome-carta-espansione). È una ricostruzione
    euristica basata sullo schema URL osservato sul sito, non garantita al
    100% per nomi con caratteri particolari."""
    if not nome_espansione:
        return None
    slug = f"{_slugify(nome_carta)}-{_slugify(nome_espansione)}"
    return f"https://www.cardtrader.com/en/cards/{slug}"


def miglior_prezzo(ct_client, nome_carta, lingua_richiesta, foil_richiesto, escluse=None, incluse=None):
    """Cerca il prezzo più basso che rispetta i filtri (condizione Mint/NM/SP,
    CardTrader Zero). Ritorna (risultato, errore):
      - risultato = (prezzo, dettagli, numero_offerte_valide) se trovato
      - errore = messaggio testuale se non trovato (che include, quando
        possibile, informazioni sul mercato anche fuori dai filtri richiesti,
        solo a scopo informativo)."""
    blueprint_ids = trova_blueprint_ids(ct_client, nome_carta, escluse, incluse)
    if not blueprint_ids:
        return None, "nessuna edizione trovata su CardTrader (o tutte escluse dai filtri edizione)"

    lingue = lingue_da_controllare(lingua_richiesta)
    foils = foil_da_controllare(foil_richiesto)

    miglior = None  # (prezzo_eur, dettagli)
    numero_offerte_valide = 0
    prezzi_validi = []
    # per il messaggio informativo se non troviamo nulla nelle condizioni richieste
    miglior_altre_condizioni = None

    for blueprint_id in blueprint_ids:
        for lingua in lingue:
            for foil in foils:
                try:
                    prodotti = ct_client.marketplace_products(blueprint_id, language=lingua, foil=foil)
                except requests.HTTPError:
                    continue
                time.sleep(1.1)  # rate limit: 1 richiesta/sec

                for p in prodotti:
                    user = p.get("user", {})
                    if not user.get("can_sell_via_hub"):
                        continue  # richiesto CardTrader Zero
                    if user.get("on_vacation"):
                        continue

                    exp = p.get("expansion") or {}
                    exp_ids = {x for x in (_norm_edizione(exp.get("code") or ""),
                                           _norm_edizione(exp.get("name_en") or "")) if x}
                    if exp_ids:
                        if escluse and exp_ids & set(escluse):
                            continue
                        if incluse and not (exp_ids & set(incluse)):
                            continue

                    prezzo_cents = p.get("price_cents")
                    valuta = p.get("price_currency")
                    if prezzo_cents is None or valuta is None:
                        # fallback: alcune risposte potrebbero non avere i
                        # campi convertiti, usiamo il prezzo nativo del venditore
                        prezzo_cents = p["price"]["cents"]
                        valuta = p["price"]["currency"]
                    if valuta != "EUR":
                        continue  # per semplicità confrontiamo solo prezzi in EUR
                    prezzo = prezzo_cents / 100.0

                    props = p.get("properties_hash", {})
                    condizione = props.get("condition")

                    if condizione not in CONDIZIONI_ACCETTATE:
                        # non valido per l'acquisto, ma lo teniamo per l'informazione
                        if miglior_altre_condizioni is None or prezzo < miglior_altre_condizioni[0]:
                            miglior_altre_condizioni = (prezzo, condizione)
                        continue

                    numero_offerte_valide += 1
                    prezzi_validi.append(prezzo)
                    if miglior is None or prezzo < miglior[0]:
                        prezzo_nativo = p.get("price", {})
                        miglior = (prezzo, {
                            "condizione": condizione,
                            "lingua": lingua,
                            "foil": foil,
                            "espansione": p.get("expansion", {}).get("name_en"),
                            "venditore": user.get("username"),
                            "product_id": p.get("id"),
                            "blueprint_id": blueprint_id,
                            "prezzo_nativo": f"{prezzo_nativo.get('cents', 0) / 100:.2f} {prezzo_nativo.get('currency', '?')}"
                            if prezzo_nativo else None,
                            "quantita": p.get("quantity"),
                        })

    if miglior is None:
        if miglior_altre_condizioni:
            prezzo_alt, condizione_alt = miglior_altre_condizioni
            return None, (
                f"nessuna offerta Mint/Near Mint/Slightly Played trovata; mercato non vuoto: "
                f"offerta più economica disponibile in condizione '{condizione_alt}' a {prezzo_alt:.2f}€ "
                f"(non considerata per l'acquisto)"
            )
        return None, "nessuna offerta trovata con i filtri richiesti (mercato vuoto)"

    miglior[1]["prezzi_offerte"] = sorted(prezzi_validi)
    return (miglior[0], miglior[1], numero_offerte_valide), None


def analizza_occasione(prezzi_offerte, soglia_pct, n_riferimento=N_OFFERTE_RIFERIMENTO, minimo_offerte=5):
    """Confronta l'offerta più economica con la media delle (fino a) 10 offerte
    più economiche (minimo incluso). Ritorna (media, sconto_pct, e_occasione)
    oppure None se le offerte sono troppo poche per un confronto sensato."""
    if len(prezzi_offerte) < minimo_offerte:
        return None
    migliori = sorted(prezzi_offerte)[:n_riferimento]
    media = sum(migliori) / len(migliori)
    if media <= 0:
        return None
    sconto = 100 * (media - migliori[0]) / media
    return media, sconto, sconto >= soglia_pct


# ---------------------------------------------------------------------------
# Statistica: percentile storico e tendenza
# ---------------------------------------------------------------------------

def calcola_percentile(prezzi_passati, prezzo_attuale):
    """Ritorna la posizione percentile di prezzo_attuale rispetto ai
    prezzi passati: un valore basso (es. 10) significa che il prezzo
    attuale è tra i più bassi mai osservati (batte il 90% delle rilevazioni)."""
    if not prezzi_passati:
        return None
    n_minori_o_uguali = sum(1 for p in prezzi_passati if p <= prezzo_attuale)
    return 100 * n_minori_o_uguali / len(prezzi_passati)


def calcola_tendenza(ultimi_prezzi):
    """Guarda le ultime rilevazioni (inclusa quella attuale, la più recente
    per ultima) e ritorna una descrizione testuale della tendenza."""
    if len(ultimi_prezzi) < 2:
        return "dati insufficienti"
    rilevanti = ultimi_prezzi[-3:]  # ultime al massimo 3 rilevazioni
    differenze = [rilevanti[i + 1] - rilevanti[i] for i in range(len(rilevanti) - 1)]
    if all(d < 0 for d in differenze):
        return "🔻 ancora in calo"
    if all(d > 0 for d in differenze):
        return "🔺 in risalita"
    return "➡️ stabile/misto"


# ---------------------------------------------------------------------------
# Dati per l'app (versione compatta dello storico)
# ---------------------------------------------------------------------------

def _serie_compatta(rilevazioni, ore_dettaglio=48, ore_bucket=6):
    """Ultime 48 ore a piena risoluzione, prima un punto (il prezzo minimo)
    ogni 6 ore. Ritorna [[epoch_secondi, prezzo], ...]."""
    from datetime import datetime, timezone
    punti = []
    for r in rilevazioni:
        try:
            t = int(datetime.strptime(r["data"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
        except (KeyError, ValueError):
            continue
        punti.append((t, r["prezzo"]))
    if not punti:
        return []
    limite = punti[-1][0] - ore_dettaglio * 3600
    vecchi = {}
    recenti = []
    for t, p in punti:
        if t >= limite:
            recenti.append([t, p])
        else:
            bucket = t // (ore_bucket * 3600)
            if bucket not in vecchi or p < vecchi[bucket][1]:
                vecchi[bucket] = [t, p]
    return [vecchi[k] for k in sorted(vecchi)] + recenti


def scrivi_dati_app(storico, righe, percentile_default, occasione_default):
    """Scrive data/app_data.json: per ogni carta della lista, statistiche,
    ultima offerta migliore e una serie storica ridotta per i grafici."""
    varianti = {}
    for riga in righe:
        chiave = chiave_carta(riga)
        record = storico.get(chiave)
        if not record:
            continue
        prezzi = [r["prezzo"] for r in record.get("rilevazioni", [])]
        soglia_p = float(riga["percentile_soglia"]) if riga["percentile_soglia"] else percentile_default
        soglia_o = float(riga["soglia_occasione"]) if riga["soglia_occasione"] else occasione_default
        info = {
            "n": len(prezzi),
            "soglia_percentile": soglia_p,
            "soglia_occasione": soglia_o,
            "fallimenti": record.get("fallimenti_consecutivi", 0),
            "ultimo_errore": record.get("ultimo_errore"),
            "dettaglio": record.get("ultimo_dettaglio"),
            "serie": _serie_compatta(record.get("rilevazioni", [])),
        }
        if prezzi:
            ultimo = prezzi[-1]
            passati = prezzi[:-1]
            info.update({
                "prezzo": ultimo,
                "min": min(prezzi),
                "max": max(prezzi),
                "media": round(sum(prezzi) / len(prezzi), 4),
                "tendenza": calcola_tendenza(prezzi[-3:]),
                "percentile": (
                    round(calcola_percentile(passati, ultimo), 1)
                    if len(passati) >= MINIMO_PUNTI_PER_STATISTICA else None
                ),
                "ultimo_controllo": record["rilevazioni"][-1]["data"],
            })
            info["prezzo_basso"] = info["percentile"] is not None and info["percentile"] <= soglia_p
            det = info["dettaglio"] or {}
            info["occasione"] = det.get("sconto") is not None and det["sconto"] >= soglia_o
        varianti[chiave] = info

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(APP_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"generato": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "varianti": varianti},
            f, ensure_ascii=False, separators=(",", ":"),
        )


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def invia_telegram(bot_token, chat_id, testo, tastiera=None):
    """tastiera: lista di righe di bottoni [[(testo, callback_data), ...], ...]"""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    dati = {"chat_id": chat_id, "text": testo, "parse_mode": "HTML"}
    if tastiera:
        dati["reply_markup"] = json.dumps(_inline_keyboard(tastiera))
    r = requests.post(url, data=dati, timeout=15)
    if not r.ok:
        print(f"Errore invio Telegram: {r.text}", file=sys.stderr)


def _inline_keyboard(tastiera):
    return {"inline_keyboard": [
        [{"text": t, "callback_data": d} for t, d in riga] for riga in tastiera
    ]}


def rispondi_callback(bot_token, callback_query_id):
    """Toglie il 'caricamento' dal bottone cliccato (innocuo se già fatto)."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
            data={"callback_query_id": callback_query_id}, timeout=10,
        )
    except requests.RequestException:
        pass


def invia_telegram_foto(bot_token, chat_id, percorso_immagine, didascalia="", tastiera=None):
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    dati = {"chat_id": chat_id, "caption": didascalia, "parse_mode": "HTML"}
    if tastiera:
        dati["reply_markup"] = json.dumps(_inline_keyboard(tastiera))
    with open(percorso_immagine, "rb") as f:
        r = requests.post(
            url,
            data=dati,
            files={"photo": f},
            timeout=30,
        )
    if not r.ok:
        print(f"Errore invio foto Telegram: {r.text}", file=sys.stderr)


def leggi_messaggi_telegram(bot_token, offset=None):
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params = {"timeout": 0}
    if offset is not None:
        params["offset"] = offset
    r = requests.get(url, params=params, timeout=20)
    if r.status_code == 409:
        # Telegram rifiuta getUpdates mentre un webhook è attivo: non è un
        # errore da segnalare, è il comportamento atteso quando il bot
        # riceve i comandi tramite il Cloudflare Worker invece che a polling.
        print("getUpdates non disponibile: probabilmente un webhook Telegram è attivo (comportamento atteso).")
        return []
    r.raise_for_status()
    return r.json().get("result", [])


def carica_offset_telegram():
    if os.path.exists(TELEGRAM_OFFSET_FILE):
        with open(TELEGRAM_OFFSET_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("offset")
    return None


def salva_offset_telegram(offset):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(TELEGRAM_OFFSET_FILE, "w", encoding="utf-8") as f:
        json.dump({"offset": offset}, f)
