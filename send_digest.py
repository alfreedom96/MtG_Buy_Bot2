#!/usr/bin/env python3
"""
CardTrader Price Watch - riepilogo
------------------------------------
Manda su Telegram un riepilogo di tutte le carte monitorate, raggruppando
le varianti (lingua/foil) sotto il nome della carta per restare leggibile
anche con molte righe. Non interroga CardTrader: usa solo lo storico già
raccolto da check_prices.py, quindi è veloce ed economico da eseguire spesso.
"""

from common import MINIMO_PUNTI_PER_STATISTICA, carica_storico, env, invia_telegram, nome_da_chiave

LIMITE_CARATTERI_TELEGRAM = 3500  # margine di sicurezza sotto il limite di 4096


def formatta_nome(nome):
    """Mette in maiuscolo la prima lettera di ogni parola, senza rovinare
    gli apostrofi (es. 'wizard's staff' -> 'Wizard's Staff')."""
    return " ".join(p[:1].upper() + p[1:] if p else p for p in nome.split(" "))


def etichetta_variante(chiave):
    parti = chiave.split("|")
    lingua = parti[1] if len(parti) > 1 else "?"
    foil = parti[2] if len(parti) > 2 else "?"
    foil_testo = "foil" if foil == "si" else ("non foil" if foil == "no" else foil)
    return f"{lingua}, {foil_testo}"


def costruisci_blocchi_riepilogo(storico):
    """Raggruppa le varianti (lingua/foil) sotto il nome della carta, così
    ogni carta compare una volta sola invece di ripetere nome e statistiche
    per ogni riga."""
    gruppi = {}
    for chiave in sorted(storico.keys()):
        record = storico[chiave]
        rilevazioni = record.get("rilevazioni", [])
        if not rilevazioni:
            continue
        nome = nome_da_chiave(chiave)
        gruppi.setdefault(nome, []).append((chiave, rilevazioni))

    blocchi = []
    for nome in sorted(gruppi.keys(), key=str.lower):
        righe_varianti = []
        for chiave, rilevazioni in gruppi[nome]:
            prezzi = [r["prezzo"] for r in rilevazioni]
            ultimo = prezzi[-1]
            minimo = min(prezzi)
            massimo = max(prezzi)
            avviso = " ⚠️ pochi dati" if len(prezzi) < MINIMO_PUNTI_PER_STATISTICA else ""
            righe_varianti.append(
                f"• {etichetta_variante(chiave)}: <b>{ultimo:.2f}€</b> "
                f"(min {minimo:.2f} · max {massimo:.2f}){avviso}"
            )
        blocchi.append(f"<b>{formatta_nome(nome)}</b>\n" + "\n".join(righe_varianti))
    return blocchi


def invia_a_blocchi(tg_token, tg_chat_id, titolo, blocchi):
    if not blocchi:
        invia_telegram(tg_token, tg_chat_id, f"{titolo}\n\nNessun dato ancora disponibile.")
        return

    testo = titolo + "\n\n"
    for blocco in blocchi:
        if len(testo) + len(blocco) + 2 > LIMITE_CARATTERI_TELEGRAM:
            invia_telegram(tg_token, tg_chat_id, testo)
            testo = ""
        testo += blocco + "\n\n"
    if testo.strip():
        invia_telegram(tg_token, tg_chat_id, testo)


def main():
    tg_token = env("TELEGRAM_BOT_TOKEN", required=True)
    tg_chat_id = env("TELEGRAM_CHAT_ID", required=True)

    storico = carica_storico()
    blocchi = costruisci_blocchi_riepilogo(storico)
    invia_a_blocchi(tg_token, tg_chat_id, "📊 Riepilogo prezzi carte monitorate", blocchi)
    print(f"Riepilogo inviato ({len(blocchi)} carte).")


if __name__ == "__main__":
    main()
