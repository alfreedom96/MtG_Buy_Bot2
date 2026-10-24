#!/usr/bin/env python3
"""Genera cloudflare_worker.js unendo worker/worker_src.js e i file dell'app
in pwa/. Il file generato è l'unico da incollare nell'editor di Cloudflare.

Uso:  python build_worker.py
"""
import base64
import json
import os

QUI = os.path.dirname(os.path.abspath(__file__))
PWA = os.path.join(QUI, "pwa")

TIPI = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".png": "image/png",
}


def main():
    assets = {}
    for nome in sorted(os.listdir(PWA)):
        est = os.path.splitext(nome)[1]
        if est not in TIPI:
            continue
        with open(os.path.join(PWA, nome), "rb") as f:
            dati = f.read()
        if est == ".png":
            assets["/" + nome] = {"type": TIPI[est], "b64": True, "body": base64.b64encode(dati).decode()}
        else:
            assets["/" + nome] = {"type": TIPI[est], "body": dati.decode("utf-8")}

    with open(os.path.join(QUI, "worker", "worker_src.js"), encoding="utf-8") as f:
        sorgente = f.read()
    # ensure_ascii evita caratteri che in JavaScript non sono ammessi nelle stringhe
    risultato = sorgente.replace("__ASSETS__", json.dumps(assets, ensure_ascii=True, indent=1), 1)
    with open(os.path.join(QUI, "cloudflare_worker.js"), "w", encoding="utf-8") as f:
        f.write(risultato)
    print(f"cloudflare_worker.js generato ({len(risultato) // 1024} KB, {len(assets)} file dell'app)")


if __name__ == "__main__":
    main()
