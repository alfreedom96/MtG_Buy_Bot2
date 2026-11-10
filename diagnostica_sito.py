"""Diagnostica una tantum: quali richieste all'API restituiscono dati vecchi (cache)?"""
import json, os, time, requests
H = {"Authorization": f"Bearer {os.environ['CARDTRADER_TOKEN']}"}
U = "https://api.cardtrader.com/api/v2/marketplace/products"
VARIANTI = {
    "senza_filtri": {"blueprint_id": 420697},
    "en": {"blueprint_id": 420697, "language": "en"},
    "it": {"blueprint_id": 420697, "language": "it"},
    "en_nonfoil": {"blueprint_id": 420697, "language": "en", "foil": "false"},
    "nonfoil": {"blueprint_id": 420697, "foil": "false"},
    "en_EN": {"blueprint_id": 420697, "language": "EN"},
}
def sintesi(data):
    ps = [p for l in data.values() for p in l]
    pk = {p["user"]["username"]: (p["id"], p["price_cents"], p["quantity"]) for p in ps
          if p["user"]["username"] in ("Packfresh", "Selvi54tcg", "Ventura Games", "Hometown Games")}
    return {"n": len(ps), "venditori": pk}
out = []
for giro in range(6):
    for nome, params in VARIANTI.items():
        r = requests.get(U, headers=H, params=params, timeout=30)
        out.append({"giro": giro, "ora": time.strftime("%H:%M:%S"), "variante": nome, "stato": r.status_code,
                    "runtime": r.headers.get("x-runtime"), "etag": r.headers.get("etag"),
                    **(sintesi(r.json()) if r.ok else {"testo": r.text[:200]})})
        time.sleep(0.4)
    time.sleep(55)
json.dump(out, open("data/diag_sito.json", "w"), ensure_ascii=False, indent=1)
