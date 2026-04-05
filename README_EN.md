# Polymarket Whale Tracker v3

An automated bot that monitors Polymarket prediction markets, analyzes **large-money moves (whale wallets)** using Claude AI, and alerts you via **Telegram** and **Email** when there's a trade worth following.

Includes a **persistent leaderboard**, **wash trading filter**, **self-improving algorithm**, and a free **hacker-style web dashboard** on GitHub Pages.

---

## How it works

```
Every day at 09:00 and 21:00 (Italian time) — fully automatic
        ↓
Downloads the top-trader leaderboard from Polymarket
        ↓
Fetches recent trades from each top-wallet (whale-first detection)
        ↓
Checks resolution of previous COPY signals (self-improving)
        ↓
Claude AI analyzes up to 10 moves with whale context + track record
        ↓
Updates whale_state.json in the repo (leaderboard + accuracy + signals)
        ↓
Sends Telegram + Email with analysis, leaderboard and track record
```

---

## Setup (one-time)

### Step 1 — Add Secrets to GitHub

Go to **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|--------|-------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key ([console.anthropic.com](https://console.anthropic.com)) |
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token |
| `TELEGRAM_CHAT_ID` | Your Telegram User ID (get it via @userinfobot) |
| `GMAIL_APP_PASSWORD` | See Step 2 below |

---

### Step 2 — Create a Gmail App Password (for email alerts)

1. Go to [myaccount.google.com](https://myaccount.google.com)
2. **Security** → **2-Step Verification** → enable if not already done
3. Back in **Security** → search for **"App passwords"**
4. Create a new app password (name: "Polymarket Bot")
5. Copy the 16-character code (e.g. `abcd efgh ijkl mnop`)
6. Add it as the `GMAIL_APP_PASSWORD` secret on GitHub (no spaces)

---

### Step 3 — Merge the branch

1. Go to GitHub → **Pull requests**
2. Open the PR → **"Merge pull request"** → **"Confirm merge"**

Done. The bot runs automatically at 09:00 and 21:00.

---

### Step 4 — Enable the Web Dashboard (optional, free)

1. Go to **Settings → Pages**
2. Source: **Deploy from branch** → branch: `main` → folder: `/ (root)`
3. Save → after 1-2 minutes the dashboard is live at `https://bruccio.github.io/Poly`

The page updates automatically after each bot run.

---

### Step 5 — Manual test (optional)

1. **Actions** → **"Whale Tracker v3"** → **"Run workflow"**
2. After ~2 minutes you receive Telegram + Email
3. The file `whale_state.json` is committed to the repo with updated data

---

## Features

### Automatic Whale Detection (whale-first)
No manual threshold needed. The bot downloads the **top-30 trader leaderboard** from Polymarket, then fetches the **recent trades from each wallet**. Only verified high-trust traders are analyzed — no guesswork.

### Whale Tier System
| Tier | Criteria |
|------|----------|
| Top Whale | Historical volume > $1M |
| Big Whale | Historical volume > $500k |
| Whale | In the top-50 leaderboard |

### Persistent Leaderboard
Each run updates `whale_state.json` with a **trust score** (0-100) per wallet based on profit and volume. High-trust wallets get priority in the analysis.

### Wash Trading Filter
The bot detects wallets that repeatedly buy and sell the same position. Wash traders are automatically excluded from the report.

### Self-Improving Algorithm
Every market with a COPY verdict is tracked in `whale_state.json`. When the market resolves, the bot updates the **accuracy track record** automatically. Over time, Claude's prompt includes this metric for better-calibrated advice.

### 3-Verdict System
| Verdict | Meaning |
|---------|---------|
| ✅ COPY | Strong opportunity — price looks clearly mispriced |
| 👁️ WATCH | Interesting — worth monitoring, could become COPY |
| ⏭️ SKIP | Not interesting, too risky, or sports/entertainment |

### Sport Filter (3 layers)
- Keyword blocklist (100+ terms: teams, leagues, sports events)
- Regex patterns (e.g. "Will X win on YYYY-MM-DD?")
- Claude response flag (if Claude mentions "sport", the verdict is forced to SKIP)

### Reddit Insights
Every 10 runs the bot checks hot posts on **r/Polymarket** looking for strategies and underpriced markets. Results are included in Claude's analysis context.

### Hacker Dashboard
Static page on GitHub Pages showing leaderboard, COPY signals with outcome, historical accuracy, and Reddit insights. **Zero extra cost.**

---

## What you receive

### Telegram
```
🐋 Big Moves on Polymarket
05/04/2026 09:00

Analyzed 10 non-sport markets.
📊 Track record: 67% accuracy (3 resolved signals)
2 markets worth attention. 👇

✅ COPY
📌 Will the Fed cut rates in June 2026?
🐋 HorizonSplendidView 🟢 Trust: 95/100
💡 Top whale with verified track record bets on rate cut
📖 A $180k bet on YES at 28¢ — strong conviction...
🟡 Risk: 3/10

─────────────────────
🏆 Top Whale Tracker
1. HorizonSplendidView +$4,598,457 (trust 95)
2. beachboy4 +$3,762,306 (trust 92)
...
```

### Email
Same analysis in HTML format with leaderboard table, sent to `brunoricciohsl@gmail.com`.

---

## File structure

```
whale_tracker.py          Main script (v3)
whale_state.json          Persistent state — created/updated automatically
index.html                Web dashboard (GitHub Pages)
requirements.txt          Python dependencies (requests only)
.github/
  workflows/
    main.yml              GitHub Actions workflow — cron 09:00 and 21:00
README.md                 Italian README
README_EN.md              This file (English)
```

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_WHALES` | `10` | Markets analyzed per run |
| `ONLY_NOTIFY_ON_COPY` | `false` | `true` = notify only if there is at least one COPY |

> No manual threshold needed — whale detection is fully automatic.

---

## Common issues

| Problem | Solution |
|---------|----------|
| No Telegram message | Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID |
| No email | Check GMAIL_APP_PASSWORD in secrets |
| Sports bets still showing | The filter is aggressive — if something slips through, open an issue |
| Dashboard not updating | Wait for the next run — the bot commits whale_state.json after each run |
| Accuracy always N/A | Normal at first — it populates as COPY markets resolve (weeks/months) |
| Workflow times out | Rare — default 10 markets is fine |
| Workflow starts late | GitHub Actions schedules can be delayed up to 15 min — normal |

---

> ⚠️ Trading on Polymarket involves risk. Never invest money you cannot afford to lose. This tool is for informational purposes only — the final decision is always yours.
