#!/usr/bin/env python3
"""
Memoria tra un controllo e l'altro (data/cache_carte.json) e dati di
mercato presi da Scryfall.

Per ogni carta seguita conserva:
  - le stampe trovate su Scryfall (edizioni, data di uscita e prezzi
    Cardmarket), riscaricate al massimo ogni 6 ore: Scryfall aggiorna i
    prezzi una volta al giorno, non serve chiederli a ogni controllo;
  - i blueprint di CardTrader (le "schede prodotto") per ogni espansione,
    riscaricati ogni 7 giorni: sono la parte più lenta della ricerca;
  - le edizioni già viste, per accorgersi delle ristampe.

Senza questa memoria ogni controllo rifaceva tutte queste ricerche per
ogni carta: con molte carte un giro poteva non finire prima del successivo.
"""

import json
import os
import time

import requests

from common import DATA_DIR, SCRYFALL_BASE, SCRYFALL_HEADERS, _norm_edizione, pulisci_nome

CACHE_FILE = os.path.join(DATA_DIR, "cache_carte.json")
ORE_SCRYFALL = 6
GIORNI_BLUEPRINT = 7
# Tipi di set che non contano come "ristampa" (pedine, oggetti da collezione,
# promo, versioni solo digitali)
TIPI_SET_IGNORATI = {"token", "memorabilia", "promo", "minigame", "vanguard"}


def _scarica_pagine(query):
    """Ritorna la lista di carte trovate (anche su più pagine), [] se Scryfall
    dice che non esistono, None se c'è un problema di rete o del servizio."""
    url = f"{SCRYFALL_BASE}/cards/search"
    params = {"q": query, "unique": "prints", "order": "released"}
    risultati = []
    for _ in range(4):  # al massimo 4 pagine (700 stampe)
        try:
            r = requests.get(url, params=params, headers=SCRYFALL_HEADERS, timeout=30)
        except requests.RequestException as e:
            print(f"   [avviso] Scryfall non raggiungibile: {e}")
            return None
        if r.status_code in (400, 404):
            return risultati
        if not r.ok:
            print(f"   [avviso] Scryfall ha risposto {r.status_code}")
            return None
        j = r.json()
        risultati.extend(j.get("data", []))
        if not j.get("has_more") or not j.get("next_page"):
            break
        url, params = j["next_page"], None
        time.sleep(0.1)  # Scryfall chiede 50-100 ms tra una richiesta e l'altra
    return risultati


def _prezzo(valore):
    try:
        return float(valore) if valore not in (None, "") else None
    except ValueError:
        return None


def scarica_stampe(nome_carta):
    """Stampe della carta su Scryfall. Ritorna None se Scryfall non risponde."""
    nome = pulisci_nome(nome_carta)
    dati = _scarica_pagine(f'!"{nome}"')
    if dati is None:
        return None
    if not dati:
        # carte doppio-fronte o split: ricerca più permissiva, poi filtro io
        dati = _scarica_pagine(nome)
        if dati is None:
            return None
        nl = nome.lower()
        dati = [c for c in dati if c.get("name", "").lower() == nl or nl in c.get("name", "").lower().split(" // ")]
    stampe = []
    for c in dati:
        prezzi = c.get("prices") or {}
        stampe.append({
            "set": c.get("set", "").lower(),
            "nome_set": c.get("set_name", ""),
            "uscita": c.get("released_at", ""),
            "tipo": c.get("set_type", ""),
            "digitale": bool(c.get("digital")),
            "eur": _prezzo(prezzi.get("eur")),
            "eur_foil": _prezzo(prezzi.get("eur_foil")),
        })
    return stampe


def _rilevante_per_ristampe(s):
    return not s["digitale"] and s["tipo"] not in TIPI_SET_IGNORATI


