# CardTrader Price Watch

Controlla periodicamente il prezzo di una lista di carte Magic su CardTrader
e manda una notifica Telegram quando il prezzo scende oltre una soglia
statistica, con la possibilità di chiedere grafici e riepiloghi a comando.

## ⚠️ Struttura a due repository (codice pubblico + dati privati)

Questo progetto è pensato per stare in un **repository pubblico** (per non
avere il limite di minuti Actions dei repository privati), ma i tuoi dati
(che carte segui, i prezzi trovati) restano in un **secondo repository
privato separato**, così nessuno può vederli.

### Se stai partendo da zero

1. Crea un repository **pubblico** nuovo (es. `MtG_Buy_Bot`) e caricaci
   tutti i file di questo progetto **tranne** la cartella `data/` (che qui
   non esiste nemmeno, è normale: arriva dal secondo repository).
2. Crea un repository **privato** separato (es. `MtG_Buy_Bot_data`), con
   dentro un file `price_history.json` con contenuto `{}` e un
   `telegram_offset.json` con contenuto `{}`.
3. In ciascuno dei tre file dentro `.github/workflows/` del repository
   pubblico, sostituisci `TUO_UTENTE/NOME_REPO_DATI_PRIVATO` con il nome
   reale del repository privato appena creato.
4. Crea un Personal Access Token fine-grained limitato **solo** al
   repository privato dei dati, con permesso "Contents: Read and write".
