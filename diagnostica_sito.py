"""Diagnostica una tantum: confronta ciò che mostra il sito CardTrader con
ciò che restituisce l'API per Vault of Champions (Commander: Star Trek)."""
import gzip, json, os, time, requests
H = {"Authorization": f"Bearer {os.environ['CARDTRADER_TOKEN']}"}
out = {"ora": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
r = requests.get("https://api.cardtrader.com/api/v2/marketplace/products", headers=H,
                 params={"blueprint_id": 420697}, timeout=30)
out["api_stato"] = r.status_code
out["api"] = r.json()
for url in ["https://www.cardtrader.com/en/cards/vault-of-champions-commander-star-trek",
            "https://www.cardtrader.com/it/cards/vault-of-champions-commander-star-trek"]:
    try:
        s = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
                                       "Accept-Language": "en"}, timeout=30)
        out.setdefault("sito", []).append({"url": url, "stato": s.status_code, "finale": s.url, "lunghezza": len(s.text)})
        open("data/diag_sito_%s.html.gz" % url.split("/")[3], "wb").write(gzip.compress(s.content))
    except requests.RequestException as e:
        out.setdefault("sito", []).append({"url": url, "errore": str(e)})
json.dump(out, open("data/diag_sito.json", "w"), ensure_ascii=False)
