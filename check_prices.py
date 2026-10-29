#!/usr/bin/env python3
"""
CardTrader Price Watch - controllo prezzi
------------------------------------------
Per ogni carta seguita (lista dell'app o del vecchio foglio Google) trova il
prezzo più basso su CardTrader che rispetta i filtri, aggiorna lo storico e
manda gli avvisi (notifica nell'app e, se attivo, Telegram):

  🎯 prezzo obiettivo   il prezzo è sceso sotto il valore scelto per la carta
  🔥 occasione          il minimo è molto sotto la media delle offerte più economiche
  💰 prezzo basso       tra i più bassi mai visti e sotto la mediana storica
  📈 impennata          il prezzo sale rapidamente (+15% rispetto ai 3 giorni prima)
  🆕 ristampa           è uscita (o è annunciata) una nuova stampa della carta
  ⚠️ problemi           una carta non dà risultati da molti controlli, oppure
                        CardTrader rifiuta il token (un solo avviso, non uno per carta)

Salva anche, per l'app: il prezzo Cardmarket (preso da Scryfall), il minimo
assoluto su CardTrader (anche venditori senza CardTrader Zero) e lo stato
del sistema. Il lunedì mattina manda un riepilogo settimanale nell'app.

Variabili d'ambiente:
    CARDTRADER_TOKEN          Token API di CardTrader (obbligatorio)
    TELEGRAM_BOT_TOKEN        Token del bot Telegram (facoltativo)
    TELEGRAM_CHAT_ID          Chat ID Telegram (facoltativo)
    WORKER_URL + APP_PASSWORD Lista carte e notifiche dell'app, oppure
    SHEET_CSV_URL             link CSV pubblico del foglio Google (vecchio metodo)
    DEFAULT_PERCENTILE_SOGLIA, DEFAULT_SOGLIA_OCCASIONE, DEFAULT_SOGLIA_RIBASSO,
    DEFAULT_SOGLIA_RIALZO     soglie predefinite (facoltative)
"""

import base64
import json
import time
from datetime import datetime, timezone

import requests

from common import (
    MAX_PUNTI_STORICO,
    MINIMO_PUNTI_PER_STATISTICA,
    SOGLIA_FALLIMENTI_CONSECUTIVI,
    SOGLIA_RIALZO_PREDEFINITA,
    SOGLIA_RIBASSO_PREDEFINITA,
    Avvisi,
    CardTraderClient,
    _valore,
    analizza_occasione,
    calcola_tendenza,
    carica_storico,
    chiave_carta,
    env,
    invia_notifica_app,
    leggi_lista_carte_configurata,
    link_pagina_carta,
    migra_chiavi_minuscole,
    miglior_prezzo,
    salva_storico,
    scrivi_dati_app,
    url_carta_app,
    valuta_prezzo_basso,
    valuta_rialzo,
)
from common import CONDIZIONI_VISTE, ESEMPI_FIRMATE, OFFERTE_SCARTATE, condizione_accettata
from mercato import CacheCarte, prezzo_cardmarket

ORE_TRA_PREZZI_CARDMARKET = 6


def euro(x):
    """12.5 -> '12,50 €'"""
    return f"{x:.2f} €".replace(".", ",")