5. Nel repository **pubblico**, aggiungi questo token come secret con nome
   `DATA_REPO_TOKEN` (oltre agli altri secret: `CARDTRADER_TOKEN`,
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SHEET_CSV_URL`, e
   opzionalmente `HEALTHCHECKS_URL`).

### Se stai migrando da un repository già esistente (privato, con dati dentro)

**Non limitarti a rendere pubblico il repository attuale**: la sua
cronologia di commit contiene già lo storico prezzi con nomi di carte
reali, e diventerebbe visibile a chiunque per sempre (git non dimentica,
anche cancellando il file oggi resterebbe nei commit passati).

1. Copia il contenuto attuale di `data/price_history.json` e
   `data/telegram_offset.json` dal vecchio repository.
2. Crea il repository privato dei dati e incollaci dentro quei due file,
   con il loro contenuto reale (non `{}`).
3. Crea un repository pubblico nuovo di zecca (non riusare quello vecchio)
   e caricaci il codice, seguendo i punti sopra.
4. Aggiorna il webhook Telegram, la variabile `GITHUB_REPO` sul Cloudflare
   Worker, e l'URL nel cronjob su cron-job.org perché puntino al nuovo
   repository pubblico.
5. Quando sei sicuro che tutto funzioni, archivia o elimina il vecchio
   repository (Settings → Danger Zone → Archive/Delete).

## Struttura del progetto

- `common.py` — funzioni condivise (API CardTrader/Scryfall, storico, Telegram).
- `check_prices.py` — controllo prezzi, eseguito ogni ora.
- `send_digest.py` — riepilogo di tutte le carte, eseguito ogni settimana (e disponibile a comando).
- `bot_commands.py` — ascolta i comandi che scrivi al bot (`/grafico`, `/riepilogo`, `/help`).
- `cloudflare_worker.js` — codice del ponte Telegram → GitHub per risposte istantanee ai comandi.
- tre workflow in `.github/workflows/` che eseguono gli script sopra,
  ciascuno dei quali scarica anche il repository dati privato in `data/`
  prima di partire.

- `pwa/` — l'app per il telefono (PWA, Progressive Web App): `index.html`, `app.js`, service worker, icone.
- `worker/worker_src.js` — sorgente del Cloudflare Worker (API dell'app + webhook Telegram).
- `build_worker.py` — unisce `worker/` e `pwa/` in `cloudflare_worker.js`, l'unico file da incollare su Cloudflare.

## Come funziona

1. Ogni ora (tramite cron-job.org, vedi sezione dedicata), GitHub Actions esegue `check_prices.py`.
2. Lo script legge la lista carte da un Google Sheet pubblicato come CSV.
3. Per ogni carta, trova tutte le edizioni tramite Scryfall e interroga le
   API di CardTrader per trovare il prezzo più basso che rispetta i filtri
   (lingua, condizione Mint/Near Mint/Slightly Played, foil, venduto tramite
   CardTrader Zero). Se non trova nulla nelle condizioni richieste ma il
   mercato non è vuoto, te lo segnala comunque a scopo informativo.
4. Salva il prezzo trovato nello storico completo di quella carta (nel
   repository dati privato).
5. Appena ci sono almeno 5 rilevazioni storiche, calcola in che **percentile**
   si trova il prezzo attuale rispetto a tutte le rilevazioni passate e la
   **tendenza recente** (in calo / stabile / in risalita).
6. Se il prezzo è sotto la soglia di percentile impostata **e** non era già
   stato segnalato a un prezzo uguale o inferiore, manda un messaggio
   Telegram con prezzo, percentile, media storica, minimo storico, numero
   di offerte trovate, venditore e un link diretto alla pagina della carta.
7. Se una carta fallisce per 10 controlli consecutivi, ricevi un avviso
   tecnico separato.
8. Le carte rimosse dal foglio Google vengono rimosse anche dallo storico
   al controllo successivo.
9. Una volta a settimana (o a comando con `/riepilogo`), ricevi un
   riepilogo compatto di tutte le carte monitorate.
10. In qualsiasi momento puoi scrivere `/grafico Nome Carta` al bot per
    ricevere un grafico dell'andamento del prezzo nel tempo (una immagine
    per ogni variante lingua/foil che tieni per quella carta).

## Setup (una tantum)

### 1. Crea il Google Sheet con la lista carte

Crea un foglio Google con queste colonne (l'intestazione, prima riga):

| nome_carta      | lingua   | foil       | percentile_soglia | soglia_occasione | edizioni_escluse | edizioni_incluse |
|-----------------|----------|------------|-------------------|------------------|------------------|------------------|
| Lightning Bolt  | entrambe | si         | 15                | 10               | mh3; cmm         |                  |
| Black Lotus     | en       | no         | 5                 |                  |                  | lea; leb         |
| Sol Ring        |          |            |                   |                  |                  |                  |

- **nome_carta**: obbligatoria, nome esatto in inglese della carta.
- **lingua**: `it`, `en`, o `entrambe` (default `entrambe`).
- **foil**: `si`, `no`, o `qualsiasi` (default `si`).
- **percentile_soglia**: sotto quale percentile storico considerare il
  prezzo "basso" per questa carta (default 15 se vuota).
- **soglia_ribasso** (opzionale): per l'avviso "prezzo basso" il prezzo deve
  essere, oltre che sotto il percentile, almeno questo % sotto la mediana
  storica (il valore centrale dei prezzi registrati). Evita avvisi per
  variazioni di pochi centesimi quando il prezzo è fermo da tempo (default 5).
- **soglia_occasione** (opzionale): sconto minimo in % del prezzo più basso
  rispetto alla **media delle 10 offerte più economiche** (minimo incluso)
  per segnalare un'"occasione" 🔥 (default 10 se vuota). Le offerte "fuori scala" verso l'alto (oltre 1,5 volte
  la mediana delle 10 più economiche) sono escluse dalla media. Serve a notare le
  carte vendute a un prezzo "fuori mercato". Servono almeno 5 offerte valide
  per il confronto. Non ripete la notifica per la stessa offerta; si
  riarma quando l'occasione sparisce.
- **edizioni_escluse** (opzionale): edizioni CardTrader da ignorare per
  quella carta, separate da `;`. Puoi scrivere il codice (`pspm`), il nome
  (`Marvel's Spider-Man Promos`) o incollare il link della pagina
  dell'edizione su CardTrader.
- **edizioni_incluse** (opzionale): se compilata, vengono considerate
  **solo** queste edizioni (anche quelle che esistono solo su CardTrader,
  come `The Hobbit Collectors`). Se un'edizione è in entrambe, vince
  l'esclusione.
  Il filtro è applicato prima di interrogare CardTrader, quindi anche la
  media delle 10 offerte riguarda solo le edizioni scelte (e il prezzo
  storico/percentile ne tiene conto: cambiando i filtri la serie storica
  di quella riga può risultare non omogenea).

Poi: **File → Condividi → Pubblica sul web**, formato **CSV**, copia il link.

### 2. Ottieni il token API di CardTrader

Account CardTrader → impostazioni → sezione API/sviluppatori → genera token.

### 3. Crea un bot Telegram

1. Su Telegram cerca **@BotFather**, invia `/newbot` e segui le istruzioni.
2. Scrivi un messaggio al tuo bot per attivare la chat.
3. Apri `https://api.telegram.org/bot<TOKEN>/getUpdates` e cerca `"chat":{"id": ...}`.

### 4. Crea i repository e i secret

Segui la sezione "Struttura a due repository" in cima a questo README, poi
su **Settings → Secrets and variables → Actions** del repository pubblico
aggiungi tutti i secret elencati lì.

### 5. Prova

Vai su **Actions**, lancia manualmente i tre workflow con **Run workflow**
e controlla i log.

## Risposta istantanea ai comandi Telegram (webhook)

Di base i comandi vengono controllati ogni ora (fallback). Per una risposta
in pochi secondi, si collega Cloudflare Workers come ponte:

### 1. Crea un token GitHub per far partire il workflow da remoto

Settings → Developer settings → Personal access tokens → Fine-grained
tokens → limita al repository pubblico, permesso "Actions: Read and write".

### 2. Crea il Cloudflare Worker

1. [workers.cloudflare.com](https://workers.cloudflare.com) → account gratuito.
2. Crea un Worker ("Start with Hello World!" se non vedi un'opzione più diretta).
3. Apri l'editor del codice, incolla il contenuto di `cloudflare_worker.js`.
4. Salva e pubblica. Annota l'URL del Worker.
5. Settings → Variables: aggiungi `TELEGRAM_CHAT_ID`, `GITHUB_REPO`
   (`utente/nome-repo-pubblico`), `GITHUB_TOKEN` (come secret criptato).

### 3. Collega Telegram al Worker

```
https://api.telegram.org/bot<TOKEN_BOT>/setWebhook?url=<URL_DEL_WORKER>
```

Per controllare lo stato: `https://api.telegram.org/bot<TOKEN_BOT>/getWebhookInfo`

### 4. Prova

Scrivi `/help` al bot: risposta entro pochi secondi.

**Nota**: con un webhook attivo, Telegram rifiuta le chiamate al vecchio
sistema di polling (`getUpdates`, errore 409) — è gestito automaticamente
dal codice, non causa errori.

## Trigger esterno affidabile per il controllo prezzi

Lo scheduler interno di GitHub Actions non è affidabile per esecuzioni
frequenti (può saltare ore di esecuzioni anche con cron orario, limite
noto della piattaforma). Soluzione: cron-job.org chiama direttamente
`workflow_dispatch`.

### 1. Riusa o crea un token GitHub

Come sopra, scope sul repository pubblico, permesso "Actions: Read and write".

### 2. Crea un account su cron-job.org

1. [cron-job.org](https://cron-job.org) → account gratuito → nuovo cronjob.
2. **URL**: `https://api.github.com/repos/utente/nome-repository/actions/workflows/check_prices.yml/dispatches`
3. **Method**: `POST`
4. **Schedule**: ogni ora.
5. **Headers**:
   - `Authorization: Bearer IL_TUO_TOKEN_GITHUB`
   - `Accept: application/vnd.github+json`
   - `Content-Type: application/json`
   - `User-Agent: cron-job-org-cardtrader`
6. **Body**: `{"ref": "main"}`
7. Salva e attiva.

### 3. Prova

"Execute now": dovresti vedere **204 No Content**. Se 401, il token/header
non è corretto (ricontrolla il prefisso `Bearer ` nel valore
dell'header Authorization). Se 404, controlla il nome del repository.

## Monitoraggio esterno (sapere se lo script si ferma)

1. [healthchecks.io](https://healthchecks.io) → account gratuito → nuovo check.
2. Periodo atteso: 1 ora, grace time: almeno 15-20 minuti.
3. Copia l'URL del ping, aggiungilo come secret `HEALTHCHECKS_URL`.
4. (Opzionale) Collega Telegram o email nelle "Integrations" del check.

Ogni esecuzione riuscita di `check_prices.py` manda un segnale; se manca
per troppo tempo, healthchecks.io ti avvisa.

## Comandi del bot Telegram

- `/riepilogo` — riepilogo compatto di tutte le carte monitorate.
- `/grafico Nome Carta` — grafico dell'andamento storico del prezzo (una
  immagine per ogni variante lingua/foil per quella carta).
- `/help` — elenco dei comandi. Anche **qualunque altro messaggio** mostra l'help, con bottoni cliccabili: 📊 Riepilogo e 📈 Grafico (elenco aggiornato delle carte, da toccare per ottenere i grafici di tutte le varianti).

## Limiti da conoscere

- CardTrader non ha una ricerca "per nome su tutte le edizioni": lo script
  usa Scryfall per scoprire le edizioni. Con nomi particolari (doppio-fronte,
  split card) c'è un secondo tentativo di ricerca più permissivo; se anche
  quello fallisce, dopo 10 controlli falliti di fila ricevi un avviso.
- Le API di CardTrader restituiscono solo le 25 offerte più economiche per
  ogni combinazione lingua/foil interrogata: in casi estremi l'offerta
  migliore potrebbe non comparire.
- Il confronto di prezzo è fatto solo in EUR.
- Servono almeno 5 rilevazioni storiche prima che lo script inizi a
  valutare il percentile.
- Il link diretto alla pagina prodotto è ricostruito euristicamente dal
  nome carta ed espansione: funziona nella maggior parte dei casi, ma può
  occasionalmente puntare a una pagina inesistente per nomi particolari.
- Con controlli molto frequenti e più workflow attivi, possono verificarsi
  scontri di push tra due esecuzioni quasi simultanee sullo stesso file:
  gestito con pull+retry automatico.


## Bottoni Telegram (Cloudflare Worker)

Il Worker deve ricevere anche i click sui bottoni (`callback_query`): usa il
file `cloudflare_worker.js` aggiornato. Facoltativo ma consigliato: aggiungi
al Worker la variabile `TELEGRAM_BOT_TOKEN` (come secret) per togliere subito
l'icona di caricamento dai bottoni cliccati. Ogni click avvia il workflow dei
comandi, quindi la risposta arriva in pochi secondi.


## App per il telefono (PWA)

L'app sostituisce il foglio Google: cerchi le carte, scegli lingua, finitura,
edizioni e soglie, vedi prezzi, grafici e l'offerta migliore con il link a
CardTrader. È una PWA (Progressive Web App): un sito che si installa sulla
schermata Home di Android e si comporta come un'app. È ospitata dal Cloudflare
Worker, che salva la lista carte in Cloudflare KV (Key-Value, un piccolo
archivio dati gratuito) e legge i prezzi da `app_data.json` nel repository dati
privato. Le notifiche restano su Telegram.

### Configurazione (una tantum)

1. **Cloudflare, archivio KV**: Storage & Databases → KV → Create → nome `price-watch`.
2. **Cloudflare, collega il KV al Worker**: apri il Worker → Bindings → Add binding →
   KV namespace → Variable name `DB`, namespace `price-watch` → Save.
3. **Cloudflare, variabili del Worker** (Settings → Variables and Secrets):
   - `APP_PASSWORD` (tipo Secret): una password lunga a tua scelta.
   - `DATA_REPO` (tipo Text): `tuo-utente/nome-repo-dati-privato`.
4. **GitHub, permesso di lettura dei dati**: il token salvato nel Worker come
   `GITHUB_TOKEN` deve poter leggere il repository dati. Su GitHub: foto profilo →
   Settings → Developer settings → Personal access tokens → Fine-grained tokens →
   apri il token → Edit → in Repository access aggiungi il repository dati →
   in Permissions imposta Contents: Read-only → Update. Il valore del token non
   cambia. In alternativa crea un token separato e salvalo nel Worker come `DATA_TOKEN`.
5. **Cloudflare, nuovo codice**: Edit code → incolla tutto `cloudflare_worker.js` → Deploy.
6. **Telefono**: apri in Chrome l'indirizzo del Worker (`https://....workers.dev`),
   inserisci la password, poi Menu → Importa dal foglio Google e incolla il link CSV.
   Infine menu di Chrome (⋮) → Installa app (o Aggiungi a schermata Home).
7. **GitHub, secret del repository del codice**: aggiungi `WORKER_URL` (l'indirizzo
   del Worker) e `APP_PASSWORD` (la stessa password). Da questo momento il
   controllo prezzi legge la lista dall'app e il foglio Google viene ignorato.
8. Nell'app premi il bottone di aggiornamento: dopo qualche minuto compaiono i prezzi.

### Notifiche nell'app

Gli avvisi (occasione 🔥, prezzo basso 💰, carta che continua a fallire ⚠️)
arrivano come notifiche push sul telefono, anche ad app chiusa. Toccando la
notifica si apre la scheda della carta.

- Si attivano dall'app: banner in alto oppure Menu → Notifiche → Attiva.
- Menu → Notifiche → "Avvisi anche su Telegram": Sì / No. Con No gli avvisi
  arrivano solo nell'app; i comandi del bot e il riepilogo settimanale restano su Telegram.
- Non serve configurare nulla: il Worker crea da solo le chiavi VAPID
  (Voluntary Application Server Identification, la "firma" di chi invia le
  notifiche) e le salva nel KV, insieme all'elenco dei telefoni registrati.
- Come funziona: GitHub Actions chiama `POST /api/notifica` sul Worker, che
  cifra il messaggio (standard Web Push) e lo consegna al servizio di
  notifiche di Google, che lo recapita al telefono.

### Modificare l'app

Dopo aver cambiato file in `pwa/` o `worker/`, esegui `python build_worker.py`
e incolla di nuovo `cloudflare_worker.js` su Cloudflare.


## Avvisi e dati aggiuntivi

| Avviso | Quando scatta | Impostazione per carta |
|---|---|---|
| 🎯 Prezzo obiettivo | il prezzo scende fino al valore scelto | `prezzo_obiettivo` (vuoto = nessuno) |
| 🔥 Occasione | il minimo è sotto la media delle 10 offerte più economiche | `soglia_occasione` (10%) |
| 💰 Prezzo basso | tra i più bassi mai visti e sotto la mediana storica | `percentile_soglia` (15), `soglia_ribasso` (5%) |
| 📈 Rialzo | livello delle ultime 6 ore oltre la mediana dei 3 giorni prima | `soglia_rialzo` (15%, 0 = mai) |
| 🆕 Ristampa | Scryfall elenca una nuova stampa (anche annunciata) | — |
| 🔒 Token | CardTrader rifiuta il token: un solo avviso, più uno quando torna a funzionare | — |

- **Cardmarket**: prezzo di riferimento preso da Scryfall (aggiornato una volta al
  giorno), per la versione più economica che rispetta finitura ed edizioni.
- **Tutti i venditori**: minimo su CardTrader anche senza CardTrader Zero (solo
  grafico e scheda; gli avvisi restano su CardTrader Zero).
- **Riepilogo settimanale**: notifica nell'app il lunedì dalle 8 (ora UTC).
- **Stato del sistema** (menu dell'app): ultimo aggiornamento, ultime esecuzioni,
  carte con problemi, scadenza dei token, telefoni registrati.
- **Memoria** (`data/cache_carte.json`): stampe Scryfall riscaricate ogni 6 ore,
  blueprint CardTrader ogni 7 giorni. Il workflow deve salvarla
  (`git add price_history.json app_data.json cache_carte.json`).
