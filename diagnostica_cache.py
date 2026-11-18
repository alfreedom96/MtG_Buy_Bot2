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
    e = {"t": round((time.time() - inizio) / 60, 1), "giro": giro, "k": nome, "bp": bp}
    try:
        r = requests.get(U, headers=H, params={"blueprint_id": bp, "language": "en", "foil": foil}, timeout=30)
    except requests.RequestException as err:
        e["errore"] = str(err)[:120]
        log.append(e)
        return
    e.update(stato=r.status_code, rt=r.headers.get("x-runtime"), server=r.headers.get("X-Server-Name"))
    try:
        if r.ok:
            ps = sorted((p.get("id") or 0, p.get("price_cents") or 0, p.get("quantity") or 0)
                        for l in r.json().values() if isinstance(l, list) for p in l if isinstance(p, dict))
            e.update(n=len(ps), h=hashlib.md5(json.dumps(ps).encode()).hexdigest()[:8], max_id=max((p[0] for p in ps), default=0))
        else:
            e["testo"] = r.text[:120]
    except Exception as err:
        e["errore_lettura"] = f"{type(err).__name__}: {err}"[:200]
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
