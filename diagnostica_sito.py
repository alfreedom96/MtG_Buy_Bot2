"""Diagnostica una tantum: l'API restituisce dati diversi chiedendo per
espansione invece che per scheda? E ripetendo la richiesta nel tempo?"""
import json, os, time, requests
H = {"Authorization": f"Bearer {os.environ['CARDTRADER_TOKEN']}"}
U = "https://api.cardtrader.com/api/v2/marketplace/products"
VENDITORI = ("Packfresh", "Selvi54tcg", "Hometown Games", "Ventura Games", "Topdeck Hero Boucherville",
             "Dobi-trading", "Magic Stronghold", "Alphastrikegames")
def estrai(data):
    out = []
    for bp, lista in data.items():
        for p in lista:
            if str(p.get("blueprint_id")) == "420697" or str(bp) == "420697":
                u = p["user"]["username"]
                if u in VENDITORI:
                    out.append([u, p["id"], p["price_cents"], p["quantity"], (p.get("properties_hash") or {}).get("mtg_language")])
    return sorted(out)
out = {}
for i in range(3):
    r = requests.get(U, headers=H, params={"blueprint_id": 420697}, timeout=30)
    out[f"scheda_{i}"] = {"ora": time.strftime("%H:%M:%S"), "stato": r.status_code, "offerte": estrai(r.json()),
                          "intestazioni": dict(r.headers)}
    time.sleep(2)
r = requests.get(U, headers=H, params={"expansion_id": 4661}, timeout=60)
out["espansione"] = {"ora": time.strftime("%H:%M:%S"), "stato": r.status_code,
                     "offerte": estrai(r.json()) if r.ok else r.text[:300], "intestazioni": dict(r.headers)}
r = requests.get(U, headers=H, params={"blueprint_id": 420697, "language": "en"}, timeout=30)
out["scheda_en"] = {"stato": r.status_code, "offerte": estrai(r.json())}
json.dump(out, open("data/diag_sito.json", "w"), ensure_ascii=False, indent=1)
