#!/usr/bin/env python3
"""
CardTrader Price Watch - comandi Telegram
--------------------------------------------
Controlla se sono arrivati nuovi messaggi al bot Telegram e risponde ai
comandi supportati:

    /help                 elenca i comandi disponibili
    /riepilogo            manda subito il riepilogo di tutte le carte
    /grafico <nome carta> manda un grafico dell'andamento storico del prezzo

Può essere invocato in due modi:
1. Da un webhook Telegram (tramite un Cloudflare Worker) che passa il testo
   del comando già pronto come input del workflow_dispatch: risposta quasi
   istantanea, nessun polling.
2. Da uno schedule di fallback (se il webhook non fosse configurato o
   smettesse di funzionare), che controlla i messaggi in sospeso con
   getUpdates.

Ignora messaggi che non provengono dalla chat configurata in
TELEGRAM_CHAT_ID, per sicurezza (è un bot personale).
"""

import io
import os
from datetime import datetime

import matplotlib
matplotlib.use("Agg")  # nessun display, solo generazione file immagine
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from common import (
    carica_offset_telegram,
    carica_storico,
    env,
    invia_telegram,
    invia_telegram_foto,
    leggi_messaggi_telegram,
    nome_da_chiave,
    salva_offset_telegram,
)
from send_digest import costruisci_righe_riepilogo, invia_a_blocchi

TESTO_HELP = (
    "Comandi disponibili:\n"
    "/riepilogo - riepilogo di tutte le carte monitorate\n"
    "/grafico Nome Carta - grafico dell'andamento storico del prezzo "
    "(se tieni più varianti della stessa carta, es. foil e non-foil, le manda tutte)\n"
    "/help - questo messaggio"
)


def trova_record_per_nome(storico, nome_richiesto):
    """Cerca tra le chiavi dello storico tutte le varianti (lingua/foil)
    che corrispondono al nome richiesto (case-insensitive), ordinate per
    numero di rilevazioni decrescente."""
    nome_lower = nome_richiesto.strip().lower()
    candidati = [
        (chiave, record) for chiave, record in storico.items()
        if nome_da_chiave(chiave).strip().lower() == nome_lower
    ]
    candidati.sort(key=lambda kv: len(kv[1].get("rilevazioni", [])), reverse=True)
    return candidati


def etichetta_variante(chiave):
    parti = chiave.split("|")
    lingua = parti[1] if len(parti) > 1 else "?"
    foil = parti[2] if len(parti) > 2 else "?"
    foil_testo = "foil" if foil == "si" else ("non foil" if foil == "no" else foil)
    return f"{lingua}, {foil_testo}"


def genera_grafico(chiave, record):
    rilevazioni = record.get("rilevazioni", [])
    date = [r["data"] for r in rilevazioni]
    prezzi = [r["prezzo"] for r in rilevazioni]

    date_dt = [datetime.strptime(d, "%Y-%m-%dT%H:%M:%SZ") for d in date]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(date_dt, prezzi, "-o", markersize=3, linewidth=1.5, color="#2563eb")
    ax.set_title(nome_da_chiave(chiave))
    ax.set_ylabel("Prezzo (EUR)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    fig.autofmt_xdate()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    buf.seek(0)
    return buf


def gestisci_comando(testo, tg_token, tg_chat_id, storico):
    comando, _, argomento = testo.strip().partition(" ")
    comando = comando.lower()
    argomento = argomento.strip()

    if comando in ("/help", "/start"):
        invia_telegram(tg_token, tg_chat_id, TESTO_HELP)

    elif comando == "/riepilogo":
        righe = costruisci_righe_riepilogo(storico)
        invia_a_blocchi(tg_token, tg_chat_id, "📊 Riepilogo prezzi carte monitorate", righe)

    elif comando == "/grafico":
        if not argomento:
            invia_telegram(tg_token, tg_chat_id, "Usa: /grafico Nome Carta")
            return
        candidati = trova_record_per_nome(storico, argomento)
        candidati_validi = [
            (chiave, record) for chiave, record in candidati
            if len(record.get("rilevazioni", [])) >= 2
        ]
        if not candidati_validi:
            invia_telegram(
                tg_token, tg_chat_id,
                f"Non ho ancora abbastanza dati storici per '{argomento}'. "
                f"Controlla di aver scritto il nome esatto come nel foglio."
            )
            return
        import tempfile, os as _os
        for chiave, record in candidati_validi:
            buf = genera_grafico(chiave, record)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(buf.read())
                percorso = tmp.name
            try:
                didascalia = f"{nome_da_chiave(chiave)} ({etichetta_variante(chiave)})"
                invia_telegram_foto(tg_token, tg_chat_id, percorso, didascalia=didascalia)
            finally:
                _os.remove(percorso)

    else:
        invia_telegram(tg_token, tg_chat_id, "Comando non riconosciuto. Scrivi /help per la lista.")


def main():
    tg_token = env("TELEGRAM_BOT_TOKEN", required=True)
    tg_chat_id = str(env("TELEGRAM_CHAT_ID", required=True))

    comando_diretto = os.environ.get("COMANDO_DIRETTO", "").strip()
    if comando_diretto:
        # Arrivato dal webhook (Cloudflare Worker): il testo del comando è
        # già noto, non serve interrogare Telegram con getUpdates.
        print(f"Comando diretto ricevuto dal webhook: {comando_diretto}")
        storico = carica_storico()
        try:
            gestisci_comando(comando_diretto, tg_token, tg_chat_id, storico)
        except Exception as e:
            print(f"[errore] problema gestendo il comando '{comando_diretto}': {e}")
            invia_telegram(tg_token, tg_chat_id, f"Si è verificato un errore elaborando il comando: {e}")
        print("Fatto.")
        return

    offset = carica_offset_telegram()
    messaggi = leggi_messaggi_telegram(tg_token, offset=offset)

    if not messaggi:
        print("Nessun nuovo messaggio.")
        return

    storico = carica_storico()
    ultimo_offset = offset

    for update in messaggi:
        ultimo_offset = update["update_id"] + 1
        messaggio = update.get("message") or update.get("edited_message")
        if not messaggio:
            continue
        chat_id_mittente = str(messaggio.get("chat", {}).get("id"))
        testo = messaggio.get("text", "")
        if chat_id_mittente != tg_chat_id:
            print(f"Messaggio ignorato da chat non autorizzata: {chat_id_mittente}")
            continue
        if not testo.startswith("/"):
            continue
        print(f"Comando ricevuto: {testo}")
        try:
            gestisci_comando(testo, tg_token, tg_chat_id, storico)
        except Exception as e:
            print(f"[errore] problema gestendo il comando '{testo}': {e}")
            invia_telegram(tg_token, tg_chat_id, f"Si è verificato un errore elaborando il comando: {e}")

    salva_offset_telegram(ultimo_offset)
    print("Fatto.")


if __name__ == "__main__":
    main()
