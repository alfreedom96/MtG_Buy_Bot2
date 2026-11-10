"""Diagnostica una tantum: un parametro in più aggira la cache dell'API?"""
import json, os, time, requests
H = {"Authorization": f"Bearer {os.environ['CARDTRADER_TOKEN']}"}
U = "https://api.cardtrader.com/api/v2/marketplace/products"
def sintesi(data):
    ps = [p for l in data.values() for p in l]
    pk = {p["user"]["username"]: (p["id"], p["price_cents"], p["quantity"]) for p in ps
          if p["user"]["username"] in ("Packfresh", "Selvi54tcg", "Ventura Games", "Hometown Games")}
    return {"n": len(ps), "venditori": pk}
out = {"giri": []}
for giro in range(2):
    ts = int(time.time() * 1000)
    varianti = {
        "senza_filtri": {"blueprint_id": 420697},
        "underscore": {"blueprint_id": 420697, "_": ts},
        "t": {"blueprint_id": 420697, "t": ts},
        "nocache": {"blueprint_id": 420697, "nocache": ts},
        "con_espansione": {"blueprint_id": 420697, "expansion_id": 4661},
        "espansione_sola": {"expansion_id": 4661},
    }
    for nome, params in varianti.items():
        r = requests.get(U, headers=H, params=params, timeout=60)
        j = r.json() if r.ok else {}
        if nome == "espansione_sola":
            j = {k: v for k, v in j.items() if str(k) == "420697"}
        out["giri"].append({"giro": giro, "ora": time.strftime("%H:%M:%S"), "variante": nome, "stato": r.status_code,
                            "runtime": r.headers.get("x-runtime"), **(sintesi(j) if r.ok else {"testo": r.text[:200]})})
        if giro == 0 and nome == "t" and r.ok:
            ps = [p for l in j.values() for p in l]
            out["esempio_prodotto"] = ps[0] if ps else None
        time.sleep(0.5)
    time.sleep(20)
json.dump(out, open("data/diag_sito.json", "w"), ensure_ascii=False, indent=1)
