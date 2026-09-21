#!/usr/bin/env python3
"""
CardTrader Price Watch - controllo prezzi
------------------------------------------
Per ogni carta del Google Sheet, trova il prezzo più basso su CardTrader
che rispetta i filtri richiesti, aggiorna lo storico e manda una notifica
Telegram quando il prezzo è statisticamente basso rispetto allo storico
(percentile) e non era già stato segnalato a un prezzo pari o inferiore.

Manda anche un avviso "tecnico" separato se una carta fallisce per troppe
esecuzioni consecutive (segnale di un problema di configurazione, non un
problema di prezzo).

Variabili d'ambiente richieste:
    CARDTRADER_TOKEN          Token API di CardTrader (Bearer token)
    TELEGRAM_BOT_TOKEN        Token del bot Telegram
    TELEGRAM_CHAT_ID          Chat ID a cui mandare le notifiche
    SHEET_CSV_URL             Link CSV pubblico del Google Sheet
    DEFAULT_PERCENTILE_SOGLIA Percentile di default sotto il quale un
                              prezzo è considerato "basso" (es. 15)
"""

import time

from common import (
    CardTraderClient,
    MAX_PUNTI_STORICO,
    MINIMO_PUNTI_PER_STATISTICA,
    SOGLIA_FALLIMENTI_CONSECUTIVI,
    calcola_percentile,
    calcola_tendenza,
    carica_storico,
    env,
    invia_telegram,
    leggi_lista_carte,
    link_pagina_carta,
    miglior_prezzo,
    salva_storico,
)


def gestisci_fallimento(record, nome, motivo, tg_token, tg_chat_id):
    """Aggiorna il contatore di fallimenti consecutivi per una carta e, se
    supera la soglia, manda un avviso tecnico una sola volta (finché non
    torna a funzionare)."""
    record["fallimenti_consecutivi"] = record.get("fallimenti_consecutivi", 0) + 1
    if (
        record["fallimenti_consecutivi"] >= SOGLIA_FALLIMENTI_CONSECUTIVI
        and not record.get("allarme_fallimento_inviato")
    ):
        invia_telegram(
            tg_token, tg_chat_id,
            f"⚠️ <b>{nome}</b>\n"
            f"Nessun risultato valido da {record['fallimenti_consecutivi']} controlli consecutivi.\n"
            f"Ultimo motivo: {motivo}\n"
            f"Controlla il nome della carta nel foglio o i filtri impostati."
        )
        record["allarme_fallimento_inviato"] = True


