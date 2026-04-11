# Polymarket Whale Tracker

Bot automatico che monitora i mercati di [Polymarket](https://polymarket.com), analizza i movimenti dei **grandi investitori ("whale", >$100k)** con Claude AI e ti avvisa via **Telegram** e **Email** in tempo reale.

Il progetto punta a diventare una piattaforma completa di **copy trading sui prediction market**, con rilevazione di movimenti anomali, possibile insider trading e esecuzione automatica degli ordini tramite la CLOB API di Polymarket.

---

## Come funziona oggi

```
Ogni giorno alle 09:00 e 21:00 (orario italiano) — GitHub Actions automatico
        ↓
Build della cache Gamma (1000+ mercati attivi + 500 chiusi di recente)
        ↓
Scarica la leaderboard dei top trader da Polymarket
        ↓
Filtra i trade: sport / date scadute / mercati risolti / wash trading
        ↓
Claude AI analizza fino a 10 movimenti con contesto whale + track record
        ↓
Notifica Telegram + Email solo per COPY e WATCH (mai per SKIP)
        ↓
Aggiorna whale_state.json + dashboard GitHub Pages
```

---

## Filtri e protezioni

| Livello | Filtro | Descrizione |
|---------|--------|-------------|
| 1 | Sport | 100+ keyword + pattern regex (es. "Will X win on YYYY-MM-DD?") |
| 2a | Date scadute (testo) | Regex su mesi/quarter/anni passati nel titolo del mercato |
| 2b | Mercati risolti (Gamma API) | Cache bulk di 1500 mercati, lookup 3-tier (esatto → parziale → search) |
| 2c | EndDate strutturata | Campo `endDate` dalla risposta API, confronto con data odierna UTC |
| 3 | Wash trading | Esclusione wallet che comprano e vendono ripetutamente la stessa posizione |

---

## Setup (una volta sola)

### Step 1 — Secrets su GitHub
Vai in **Settings → Secrets and variables → Actions** e aggiungi:

| Secret | Valore |
|--------|--------|
| `ANTHROPIC_API_KEY` | API key Anthropic ([console.anthropic.com](https://console.anthropic.com)) |
| `TELEGRAM_BOT_TOKEN` | Token del tuo bot Telegram |
| `TELEGRAM_CHAT_ID` | Il tuo User ID Telegram (ottienilo con @userinfobot) |
| `GMAIL_APP_PASSWORD` | Password app Gmail (vedi Step 2) |

### Step 2 — Gmail App Password
1. Vai su [myaccount.google.com](https://myaccount.google.com) → **Sicurezza**
2. Abilita la **Verifica in 2 passaggi** se non ancora attiva
3. Cerca **"Password per le app"** → crea una nuova (nome: "Polymarket Bot")
4. Copia il codice a 16 caratteri e aggiungilo come secret `GMAIL_APP_PASSWORD`

### Step 3 — Abilita la Dashboard Web (gratuito)
1. **Settings → Pages** → Source: **Deploy from branch** → `main` → `/ (root)`
2. Dopo 1-2 minuti la dashboard è live su `https://bruccio.github.io/Poly`

### Step 4 — Test manuale
**Actions → "Whale Tracker" → "Run workflow"** — dopo ~2 minuti ricevi Telegram + Email.

---

## Sistema di verdetti

| Verdetto | Significato |
|----------|-------------|
| COPY | Opportunità solida — prezzo chiaramente sottovalutato |
| WATCH | Interessante — vale la pena monitorare |
| SKIP | Non interessante, troppo rischioso, sport o mercato scaduto |

Solo **COPY** e **WATCH** generano una notifica. I SKIP sono silenziosi.

---

## Struttura del progetto

```
whale_tracker.py          Logica core: leaderboard, analisi Claude, notifiche
poly_live.py              Monitoraggio H24 via WebSocket (per VPS/server)
whale_state.json          Stato persistente (leaderboard, scommesse recenti, accuracy)
index.html                Dashboard web (GitHub Pages)
tests/
  test_whale_tracker.py   46 test pytest (sport filter, date filter, risoluzione)
.github/workflows/
  main.yml                Automazione GitHub Actions (cron 09:00 + 21:00)
requirements.txt          Dipendenze Python
```

---

## Roadmap

Il progetto segue una roadmap a 12 mesi verso una piattaforma di copy trading completa:

| Fase | Focus |
|------|-------|
| **MVP (ora)** | Whale detection, filtri, notifiche Claude AI, dashboard GitHub Pages |
| **Sprint 1** | Data pipeline robusta: TimescaleDB, WebSocket live feed, Polygon RPC sync |
| **Sprint 2** | Analytics engine: anomaly detection (Isolation Forest), clustering wallet sospetti |
| **Sprint 3** | Copy trading: integrazione CLOB API Polymarket, gestione rischio (stop-loss, slippage) |
| **Sprint 4** | UI/UX avanzata + hardening sicurezza chiavi (AES-256 / WalletConnect) |

---

## API usate

| API | Scopo | Auth |
|-----|-------|------|
| Gamma API | Mercati, eventi, risoluzione | Pubblica |
| Data API | Posizioni, trade, leaderboard | Pubblica |
| CLOB API | Esecuzione ordini (futuro) | API key |
| Polygon RPC | Verifica on-chain (futuro) | Provider (Alchemy/Infura) |

---

> **Disclaimer**: Il trading comporta rischi significativi. Questo strumento fornisce segnali basati sull'attività osservabile delle whale, ma la decisione finale spetta sempre a te. Non investire mai ciò che non puoi permetterti di perdere.
