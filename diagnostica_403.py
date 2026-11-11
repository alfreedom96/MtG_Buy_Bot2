"""Diagnostica una tantum: perché CardTrader risponde 403?"""
import json, os, requests
H = {"Authorization": f"Bearer {os.environ['CARDTRADER_TOKEN']}"}
out = {"ip": requests.get("https://api.ipify.org", timeout=10).text}
for nome, url in [("info", "https://api.cardtrader.com/api/v2/info"),
                  ("expansions", "https://api.cardtrader.com/api/v2/expansions"),
                  ("mercato", "https://api.cardtrader.com/api/v2/marketplace/products?blueprint_id=23962&language=en"),
                  ("senza_token", "https://api.cardtrader.com/api/v2/info")]:
    r = requests.get(url, headers={} if nome == "senza_token" else H, timeout=30)
    out[nome] = {"stato": r.status_code, "intestazioni": dict(r.headers), "testo": r.text[:600]}
json.dump(out, open("data/diag_403.json", "w"), ensure_ascii=False, indent=1)
