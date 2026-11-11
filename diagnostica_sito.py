"""Diagnostica una tantum: quali varianti della richiesta (equivalenti come
risultato) ottengono una risposta nuova invece della copia in cache?"""
import json, os, time, requests
H = {"Authorization": f"Bearer {os.environ['CARDTRADER_TOKEN']}"}
U = "https://api.cardtrader.com/api/v2/marketplace/products"
VEND = ("Packfresh", "Selvi54tcg", "Ventura Games", "Hometown Games", "Alphastrikegames")
VARIANTI = [
    ("en", "blueprint_id=420697&language=en"),
    ("EN", "blueprint_id=420697&language=EN"),
    ("En", "blueprint_id=420697&language=En"),
    ("eN", "blueprint_id=420697&language=eN"),
    ("zero_davanti", "blueprint_id=0420697&language=en"),
    ("due_zeri", "blueprint_id=00420697&language=en"),
    ("piu", "blueprint_id=%2B420697&language=en"),
    ("ordine_inverso", "language=en&blueprint_id=420697"),
    ("doppio", "blueprint_id=420697&blueprint_id=420697&language=en"),
    ("foil_false", "blueprint_id=420697&language=en&foil=false"),
    ("foil_0", "blueprint_id=420697&language=en&foil=0"),
    ("foil_False", "blueprint_id=420697&language=en&foil=False"),
    ("zero_davanti_2", "blueprint_id=0420697&language=en"),
    ("tre_zeri_ts", "blueprint_id=000420697&language=en"),
]
out = []
for nome, q in VARIANTI:
    r = requests.get(f"{U}?{q}", headers=H, timeout=30)
    e = {"variante": nome, "stato": r.status_code, "rt": r.headers.get("x-runtime")}
    if r.ok:
        ps = [p for l in r.json().values() for p in l]
        e["n"] = len(ps)
        e["v"] = {p["user"]["username"][:8]: [p["price_cents"], p["quantity"]] for p in sorted(ps, key=lambda p: -p["price_cents"])
                  if p["user"]["username"] in VEND}
    else:
        e["testo"] = r.text[:150]
    out.append(e)
    time.sleep(0.5)
json.dump(out, open("data/diag_varianti.json", "w"), ensure_ascii=False, indent=1)
