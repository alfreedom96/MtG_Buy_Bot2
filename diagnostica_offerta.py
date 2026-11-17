"""Diagnostica una tantum: dati completi di un'offerta che non compare sul sito."""
import json, os, time, requests
H = {"Authorization": f"Bearer {os.environ['CARDTRADER_TOKEN']}"}
U = "https://api.cardtrader.com/api/v2/marketplace/products"
out = {"ora": time.strftime("%H:%M:%S")}
for nome, params in [("en", {"blueprint_id": 405350, "language": "en"}),
                     ("nonfoil", {"blueprint_id": 405350, "foil": "false"}),
                     ("senza_filtri", {"blueprint_id": 405350})]:
    r = requests.get(U, headers=H, params=params, timeout=30)
    ps = [p for l in r.json().values() for p in l]
    out[nome] = {"n": len(ps),
                 "end_games": [p for p in ps if p["user"]["username"] == "The End Games"],
                 "confronto": [p for p in ps if p["user"]["username"] == "Kryptonite Kollectibles"][:1]}
    time.sleep(1)
json.dump(out, open("data/diag_offerta.json", "w"), ensure_ascii=False, indent=1)