def ora_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def scadenza_token(token):
    """Se il token è un JWT (JSON Web Token, il formato usato da CardTrader)
    legge la data di scadenza. Ritorna una data ISO o None."""
    try:
        parte = token.split(".")[1]
        parte += "=" * (-len(parte) % 4)
        exp = json.loads(base64.urlsafe_b64decode(parte)).get("exp")
        return datetime.fromtimestamp(int(exp), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if exp else None
    except Exception:
        return None


def token_rifiutato(errore):
    return (
        isinstance(errore, requests.HTTPError)
        and errore.response is not None
        and errore.response.status_code in (401, 403)
    )


def descrizione_dettagli(d):
    return f"{d['condizione']}, {d['lingua'].upper()}, {'foil' if d['foil'] else 'non foil'}, {d['espansione']}"


def gestisci_fallimento(record, nome, motivo, avvisi):
    """Conta i fallimenti consecutivi di una carta e, oltre la soglia, manda
    un avviso tecnico una sola volta (finché non torna a funzionare)."""
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


def avvisa_ristampe(nome, nuove, avvisi):
    for s in nuove:
        uscita = s.get("uscita") or ""
        try:
            data = datetime.strptime(uscita, "%Y-%m-%d").strftime("%d/%m/%Y")
            futura = uscita > time.strftime("%Y-%m-%d")
        except ValueError:
            data, futura = uscita, False
        quando = f"in uscita il {data}" if futura else f"uscita il {data}"
        avvisi.invia(
            f"🆕 <b>Ristampa: {nome}</b>\n{s['nome_set']} ({quando}).\n"
            f"Spesso dopo una ristampa il prezzo scende: può convenire aspettare.",
            f"🆕 Ristampa: {nome}",
            f"{s['nome_set']}, {quando}. Spesso dopo una ristampa il prezzo scende: può convenire aspettare.",
            url_carta_app(nome), f"ristampa-{nome.lower()}-{s['set']}",
        )
        print(f"   -> avviso RISTAMPA: {s['nome_set']}")


def aggiorna_cardmarket(record, stampe, riga):
    """Aggiunge un punto alla serie Cardmarket se il prezzo è cambiato o se
    sono passate almeno 6 ore dall'ultimo (Scryfall lo aggiorna una volta al giorno)."""
    cm = prezzo_cardmarket(stampe, riga["foil"], riga.get("edizioni_escluse"), riga.get("edizioni_incluse"))
    if not cm:
        return None
    serie = record.setdefault("cardmarket", [])
    ultimo = serie[-1] if serie else None
    vecchio = ultimo is None or time.time() - (datetime.strptime(ultimo["data"], "%Y-%m-%dT%H:%M:%SZ")
                                               .replace(tzinfo=timezone.utc).timestamp()) >= ORE_TRA_PREZZI_CARDMARKET * 3600
    if ultimo is None or abs(ultimo["prezzo"] - cm[0]) >= 0.005 or vecchio:
        serie.append({"data": ora_iso(), "prezzo": cm[0], "set": cm[1]})
        del serie[:-2000]
    return cm


GIORNI_MEMORIA_AVVISI = 7


def _giorni_fa(iso):
    try:
        t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        return (time.time() - t) / 86400
    except (TypeError, ValueError):
        return 1e9


def controlla_obiettivo(record, nome, chiave, prezzo, obiettivo, dettagli, avvisi):
    """🎯 Avvisa quando il prezzo scende sotto l'obiettivo. Per non ripetersi
    quando un'offerta compare e scompare: dopo un avviso ne manda un altro solo
    se il prezzo scende di un ulteriore 5%, oppure dopo 7 giorni."""
    if obiettivo is None or prezzo > obiettivo:
        return
    ultimo = record.get("obiettivo_notificato")
    if isinstance(ultimo, (int, float)):  # formato vecchio
        ultimo = {"prezzo": ultimo, "data": ora_iso()}
    if ultimo and prezzo > ultimo["prezzo"] * 0.95 and _giorni_fa(ultimo.get("data")) < GIORNI_MEMORIA_AVVISI:
        return
    link = link_pagina_carta(nome, dettagli.get("espansione"))
    avvisi.invia(
        f"🎯 <b>{nome}</b> a {euro(prezzo)}, sotto il tuo obiettivo di {euro(obiettivo)}\n"
        f"{descrizione_dettagli(dettagli)}\nVenditore: {dettagli.get('venditore')}"
        + (f"\n🔗 {link}" if link else ""),
        f"🎯 {nome} a {euro(prezzo)}",
        f"Sotto il tuo obiettivo di {euro(obiettivo)}. {dettagli['condizione']}, {dettagli['espansione']}, "
        f"venditore {dettagli.get('venditore')}.",
        url_carta_app(nome), f"obiettivo-{chiave}",
    )
    record["obiettivo_notificato"] = {"prezzo": prezzo, "data": ora_iso()}
    print("   -> avviso OBIETTIVO inviato")


def controlla_rialzo(record, nome, chiave, rilevazioni, soglia, avvisi):
    """📈 Avvisa quando il prezzo sale rapidamente. Ripete solo se sale di
    un'altra soglia rispetto all'ultimo avviso; si riarma quando il rialzo rientra."""
    r = valuta_rialzo(rilevazioni, soglia)
    if not r:
        return
    print(f"   rialzo: livello {r['livello']:.2f}€ contro {r['riferimento']:.2f}€ dei giorni prima "
          f"({r['rialzo']:+.1f}%, soglia {soglia:.0f}%)")
    if not r["attivo"]:
        record["rialzo_notificato"] = None
        return
    ultimo = record.get("rialzo_notificato")
    if ultimo is not None and r["livello"] < ultimo * (1 + soglia / 100):
        return
    avvisi.invia(
        f"📈 <b>{nome}</b> in rialzo: {euro(r['livello'])}, +{r['rialzo']:.0f}% rispetto ai giorni precedenti "
        f"({euro(r['riferimento'])}).\nSe ti serve, potrebbe convenire comprare prima che salga ancora.",
        f"📈 {nome} in rialzo: +{r['rialzo']:.0f}%",
        f"Ora {euro(r['livello'])}, nei giorni precedenti {euro(r['riferimento'])}. "
        f"Se ti serve, potrebbe convenire comprare prima che salga ancora.",
        url_carta_app(nome), f"rialzo-{chiave}",
    )
    record["rialzo_notificato"] = r["livello"]
    print("   -> avviso RIALZO inviato")


def controlla_prezzo_basso(record, nome, chiave, prezzo, prezzi_passati, soglie, dettagli, numero_offerte, avvisi):
    if len(prezzi_passati) < MINIMO_PUNTI_PER_STATISTICA:
        print(f"   ancora in fase di raccolta dati ({len(prezzi_passati)}/{MINIMO_PUNTI_PER_STATISTICA} rilevazioni)")
        return
    percentile, mediana, ribasso, basso = valuta_prezzo_basso(
        prezzi_passati, prezzo, soglie["percentile"], soglie["ribasso"])
    media = sum(prezzi_passati) / len(prezzi_passati)
    tendenza = calcola_tendenza(prezzi_passati[-2:] + [prezzo])
    minimo_storico = min(prezzi_passati + [prezzo])
    print(f"   percentile: {percentile:.0f} (soglia {soglie['percentile']:.0f}), {ribasso:.1f}% sotto la mediana "
          f"storica {mediana:.2f}€ (soglia {soglie['ribasso']:.0f}%), tendenza: {tendenza}")
    ultima = record.get("ultima_notifica_prezzo")
    if basso and (ultima is None or prezzo < ultima - 0.01):
        link = link_pagina_carta(nome, dettagli.get("espansione"))
        avvisi.invia(
            f"💰 <b>{nome}</b>\n"
            f"Prezzo attuale: {prezzo:.2f}€ — tra i più bassi mai visti, {ribasso:.0f}% sotto la mediana storica "
            f"(percentile {percentile:.0f}, mediana {mediana:.2f}€, media {media:.2f}€, minimo storico {minimo_storico:.2f}€)\n"
            f"Tendenza recente: {tendenza}\nBasato su {numero_offerte} offerte valide.\n"
            f"{descrizione_dettagli(dettagli)}\nVenditore: {dettagli.get('venditore')}"
            + (f"\n🔗 {link}" if link else ""),
            f"💰 {nome} a {euro(prezzo)}",
            f"{ribasso:.0f}% sotto la mediana storica ({euro(mediana)}), tra i prezzi più bassi mai visti. "
            f"{dettagli['condizione']}, {dettagli['espansione']}.",
            url_carta_app(nome), f"basso-{chiave}",
        )
        record["ultima_notifica_prezzo"] = prezzo
        print("   -> avviso PREZZO BASSO inviato")


def controlla_occasione(record, nome, chiave, prezzo, prezzi_offerte, soglia, dettagli, avvisi):
    """🔥 Occasione. Per evitare avvisi ripetuti o su offerte "fantasma":
      - l'offerta deve essere la più economica in due controlli di fila
        (un'offerta che compare e sparisce subito non conta);
      - la stessa offerta non viene segnalata di nuovo per 7 giorni,
        anche se nel frattempo sparisce e ricompare."""
    analisi = analizza_occasione(prezzi_offerte, soglia)
    if analisi is None:
        print(f"   occasione: troppe poche offerte ({len(prezzi_offerte)}) per il confronto")
        record["occasione_candidata"] = None
        return None
    mercato, sconto = analisi["mercato"], analisi["sconto"]
    quale = "terza" if analisi["riferimento_terza"] else "seconda"
    print(f"   occasione: mercato {mercato:.2f}€ (mediana di {analisi['usate']} offerte), minimo {sconto:.1f}% sotto "
          f"(soglia {soglia:.0f}%); stacco dalla {quale} offerta ({analisi['riferimento']:.2f}€): "
          f"{analisi['stacco_eur']:.2f}€ = {analisi['stacco_pct']:.1f}% (minimo 10% e 0,40€) -> "
          f"{'OCCASIONE' if analisi['occasione'] else 'no'}")
    pid = dettagli.get("product_id")
    if not analisi["occasione"]:
        record["occasione_candidata"] = None
        return analisi
    candidata = record.get("occasione_candidata")
    record["occasione_candidata"] = pid
    if candidata != pid:
        print("   occasione da confermare al prossimo controllo")
        return analisi
    notificate = {k: v for k, v in (record.get("occasioni_notificate") or {}).items()
                  if _giorni_fa(v) < GIORNI_MEMORIA_AVVISI}
    record["occasioni_notificate"] = notificate
    if str(pid) in notificate:
        return analisi
    link = link_pagina_carta(nome, dettagli.get("espansione"))
    quale = "terza" if analisi["riferimento_terza"] else "seconda"
    avvisi.invia(
        f"🔥 <b>Occasione: {nome}</b> a <b>{euro(prezzo)}</b>\n"
        f"{sconto:.0f}% sotto il prezzo di mercato ({euro(mercato)}). "
        f"La {quale} offerta più economica costa {euro(analisi['riferimento'])}.\n"
        f"{descrizione_dettagli(dettagli)}\nVenditore: {dettagli.get('venditore')} (offerta {pid})"
        + (f"\n🔗 {link}" if link else ""),
        f"🔥 Occasione: {nome} a {euro(prezzo)}",
        f"{sconto:.0f}% sotto il prezzo di mercato ({euro(mercato)}); la {quale} offerta costa {euro(analisi['riferimento'])}. "
        f"{dettagli['condizione']}, {dettagli['lingua'].upper()}, {dettagli['espansione']}, venditore {dettagli.get('venditore')}.",
        url_carta_app(nome), f"occasione-{chiave}",
    )
    notificate[str(pid)] = ora_iso()
    # ricordo l'ultima occasione segnalata, per mostrarla nell'app anche dopo che sparisce
    record["ultima_occasione_info"] = {
        "prezzo": prezzo, "venditore": dettagli.get("venditore"), "espansione": dettagli.get("espansione"),
        "product_id": pid, "data": ora_iso(),
    }
    print("   -> avviso OCCASIONE inviato")
    return analisi


def riepilogo_settimanale(varianti, righe, cache):
    """Il lunedì dalle 8 (ora UTC) manda una volta sola un riepilogo nell'app."""
    adesso = datetime.now(timezone.utc)
    settimana = adesso.strftime("%G-W%V")
    if adesso.weekday() != 0 or adesso.hour < 8 or cache.meta.get("ultimo_riepilogo") == settimana:
        return
    nomi = {chiave_carta(r): r["nome_carta"] for r in righe}
    conta = lambda campo: sum(1 for v in varianti.values() if v.get(campo))
    rialzi = sum(1 for v in varianti.values() if (v.get("rialzo") or {}).get("attivo"))
    cali = sorted(((v["var7"], k) for k, v in varianti.items() if v.get("var7") is not None), key=lambda x: x[0])
    parti = []
    for n, testo in ((conta("sotto_obiettivo"), "sotto obiettivo"), (conta("occasione"), "occasioni"),
                     (conta("prezzo_basso"), "prezzi bassi"), (rialzi, "in rialzo")):
        if n:
            parti.append(f"{n} {testo}")
    testo = (", ".join(parti) if parti else "Nessuna segnalazione") + "."
    if cali and cali[0][0] < 0:
        testo += f" Calo maggiore in 7 giorni: {nomi.get(cali[0][1], cali[0][1])} {cali[0][0]:.0f}%."
    if cali and cali[-1][0] > 0:
        testo += f" Rialzo maggiore: {nomi.get(cali[-1][1], cali[-1][1])} +{cali[-1][0]:.0f}%."
    invia_notifica_app(f"📊 Riepilogo settimanale ({len(righe)} varianti seguite)", testo, "/", "riepilogo")
    cache.meta["ultimo_riepilogo"] = settimana
    print(f"Riepilogo settimanale inviato: {testo}")


def main():
    inizio = time.time()
    CONDIZIONI_VISTE.clear()
    OFFERTE_SCARTATE.clear()
    ESEMPI_FIRMATE.clear()
    ct_token = env("CARDTRADER_TOKEN", required=True)
    tg_token = env("TELEGRAM_BOT_TOKEN", "")
    tg_chat_id = env("TELEGRAM_CHAT_ID", "")
    soglie_predefinite = {
        "percentile": float(env("DEFAULT_PERCENTILE_SOGLIA", "15")),
        "occasione": float(env("DEFAULT_SOGLIA_OCCASIONE", "15")),
        "ribasso": float(env("DEFAULT_SOGLIA_RIBASSO", str(SOGLIA_RIBASSO_PREDEFINITA))),
        "rialzo": float(env("DEFAULT_SOGLIA_RIALZO", str(SOGLIA_RIALZO_PREDEFINITA))),
    }

    ct_client = CardTraderClient(ct_token)
    storico = migra_chiavi_minuscole(carica_storico())
    cache = CacheCarte()
    righe, fonte, impostazioni = leggi_lista_carte_configurata()
    avvisi = Avvisi(tg_token, tg_chat_id, impostazioni)
    meta = {"cardtrader_scadenza": scadenza_token(ct_token), "n_carte": len(righe), "errore_globale": None}
    print(f"Carte da controllare: {len(righe)} (lista letta da: {fonte}); "
          f"avvisi: app {'sì' if fonte == 'app' else 'no'}, Telegram {'sì' if avvisi.telegram else 'no'}")

    # --- Il token CardTrader funziona? Se no: un solo avviso, non uno per carta ---
    try:
        ct_client.espansioni_magic()
        token_ok = True
    except requests.HTTPError as e:
        if not token_rifiutato(e):
            raise
        token_ok = False
        meta["errore_globale"] = (f"CardTrader rifiuta il token (errore {e.response.status_code}): "
                                  f"i prezzi non si aggiornano. Controlla il secret CARDTRADER_TOKEN su GitHub.")
        print(meta["errore_globale"])
        if not cache.meta.get("allarme_token"):
            avvisi.invia(f"🔒 <b>{meta['errore_globale']}</b>", "🔒 CardTrader rifiuta il token",
                         meta["errore_globale"], "/", "token-cardtrader")
            cache.meta["allarme_token"] = True
    if token_ok and cache.meta.get("allarme_token"):
        avvisi.invia("✅ CardTrader accetta di nuovo il token: i controlli prezzi sono ripartiti.",
                     "✅ CardTrader di nuovo raggiungibile", "I controlli prezzi sono ripartiti.", "/", "token-cardtrader")
        cache.meta["allarme_token"] = False

    for riga in (righe if token_ok else []):
        nome = riga["nome_carta"]
        chiave = chiave_carta(riga)
        soglie = {
            "percentile": _valore(riga, "percentile_soglia", soglie_predefinite["percentile"]),
            "occasione": _valore(riga, "soglia_occasione", soglie_predefinite["occasione"]),
            "ribasso": _valore(riga, "soglia_ribasso", soglie_predefinite["ribasso"]),
            "rialzo": _valore(riga, "soglia_rialzo", soglie_predefinite["rialzo"]),
        }
        obiettivo = _valore(riga, "prezzo_obiettivo", None)
        record = storico.get(chiave, {"rilevazioni": [], "ultima_notifica_prezzo": None})

        print(f"-> {nome} ...")
        try:
            # Stampe e prezzo Cardmarket (dalla memoria, aggiornati ogni 6 ore)
            stampe, nuove = cache.stampe(nome)
            avvisa_ristampe(nome, nuove, avvisi)
            cm = aggiorna_cardmarket(record, stampe, riga)
            if cm:
                print(f"   Cardmarket: {cm[0]:.2f}€ ({cm[1]})")

            risultato, errore = miglior_prezzo(
                ct_client, nome, riga["lingua"], riga["foil"],
                riga.get("edizioni_escluse"), riga.get("edizioni_incluse"), cache,
            )
            if errore:
                print(f"   {errore}")
                gestisci_fallimento(record, nome, errore, avvisi)
                storico[chiave] = record
                continue

            record["fallimenti_consecutivi"] = 0
            record["allarme_fallimento_inviato"] = False
            record["ultimo_errore"] = None

            prezzo, dettagli, numero_offerte = risultato
            prezzi_offerte = dettagli.pop("prezzi_offerte", [])
            offerte_riferimento = dettagli.pop("offerte_riferimento", [])
            assoluto = dettagli.pop("minimo_assoluto", None)
            rilevazioni = record.get("rilevazioni", [])
            prezzi_passati = [r["prezzo"] for r in rilevazioni]
            print(f"   prezzo: {prezzo:.2f}€ ({descrizione_dettagli(dettagli)}, venditore {dettagli.get('venditore')}, "
                  f"{numero_offerte} offerte valide)"
                  + (f"; minimo assoluto {assoluto['prezzo']:.2f}€ ({assoluto['venditore']})" if assoluto else ""))

            controlla_obiettivo(record, nome, chiave, prezzo, obiettivo, dettagli, avvisi)
            controlla_prezzo_basso(record, nome, chiave, prezzo, prezzi_passati, soglie, dettagli, numero_offerte, avvisi)
            analisi = controlla_occasione(record, nome, chiave, prezzo, prezzi_offerte, soglie["occasione"], dettagli, avvisi)

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
                "media20": round(analisi["media"], 4) if analisi else None,  # nome storico: media di riferimento
                "n_riferimento": analisi["usate"] if analisi else None,
                "mercato": round(analisi["mercato"], 4) if analisi else None,
                "riferimento": round(analisi["riferimento"], 2) if analisi else None,
                "riferimento_terza": bool(analisi and analisi["riferimento_terza"]),
                "stacco_eur": round(analisi["stacco_eur"], 2) if analisi else None,
                "stacco_pct": round(analisi["stacco_pct"], 1) if analisi else None,
                "occasione": bool(analisi and analisi["occasione"]),
                "offerte": offerte_riferimento,
                "sconto": round(analisi["sconto"], 2) if analisi else None,
                "assoluto": assoluto,
                "product_id": dettagli.get("product_id"),
                "ultima_occasione": record.get("ultima_occasione_info")
                if _giorni_fa((record.get("ultima_occasione_info") or {}).get("data")) < GIORNI_MEMORIA_AVVISI else None,
            }

            rilevazioni.append({
                "data": ora_iso(),
                "prezzo": prezzo,
                # chiavi corte: il file ha migliaia di righe
                "v": dettagli.get("venditore"),     # chi offriva il prezzo migliore
                "e": dettagli.get("espansione"),    # di quale edizione
                "a": assoluto["prezzo"] if assoluto else None,  # minimo anche senza CardTrader Zero
            })
            record["rilevazioni"] = rilevazioni[-MAX_PUNTI_STORICO:]
            controlla_rialzo(record, nome, chiave, record["rilevazioni"], soglie["rialzo"], avvisi)
            storico[chiave] = record

        except Exception as e:
            if token_rifiutato(e):
                meta["errore_globale"] = "CardTrader ha rifiutato il token durante il controllo: controlla CARDTRADER_TOKEN."
                print(meta["errore_globale"])
                break
            # un problema su una carta non blocca le altre
            print(f"   [errore] problema imprevisto con '{nome}', la salto: {e}")
            gestisci_fallimento(record, nome, str(e), avvisi)
            storico[chiave] = record

    # Rimuove dallo storico e dalla memoria le carte non più seguite (solo se
    # la lista non è vuota: una lista vuota per errore non deve cancellare tutto)
    if righe:
        chiavi_attuali = {chiave_carta(r) for r in righe}
        for chiave in set(storico) - chiavi_attuali:
            print(f"Rimuovo dallo storico (non più seguita): {chiave}")
            del storico[chiave]
        cache.tieni_solo([r["nome_carta"] for r in righe])
    else:
        print("[avviso] la lista carte risulta vuota: salto la pulizia dello storico per sicurezza")

    meta["durata_s"] = round(time.time() - inizio)
    meta["condizioni_viste"] = dict(CONDIZIONI_VISTE.most_common())
    meta["offerte_scartate"] = dict(OFFERTE_SCARTATE.most_common())
    meta["esempi_firmate"] = list(ESEMPI_FIRMATE)
    if OFFERTE_SCARTATE:
        print("Offerte scartate:", ", ".join(f"{c} {n}" for c, n in OFFERTE_SCARTATE.most_common()))
    if CONDIZIONI_VISTE:
        print("Condizioni delle offerte viste:", ", ".join(f"{c} {n}" for c, n in CONDIZIONI_VISTE.most_common()))
    salva_storico(storico)
    varianti = scrivi_dati_app(storico, righe, soglie_predefinite, meta)
    if token_ok:
        riepilogo_settimanale(varianti, righe, cache)
    cache.salva()
    print(f"Fatto in {meta['durata_s']} secondi.")


if __name__ == "__main__":
    main()