class CacheCarte:
    def __init__(self, percorso=CACHE_FILE):
        self.percorso = percorso
        self.dati = {"carte": {}, "meta": {}}
        if os.path.exists(percorso):
            try:
                with open(percorso, encoding="utf-8") as f:
                    self.dati = json.load(f)
            except (OSError, ValueError):
                print("[avviso] memoria delle carte illeggibile: la ricreo")
        self.dati.setdefault("carte", {})
        self.dati.setdefault("meta", {})

    @property
    def meta(self):
        return self.dati["meta"]

    def salva(self):
        os.makedirs(os.path.dirname(self.percorso), exist_ok=True)
        with open(self.percorso, "w", encoding="utf-8") as f:
            json.dump(self.dati, f, ensure_ascii=False, separators=(",", ":"))

    def _voce(self, nome_carta):
        return self.dati["carte"].setdefault(pulisci_nome(nome_carta).lower(), {})

    def stampe(self, nome_carta):
        """Ritorna (stampe, nuove_stampe). nuove_stampe contiene le edizioni
        apparse dall'ultimo aggiornamento (vuota al primo giro, per non
        segnalare come "ristampa" tutto lo storico della carta)."""
        v = self._voce(nome_carta)
        if "stampe" in v and time.time() - v.get("scryfall_agg", 0) < ORE_SCRYFALL * 3600:
            return v["stampe"], []
        stampe = scarica_stampe(nome_carta)
        time.sleep(0.1)
        if stampe is None:
            return v.get("stampe", []), []  # Scryfall non risponde: uso quelle vecchie
        rilevanti = {}
        for s in stampe:
            if _rilevante_per_ristampe(s):
                rilevanti.setdefault(s["set"], s)
        nuove = []
        if "set_noti" in v:
            noti = set(v["set_noti"])
            nuove = [s for codice, s in rilevanti.items() if codice not in noti]
        v["set_noti"] = sorted(set(v.get("set_noti", [])) | set(rilevanti))
        v["stampe"] = stampe
        v["scryfall_agg"] = time.time()
        return stampe, nuove

    def blueprint(self, ct_client, nome_carta, expansion_id):
        """Blueprint CardTrader con questo nome in questa espansione."""
        v = self._voce(nome_carta)
        if "blueprint" not in v or time.time() - v.get("bp_agg", 0) > GIORNI_BLUEPRINT * 86400:
            v["blueprint"] = {}
            v["bp_agg"] = time.time()
        chiave = str(expansion_id)
        if chiave in v["blueprint"]:
            return v["blueprint"][chiave]
        try:
            blueprints = ct_client.blueprints_per_espansione(expansion_id)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                raise  # token rifiutato: lo gestisce il controllo principale
            return []  # errore temporaneo: non lo memorizzo, riprovo al prossimo giro
        nome_lower = pulisci_nome(nome_carta).lower()
        ids = [bp["id"] for bp in blueprints if bp.get("name", "").strip().lower() == nome_lower]
        v["blueprint"][chiave] = ids
        time.sleep(0.4)  # rispetto dei limiti di CardTrader
        return ids

    def tieni_solo(self, nomi):
        """Toglie dalla memoria le carte non più seguite."""
        attivi = {pulisci_nome(n).lower() for n in nomi}
        for k in list(self.dati["carte"]):
            if k not in attivi:
                del self.dati["carte"][k]


def prezzo_cardmarket(stampe, foil_richiesto, escluse=None, incluse=None):
    """Prezzo Cardmarket (fornito da Scryfall) della versione più economica
    che rispetta finitura ed edizioni scelte. Ritorna (prezzo, nome_set) o None.
    Le edizioni che esistono solo su CardTrader non hanno un prezzo Cardmarket."""
    escluse, incluse = escluse or [], incluse or []
    campi = {"si": ["eur_foil"], "no": ["eur"]}.get(foil_richiesto, ["eur", "eur_foil"])
    migliore = None
    for s in stampe:
        if s["digitale"]:
            continue
        ids = {_norm_edizione(s["set"]), _norm_edizione(s["nome_set"])}
        if escluse and ids & set(escluse):
            continue
        if incluse and not ids & set(incluse):
            continue
        for campo in campi:
            p = s.get(campo)
            if p and (migliore is None or p < migliore[0]):
                migliore = (p, s["nome_set"])
    return migliore
