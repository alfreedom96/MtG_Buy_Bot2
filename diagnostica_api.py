#!/usr/bin/env python3
"""
Diagnostica una tantum dell'API (interfaccia per programmi) di CardTrader.

A) Una richiesta senza filtri di lingua e finitura restituisce le stesse
   offerte (italiano e inglese, foil e non foil) delle 4 richieste filtrate?
   Oppure CardTrader taglia la risposta a un numero massimo di offerte?
B) Qual è il limite reale di richieste al secondo su /marketplace/products?
   (la documentazione dice sia 1 sia 10 al secondo). Si sale a gradini e ci
   si ferma al primo rifiuto (errore 429 "troppe richieste").

Scrive il risultato in data/diagnostica_api.json.
"""

import json
import os
import random
import threading
import time

import requests

BASE = "https://api.cardtrader.com/api/v2/marketplace/products"
TOKEN = os.environ["CARDTRADER_TOKEN"]
H = {"Authorization": f"Bearer {TOKEN}", "Cache-Control": "no-cache"}
uscita = {"inizio": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def chiedi(params):
    t0 = time.time()
    r = requests.get(BASE, headers=H, params={**params, "_": int(t0 * 1000)}, timeout=30)
    intest = {k: v for k, v in r.headers.items() if any(s in k.lower() for s in ("rate", "retry", "limit"))}
    return r, round(time.time() - t0, 2), intest


def prodotti(r):
    out = []
    for lista in r.json().values():
        out.extend(lista)
    return out


def chiave(p):
    props = p.get("properties_hash") or {}
    return str(props.get("mtg_language") or "").lower(), bool(props.get("mtg_foil"))


# ---------------- A) filtrate contro senza filtri ----------------
cache = json.load(open("data/cache_carte.json"))
bps = sorted({b for v in cache["carte"].values() for l in (v.get("blueprint") or {}).values() for b in l})
random.seed(1)
campione = random.sample(bps, min(15, len(bps)))
confronti = []
for bp in campione:
    filtrate = {}
    for lingua in ("it", "en"):
        for foil in (True, False):
            r, _, _ = chiedi({"blueprint_id": bp, "language": lingua, "foil": str(foil).lower()})
            r.raise_for_status()
            for p in prodotti(r):
                filtrate[p["id"]] = p.get("price_cents")
            time.sleep(1.1)
    libere = {}
    lingue_viste = {}
    totale_libere = []
    for _ in range(2):  # due volte: CardTrader alterna due versioni dei dati
        r, _, _ = chiedi({"blueprint_id": bp})
        r.raise_for_status()
        lista = prodotti(r)
        totale_libere.append(len(lista))
        for p in lista:
            l, f = chiave(p)
            lingue_viste[l] = lingue_viste.get(l, 0) + 1
            if l in ("it", "en"):
                libere[p["id"]] = p.get("price_cents")
        time.sleep(1.1)
    mancanti = [i for i in filtrate if i not in libere]
    in_piu = [i for i in libere if i not in filtrate]
    confronti.append({
        "blueprint": bp,
        "filtrate_it_en": len(filtrate),
        "senza_filtri_totale": totale_libere,
        "senza_filtri_it_en": len(libere),
        "lingue_senza_filtri": lingue_viste,
        "mancanti_senza_filtri": len(mancanti),
        "prezzo_min_mancanti": min((filtrate[i] for i in mancanti), default=None),
        "prezzo_min_filtrate": min(filtrate.values(), default=None),
        "prezzo_min_senza_filtri": min(libere.values(), default=None),
        "in_piu_senza_filtri": len(in_piu),
    })
    print(confronti[-1], flush=True)
uscita["confronto"] = confronti

# ---------------- B) limite di richieste al secondo ----------------
time.sleep(30)
gradini = [(2, 6), (4, 10), (7, 14), (10, 20)]  # (richieste al secondo, numero di richieste)
risultati = []
for rate, n in gradini:
    esiti = []
    blocco = threading.Lock()

    def lancia(i, bp):
        try:
            r, dur, intest = chiedi({"blueprint_id": bp})
            with blocco:
                esiti.append({"i": i, "stato": r.status_code, "durata": dur, "intestazioni": intest,
                              "testo": r.text[:150] if r.status_code != 200 else ""})
        except requests.RequestException as e:
            with blocco:
                esiti.append({"i": i, "errore": str(e)[:150]})

    t0 = time.time()
    fili = []
    for i in range(n):
        attesa = t0 + i / rate - time.time()
        if attesa > 0:
            time.sleep(attesa)
        th = threading.Thread(target=lancia, args=(i, bps[i % len(bps)]))
        th.start()
        fili.append(th)
    for th in fili:
        th.join()
    rifiutate = [e for e in esiti if e.get("stato") != 200]
    risultati.append({"al_secondo": rate, "richieste": n, "secondi": round(time.time() - t0, 2),
                      "rifiutate": len(rifiutate), "esempi_rifiuto": rifiutate[:3],
                      "intestazioni_esempio": next((e["intestazioni"] for e in esiti if e.get("intestazioni")), {})})
    print(risultati[-1], flush=True)
    if rifiutate:
        break  # mi fermo al primo gradino con rifiuti
    time.sleep(20)
uscita["limite"] = risultati
uscita["fine"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
json.dump(uscita, open("data/diagnostica_api.json", "w"), ensure_ascii=False, indent=1)
