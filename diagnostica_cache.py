"""Diagnostica una tantum (3 ore): come si rinnova la cache dell'API CardTrader.
Kf (lingua en, non foil) chiesta ogni 5 minuti; Kt (lingua en, foil) solo ogni ora.
Se Kf cambia a intervalli regolari dalla prima richiesta, la cache ha una durata
fissa; se Kt cambia e Kf no, la cache si allunga a ogni richiesta."""
import hashlib, json, os, time, requests
H = {"Authorization": f"Bearer {os.environ['CARDTRADER_TOKEN']}"}
U = "https://api.cardtrader.com/api/v2/marketplace/products"
BP = [400829, 286794, 288823, 405522, 2470, 1611, 340841, 411471, 411342, 420155,
      420242, 420661, 149762, 332776, 405311, 420159, 381556, 405470, 405431, 413641]
log, inizio = [], time.time()
def chiedi(nome, bp, foil, giro):
    r = requests.get(U, headers=H, params={"blueprint_id": bp, "language": "en", "foil": foil}, timeout=30)
    e = {"t": round((time.time() - inizio) / 60, 1), "giro": giro, "k": nome, "bp": bp, "stato": r.status_code,
         "rt": r.headers.get("x-runtime"), "etag": r.headers.get("etag")}
    if r.ok:
        ps = sorted((p["id"], p["price_cents"], p["quantity"]) for l in r.json().values() for p in l)
        e.update(n=len(ps), h=hashlib.md5(json.dumps(ps).encode()).hexdigest()[:8], max_id=max((p[0] for p in ps), default=0))
    if giro == 0 and bp == BP[0]:
        e["intestazioni"] = dict(r.headers)
    log.append(e)
    time.sleep(0.3)
for giro in range(37):  # 0..180 minuti
    for bp in BP:
        chiedi("Kf", bp, "false", giro)
        if giro % 12 == 0:
            chiedi("Kt", bp, "true", giro)
    json.dump(log, open("data/diag_cache.json", "w"))
    attesa = inizio + (giro + 1) * 300 - time.time()
    if attesa > 0 and giro < 36:
        time.sleep(attesa)
