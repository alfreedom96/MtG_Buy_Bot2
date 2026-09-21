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

CONDIZIONI_ACCETTATE = {"Mint", "Near Mint", "Slightly Played"}
MINIMO_PUNTI_PER_STATISTICA = 5
MAX_PUNTI_STORICO = 1000  # limite per non far crescere il file all'infinito
SOGLIA_FALLIMENTI_CONSECUTIVI = 10  # dopo quanti fallimenti di fila avvisare


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

def leggi_lista_carte(sheet_csv_url):
    """Scarica il Google Sheet pubblicato come CSV e restituisce le righe.

    Colonne attese (intestazioni, case-insensitive):
      - nome_carta          (obbligatoria)
      - lingua              (opzionale: it / en / entrambe - default entrambe)
      - foil                (opzionale: si / no / qualsiasi - default si)
      - percentile_soglia   (opzionale: numero 1-100 - default DEFAULT_PERCENTILE_SOGLIA)
    """
    import csv
    import io

    resp = requests.get(sheet_csv_url, timeout=30)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    righe = []
    for raw in reader:
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        nome = row.get("nome_carta") or row.get("nome") or row.get("carta")
        if not nome:
            continue
        righe.append({
            "nome_carta": nome,
            "lingua": (row.get("lingua") or "entrambe").lower(),
            "foil": (row.get("foil") or "si").lower(),
            "percentile_soglia": row.get("percentile_soglia") or "",
        })
    return righe


# ---------------------------------------------------------------------------
# Client CardTrader (con retry a backoff crescente sui rate limit)
# ---------------------------------------------------------------------------

class CardTraderClient:
    def __init__(self, token):
        self.headers = {"Authorization": f"Bearer {token}"}
        self._expansions = None

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
            self._expansions = {
                e["code"].lower(): e["id"] for e in data if e.get("game_id") == 1 and e.get("code")
            }
        return self._expansions

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


def trova_blueprint_ids(ct_client, nome_carta):
    """Trova tutti i blueprint_id di CardTrader corrispondenti a tutte le
    edizioni della carta, incrociando Scryfall (per sapere in quali set
    è stata stampata) con l'elenco espansioni/blueprint di CardTrader."""
    set_codes = stampe_scryfall(nome_carta)
    espansioni = ct_client.espansioni_magic()
    blueprint_ids = set()
    nome_lower = pulisci_nome(nome_carta).lower()

    expansion_ids_da_controllare = set()
    for code in set_codes:
        if code in espansioni:
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


def miglior_prezzo(ct_client, nome_carta, lingua_richiesta, foil_richiesto):
    """Cerca il prezzo più basso che rispetta i filtri (condizione Mint/NM,
    CardTrader Zero). Ritorna (risultato, errore):
      - risultato = (prezzo, dettagli, numero_offerte_valide) se trovato
      - errore = messaggio testuale se non trovato (che include, quando
        possibile, informazioni sul mercato anche fuori dai filtri richiesti,
        solo a scopo informativo)."""
    blueprint_ids = trova_blueprint_ids(ct_client, nome_carta)
    if not blueprint_ids:
        return None, "nessuna edizione trovata su CardTrader"

    lingue = lingue_da_controllare(lingua_richiesta)
    foils = foil_da_controllare(foil_richiesto)

    miglior = None  # (prezzo_eur, dettagli)
    numero_offerte_valide = 0
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

    return (miglior[0], miglior[1], numero_offerte_valide), None


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
# Telegram
# ---------------------------------------------------------------------------

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


def invia_telegram(bot_token, chat_id, testo):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    r = requests.post(url, data={"chat_id": chat_id, "text": testo, "parse_mode": "HTML"}, timeout=15)
    if not r.ok:
        print(f"Errore invio Telegram: {r.text}", file=sys.stderr)


def invia_telegram_foto(bot_token, chat_id, percorso_immagine, didascalia=""):
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    with open(percorso_immagine, "rb") as f:
        r = requests.post(
            url,
            data={"chat_id": chat_id, "caption": didascalia, "parse_mode": "HTML"},
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
