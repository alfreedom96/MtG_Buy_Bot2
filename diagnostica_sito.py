"""Diagnostica una tantum: le chiavi non più richieste da un po' si rinnovano?"""
import hashlib, json, os, time, requests
H = {"Authorization": f"Bearer {os.environ['CARDTRADER_TOKEN']}"}
U = "https://api.cardtrader.com/api/v2/marketplace/products"
CHIAVI = [("EN_nf_1min", {"language": "EN", "foil": "false"}), ("EN_f_5min", {"language": "EN", "foil": "true"}),
          ("IT_nf_10min", {"language": "IT", "foil": "false"}), ("IT_f_20min", {"language": "IT", "foil": "true"}),
          ("senza_filtri_1min", {})]
out = {"ora": time.strftime("%H:%M:%S"), "righe": []}
for nome, extra in CHIAVI:
    for bp in (23962, 13700, 23490):
        r = requests.get(U, headers=H, params={"blueprint_id": bp, **extra}, timeout=30)
        ps = sorted((p["id"], p["price_cents"], p["quantity"]) for l in r.json().values() for p in l)
        out["righe"].append({"k": nome, "bp": bp, "rt": float(r.headers.get("x-runtime") or 0), "n": len(ps),
                             "h": hashlib.md5(json.dumps(ps).encode()).hexdigest()[:8], "max_id": max(p[0] for p in ps) if ps else None})
        time.sleep(0.4)
for nome, extra in [("vault_en", {"language": "en"}), ("vault_en_nf", {"language": "en", "foil": "false"}),
                    ("vault_en_f", {"language": "en", "foil": "true"})]:
    r = requests.get(U, headers=H, params={"blueprint_id": 420697, **extra}, timeout=30)
    ps = [p for l in r.json().values() for p in l]
    out["righe"].append({"k": nome, "rt": r.headers.get("x-runtime"), "n": len(ps),
                         "packfresh": [p["price_cents"] for p in ps if p["user"]["username"] == "Packfresh"],
                         "max_id": max((p["id"] for p in ps), default=None)})
    time.sleep(0.4)
json.dump(out, open("data/diag_inattive.json", "w"), indent=1)
