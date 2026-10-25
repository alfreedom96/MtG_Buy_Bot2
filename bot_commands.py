#!/usr/bin/env python3
"""
CardTrader Price Watch - comandi Telegram
--------------------------------------------
Controlla se sono arrivati nuovi messaggi al bot Telegram e risponde ai
comandi supportati:

    qualunque testo       mostra l'help con i bottoni cliccabili
    /help                 elenca i comandi disponibili
    /riepilogo            manda subito il riepilogo di tutte le carte
    /grafico <nome carta> manda un grafico dell'andamento storico del prezzo
                          (se ci sono più varianti, es. foil e non-foil, le
                          manda tutte)

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

import hashlib
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
    rispondi_callback,
    salva_offset_telegram,
)
from send_digest import costruisci_blocchi_riepilogo, formatta_nome, invia_a_blocchi

TESTO_HELP = (
    "👋 <b>Cosa vuoi fare?</b>\n"
    "Tocca un bottone qui sotto.\n\n"
    "📊 <b>Riepilogo</b> - prezzi di tutte le carte monitorate\n"
    "📈 <b>Grafico</b> - scegli una carta e vedi l'andamento del prezzo\n\n"
    "<i>Puoi anche scrivere /grafico Nome Carta.</i>"
)

TASTIERA_HELP = [[("📊 Riepilogo", "riepilogo"), ("📈 Grafico", "grafici:0")]]
CARTE_PER_PAGINA = 30


def nomi_carte(storico):
    """Nomi unici (minuscoli) delle carte nello storico, in ordine alfabetico."""
    return sorted({nome_da_chiave(k).strip().lower() for k in storico})


def id_carta(nome):
    """Identificativo corto (callback_data Telegram max 64 byte)."""
    return hashlib.sha1(nome.strip().lower().encode("utf-8")).hexdigest()[:10]


def invia_help(tg_token, tg_chat_id):
    invia_telegram(tg_token, tg_chat_id, TESTO_HELP, tastiera=TASTIERA_HELP)


def invia_elenco_carte(tg_token, tg_chat_id, storico, pagina=0):
    nomi = nomi_carte(storico)
    if not nomi:
        invia_telegram(tg_token, tg_chat_id, "Non ci sono ancora carte monitorate.", tastiera=TASTIERA_HELP)
        return
    n_pagine = (len(nomi) + CARTE_PER_PAGINA - 1) // CARTE_PER_PAGINA
    pagina = max(0, min(pagina, n_pagine - 1))
    fetta = nomi[pagina * CARTE_PER_PAGINA:(pagina + 1) * CARTE_PER_PAGINA]
    righe, riga = [], []
    for nome in fetta:
        riga.append((formatta_nome(nome), f"g:{id_carta(nome)}"))
        if len(riga) == 2:
            righe.append(riga)
            riga = []
    if riga:
        righe.append(riga)
    nav = []
    if pagina > 0:
        nav.append(("⬅️ Indietro", f"grafici:{pagina - 1}"))
    if pagina < n_pagine - 1:
        nav.append(("Avanti ➡️", f"grafici:{pagina + 1}"))
    if nav:
        righe.append(nav)
    righe.append([("🏠 Menu", "menu")])
    titolo = "📈 <b>Di quale carta vuoi il grafico?</b>"
    if n_pagine > 1:
        titolo += f" (pagina {pagina + 1}/{n_pagine})"
    invia_telegram(tg_token, tg_chat_id, titolo, tastiera=righe)


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
    # scala minima ±5%: variazioni di pochi centesimi restano piatte
    centro = (min(prezzi) + max(prezzi)) / 2
    meta = max((max(prezzi) - min(prezzi)) / 2, centro * 0.05, 0.01)
    ax.set_ylim(centro - meta * 1.1, centro + meta * 1.1)
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


def invia_grafici(tg_token, tg_chat_id, nome, candidati):
    candidati_validi = [
        (chiave, record) for chiave, record in candidati
        if len(record.get("rilevazioni", [])) >= 2
    ]
    if not candidati_validi:
        invia_telegram(
            tg_token, tg_chat_id,
            f"Non ho ancora abbastanza dati storici per '{nome}'. "
            f"Controlla di aver scritto il nome esatto come nel foglio.",
            tastiera=[[("📈 Altre carte", "grafici:0"), ("🏠 Menu", "menu")]],
        )
        return
    import tempfile
    for i, (chiave, record) in enumerate(candidati_validi):
        buf = genera_grafico(chiave, record)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(buf.read())
            percorso = tmp.name
        try:
            didascalia = f"{formatta_nome(nome_da_chiave(chiave))} ({etichetta_variante(chiave)})"
            ultimo = i == len(candidati_validi) - 1
            invia_telegram_foto(
                tg_token, tg_chat_id, percorso, didascalia=didascalia,
                tastiera=[[("📈 Altre carte", "grafici:0"), ("🏠 Menu", "menu")]] if ultimo else None,
            )
        finally:
            os.remove(percorso)


def gestisci_comando(testo, tg_token, tg_chat_id, storico):
    """Gestisce sia i comandi testuali (/grafico ...) sia i click sui bottoni
    (che arrivano come 'cb:<dati>'). Qualunque altro testo mostra l'help."""
    testo = testo.strip()

    if testo.startswith("cb:"):
        dati = testo[3:]
        if dati == "riepilogo":
            blocchi = costruisci_blocchi_riepilogo(storico)
            invia_a_blocchi(tg_token, tg_chat_id, "📊 Riepilogo prezzi carte monitorate", blocchi)
        elif dati.startswith("grafici:"):
            try:
                pagina = int(dati.split(":", 1)[1])
            except ValueError:
                pagina = 0
            invia_elenco_carte(tg_token, tg_chat_id, storico, pagina)
        elif dati.startswith("g:"):
            cid = dati[2:]
            nome = next((n for n in nomi_carte(storico) if id_carta(n) == cid), None)
            if nome is None:
                invia_telegram(tg_token, tg_chat_id, "Questa carta non è più monitorata. Ecco l'elenco aggiornato:")
                invia_elenco_carte(tg_token, tg_chat_id, storico, 0)
            else:
                invia_grafici(tg_token, tg_chat_id, nome, trova_record_per_nome(storico, nome))
        else:  # "menu" o sconosciuto
            invia_help(tg_token, tg_chat_id)
        return

    comando, _, argomento = testo.partition(" ")
    comando = comando.lower()
    argomento = argomento.strip()

    if comando == "/riepilogo":
        blocchi = costruisci_blocchi_riepilogo(storico)
        invia_a_blocchi(tg_token, tg_chat_id, "📊 Riepilogo prezzi carte monitorate", blocchi)
    elif comando == "/grafico":
        if not argomento:
            invia_elenco_carte(tg_token, tg_chat_id, storico, 0)
        else:
            invia_grafici(tg_token, tg_chat_id, argomento, trova_record_per_nome(storico, argomento))
    else:
        # /help, /start, comandi sconosciuti e qualunque testo libero
        invia_help(tg_token, tg_chat_id)


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
        cb = update.get("callback_query")
        if cb:
            chat_id_mittente = str((cb.get("message") or {}).get("chat", {}).get("id"))
            if chat_id_mittente != tg_chat_id:
                continue
            rispondi_callback(tg_token, cb["id"])
            testo = "cb:" + (cb.get("data") or "menu")
        else:
            messaggio = update.get("message") or update.get("edited_message")
            if not messaggio:
                continue
            chat_id_mittente = str(messaggio.get("chat", {}).get("id"))
            testo = messaggio.get("text", "")
            if chat_id_mittente != tg_chat_id:
                print(f"Messaggio ignorato da chat non autorizzata: {chat_id_mittente}")
                continue
            if not testo:
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
