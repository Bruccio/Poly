# 🐋 Polymarket Whale Tracker

Bot automatico che monitora i mercati con più volume su [Polymarket](https://polymarket.com), analizza i movimenti dei grandi investitori con Claude AI e ti avvisa via **Telegram** e **Email** quando c'è qualcosa che vale la pena copiare.

---

## Come funziona

```
Ogni giorno alle 09:00 e 21:00 (ora italiana) — automatico, senza fare nulla
        ↓
GitHub scarica i mercati Polymarket con più volume
        ↓
Claude AI analizza i top 8 movimenti
        ↓
Se trova opportunità → Telegram + Email con analisi e sizing consigliato
Se non trova nulla   → silenzio (nessuno spam)
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

Serve per mandare email da Gmail senza usare la password normale.

1. Vai su [myaccount.google.com](https://myaccount.google.com)
2. **Sicurezza** → **Verifica in due passaggi** → abilitala se non l'hai già
3. Torna in **Sicurezza** → cerca **"Password per le app"**
4. Crea una nuova app password (nome: "Polymarket Bot")
5. Copia i 16 caratteri che ti dà (es. `abcd efgh ijkl mnop`)
6. Aggiungila come secret `GMAIL_APP_PASSWORD` su GitHub (senza spazi)

---

### Step 3 — Mergia il branch

Se stai lavorando sul branch `claude/fix-polymarket-tracker-8YE0J`:
1. Vai su GitHub → **Pull requests**
2. Apri il PR → **"Merge pull request"** → **"Confirm merge"**

Fatto. Il bot parte da solo alle 09:00 e 21:00.

---

### Step 4 — Test manuale (opzionale)

Per verificare subito che tutto funzioni:
1. **Actions** → **"Whale Briefing Giornaliero"** → **"Run workflow"**
2. Dopo ~2 minuti ricevi Telegram + Email ✅

---

## Parametri

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `MIN_WHALE_SIZE` | `50000` | Volume minimo in USDC per considerare un mercato |
| `MAX_WHALES` | `8` | Mercati analizzati per run (8 = ~90 secondi) |
| `BANKROLL` | `10000` | Il tuo capitale — usato per calcolare il sizing |
| `ONLY_NOTIFY_ON_COPY` | `false` | `true` = notifica solo se c'è almeno un COPY |

---

## Cosa ricevi

### Telegram
```
🐋 Grandi Mosse su Polymarket
03/04/2026 09:00

Analizzati 8 movimenti da >$50k. 2 meritano attenzione.

✅ DA VALUTARE
📌 Will the Fed cut rates in May 2026?
💡 Sì, un grande investitore scommette con alta fiducia
📖 Un wallet da $85k ha puntato su un taglio dei tassi...
🟡 Rischio: 5/10  |  💰 Puoi mettere fino a $500

⏭ LASCIA PERDERE
📌 Will BTC reach $120k before June?
📖 Movimento speculativo senza basi solide...
🔴 Rischio: 8/10
```

### Email
Stessa analisi in formato HTML con grafica, inviata a `brunoricciohsl@gmail.com`.

---

## Struttura file

```
whale_tracker.py          Script principale (Polymarket + Claude + Telegram + Email)
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
| Nessun messaggio Telegram | Verifica i secrets TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID |
| Nessuna email | Crea la Gmail App Password (Step 2) e aggiungila come secret |
| Workflow va in timeout | MAX_WHALES troppo alto — tienilo a 8 o meno |
| "Nessuna opportunità" ogni volta | Normale se i mercati sono calmi — abbassa MIN_WHALE_SIZE a 25000 |
| Workflow parte in ritardo | GitHub Actions ha ritardi fino a 15 min sulle schedule — è normale |

---

> ⚠️ Il trading su Polymarket comporta rischi. Non investire mai denaro che non puoi permetterti di perdere. Questo tool è a scopo informativo — la decisione finale è sempre tua.
