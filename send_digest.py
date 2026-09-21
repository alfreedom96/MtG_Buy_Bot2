#!/usr/bin/env python3
"""
CardTrader Price Watch - riepilogo
------------------------------------
Manda su Telegram un riepilogo di tutte le carte monitorate, con prezzo
più recente, media storica, minimo storico e numero di rilevazioni.
Non interroga CardTrader: usa solo lo storico già raccolto da
check_prices.py, quindi è veloce ed economico da eseguire spesso.
"""

from common import carica_storico, env, invia_telegram, nome_da_chiave

LIMITE_CARATTERI_TELEGRAM = 3500  # margine di sicurezza sotto il limite di 4096


def costruisci_righe_riepilogo(storico):
    righe = []
    for chiave in sorted(storico.keys()):
        record = storico[chiave]
        rilevazioni = record.get("rilevazioni", [])
        if not rilevazioni:
            continue
        prezzi = [r["prezzo"] for r in rilevazioni]
        ultimo = prezzi[-1]
        media = sum(prezzi) / len(prezzi)
        minimo = min(prezzi)
        massimo = max(prezzi)
        nome = nome_da_chiave(chiave)
        parti = chiave.split("|")
        lingua = parti[1] if len(parti) > 1 else "?"
        foil = parti[2] if len(parti) > 2 else "?"
        etichetta_variante = f"{lingua}, {'foil' if foil == 'si' else ('non foil' if foil == 'no' else foil)}"
        ultima_data = rilevazioni[-1]["data"]
        righe.append(
            f"<b>{nome}</b> ({etichetta_variante}): {ultimo:.2f}€ (media {media:.2f}€, min {minimo:.2f}€, max {massimo:.2f}€, "
            f"{len(prezzi)} rilevazioni, ultima il {ultima_data})"
        )
    return righe


def invia_a_blocchi(tg_token, tg_chat_id, titolo, righe):
    if not righe:
        invia_telegram(tg_token, tg_chat_id, f"{titolo}\n\nNessun dato ancora disponibile.")
        return

    blocco = titolo + "\n\n"
    for riga in righe:
        if len(blocco) + len(riga) + 1 > LIMITE_CARATTERI_TELEGRAM:
            invia_telegram(tg_token, tg_chat_id, blocco)
            blocco = ""
        blocco += riga + "\n"
    if blocco.strip():
        invia_telegram(tg_token, tg_chat_id, blocco)


def main():
    tg_token = env("TELEGRAM_BOT_TOKEN", required=True)
    tg_chat_id = env("TELEGRAM_CHAT_ID", required=True)

    storico = carica_storico()
    righe = costruisci_righe_riepilogo(storico)
    invia_a_blocchi(tg_token, tg_chat_id, "📊 Riepilogo prezzi carte monitorate", righe)
    print(f"Riepilogo inviato ({len(righe)} carte).")


if __name__ == "__main__":
    main()
