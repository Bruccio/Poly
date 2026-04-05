# 🐋 Polymarket Whale Tracker v2

Bot automatico che monitora i mercati con più volume su [Polymarket](https://polymarket.com), analizza i movimenti dei **grandi investitori (>$100k)** con Claude AI e ti avvisa via **Telegram** e **Email** quando c'è qualcosa che vale la pena copiare.

Include **leaderboard persistente**, **filtro wash trading**, **algoritmo self-improving** e **dashboard web** gratuita su GitHub Pages.

---

## Come funziona

```
Ogni giorno alle 09:00 e 21:00 (ora italiana) — automatico, senza fare nulla
        ↓
Scarica la leaderboard dei top trader da Polymarket /breaking
        ↓
Controlla le resolution dei mercati COPY precedenti (self-improving)
        ↓
Recupera i mercati con volume >$100k, filtra sport + wash trader
        ↓
Claude AI analizza i top 10 movimenti con contesto whale + track record
        ↓
Aggiorna whale_state.json nel repo (leaderboard + accuracy + segnali)
        ↓
Invia Telegram + Email con analisi, leaderboard e track record
```

---

## Setup (una volta sola)

### Step 1 — Aggiungi i Secrets su GitHub

Vai su **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Valore |
|--------|--------|
| `ANTHROPIC_API_KEY` | La tua API key Anthropic ([console.anthropic.com](https://console.anthropic.com)) |
| `TELEGRAM_BOT_TOKEN` | Token del tuo bot Telegram |
| `TELEGRAM_CHAT_ID` | Il tuo User ID Telegram (cercalo su @userinfobot) |
| `GMAIL_APP_PASSWORD` | Vedi Step 2 ↓ |

---

### Step 2 — Crea la Gmail App Password (per l'email)

1. Vai su [myaccount.google.com](https://myaccount.google.com)
2. **Sicurezza** → **Verifica in due passaggi** → abilitala se non l'hai già
3. Torna in **Sicurezza** → cerca **"Password per le app"**
4. Crea una nuova app password (nome: "Polymarket Bot")
5. Copia i 16 caratteri (es. `abcd efgh ijkl mnop`)
6. Aggiungila come secret `GMAIL_APP_PASSWORD` su GitHub (senza spazi)

---

### Step 3 — Mergia il branch

1. Vai su GitHub → **Pull requests**
2. Apri il PR → **"Merge pull request"** → **"Confirm merge"**

Fatto. Il bot parte da solo alle 09:00 e 21:00.

---

### Step 4 — Attiva la Dashboard Web (opzionale, gratis)

1. Vai su **Settings → Pages**
2. Source: **Deploy from branch** → branch: `main` → folder: `/ (root)`
3. Salva → dopo 1-2 minuti la dashboard è online su `https://bruccio.github.io/Poly`

La pagina si aggiorna automaticamente ad ogni run del bot.

---

### Step 5 — Test manuale (opzionale)

1. **Actions** → **"Whale Tracker v2"** → **"Run workflow"**
2. Dopo ~2 minuti ricevi Telegram + Email ✅
3. Il file `whale_state.json` viene committato nel repo con i dati aggiornati

---

## Parametri

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `MIN_WHALE_SIZE` | `100000` | Volume minimo in USDC — solo Alpha/Insider (>$100k) |
| `MAX_WHALES` | `10` | Mercati analizzati per run |
| `ONLY_NOTIFY_ON_COPY` | `false` | `true` = notifica solo se c'è almeno un COPY |

---

## Funzionalità v2

### 🏆 Leaderboard Persistente
Ad ogni run il bot scarica i top trader da `data-api.polymarket.com` e assegna un **trust score** (0-100) a ogni whale basato su profitto e volume. Le whale con trust score più alto hanno priorità nell'analisi.

### 🚿 Filtro Wash Trading
Il bot controlla se un wallet ha pattern sospetti (compra e vende la stessa posizione ripetutamente). I wash trader vengono automaticamente esclusi dal report.

### 📈 Self-Improving Algorithm
Ogni mercato con verdetto COPY viene tracciato in `whale_state.json`. Quando il mercato si risolve, il bot aggiorna automaticamente il **track record di accuracy** del sistema. Nel tempo il prompt Claude include questa metrica per dare consigli più calibrati.

### 💬 Reddit Insights
Ogni 10 run il bot controlla i post caldi di **r/Polymarket** cercando strategie, mercati sottoprezzati e segnali. I risultati vengono inclusi nel contesto dell'analisi Claude.

### 🌐 Dashboard Web
Pagina statica su GitHub Pages che mostra leaderboard, segnali COPY con esito, accuracy storica e insight Reddit. **Zero costi aggiuntivi.**

---

## Cosa ricevi

### Telegram
```
🐋 Grandi Mosse su Polymarket
05/04/2026 09:00

Analizzati 10 mercati non-sportivi da >$100k.
📊 Track record: 67% accuracy (3 segnali risolti)
2 meritano attenzione. 👇

✅ DA VALUTARE
📌 Will the Fed cut rates in June 2026?
🐋 beachboy4 🟢 Trust: 82/100
💡 Grande whale con track record verificato punta su taglio tassi
📖 Un investitore da $180k ha comprato YES a 28¢...
🟡 Rischio: 4/10

─────────────────────
🏆 Top Whale Tracker
1. HorizonSplendidView +$4,598,457 (trust 95)
2. beachboy4 +$3,762,306 (trust 82)
...
```

### Email
Stessa analisi in formato HTML con tabella leaderboard, inviata a `brunoricciohsl@gmail.com`.

---

## Struttura file

```
whale_tracker.py          Script principale (v2)
whale_state.json          Stato persistente — creato/aggiornato automaticamente
index.html                Dashboard web (GitHub Pages)
requirements.txt          Dipendenze Python (solo requests)
.github/
  workflows/
    main.yml              Workflow GitHub Actions — cron 09:00 e 21:00
README.md                 Questo file
```

---

## Problemi comuni

| Problema | Soluzione |
|----------|-----------|
| Nessun messaggio Telegram | Verifica TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID |
| Nessuna email | Verifica GMAIL_APP_PASSWORD nei secrets |
| Ancora scommesse sportive | Il filtro è molto aggressivo — se passa qualcosa, segnalalo |
| Dashboard non si aggiorna | Aspetta il run successivo — il bot commette whale_state.json dopo ogni run |
| Accuracy sempre N/D | Normale all'inizio — si popola man mano che i mercati COPY si risolvono (settimane/mesi) |
| Workflow va in timeout | MAX_WHALES troppo alto — default 10 è già ok |
| Workflow parte in ritardo | GitHub Actions ha ritardi fino a 15 min sulle schedule — normale |

---

> ⚠️ Il trading su Polymarket comporta rischi. Non investire mai denaro che non puoi permetterti di perdere. Questo tool è a scopo informativo — la decisione finale è sempre tua.
