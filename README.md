# CardTrader Price Watch

Controlla periodicamente il prezzo di una lista di carte Magic su CardTrader
e manda una notifica Telegram quando il prezzo scende oltre una soglia.

## ⚠️ Struttura a due repository (codice pubblico + dati privati)

Questo progetto è pensato per stare in un **repository pubblico** (per non
avere il limite di minuti Actions dei repository privati), ma i tuoi dati
(che carte segui, i prezzi trovati) restano in un **secondo repository
privato separato**, così nessuno può vederli.

### Se stai partendo da zero

1. Crea un repository **pubblico** nuovo (es. `MtG_Buy_Bot`) e caricaci
   tutti i file di questo progetto **tranne** la cartella `data/` (che qui
   non esiste nemmeno, è normale: arriva dal secondo repository).
2. Crea un repository **privato** separato (es. `MtG_Buy_Bot_data`), vuoto
   o con dentro solo un file `price_history.json` con contenuto `{}` e un
   `telegram_offset.json` con contenuto `{}`.
3. In ciascuno dei tre file dentro `.github/workflows/` del repository
   pubblico, sostituisci `TUO_UTENTE/NOME_REPO_DATI_PRIVATO` con il nome
   reale del repository privato appena creato (es. `alfreedom96/MtG_Buy_Bot_data`).
4. Crea un Personal Access Token fine-grained limitato **solo** al
   repository privato dei dati, con permesso "Contents: Read and write"
   (stessa procedura già vista per gli altri token di questo progetto).