def main():
    ct_token = env("CARDTRADER_TOKEN", required=True)
    tg_token = env("TELEGRAM_BOT_TOKEN", required=True)
    tg_chat_id = env("TELEGRAM_CHAT_ID", required=True)
    sheet_url = env("SHEET_CSV_URL", required=True)
    percentile_default = float(env("DEFAULT_PERCENTILE_SOGLIA", "15"))

    ct_client = CardTraderClient(ct_token)
    storico = carica_storico()
    righe = leggi_lista_carte(sheet_url)

    print(f"Carte da controllare: {len(righe)}")

    for riga in righe:
        nome = riga["nome_carta"]
        chiave = f"{nome}|{riga['lingua']}|{riga['foil']}"
        soglia_percentile = (
            float(riga["percentile_soglia"]) if riga["percentile_soglia"] else percentile_default
        )
        record = storico.get(chiave, {"rilevazioni": [], "ultima_notifica_prezzo": None})

        print(f"-> {nome} ...")
        try:
            risultato, errore = miglior_prezzo(ct_client, nome, riga["lingua"], riga["foil"])

            if errore:
                print(f"   {errore}")
                gestisci_fallimento(record, nome, errore, tg_token, tg_chat_id)
                storico[chiave] = record
                continue

            # successo: azzeriamo il contatore di fallimenti
            record["fallimenti_consecutivi"] = 0
            record["allarme_fallimento_inviato"] = False

            prezzo, dettagli, numero_offerte = risultato
            rilevazioni_passate = record.get("rilevazioni", [])
            prezzi_passati = [r["prezzo"] for r in rilevazioni_passate]

            print(
                f"   prezzo trovato: {prezzo:.2f} EUR ({dettagli}, {numero_offerte} offerte valide) "
                f"[venditore: {dettagli.get('venditore')}, product_id: {dettagli.get('product_id')}, "
                f"blueprint_id: {dettagli.get('blueprint_id')}, prezzo nativo venditore: {dettagli.get('prezzo_nativo')}]"
            )

            if len(prezzi_passati) < MINIMO_PUNTI_PER_STATISTICA:
                print(
                    f"   ancora in fase di raccolta dati "
                    f"({len(prezzi_passati)}/{MINIMO_PUNTI_PER_STATISTICA} rilevazioni), nessuna analisi statistica"
                )
            else:
                percentile = calcola_percentile(prezzi_passati, prezzo)
                media = sum(prezzi_passati) / len(prezzi_passati)
                tendenza = calcola_tendenza(prezzi_passati[-2:] + [prezzo])
                minimo_storico = min(prezzi_passati + [prezzo])

                print(f"   percentile: {percentile:.0f} (media storica: {media:.2f}€), tendenza: {tendenza}")

                ultima_notifica_prezzo = record.get("ultima_notifica_prezzo")
                statisticamente_basso = percentile <= soglia_percentile
                mai_notificato = ultima_notifica_prezzo is None
                sceso_ulteriormente = (
                    ultima_notifica_prezzo is not None and prezzo < ultima_notifica_prezzo - 0.01
                )

                if statisticamente_basso and (mai_notificato or sceso_ulteriormente):
                    link = link_pagina_carta(nome, dettagli.get("espansione"))
                    msg = (
                        f"💰 <b>{nome}</b>\n"
                        f"Prezzo attuale: {prezzo:.2f}€ — tra i più bassi mai visti "
                        f"(percentile {percentile:.0f}, media storica {media:.2f}€, minimo storico {minimo_storico:.2f}€)\n"
                        f"Tendenza recente: {tendenza}\n"
                        f"Basato su {numero_offerte} offerte valide.\n"
                        f"{dettagli['condizione']}, {dettagli['lingua'].upper()}, "
                        f"{'foil' if dettagli['foil'] else 'non foil'}, {dettagli['espansione']}\n"
                        f"Venditore: {dettagli.get('venditore')} (product_id {dettagli.get('product_id')})"
                    )
                    if link:
                        msg += f"\n🔗 {link}"
                    invia_telegram(tg_token, tg_chat_id, msg)
                    record["ultima_notifica_prezzo"] = prezzo
                    print("   -> notifica inviata")

            rilevazioni_passate.append({
                "data": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "prezzo": prezzo,
            })
            record["rilevazioni"] = rilevazioni_passate[-MAX_PUNTI_STORICO:]
            storico[chiave] = record

        except Exception as e:
            # Non blocchiamo mai il controllo delle altre carte per un
            # problema isolato su una singola riga del foglio.
            print(f"   [errore] problema imprevisto con '{nome}', la salto: {e}")
            gestisci_fallimento(record, nome, str(e), tg_token, tg_chat_id)
            storico[chiave] = record
            continue

    # Rimuove dallo storico le carte non più presenti nel foglio: da quel
    # momento non compariranno più nei riepiloghi né saranno cercabili con
    # /grafico, dato che l'utente ha scelto di non seguirle più.
    # Protezione: se il foglio risultasse vuoto per un problema temporaneo
    # di lettura (non un errore vero e proprio, es. un formato inatteso),
    # meglio non cancellare tutto lo storico per sbaglio.
    if righe:
        chiavi_attuali = {
            f"{riga['nome_carta']}|{riga['lingua']}|{riga['foil']}" for riga in righe
        }
        chiavi_da_rimuovere = set(storico.keys()) - chiavi_attuali
        for chiave in chiavi_da_rimuovere:
            print(f"Rimuovo dallo storico (non più nel foglio): {chiave}")
            del storico[chiave]
    else:
        print("[avviso] il foglio risulta vuoto: salto la pulizia dello storico per sicurezza")

    salva_storico(storico)
    print("Fatto.")


if __name__ == "__main__":
    main()
