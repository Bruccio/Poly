# 🐋 Polymarket Whale Tracker v3 (H24 Live)

Bot automatico che monitora i mercati su [Polymarket](https://polymarket.com), analizza i movimenti dei **grandi investitori (>$100k)** con Claude AI e ti avvisa via **Telegram** e **Email** istantaneamente.

Questa versione include il **monitoraggio in tempo reale (H24)** via WebSocket, una logica di **risoluzione mercati potenziata** e la pulizia automatica dei dati obsoleti.

---

## Come funziona

### 🕒 Modalità H24 (Live)
Il bot resta in ascolto costante del flusso dati di Polymarket. Non appena viene rilevato un trade che supera la soglia impostata (es. $100k):
1.  **Filtra** immediatamente sport e wash trading.
2.  **Analizza** il movimento con Claude AI (Anthropic) usando il contesto delle news e della whale.
3.  **Invia** una notifica istantanea su Telegram se il verdetto è **COPY** o **WATCH**.

### 📊 Self-Improving & Cleanup
Ogni ora, il bot:
*   Verifica se i mercati tracciati in precedenza si sono risolti (usando una logica bulk più robusta).
*   Aggiorna il **track record di accuracy** del sistema.
*   Rimuove automaticamente i mercati "stale" (non risolti dopo 30 giorni) per mantenere la dashboard pulita.

---

## Setup (una volta sola)

### Step 1 — Secrets su GitHub
Configura i seguenti secrets in **Settings → Secrets and variables → Actions**:
*   `ANTHROPIC_API_KEY`: La tua API key Anthropic.
*   `TELEGRAM_BOT_TOKEN`: Token del tuo bot Telegram.
*   `TELEGRAM_CHAT_ID`: Il tuo User ID Telegram.
*   `GMAIL_APP_PASSWORD`: Password per le app di Gmail (per i report email).

### Step 2 — Esecuzione
Il bot è configurato per girare in due modalità:
1.  **Scheduled (Default)**: Gira ogni 12 ore (09:00 e 21:00) per un report completo.
2.  **Live (Consigliato)**: Può essere eseguito su un server/VPS per il monitoraggio H24 usando `python poly_live.py`.

---

## Novità Versione 3

### ⚡ Real-Time Monitoring
Integrazione con i WebSocket di Polymarket per notifiche istantanee. Non perderai più un movimento "insider" perché il bot girava solo due volte al giorno.

### 🔍 Risoluzione Robusta
Nuova logica `check_resolutions` che:
*   Scarica i mercati in bulk per evitare errori di ricerca.
*   Controlla i prezzi finali (0/1) se il campo `winningOutcome` è mancante.
*   Gestisce i fusi orari UTC in modo coerente.

### 🧹 Auto-Cleanup
Rimozione automatica dei mercati "fantasma" (es. "Fed decision in January" segnalato ad Aprile) se non risolti entro 30 giorni.

---

## Struttura Progetto

```
whale_tracker.py          Logica core e report periodici
poly_live.py              Monitoraggio H24 via WebSocket (Novità v3)
whale_state.json          Stato persistente (leaderboard, accuracy, segnali)
index.html                Dashboard web (GitHub Pages)
requirements.txt          Dipendenze (requests, websockets, pytest)
.github/workflows/        Automazione GitHub Actions
```

---

> ⚠️ **Disclaimer**: Il trading comporta rischi. Questo tool fornisce segnali basati sull'attività delle "whale", ma la decisione finale spetta sempre a te. Non investire mai ciò che non puoi permetterti di perdere.
