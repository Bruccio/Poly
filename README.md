# 🐋 Polymarket Whale Tracker Bot

Bot che ogni mattina alle 8:00 fetcha automaticamente i trade più grossi su Polymarket,
identifica le whale attive, analizza tutto con Claude AI, e ti manda il briefing su Telegram.

---

## Setup in 4 step

### Step 1 — Crea il bot Telegram

1. Apri Telegram e cerca **@BotFather**
2. Invia `/newbot` e segui le istruzioni
3. Copia il **token** che ti dà (es. `123456789:ABCdef...`)
4. Ora cerca **@userinfobot** su Telegram e invia `/start` → copia il tuo **Chat ID** (es. `987654321`)

> **Vuoi riceverlo in un gruppo?** Aggiungi il bot al gruppo, manda un messaggio nel gruppo,
> poi apri `https://api.telegram.org/bot<TOKEN>/getUpdates` e cerca il `chat.id` del gruppo
> (sarà un numero negativo, es. `-1001234567890`).

---

### Step 2 — Fork del repo su GitHub

1. Crea un nuovo repo su GitHub (pubblico o privato, non importa)
2. Carica questi file:
   ```
   whale_tracker.py
   requirements.txt
   .github/workflows/whale_briefing.yml
   ```
3. Puoi farlo dal terminale:
   ```bash
   git init
   git add .
   git commit -m "init whale tracker"
   git remote add origin https://github.com/TUO_USERNAME/TUO_REPO.git
   git push -u origin main
   ```

---

### Step 3 — Aggiungi i Secrets su GitHub

Vai su **Settings → Secrets and variables → Actions → New repository secret**
e aggiungi questi tre segreti:

| Nome                | Valore                                       |
|---------------------|----------------------------------------------|
| `ANTHROPIC_API_KEY` | La tua API key di Anthropic (da console.anthropic.com) |
| `TELEGRAM_BOT_TOKEN`| Il token del bot (da BotFather)              |
| `TELEGRAM_CHAT_ID`  | Il tuo Chat ID (da @userinfobot)             |

---

### Step 4 — Test manuale

1. Vai su **Actions** nel tuo repo GitHub
2. Clicca su **"🐋 Whale Briefing Giornaliero"**
3. Clicca **"Run workflow"** → **"Run workflow"**
4. Dopo ~2 minuti ricevi il primo briefing su Telegram ✅

Da quel momento gira automaticamente ogni mattina alle 8:00 ora italiana.

---

## Parametri personalizzabili

Puoi cambiare queste variabili nel workflow o nei Secrets:

| Variabile         | Default | Significato                                  |
|-------------------|---------|----------------------------------------------|
| `MIN_WHALE_SIZE`  | `10000` | Soglia minima trade in USDC ($10k)           |
| `LOOKBACK_HOURS`  | `24`    | Quante ore di storia scandagliare            |
| `TOP_MARKETS`     | `15`    | Quanti mercati top analizzare                |

Per abbassare il filtro a $5k (più whale, più rumore):
- Vai su **Actions → Run workflow** e inserisci `5000` nel campo `min_whale_size`

---

## Struttura del briefing Telegram

```
🐋 WHALE BRIEFING — 03/04/2026

📊 Panoramica mercato
Whale tracciate: 4  |  ✅ COPY: 2  |  ❌ SKIP: 1  |  👁 WATCH: 1
Rischio globale: 🟡 MEDIO
Sentiment: accumulo istituzionale su mercati geopolitici

💡 Il mercato mostra interesse crescente verso...

────────────────────────────
🐋 Whale da seguire oggi
🟢 A  `0x4f2a...8c91`  —  $87,000  —  Early Narrative Bettor
   Accumulo progressivo su 3h, OI in forte crescita

📈 Trade raccomandati

✅ Trump firma ordine tariffe UE entro maggio?
   YES @ 0.31  |  Risk 3/10  |  Kelly 4%  |  Tier A
   🪟 Finestra: valido se prezzo <0.38
   Whale tier A con accumulo sofisticato in 3 tranche...

⚠️ Alert
• Possibile wash trading su mercato "Bitcoin $100k maggio"
```

---

## Log e storico

Ogni run salva un file `whale_log.jsonl` con il briefing completo in formato JSON.
Puoi scaricarlo da **Actions → [run] → Artifacts**.

---

## Problemi comuni

**"Nessuna whale rilevata"**
→ Abbassa `MIN_WHALE_SIZE` a `5000` o aumenta `LOOKBACK_HOURS` a `48`

**"Telegram error: 400"**
→ Verifica che il `TELEGRAM_CHAT_ID` sia corretto. Per i gruppi deve essere negativo.

**"API Polymarket timeout"**
→ Normale se le API sono lente. Il bot riprova 3 volte automaticamente.

**Il workflow non parte alle 8:00**
→ GitHub Actions ha ritardi fino a 15-20 min sulla schedule. È normale.
