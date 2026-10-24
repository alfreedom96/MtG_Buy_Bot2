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
problema di prezzo), e rimuove dallo storico le carte non più presenti
nel foglio Google.

Variabili d'ambiente richieste:
    CARDTRADER_TOKEN          Token API di CardTrader (Bearer token)
    TELEGRAM_BOT_TOKEN        Token del bot Telegram
    TELEGRAM_CHAT_ID          Chat ID a cui mandare le notifiche
    WORKER_URL + APP_PASSWORD Lista carte dall'app (Cloudflare Worker), oppure
    SHEET_CSV_URL             link CSV pubblico del Google Sheet (vecchio metodo)
    DEFAULT_PERCENTILE_SOGLIA Percentile di default sotto il quale un
                              prezzo è considerato "basso" (es. 15)
"""

import time

from common import (
    CardTraderClient,
    MAX_PUNTI_STORICO,
    MINIMO_PUNTI_PER_STATISTICA,
    N_OFFERTE_RIFERIMENTO,
    SOGLIA_FALLIMENTI_CONSECUTIVI,
    calcola_percentile,
    calcola_tendenza,
    carica_storico,
    chiave_carta,
    leggi_lista_carte_configurata,
    migra_chiavi_minuscole,
    scrivi_dati_app,
    env,
    Avvisi,
    url_carta_app,
    link_pagina_carta,
    analizza_occasione,
    miglior_prezzo,
    salva_storico,
)


def euro(x):
    """12.5 -> '12,50 €'"""
    return f"{x:.2f} €".replace(".", ",")


def gestisci_fallimento(record, nome, motivo, avvisi):
    """Aggiorna il contatore di fallimenti consecutivi per una carta e, se
    supera la soglia, manda un avviso tecnico una sola volta (finché non
    torna a funzionare)."""
    record["fallimenti_consecutivi"] = record.get("fallimenti_consecutivi", 0) + 1
    record["ultimo_errore"] = motivo
    if (
        record["fallimenti_consecutivi"] >= SOGLIA_FALLIMENTI_CONSECUTIVI
        and not record.get("allarme_fallimento_inviato")
    ):
        avvisi.invia(
            f"⚠️ <b>{nome}</b>\n"
            f"Nessun risultato valido da {record['fallimenti_consecutivi']} controlli consecutivi.\n"
            f"Ultimo motivo: {motivo}\n"
            f"Controlla il nome della carta o i filtri impostati.",
            f"⚠️ {nome}: nessun risultato",
            f"{record['fallimenti_consecutivi']} controlli falliti di fila. Ultimo motivo: {motivo}",
            url_carta_app(nome), f"errore-{nome.lower()}",
        )
        record["allarme_fallimento_inviato"] = True


def main():
    ct_token = env("CARDTRADER_TOKEN", required=True)
    tg_token = env("TELEGRAM_BOT_TOKEN", "")
    tg_chat_id = env("TELEGRAM_CHAT_ID", "")
    percentile_default = float(env("DEFAULT_PERCENTILE_SOGLIA", "15"))
    occasione_default = float(env("DEFAULT_SOGLIA_OCCASIONE", "10"))

    ct_client = CardTraderClient(ct_token)
    storico = migra_chiavi_minuscole(carica_storico())
    righe, fonte, impostazioni = leggi_lista_carte_configurata()
    avvisi = Avvisi(tg_token, tg_chat_id, impostazioni)
    print(f"Avvisi: app {'sì' if fonte == 'app' else 'no'}, Telegram {'sì' if avvisi.telegram else 'no'}")

    print(f"Carte da controllare: {len(righe)} (lista letta da: {fonte})")

    for riga in righe:
        nome = riga["nome_carta"]
        chiave = chiave_carta(riga)
        soglia_percentile = (
            float(riga["percentile_soglia"]) if riga["percentile_soglia"] else percentile_default
        )
        record = storico.get(chiave, {"rilevazioni": [], "ultima_notifica_prezzo": None})

        print(f"-> {nome} ...")
        try:
            risultato, errore = miglior_prezzo(
                ct_client, nome, riga["lingua"], riga["foil"],
                riga.get("edizioni_escluse"), riga.get("edizioni_incluse"),
            )

            if errore:
                print(f"   {errore}")
                gestisci_fallimento(record, nome, errore, avvisi)
                storico[chiave] = record
                continue

            # successo: azzeriamo il contatore di fallimenti
            record["fallimenti_consecutivi"] = 0
            record["allarme_fallimento_inviato"] = False
            record["ultimo_errore"] = None

            prezzo, dettagli, numero_offerte = risultato
            prezzi_offerte = dettagli.pop("prezzi_offerte", [])
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
                    avvisi.invia(
                        msg,
                        f"💰 {nome} a {euro(prezzo)}",
                        f"Tra i prezzi più bassi mai visti: percentile {percentile:.0f}, media storica "
                        f"{euro(media)}. {dettagli['condizione']}, {dettagli['espansione']}.",
                        url_carta_app(nome), f"basso-{chiave}",
                    )
                    record["ultima_notifica_prezzo"] = prezzo
                    print("   -> notifica inviata")

            # --- Controllo "occasione": minimo vs media delle 10 offerte più economiche ---
            soglia_occ = float(riga["soglia_occasione"]) if riga.get("soglia_occasione") else occasione_default
            analisi = analizza_occasione(prezzi_offerte, soglia_occ)
            if analisi is None:
                print(f"   occasione: troppe poche offerte ({len(prezzi_offerte)}) per il confronto")
            else:
                media20, sconto, e_occasione = analisi
                print(f"   occasione: media {min(N_OFFERTE_RIFERIMENTO, len(prezzi_offerte))} offerte più economiche {media20:.2f}€, sconto del minimo {sconto:.1f}% (soglia {soglia_occ:.0f}%)")
                if not e_occasione:
                    record["ultima_occasione"] = None  # si riarma per la prossima
                else:
                    ultima = record.get("ultima_occasione") or {}
                    nuova = (
                        ultima.get("product_id") != dettagli.get("product_id")
                        or prezzo < ultima.get("prezzo", 1e9) - 0.01
                    )
                    if nuova:
                        link = link_pagina_carta(nome, dettagli.get("espansione"))
                        msg = (
                            f"🔥 <b>Occasione: {nome}</b>\n"
                            f"Prezzo minimo: <b>{prezzo:.2f}€</b> — {sconto:.0f}% sotto la media "
                            f"delle {min(N_OFFERTE_RIFERIMENTO, len(prezzi_offerte))} offerte più economiche ({media20:.2f}€)\n"
                            f"{dettagli['condizione']}, {dettagli['lingua'].upper()}, "
                            f"{'foil' if dettagli['foil'] else 'non foil'}, {dettagli['espansione']}\n"
                            f"Venditore: {dettagli.get('venditore')} (product_id {dettagli.get('product_id')})"
                        )
                        if link:
                            msg += f"\n🔗 {link}"
                        avvisi.invia(
                            msg,
                            f"🔥 Occasione: {nome} a {euro(prezzo)}",
                            f"{sconto:.0f}% sotto la media delle {min(N_OFFERTE_RIFERIMENTO, len(prezzi_offerte))} "
                            f"offerte più economiche ({euro(media20)}). {dettagli['condizione']}, "
                            f"{dettagli['espansione']}, venditore {dettagli.get('venditore')}.",
                            url_carta_app(nome), f"occasione-{chiave}",
                        )
                        record["ultima_occasione"] = {"product_id": dettagli.get("product_id"), "prezzo": prezzo}
                        print("   -> notifica OCCASIONE inviata")

            # Dettaglio dell'offerta migliore, mostrato nell'app
            record["ultimo_dettaglio"] = {
                "prezzo": prezzo,
                "condizione": dettagli.get("condizione"),
                "lingua": dettagli.get("lingua"),
                "foil": dettagli.get("foil"),
                "espansione": dettagli.get("espansione"),
                "venditore": dettagli.get("venditore"),
                "quantita": dettagli.get("quantita"),
                "link": link_pagina_carta(nome, dettagli.get("espansione")),
                "offerte_valide": numero_offerte,
                "media20": round(analisi[0], 4) if analisi else None,  # nome storico del campo: è la media di riferimento
                "n_riferimento": min(N_OFFERTE_RIFERIMENTO, len(prezzi_offerte)) if analisi else None,
                "sconto": round(analisi[1], 2) if analisi else None,
            }

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
            gestisci_fallimento(record, nome, str(e), avvisi)
            storico[chiave] = record
            continue

    # Rimuove dallo storico le carte non più presenti nel foglio: da quel
    # momento non compariranno più nei riepiloghi né saranno cercabili con
    # /grafico, dato che l'utente ha scelto di non seguirle più.
    # Protezione: se il foglio risultasse vuoto per un problema temporaneo
    # di lettura (non un errore vero e proprio, es. un formato inatteso),
    # meglio non cancellare tutto lo storico per sbaglio.
    if righe:
        chiavi_attuali = {chiave_carta(riga) for riga in righe}
        chiavi_da_rimuovere = set(storico.keys()) - chiavi_attuali
        for chiave in chiavi_da_rimuovere:
            print(f"Rimuovo dallo storico (non più nel foglio): {chiave}")
            del storico[chiave]
    else:
        print("[avviso] la lista carte risulta vuota: salto la pulizia dello storico per sicurezza")

    salva_storico(storico)
    scrivi_dati_app(storico, righe, percentile_default, occasione_default)
    print("Fatto.")


if __name__ == "__main__":
    main()
