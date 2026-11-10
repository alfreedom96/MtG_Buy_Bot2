"""Diagnostica una tantum: dopo quanto tempo la cache dell'API CardTrader si
rinnova, e se si allunga a ogni richiesta (scadenza "scorrevole")."""
import hashlib, json, os, time, requests
H = {"Authorization": f"Bearer {os.environ['CARDTRADER_TOKEN']}"}
U = "https://api.cardtrader.com/api/v2/marketplace/products"
BP = [23962, 13700, 23490]
# (nome, parametri extra, ogni quanti minuti)
CHIAVI = [("EN_nf_1min", {"language": "EN", "foil": "false"}, 1),
          ("EN_f_5min", {"language": "EN", "foil": "true"}, 5),
          ("IT_nf_10min", {"language": "IT", "foil": "false"}, 10),
          ("IT_f_20min", {"language": "IT", "foil": "true"}, 20),
          ("senza_filtri_1min", {}, 1)]
log = []
inizio = time.time()
for minuto in range(0, 61):
    for nome, extra, ogni in CHIAVI:
        if minuto % ogni:
            continue
        for bp in BP:
            r = requests.get(U, headers=H, params={"blueprint_id": bp, **extra}, timeout=30)
            if not r.ok:
                log.append({"m": minuto, "k": nome, "bp": bp, "stato": r.status_code}); continue
            ps = sorted((p["id"], p["price_cents"], p["quantity"]) for l in r.json().values() for p in l)
            log.append({"m": minuto, "k": nome, "bp": bp, "rt": float(r.headers.get("x-runtime") or 0),
                        "n": len(ps), "h": hashlib.md5(json.dumps(ps).encode()).hexdigest()[:8]})
            time.sleep(0.3)
    json.dump(log, open("data/diag_cache.json", "w"))
    attesa = inizio + (minuto + 1) * 60 - time.time()
    if attesa > 0:
        time.sleep(attesa)