5. Nel repository **pubblico**, aggiungi questo token come secret con nome
   `DATA_REPO_TOKEN` (oltre agli altri secret già visti: `CARDTRADER_TOKEN`,
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SHEET_CSV_URL`, e
   opzionalmente `HEALTHCHECKS_URL`).

### Se stai migrando da un repository già esistente (privato, con dati dentro)

**Non limitarti a rendere pubblico il repository attuale**: la sua
cronologia di commit contiene già lo storico prezzi con nomi di carte
reali, e diventerebbe visibile a chiunque per sempre (git non dimentica,
anche cancellando il file oggi resterebbe nei commit passati).

1. Copia il contenuto attuale di `data/price_history.json` e
   `data/telegram_offset.json` dal vecchio repository: ti servirà per non
   perdere lo storico raccolto finora.
2. Crea il repository **privato** dei dati (punto 2 sopra) e incolla lì
   dentro quei due file, con il loro contenuto reale (non `{}`).
3. Crea un repository **pubblico** nuovo di zecca (non riusare quello
   vecchio: avrebbe comunque la cronologia compromessa) e caricaci il
   codice aggiornato di questo progetto, seguendo i punti 1, 3, 4 e 5 della
   sezione sopra.
4. Aggiorna il webhook Telegram (`setWebhook`) e il cronjob su
   cron-job.org perché puntino al **nuovo** repository pubblico (l'URL
   dell'API GitHub cambia, dato che cambia il nome/percorso del repository).
5. Quando sei sicuro che tutto funzioni sul nuovo repository, puoi
   archiviare o eliminare quello vecchio.

## Struttura del progetto

- `common.py` — funzioni condivise (API CardTrader/Scryfall, storico, Telegram).
- `check_prices.py` — controllo prezzi, eseguito ogni ora.
- `send_digest.py` — riepilogo di tutte le carte, eseguito ogni settimana (e disponibile a comando).
- `bot_commands.py` — ascolta i comandi che scrivi al bot (`/grafico`, `/riepilogo`, `/help`), eseguito ogni 10 minuti.
- tre workflow in `.github/workflows/` che schedulano i tre script sopra,
  ciascuno dei quali scarica anche il repository dati privato in `data/`
  prima di partire.

## Come funziona

1. Ogni ora, GitHub Actions esegue `check_prices.py`.
2. Lo script legge la lista carte da un Google Sheet pubblicato come CSV.
3. Per ogni carta, trova tutte le edizioni tramite Scryfall e interroga le
   API di CardTrader per trovare il prezzo più basso che rispetta i filtri
   (lingua, condizione Mint/Near Mint/Slightly Played, foil, venduto tramite CardTrader Zero).
   Se non trova nulla nelle condizioni richieste ma il mercato non è vuoto,
   te lo segnala comunque a scopo informativo (senza notificarlo come occasione).
4. Salva il prezzo trovato nello storico completo di quella carta (dentro
   `data/price_history.json`, nel repository stesso).
5. Appena ci sono almeno 5 rilevazioni storiche, calcola in che **percentile**
   si trova il prezzo attuale rispetto a tutte le rilevazioni passate (es.
   percentile 10 = il prezzo attuale è tra il 10% più basso mai visto) e la
   **tendenza recente** (in calo / stabile / in risalita).
6. Se il prezzo è sotto la soglia di percentile impostata **e** non era già
   stato segnalato a un prezzo uguale o inferiore, manda un messaggio
   Telegram con prezzo, percentile, media storica, minimo storico, numero
   di offerte trovate e tendenza. In questo modo non vieni avvisato ogni ora
   per lo stesso prezzo basso, ma solo quando scende ulteriormente.
7. Se una carta fallisce per 10 controlli consecutivi (nome non trovato,
   nessuna offerta, errore di rete persistente), ricevi un avviso tecnico
   separato — segno che probabilmente c'è un problema di configurazione da
   controllare, non un'informazione sul prezzo.
8. Una volta a settimana (o quando vuoi, scrivendo `/riepilogo` al bot),
   ricevi un riepilogo con prezzo attuale, media, minimo e massimo storico
   di tutte le carte monitorate.
9. In qualsiasi momento puoi scrivere `/grafico Nome Carta` al bot per
   ricevere un grafico dell'andamento del prezzo nel tempo.

## Setup (una tantum)

### 1. Crea il Google Sheet con la lista carte

Crea un foglio Google con queste colonne (l'intestazione, prima riga):

| nome_carta      | lingua   | foil       | percentile_soglia |
|-----------------|----------|------------|--------------------|
| Lightning Bolt  | entrambe | si         | 15                 |
| Black Lotus     | en       | no         | 5                  |
| Sol Ring        |          |            |                    |

- **nome_carta**: obbligatoria, nome esatto in inglese della carta.
- **lingua**: `it`, `en`, o `entrambe` (default `entrambe`: prende la più economica tra le due).
- **foil**: `si`, `no`, o `qualsiasi` (default `si`).
- **percentile_soglia**: sotto quale percentile storico considerare il prezzo "basso" per questa carta (se vuota, usa il default, 15). Un valore più basso (es. 5) rende la notifica più rara ed esigente; un valore più alto (es. 25) la rende più frequente.

Poi: **File → Condividi → Pubblica sul web**, scegli il foglio giusto,
formato **CSV**, e copia il link generato (finisce con `output=csv`).

### 2. Ottieni il token API di CardTrader

Vai su cardtrader.com → impostazioni del profilo → sezione API/sviluppatori,
e genera un token. Serve un account CardTrader (gratuito).

### 3. Crea un bot Telegram

1. Su Telegram cerca **@BotFather**, invia `/newbot` e segui le istruzioni.
   Otterrai un **token** tipo `123456:ABC-DEF...`.
2. Scrivi un messaggio qualsiasi al tuo nuovo bot (per attivare la chat).
3. Apri nel browser:
   `https://api.telegram.org/bot<IL_TUO_TOKEN>/getUpdates`
   e cerca il campo `"chat":{"id": ...}` per trovare il tuo **chat_id**.

### 4. Crea il repository GitHub e carica questi file

1. Crea il repository **pubblico** del codice e il repository **privato**
   dei dati come descritto nella sezione "Struttura a due repository" in
   cima a questo README.
2. Nel repository pubblico, carica tutti i file di questo progetto
   (mantenendo la struttura delle cartelle, inclusi i tre file dentro
   `.github/workflows/`: se l'upload da browser non accetta cartelle che
   iniziano con il punto, usa "Add file → Create new file" e scrivi il
   percorso completo, es. `.github/workflows/check_prices.yml`, nel campo
   nome file). Ricorda di sostituire `TUO_UTENTE/NOME_REPO_DATI_PRIVATO`
   con il nome reale del repository privato in tutti e tre i workflow.
3. Sempre nel repository pubblico, vai su **Settings → Secrets and
   variables → Actions** e crea questi "Repository secrets":
   - `CARDTRADER_TOKEN` — il token ottenuto al punto 2
   - `TELEGRAM_BOT_TOKEN` — il token del bot ottenuto al punto 3
   - `TELEGRAM_CHAT_ID` — il chat_id ottenuto al punto 3
   - `SHEET_CSV_URL` — il link CSV ottenuto al punto 1
   - `DATA_REPO_TOKEN` — il token con accesso al repository dati privato
     (vedi sezione "Struttura a due repository")

### 5. Prova

Vai su **Actions** nel repository: dovresti vedere tre workflow ("Controllo
prezzi CardTrader", "Riepilogo settimanale CardTrader", "Comandi Telegram
CardTrader"). Lanciali manualmente uno alla volta con **Run workflow** per
verificare che funzionino, controllando i log. Da lì in poi partiranno da
soli secondo la loro programmazione. Dopo aver lanciato "Comandi Telegram"
almeno una volta, prova a scrivere `/help` al bot: entro 10 minuti (o subito,
rilanciando il workflow a mano) dovresti ricevere la lista dei comandi.

## Comandi del bot Telegram

Scrivendo al bot (rispondono entro pochi secondi se hai configurato il
webhook — vedi sezione dedicata più sotto — altrimenti entro un'ora con il
solo fallback schedulato, o subito se rilanci a mano il workflow):

- `/riepilogo` — riepilogo immediato di tutte le carte monitorate.
- `/grafico Nome Carta` — grafico dell'andamento storico del prezzo (il nome
  deve corrispondere a quello scritto nel foglio Google).
- `/help` — elenco dei comandi.

## Personalizzazioni

- **Frequenza**: modifica la riga `cron` in
  `.github/workflows/check_prices.yml`. Il formato è quello standard cron
  (minuto ora giorno mese giorno-settimana), sempre in orario UTC.
- **Soglia di percentile di default**: cambia `DEFAULT_PERCENTILE_SOGLIA` nel file
  workflow, oppure impostala per singola carta nel foglio Google.

## Trigger esterno affidabile per il controllo prezzi

Lo scheduler interno di GitHub Actions (`schedule:` nel workflow) non è
affidabile per esecuzioni frequenti: può saltare esecuzioni per ore, anche
con un cron impostato su base oraria (è un limite noto della piattaforma,
non un bug di questo progetto). La soluzione è far partire il workflow
dall'esterno, con un servizio di scheduling dedicato, usando lo stesso
principio del webhook Telegram: chiamare direttamente l'endpoint di
GitHub che avvia il workflow (`workflow_dispatch`), bypassando lo
scheduler interno.

### 1. Riusa (o crea) un token GitHub

Puoi riusare lo stesso Personal Access Token creato per il Cloudflare
Worker (sezione precedente), se copre già questo repository con permesso
"Actions: Read and write". Altrimenti creane uno nuovo con la stessa
procedura.

### 2. Crea un account su cron-job.org

1. Vai su [cron-job.org](https://cron-job.org) e crea un account gratuito.
2. Crea un nuovo cronjob ("Create cronjob").
3. **URL**:
   ```
   https://api.github.com/repos/utente/nome-repository/actions/workflows/check_prices.yml/dispatches
   ```
   (sostituisci `utente/nome-repository` con il tuo, es. `alfreedom96/MtG_Buy_Bot`)
4. **Method**: `POST`
5. **Schedule**: ogni ora (o l'intervallo che preferisci).
6. Nella sezione "Advanced" / "Headers", aggiungi:
   - `Authorization: Bearer IL_TUO_TOKEN_GITHUB`
   - `Accept: application/vnd.github+json`
   - `Content-Type: application/json`
   - `User-Agent: cron-job-org-cardtrader`
7. Nel campo del **body** della richiesta, inserisci:
   ```json
   {"ref": "main"}
   ```
8. Salva e attiva il cronjob.

### 3. Prova

Su cron-job.org puoi far partire il cronjob manualmente ("Execute now") per
testarlo subito: dovresti vedere una risposta con stato `204` (successo,
nessun contenuto), e su GitHub → Actions dovrebbe comparire una nuova
esecuzione di "Controllo prezzi CardTrader" partita "by cron-job.org" (o
comunque tramite `workflow_dispatch`), quasi immediatamente. Se la
risposta è `401` o `403`, il token non ha i permessi giusti; se è `404`,
controlla di aver scritto correttamente `utente/nome-repository`.

Da qui in poi, cron-job.org diventa il "motore" principale che fa partire
il controllo prezzi con puntualità, mentre lo schedule interno di GitHub
resta attivo come rete di sicurezza più rada (ogni 4 ore).

## Monitoraggio esterno (sapere se lo script si ferma)

GitHub esegue il workflow secondo la programmazione, ma non ti avvisa se per
qualche motivo smette di girare (es. i 60 giorni di inattività del
repository, o un problema di account). Per essere sicuro, puoi collegare un
servizio gratuito di monitoraggio "a battito cardiaco":

1. Vai su [healthchecks.io](https://healthchecks.io) e crea un account gratuito.
2. Crea un nuovo check ("Add Check"), imposta il **periodo atteso** a 1 ora
   (o alla frequenza che usi) e una **grace time** di almeno 15-20 minuti
   (per assorbire i normali ritardi del cron di GitHub).
3. Copia l'URL del ping (tipo `https://hc-ping.com/xxxxxxxx-xxxx-...`).
4. Su GitHub, aggiungi un nuovo secret `HEALTHCHECKS_URL` con quell'URL.
5. (Opzionale) Su healthchecks.io, nella sezione "Integrations", puoi
   collegare Telegram o l'email per ricevere l'avviso se lo script smette
   di dare segni di vita.

Da questo momento, ogni esecuzione riuscita di `check_prices.py` manda un
segnale a healthchecks.io; se il segnale non arriva per più del tempo
configurato, sarà healthchecks.io stesso ad avvisarti — non serve più
controllare a mano la pagina Actions per sapere se tutto sta girando bene.

## Risposta istantanea ai comandi Telegram (webhook)

Di base i comandi del bot vengono controllati ogni ora (fallback). Per
avere una risposta quasi immediata (pochi secondi invece di aspettare il
prossimo controllo programmato), si collega un piccolo "ponte" gratuito
tra Telegram e GitHub, usando Cloudflare Workers.

### 1. Crea un token GitHub per far partire il workflow da remoto

1. Vai su GitHub → icona del profilo → **Settings → Developer settings →
   Personal access tokens → Fine-grained tokens → Generate new token**.
2. Limita il token a questo solo repository ("Only select repositories").
3. In "Repository permissions", imposta **Actions: Read and write**.
4. Genera e copia il token (non sarà più visibile dopo).

### 2. Crea il Cloudflare Worker

1. Vai su [workers.cloudflare.com](https://workers.cloudflare.com) e crea
   un account gratuito.
2. Crea un nuovo Worker (es. "Create Application" → "Create Worker"), dagli
   un nome, poi apri "Edit code" (Quick Edit).
3. Cancella il codice di esempio e incolla il contenuto di
   `cloudflare_worker.js` (incluso in questo progetto).
4. Salva e pubblica ("Save and Deploy"). Annota l'URL del Worker (tipo
   `https://nome-worker.tuo-account.workers.dev`).
5. Vai nelle impostazioni del Worker → **Settings → Variables** e aggiungi:
   - `TELEGRAM_CHAT_ID` — il tuo chat id (testo semplice)
   - `GITHUB_REPO` — `utente/nome-repository` (es. `alfreedom96/MtG_Buy_Bot`)
   - `GITHUB_TOKEN` — il token creato al punto 1 (salvalo come variabile
     "criptata"/secret, non in chiaro)

### 3. Collega Telegram al Worker

Apri nel browser (sostituendo i due segnaposto):

```
https://api.telegram.org/bot<IL_TUO_TOKEN_BOT>/setWebhook?url=<URL_DEL_WORKER>
```

Dovresti vedere `{"ok":true,"result":true,"description":"Webhook was set"}`.

Per controllare in ogni momento lo stato del webhook (utile se qualcosa
smette di funzionare):

```
https://api.telegram.org/bot<IL_TUO_TOKEN_BOT>/getWebhookInfo
```

### 4. Prova

Scrivi `/help` al bot: dovresti ricevere una risposta entro pochi secondi
(il tempo che GitHub Actions impiega ad avviare il job, non istantaneo al
100% ma molto vicino). Se non arriva nulla, controlla su GitHub → Actions
se è partita una nuova esecuzione di "Comandi Telegram CardTrader": se sì,
guardane i log; se no, il problema è nel Worker o nel webhook (controlla
`getWebhookInfo` per eventuali errori riportati da Telegram).

**Nota**: una volta impostato un webhook, Telegram smette di consegnare i
messaggi al vecchio sistema di polling (`getUpdates`) finché il webhook
resta attivo. Lo schedule di fallback orario del workflow resta comunque
utile come rete di sicurezza, ma normalmente non troverà nulla da fare.

## Limiti da conoscere

- CardTrader non ha una ricerca "per nome su tutte le edizioni": lo script
  usa Scryfall per scoprire le edizioni e poi interroga CardTrader
  edizione per edizione. Per carte con nomi particolari (doppio-fronte,
  split card) c'è un secondo tentativo di ricerca più permissivo, ma in
  rari casi il nome può comunque non corrispondere: in tal caso comparirà
  un avviso nei log, e dopo 10 controlli falliti di fila ricevi un avviso
  su Telegram.
- Le API di CardTrader restituiscono solo le 25 offerte più economiche per
  ogni combinazione lingua/foil interrogata: in casi estremi (tantissime
  offerte più economiche ma con condizione non accettata) l'offerta
  migliore potrebbe non comparire. Per l'uso tipico (poche decine di carte)
  non dovrebbe essere un problema pratico.
- Il confronto di prezzo è fatto solo in EUR.
- Servono almeno 5 rilevazioni storiche per una carta prima che lo script
  inizi a valutarne il percentile: nei primi giorni vedrai solo "ancora in
  fase di raccolta dati" nei log, è normale.
- Il link diretto alla pagina del prodotto nelle notifiche è ricostruito a
  partire dal nome della carta e dell'espansione (CardTrader non lo fornisce
  nell'API): funziona nella maggior parte dei casi, ma per nomi con
  caratteri particolari potrebbe occasionalmente puntare a una pagina
  inesistente. In tal caso, cerca la carta a mano sul sito.
- Lo storico si azzera se cancelli `data/price_history.json`.
- **Minuti GitHub Actions**: con controllo prezzi ogni ora (~1 minuto a
  esecuzione) più il controllo comandi ogni 10 minuti (pochi secondi ciascuno,
  quando non ci sono messaggi) più il riepilogo settimanale, il consumo totale
  resta ben sotto i 2.000 minuti/mese gratuiti anche su un repository privato.
  Se in futuro aggiungi molte più carte e i run diventano più lunghi, tienilo
  d'occhio in **Settings → Billing → Actions**.
- Se per 60 giorni consecutivi non c'è nessun commit sul repository, GitHub
  disattiva automaticamente i workflow schedulati per inattività: basta un
  commit qualsiasi per riattivarli.
