#!/usr/bin/env python3
"""
Polymarket Whale Tracker v2 → Telegram + Email
- Soglia whale: $100k+ (solo Alpha/Insider)
- Leaderboard persistente con trust score aggiornato ogni run
- Filtro wash trading automatico
- Self-improving: traccia previsioni e verifica resolutions
- Reddit insights ogni 10 run
"""

import os
import re
import sys
import time
import json
import math
import hashlib
import smtplib
import requests
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── CREDENZIALI ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_USER_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
GMAIL_USER         = os.environ.get("GMAIL_USER", "brunoricciohsl@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
EMAIL_TO           = "brunoricciohsl@gmail.com"

# ── PARAMETRI ───────────────────────────────────────────────────────────────────
MIN_SIZE_USDC          = int(os.environ.get("MIN_WHALE_SIZE", "100000"))
MAX_WHALES             = int(os.environ.get("MAX_WHALES", "10"))
CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "30"))
ONLY_NOTIFY_ON_COPY    = os.environ.get("ONLY_NOTIFY_ON_COPY", "true").lower() != "false"

# ── STATO PERSISTENTE ────────────────────────────────────────────────────────────
STATE_FILE = "whale_state.json"

def _empty_state() -> dict:
    return {
        "leaderboard": {},
        "watched_markets": {},
        "algo_stats": {
            "total_copy_signals": 0,
            "resolved_copies": 0,
            "correct_copies": 0,
            "accuracy_pct": None,
            "last_updated": None,
        },
        "reddit_cache": {
            "last_checked": None,
            "top_strategies": [],
        },
        "run_count": 0,
    }

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
            base = _empty_state()
            for k, v in base.items():
                if k not in data:
                    data[k] = v
            return data
        except Exception as e:
            log(f"Stato corrotto, ricreo: {e}", "WARN")
    return _empty_state()

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

# ── FILTRO SPORT / ENTERTAINMENT ─────────────────────────────────────────────────
SPORT_KEYWORDS = [
    # Partite / sfide dirette
    " vs ", " vs. ", " v ", " @ ", "beat ", "beats ",
    "win the ", "score ", "scores ", "goal ", "match ", "game ",
    # Leghe americane
    "NBA", "NFL", "NHL", "MLB", "MLS", "WNBA", "NCAA", "NWSL",
    # Leghe europee
    "Premier League", "Serie A", "La Liga", "Bundesliga", "Ligue 1",
    "Eredivisie", "Primeira Liga",
    # Competizioni internazionali
    "Champions League", "Europa League", "Conference League",
    "World Cup", "Copa", "Euro 2024", "Euro 2026",
    # Sport americani
    "Super Bowl", "playoffs", "playoff", "postseason",
    "March Madness", "Stanley Cup", "World Series",
    # Sport da combattimento
    "UFC", "boxing", "MMA", "fight", "bout",
    # Motorsport
    "F1", "Formula 1", "NASCAR", "Grand Prix", "GP ",
    # Tennis
    "tennis", "ATP", "WTA", "Wimbledon", "US Open", "French Open",
    "Australian Open", "Roland Garros",
    # Golf
    "golf", "PGA", "LPGA", "Masters", "The Open",
    # Olimpiadi
    "Olympics", "Olympic", "Olimpiadi",
    # Awards / Entertainment
    "Oscar", "Grammy", "Emmy", "Eurovision", "Golden Globe",
    "Academy Award",
    # Generici sport
    "quarterback", "touchdown", "pitcher", "strikeout",
    "slam dunk", "three-pointer", "hat trick", "penalty",
    "transfer", "signing", "drafted", "roster",
    "season opener", "regular season", "division title",
]

# Pattern regex per catturare sport che sfuggono alle keyword
SPORT_PATTERNS = [
    r"will .+ win on \d{4}-\d{2}-\d{2}",       # "Will X win on 2026-04-05?" = partita
    r"will .+ win .+\b(season|series|cup|tournament|league|championship|title)\b",
    r"will .+ beat ",
    r"\b(home|away)\s+(win|team|game)\b",
    r"\b\d+\s+(goals?|points?|runs?|sets?|games?|touchdowns?)\b",
    r"\b(fc|cf|ac|as|sc|rc|afc|ssc)\s+\w+",    # nomi club: FC Barcelona, AC Milan
    r"\b(united|city|rovers|wanderers|athletic)\b.*\b(win|lose|draw|score)\b",
]

def _is_sport(title: str) -> bool:
    t = title.lower()
    if any(kw.lower() in t for kw in SPORT_KEYWORDS):
        return True
    return any(re.search(p, t) for p in SPORT_PATTERNS)


# ── LOGGING ─────────────────────────────────────────────────────────────────────
# (definito prima così le funzioni successive possono usarlo)
def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    icon = {"OK": "✓", "ERR": "✗", "WARN": "⚠"}.get(level, "·")
    print(f"[{ts}] {icon} {msg}", flush=True)


# ── LEADERBOARD POLYMARKET ──────────────────────────────────────────────────────
def fetch_breaking_leaderboard(state: dict):
    """
    Scarica la leaderboard da data-api.polymarket.com e aggiorna
    state["leaderboard"] con trust_score calcolato.
    """
    H = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    urls = [
        "https://data-api.polymarket.com/leaderboard?limit=50&sortBy=profit&window=all",
        "https://data-api.polymarket.com/leaderboard?limit=50",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=H, timeout=12)
            if r.status_code != 200:
                continue
            data = r.json()
            entries = data if isinstance(data, list) else data.get("data", data.get("leaderboard", []))
            if not entries:
                continue
            updated = 0
            for e in entries:
                wallet = (e.get("proxyWallet") or e.get("address") or e.get("wallet") or "").lower()
                if not wallet:
                    continue
                profit = float(e.get("profit") or e.get("pnl") or e.get("totalProfit") or 0)
                volume = float(e.get("volume") or e.get("totalVolume") or 0)
                username = e.get("name") or e.get("username") or wallet[:10]
                score = 50
                if profit > 0:
                    score += min(30, int(math.log10(max(profit, 1)) * 5))
                if volume > 0:
                    score += min(20, int(math.log10(max(volume, 1)) * 3))
                score = min(100, score)
                existing = state["leaderboard"].get(wallet, {})
                state["leaderboard"][wallet] = {
                    "username": username,
                    "total_profit_usd": profit,
                    "total_volume_usd": volume,
                    "trust_score": score,
                    "times_seen": existing.get("times_seen", 0),
                    "copy_accuracy": existing.get("copy_accuracy", None),
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                }
                updated += 1
            log(f"Leaderboard: {updated} whale aggiornate", "OK")
            return
        except Exception as e:
            log(f"Leaderboard fetch error: {e}", "WARN")
    log("Leaderboard: impossibile scaricare (continuo senza)", "WARN")


# ── WASH TRADING DETECTION ──────────────────────────────────────────────────────
def is_wash_trader(wallet: str) -> bool:
    """
    Heuristica: un wallet che compra E vende la stessa posizione più volte
    sullo stesso mercato è probabilmente wash trader.
    """
    if not wallet or wallet.startswith("0xpool") or wallet.startswith("0xdemo"):
        return False
    H = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    try:
        url = f"https://data-api.polymarket.com/activity?user={wallet}&limit=100&type=TRADE"
        r = requests.get(url, headers=H, timeout=10)
        if r.status_code != 200:
            return False
        trades = r.json()
        if not isinstance(trades, list):
            trades = trades.get("data", [])
        if len(trades) < 10:
            return False
        market_sides: dict = {}
        for t in trades:
            mid = t.get("market") or t.get("conditionId") or t.get("marketId") or ""
            side = (t.get("side") or t.get("type") or "").upper()
            if not mid:
                continue
            if mid not in market_sides:
                market_sides[mid] = {"BUY": 0, "SELL": 0}
            if "BUY" in side or "LONG" in side:
                market_sides[mid]["BUY"] += 1
            elif "SELL" in side or "SHORT" in side:
                market_sides[mid]["SELL"] += 1
        wash_count = sum(
            1 for c in market_sides.values()
            if c["BUY"] >= 3 and c["SELL"] >= 3
            and min(c["BUY"], c["SELL"]) / max(c["BUY"], c["SELL"]) >= 0.4
        )
        wash_pct = wash_count / max(len(market_sides), 1)
        if wash_pct >= 0.30:
            log(f"Wash trader rilevato: {wallet[:14]}... ({wash_pct:.0%} mercati wash)", "WARN")
            return True
    except Exception as e:
        log(f"Wash check error: {e}", "WARN")
    return False


# ── REDDIT INSIGHTS ─────────────────────────────────────────────────────────────
def fetch_reddit_insights(state: dict) -> str:
    """Ogni 10 run, scarica i top post di r/Polymarket e cerca strategie."""
    run_count = state.get("run_count", 0)
    cache = state.get("reddit_cache", {})
    if run_count % 10 != 1 and cache.get("top_strategies"):
        return cache["top_strategies"][0] if cache["top_strategies"] else ""
    try:
        H = {"Accept": "application/json",
             "User-Agent": "WhaleTracker/2.0 (by /u/polymarket_bot)"}
        r = requests.get("https://www.reddit.com/r/Polymarket/hot.json?limit=10",
                         headers=H, timeout=12)
        if r.status_code != 200:
            return ""
        posts = r.json().get("data", {}).get("children", [])
        KEYS = ["underpriced", "strategy", "alpha", "edge", "opportunity",
                "mispriced", "arbitrage", "expected value", "insider", "signal"]
        insights = []
        for p in posts:
            d = p.get("data", {})
            title = d.get("title", "")
            combined = (title + " " + d.get("selftext", "")[:300]).lower()
            if any(k in combined for k in KEYS):
                insights.append(f"- Reddit r/Polymarket: \"{title}\"")
        strategies = insights[:3]
        state["reddit_cache"] = {
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "top_strategies": strategies,
        }
        log(f"Reddit: {len(strategies)} insight trovati", "OK")
        return "\n".join(strategies)
    except Exception as e:
        log(f"Reddit fetch error: {e}", "WARN")
        return ""


# ── SELF-IMPROVING: CONTROLLA RESOLUTION ────────────────────────────────────────
def check_resolutions(state: dict):
    """Controlla se i mercati watched hanno avuto una resolution e aggiorna stats."""
    watched = state.get("watched_markets", {})
    if not watched:
        return
    H = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    newly_resolved = 0
    for key, market in list(watched.items()):
        if market.get("resolved"):
            continue
        question = market.get("question", "")
        if not question:
            continue
        try:
            q = requests.utils.quote(question[:50])
            r = requests.get(
                f"https://gamma-api.polymarket.com/markets?search={q}&limit=5",
                headers=H, timeout=10)
            if r.status_code != 200:
                continue
            markets = r.json()
            if not isinstance(markets, list):
                markets = markets.get("data", [])
            for m in markets:
                m_q = (m.get("question") or m.get("title") or "").lower()
                if question.lower()[:30] not in m_q:
                    continue
                if not (m.get("closed") or m.get("isResolved")):
                    continue
                winning = (m.get("winningOutcome") or "").upper()
                our_side = market.get("side", "YES").upper()
                correct = (winning == our_side or
                           (winning in ("YES", "1") and our_side == "YES") or
                           (winning in ("NO", "0") and our_side == "NO"))
                market.update({"resolved": True, "resolution": winning, "correct": correct})
                newly_resolved += 1
                break
        except Exception as e:
            log(f"Resolution check error: {e}", "WARN")
    # Ricalcola algo_stats
    all_resolved = [m for m in watched.values()
                    if m.get("resolved") and m.get("correct") is not None]
    correct = sum(1 for m in all_resolved if m.get("correct"))
    total_copies = sum(1 for m in watched.values() if m.get("our_verdict") == "COPY")
    state["algo_stats"] = {
        "total_copy_signals": total_copies,
        "resolved_copies": len(all_resolved),
        "correct_copies": correct,
        "accuracy_pct": round(correct / len(all_resolved) * 100, 1) if all_resolved else None,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    if newly_resolved:
        log(f"Resolution: {newly_resolved} mercati risolti, "
            f"accuracy: {state['algo_stats']['accuracy_pct']}%", "OK")


# ── AGGIORNA WATCHED MARKETS ─────────────────────────────────────────────────────
def update_watched_markets(state: dict, results: list):
    """Salva in watched_markets i mercati con verdict COPY o WATCH."""
    for res in results:
        if res.get("verdict") not in ("COPY", "WATCH"):
            continue
        key = hashlib.md5(res["market"].encode()).hexdigest()[:12]
        if key not in state["watched_markets"]:
            state["watched_markets"][key] = {
                "question": res["market"],
                "our_verdict": res.get("verdict", "COPY"),
                "whale_wallet": res.get("wallet", ""),
                "entry_price": res.get("price", 0),
                "side": res.get("side", "YES"),
                "date_flagged": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "resolved": False,
                "resolution": None,
                "correct": None,
            }


# ── WHALE PROFILING (ispirato da collectmarkets2) ────────────────────────────────
def profile_whale(wallet: str, state: dict) -> dict:
    """
    Scarica le transazioni del wallet e calcola volume storico.
    Risultato cachato in state["leaderboard"] per evitare chiamate ripetute.
    """
    if not wallet or wallet.startswith("0xpool") or wallet.startswith("0xdemo"):
        return {"total_volume_usd": 0, "n_markets": 0, "profiled": False}

    cached = state.get("leaderboard", {}).get(wallet, {})
    if cached.get("profiled"):
        return cached

    H = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    total_volume = 0.0
    markets_seen = set()
    try:
        for offset in [0, 500]:  # max 2 pagine = 1000 trade
            url = (f"https://data-api.polymarket.com/activity"
                   f"?user={wallet}&limit=500&offset={offset}&type=TRADE")
            r = requests.get(url, headers=H, timeout=12)
            if r.status_code != 200:
                break
            trades = r.json()
            if not isinstance(trades, list):
                trades = trades.get("data", [])
            if not trades:
                break
            for t in trades:
                amt = float(t.get("usdcSize") or t.get("amount") or t.get("size") or 0)
                total_volume += amt
                mid = t.get("market") or t.get("conditionId") or ""
                if mid:
                    markets_seen.add(mid)
            time.sleep(0.5)  # rate limit
    except Exception as e:
        log(f"Profile whale {wallet[:14]}: {e}", "WARN")

    profile = {
        **cached,
        "total_volume_usd": max(total_volume, cached.get("total_volume_usd", 0)),
        "n_markets": len(markets_seen),
        "profiled": True,
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }
    state.setdefault("leaderboard", {})[wallet] = profile
    return profile


def _classify(size, total_volume=0):
    vol = max(size, total_volume)
    if vol >= 1_000_000: return "Top Whale (>$1M)"
    if vol >= 500_000:   return "Big Whale (>$500k)"
    if vol >= 100_000:   return "Whale (>$100k)"
    return "Small Fish"


def compute_confidence(trade: dict, state: dict) -> int:
    """Punteggio di fiducia 0-100 calcolato prima di Claude."""
    score = 50
    size = _sz(trade)
    if size >= 500_000:   score += 15
    elif size >= 200_000: score += 10
    elif size >= 100_000: score += 5

    ts = trade.get("whale_trust_score", 40)
    if ts >= 80:   score += 15
    elif ts >= 60: score += 8

    price = float(trade.get("price", 0.5))
    if price < 0.15 or price > 0.85:
        score += 10  # strong conviction bet

    wallet = (trade.get("userAddress") or "").lower()
    profile = state.get("leaderboard", {}).get(wallet, {})
    if profile.get("total_volume_usd", 0) >= 1_000_000:
        score += 10

    return min(100, score)


# ── POLYMARKET ──────────────────────────────────────────────────────────────────
def _sz(t):
    """Estrae la size in USDC dal dict trade/mercato."""
    for k in ("usdcSize", "size", "amount", "tradeSize", "amountUSD",
              "makerAmountFilled", "takerAmountFilled"):
        try:
            v = float(t.get(k) or 0)
            if v > 0:
                return v
        except (ValueError, TypeError):
            pass
    return 0.0


def fetch_polymarket_whales(min_size, state: dict = None):
    """
    Multi-source crawling: aggrega dati da TUTTI gli endpoint Polymarket disponibili.
    Non si ferma al primo — prende tutto e deduplica.
    """
    H = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    all_items: list = []
    seen_titles: set = set()

    def _add_items(items: list, source: str):
        added = 0
        for item in items:
            title = (item.get("title") or item.get("question") or "").strip().lower()
            if title and title not in seen_titles:
                seen_titles.add(title)
                item["_source"] = source
                all_items.append(item)
                added += 1
        if added:
            log(f"  {source}: +{added} mercati", "OK")

    # ── Source 1: Gamma API Markets (volume 24h) ──
    try:
        r = requests.get(
            "https://gamma-api.polymarket.com/markets?limit=100&active=true&order=volume24hr&ascending=false",
            headers=H, timeout=12)
        if r.status_code == 200:
            mlist = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
            _add_items([{
                "usdcSize":    str(float(m.get("volume24hr") or m.get("volume") or 0)),
                "price":       str(m.get("bestAsk") or m.get("lastTradePrice") or 0.5),
                "side":        "YES",
                "title":       m.get("question") or m.get("title") or "Mercato",
                "userAddress": "0xpool",
                "conditionId": m.get("conditionId") or "",
            } for m in mlist
              if float(m.get("volume24hr") or m.get("volume") or 0) >= min_size],
            "gamma-markets")
    except Exception as e:
        log(f"  gamma-markets: {str(e)[:80]}", "WARN")

    # ── Source 2: Gamma API Events (volume totale) ──
    try:
        r = requests.get(
            "https://gamma-api.polymarket.com/events?limit=80&active=true&order=volume&ascending=false",
            headers=H, timeout=12)
        if r.status_code == 200:
            elist = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
            _add_items([{
                "usdcSize":    str(float(ev.get("volume") or ev.get("volumeNum") or 0)),
                "price":       "0.5",
                "side":        "YES",
                "title":       ev.get("title") or ev.get("question") or "Evento",
                "userAddress": "0xpool",
            } for ev in elist
              if float(ev.get("volume") or ev.get("volumeNum") or 0) >= min_size],
            "gamma-events")
    except Exception as e:
        log(f"  gamma-events: {str(e)[:80]}", "WARN")

    # ── Source 3: Gamma API Trending/Popular markets ──
    for tag in ["trending", "popular"]:
        try:
            r = requests.get(
                f"https://gamma-api.polymarket.com/markets?limit=50&active=true&tag={tag}",
                headers=H, timeout=10)
            if r.status_code == 200:
                mlist = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
                _add_items([{
                    "usdcSize":    str(float(m.get("volume24hr") or m.get("volume") or 0)),
                    "price":       str(m.get("bestAsk") or m.get("lastTradePrice") or 0.5),
                    "side":        "YES",
                    "title":       m.get("question") or m.get("title") or "Mercato",
                    "userAddress": "0xpool",
                } for m in mlist
                  if float(m.get("volume24hr") or m.get("volume") or 0) >= min_size],
                f"gamma-{tag}")
        except Exception as e:
            log(f"  gamma-{tag}: {str(e)[:80]}", "WARN")

    # ── Source 4: Data API Activity (trade reali con wallet) ──
    try:
        r = requests.get(
            "https://data-api.polymarket.com/activity?limit=200&type=TRADE",
            headers=H, timeout=12)
        if r.status_code == 200:
            trades = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
            _add_items([{
                "usdcSize":    str(float(t.get("usdcSize") or t.get("amount") or 0)),
                "price":       str(t.get("price") or 0.5),
                "side":        t.get("side") or "YES",
                "title":       t.get("title") or t.get("question") or "Trade",
                "userAddress": t.get("user") or t.get("proxyWallet") or "0x???",
            } for t in trades
              if float(t.get("usdcSize") or t.get("amount") or 0) >= min_size],
            "data-api-activity")
    except Exception as e:
        log(f"  data-api-activity: {str(e)[:80]}", "WARN")

    # ── Source 5: TheGraph on-chain (grandi ordini) ──
    try:
        r = requests.post(
            "https://gateway-arbitrum.network.thegraph.com/api/f087f7244e56a2bc2d48c10e5a3c1bd3/"
            "subgraphs/id/Bx1W4S7kDVxs9gC3s2G6DS8kdNBJx2sYUiABH4RvGN46",
            data='{"query":"{ orderFilledEvents(first:200,orderBy:matchedAmount,orderDirection:desc)'
                 '{matchedAmount price maker order{market{question}}} }"}',
            headers={**H, "Content-Type": "application/json"},
            timeout=15)
        if r.status_code == 200:
            events = r.json().get("data", {}).get("orderFilledEvents", [])
            _add_items([{
                "usdcSize":    str(float(e.get("matchedAmount", 0)) / 1e6),
                "price":       e.get("price", "0"),
                "side":        "YES",
                "title":       e.get("order", {}).get("market", {}).get("question", "Mercato"),
                "userAddress": e.get("maker", "0x???"),
            } for e in events
              if float(e.get("matchedAmount", 0)) / 1e6 >= min_size],
            "thegraph-onchain")
    except Exception as e:
        log(f"  thegraph: {str(e)[:80]}", "WARN")

    # ── Source 6: Top whale dalla leaderboard — i loro mercati attivi ──
    lb = (state or {}).get("leaderboard", {})
    top_wallets = sorted(lb.items(), key=lambda x: x[1].get("total_profit_usd", 0), reverse=True)[:5]
    for wallet_addr, wdata in top_wallets:
        if wallet_addr.startswith("0xpool") or wallet_addr.startswith("0xdemo"):
            continue
        try:
            r = requests.get(
                f"https://data-api.polymarket.com/activity?user={wallet_addr}&limit=20&type=TRADE",
                headers=H, timeout=10)
            if r.status_code == 200:
                trades = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
                _add_items([{
                    "usdcSize":    str(float(t.get("usdcSize") or t.get("amount") or 0)),
                    "price":       str(t.get("price") or 0.5),
                    "side":        t.get("side") or "YES",
                    "title":       t.get("title") or t.get("question") or "Trade",
                    "userAddress": wallet_addr,
                } for t in trades
                  if float(t.get("usdcSize") or t.get("amount") or 0) >= min_size * 0.5],
                f"whale-{wdata.get('username', wallet_addr[:8])}")
            time.sleep(0.3)
        except Exception as e:
            log(f"  whale-{wallet_addr[:8]}: {str(e)[:60]}", "WARN")

    if not all_items:
        return False, [], 0

    log(f"Multi-source totale: {len(all_items)} mercati unici raccolti", "OK")

    # ── Filtra sport e sotto soglia ──
    filtered = [
        t for t in all_items
        if _sz(t) >= min_size * 0.5  # più permissivo per whale top
        and not _is_sport(t.get("title") or t.get("question") or "")
    ]

    # ── Arricchisci con trust_score e filtra wash trader ──
    leaderboard = (state or {}).get("leaderboard", {})
    enriched = []
    for t in filtered:
        wallet = (t.get("userAddress") or t.get("maker") or "0xpool").lower()
        lb_entry = leaderboard.get(wallet, {})
        t["whale_trust_score"] = lb_entry.get("trust_score", 40)
        t["whale_username"] = lb_entry.get("username", wallet[:10])
        if not wallet.startswith("0xpool") and not wallet.startswith("0xdemo"):
            if is_wash_trader(wallet):
                log(f"Escluso wash trader: {wallet[:14]}...", "WARN")
                continue
        enriched.append(t)

    # ── Ordina per trust_score × size ──
    enriched.sort(key=lambda x: x["whale_trust_score"] * _sz(x), reverse=True)

    # ── Diversità: seleziona MAX_WHALES con titoli diversi ──
    seen_words: set[str] = set()
    diverse: list = []
    for t in enriched[:40]:
        title = (t.get("title") or t.get("question") or "").lower()
        words = [w for w in title.split() if len(w) > 3][:2]
        key = " ".join(words)
        if key not in seen_words:
            seen_words.add(key)
            diverse.append(t)
        if len(diverse) >= MAX_WHALES:
            break

    whales = diverse or enriched[:MAX_WHALES]
    log(f"Pipeline: {len(all_items)} raw → {len(filtered)} non-sport → "
        f"{len(enriched)} post-wash → {len(whales)} selezionati", "OK")
    return True, whales, len(all_items)


# ── CLAUDE ──────────────────────────────────────────────────────────────────────
MODELS = [
    "claude-haiku-4-5-20251001",
    "claude-3-5-haiku-20241022",
    "claude-3-haiku-20240307",
]

SYSTEM_PROMPT = (
    "Sei un analista finanziario esperto di mercati predittivi (Polymarket).\n"
    "Stai valutando un mercato con alto volume dove una whale (grande investitore) ha piazzato >$100k.\n\n"
    "Il tuo compito: decidere se vale la pena seguire questa whale.\n\n"
    "REGOLE ASSOLUTE:\n"
    "- Qualsiasi scommessa sportiva (calcio, basket, tennis, F1, partite, match) → SKIP\n"
    "- Qualsiasi evento entertainment (Oscar, Grammy, Eurovision) → SKIP\n\n"
    "CRITERI DI VALUTAZIONE:\n"
    "- Il prezzo è diverso dalla probabilità reale? (>10% gap = interessante)\n"
    "- La whale ha trust score alto? (>70 = affidabile)\n"
    "- Il mercato è su un evento verificabile con deadline chiara?\n"
    "- C'è un catalizzatore imminente (elezione, decisione Fed, deadline legale, ecc.)?\n\n"
    "VERDETTI (3 opzioni):\n"
    "- COPY = Forte opportunità, prezzo chiaramente sbagliato o segnale forte dalla whale\n"
    "- WATCH = Interessante, da tenere d'occhio, potrebbe diventare COPY\n"
    "- SKIP = Non interessante, troppo rischioso, o è sport/entertainment\n\n"
    "IMPORTANTE: Non essere troppo conservativo! Se una whale da milioni punta $200k+ "
    "su un mercato politico/economico con prezzo basso, probabilmente sa qualcosa. "
    "Almeno 2-3 mercati su 10 dovrebbero essere COPY o WATCH.\n\n"
    "Rispondi SOLO in questo formato, in italiano:\n"
    "COPY (o WATCH o SKIP)\n"
    "Rischio: X/10\n"
    "Vale la pena?: [1 frase chiara]\n"
    "Cosa sta succedendo: [2-3 righe, spiega il mercato come a un amico]\n"
    "Sospetto?: No/Forse/Sì"
)


def analyze_with_claude(trade, state: dict = None, reddit_context: str = ""):
    size        = _sz(trade)
    price       = float(trade.get("price") or trade.get("outcomePrice") or 0.5)
    side        = trade.get("side") or "YES"
    market      = trade.get("title") or trade.get("question") or "Mercato"
    wallet_full = (trade.get("maker") or trade.get("userAddress") or "0x???").lower()
    wallet      = wallet_full[:14] + "..."
    trust_score = trade.get("whale_trust_score", 40)
    whale_name  = trade.get("whale_username", wallet)

    # Profila whale con volume storico
    state = state or {}
    profile = profile_whale(wallet_full, state)
    total_vol = profile.get("total_volume_usd", 0)
    tier = _classify(size, total_vol)

    # Confidence score pre-Claude
    confidence = compute_confidence(trade, state)

    algo_stats = state.get("algo_stats", {})
    accuracy   = algo_stats.get("accuracy_pct")
    acc_str    = (f"Track record sistema: {accuracy}% di COPY corretti"
                  if accuracy is not None else "Track record sistema: in raccolta dati")

    text = (
        f"Mercato: {market}\n"
        f"Direzione: {side}\n"
        f"Prezzo attuale: {price:.3f} (probabilità implicita {price*100:.0f}%)\n"
        f"Volume/Size singola: ${size:,.0f} USDC\n"
        f"Volume storico wallet: ${total_vol:,.0f} USDC\n"
        f"Tier: {tier}\n"
        f"Whale: {whale_name} (trust score: {trust_score}/100)\n"
        f"Confidence pre-analisi: {confidence}/100\n"
        f"{acc_str}"
    )
    if reddit_context:
        text += f"\n\nInsight recenti da r/Polymarket:\n{reddit_context}"

    last_err = None
    for model in MODELS:
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": model,
                    "max_tokens": 500,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": f"Analizza:\n\n{text}"}],
                },
                timeout=30,
            )
            if r.status_code == 200:
                raw = "".join(b.get("text", "") for b in r.json().get("content", []))
                log(f"Claude OK ({model})", "OK")
                return _parse_claude(raw, market, side, price, size, wallet, tier,
                                     trust_score, whale_name, confidence)
            err = r.json().get("error", {}).get("message", r.text[:100])
            log(f"Claude {r.status_code} ({model}): {err}", "WARN")
            last_err = err
        except Exception as e:
            log(f"Claude eccezione ({model}): {e}", "WARN")
            last_err = str(e)

    raise RuntimeError(last_err)


def _parse_claude(raw, market, side, price, size, wallet, tier,
                  trust_score=40, whale_name="", confidence=50):
    def g(pat, flags=re.I):
        m = re.search(pat, raw, flags)
        return m.group(1).strip() if m else None

    # 3 verdetti: COPY, WATCH, SKIP
    if re.search(r"^COPY", raw, re.M):
        verdict = "COPY"
    elif re.search(r"^WATCH", raw, re.M):
        verdict = "WATCH"
    else:
        verdict = "SKIP"

    # Se Claude menziona sport nella risposta, forza SKIP + flag
    sport_in_response = bool(re.search(
        r"\b(sport|calcio|basket|partita|football|soccer|tennis|match)\b",
        raw, re.I
    ))
    if sport_in_response:
        verdict = "SKIP"

    return {
        "market":           market,
        "side":             side,
        "price":            price,
        "size":             size,
        "wallet":           wallet,
        "whale_name":       whale_name,
        "tier":             tier,
        "trust_score":      trust_score,
        "confidence":       confidence,
        "verdict":          verdict,
        "is_sport_flagged": sport_in_response,
        "risk_score":       int(g(r"Rischio[:\s]+(\d+)") or 5),
        "vale_pena":        g(r"Vale la pena\?[:\s]*(.+?)(?:\n|$)") or "",
        "spiegazione":      (g(r"Cosa sta succedendo[:\s]*(.+?)(?:\n(?:Sospetto))",
                               re.I | re.S) or raw[:300])[:400],
        "sospetto":         g(r"Sospetto\?[:\s]*(.+?)(?:\n|$)") or "No",
    }


# ── TELEGRAM ────────────────────────────────────────────────────────────────────
def build_message(results, state: dict = None, is_demo=False, best_skip=None):
    ts  = datetime.now().strftime("%d/%m/%Y %H:%M")
    msg = f"🐋 *Grandi Mosse su Polymarket*{' _(DEMO)_' if is_demo else ''}\n_{ts}_\n\n"

    copy_count = sum(1 for t in results if t["verdict"] == "COPY")
    watch_count = sum(1 for t in results if t["verdict"] == "WATCH")
    msg += f"Analizzati {len(results)} mercati non\\-sportivi da >\\${MIN_SIZE_USDC // 1000}k.\n"

    # Track record accuracy
    algo_stats = (state or {}).get("algo_stats", {})
    acc = algo_stats.get("accuracy_pct")
    resolved = algo_stats.get("resolved_copies", 0)
    if acc is not None:
        msg += f"📊 Track record: *{acc}%* accuracy \\({resolved} segnali risolti\\)\n"

    if copy_count or watch_count:
        parts = []
        if copy_count:
            parts.append(f"*{copy_count}* COPY")
        if watch_count:
            parts.append(f"*{watch_count}* WATCH")
        msg += f"{', '.join(parts)} — da seguire. 👇\n\n"
    else:
        msg += "Nessun COPY/WATCH oggi — ecco il *meno peggio*. 👇\n\n"

    # Mostra COPY, poi WATCH, poi meno peggio (mai sport)
    to_show = [t for t in results if t["verdict"] == "COPY"]
    to_show += [t for t in results if t["verdict"] == "WATCH"]
    if not to_show and best_skip:
        to_show = [best_skip]
    if not to_show:
        to_show = results[:3]

    for t in to_show:
        m = t["market"][:70] + ("..." if len(t["market"]) > 70 else "")
        v = t["verdict"]
        if v == "COPY":
            msg += "✅ *COPY — DA VALUTARE*\n"
        elif v == "WATCH":
            msg += "👁️ *WATCH — TIENI D'OCCHIO*\n"
        else:
            msg += "⭐ *IL MENO PEGGIO DI OGGI*\n"
        msg += f"📌 _{m}_\n"
        # Whale info + tier
        wname = t.get("whale_name", "")
        ts_val = t.get("trust_score", 40)
        conf = t.get("confidence", 50)
        if wname and "0xpool" not in wname and "0xdemo" not in wname:
            trust_icon = "🟢" if ts_val >= 70 else "🟡" if ts_val >= 50 else "🔴"
            msg += f"🐋 {wname} {trust_icon} Trust: {ts_val}/100 | {t.get('tier','')}\n"
        msg += f"🎯 Confidence: {conf}/100\n"
        if t.get("vale_pena"):
            msg += f"💡 {t['vale_pena']}\n"
        if t.get("spiegazione"):
            msg += f"📖 {t['spiegazione'][:300]}\n"
        risk = t["risk_score"]
        icon = "🟢" if risk <= 3 else "🟡" if risk <= 6 else "🔴"
        msg += f"{icon} Rischio: {risk}/10\n"
        if t.get("sospetto", "No") not in ("No", "N/A"):
            msg += "⚠️ Potrebbe essere gonfiato artificialmente\n"
        msg += "\n"

    # Top 5 whale dalla leaderboard
    leaderboard = (state or {}).get("leaderboard", {})
    if leaderboard:
        sorted_lb = sorted(leaderboard.values(),
                           key=lambda x: x.get("total_profit_usd", 0), reverse=True)[:5]
        msg += "─────────────────────\n🏆 *Top Whale Tracker*\n"
        for i, w in enumerate(sorted_lb, 1):
            profit = w.get("total_profit_usd", 0)
            ts_val = w.get("trust_score", 40)
            name = w.get("username", "?")[:18]
            msg += f"{i}\\. {name} \\+\\${profit:,.0f} \\(trust {ts_val}\\)\n"
        msg += "\n"

    return msg + "_Polymarket Whale Tracker v3 — Bruno_"


def send_telegram(message):
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_USER_ID, "text": message, "parse_mode": "Markdown"},
        timeout=15,
    )
    d = r.json()
    if not d.get("ok"):
        log(f"Telegram: {d.get('description', 'errore')}", "ERR")
    return d.get("ok", False)


# ── EMAIL ───────────────────────────────────────────────────────────────────────
def build_email_html(results, state: dict = None, is_demo=False, best_skip=None):
    ts = datetime.now().strftime("%d/%m/%Y %H:%M")
    copy_count = sum(1 for t in results if t["verdict"] == "COPY")
    watch_count = sum(1 for t in results if t["verdict"] == "WATCH")
    to_show = [t for t in results if t["verdict"] in ("COPY", "WATCH")] or \
              ([best_skip] if best_skip else results[:3])

    algo_stats = (state or {}).get("algo_stats", {})
    acc = algo_stats.get("accuracy_pct")
    resolved = algo_stats.get("resolved_copies", 0)
    acc_html = (f'<p>📊 <b>Track record:</b> '
                f'<span style="color:#1a7a3a;">{acc}%</span> accuracy ({resolved} segnali risolti)</p>'
                if acc is not None else "")

    rows = ""
    for t in to_show:
        v = t["verdict"]
        color   = "#1a7a3a" if v == "COPY" else "#2a6dd9" if v == "WATCH" else "#555"
        bg      = "#f0fff4" if v == "COPY" else "#f0f4ff" if v == "WATCH" else "#fafafa"
        badge   = "✅ COPY — DA VALUTARE" if v == "COPY" else \
                  "👁️ WATCH — DA SEGUIRE" if v == "WATCH" else "⭐ Il meno peggio di oggi"
        risk    = t["risk_score"]
        risk_color = "#2a9d2a" if risk <= 3 else "#e6a817" if risk <= 6 else "#d9534f"
        wname = t.get("whale_name", "")
        ts_val = t.get("trust_score", 40)
        trust_html = ""
        if wname and "0xpool" not in wname and "0xdemo" not in wname:
            tc = "#2a9d2a" if ts_val >= 70 else "#e6a817" if ts_val >= 50 else "#d9534f"
            trust_html = f'<span style="color:{tc};font-size:12px;">🐋 {wname} — Trust {ts_val}/100</span><br>'
        rows += f"""
        <tr style="background:{bg};border-bottom:1px solid #e0e0e0;">
          <td style="padding:16px;">
            <span style="font-weight:bold;color:{color};">{badge}</span><br>
            {trust_html}
            <span style="font-size:15px;font-weight:600;">{t['market'][:90]}</span><br><br>
            {f"<b>💡 {t['vale_pena']}</b><br>" if t.get('vale_pena') else ""}
            {f"📖 {t['spiegazione'][:200]}<br>" if t.get('spiegazione') else ""}
            <br>
            <span style="color:{risk_color};font-weight:bold;">Rischio: {risk}/10</span>
            {"<br>⚠️ <i>Potrebbe essere gonfiato artificialmente</i>" if t.get('sospetto','No') not in ('No','N/A') else ""}
          </td>
        </tr>"""

    leaderboard = (state or {}).get("leaderboard", {})
    lb_html = ""
    if leaderboard:
        sorted_lb = sorted(leaderboard.values(),
                           key=lambda x: x.get("total_profit_usd", 0), reverse=True)[:5]
        lb_rows = "".join(
            f'<tr><td style="padding:6px 12px;">{i}.</td>'
            f'<td style="padding:6px 12px;font-weight:600;">{w.get("username","?")[:20]}</td>'
            f'<td style="padding:6px 12px;color:#1a7a3a;">+${w.get("total_profit_usd",0):,.0f}</td>'
            f'<td style="padding:6px 12px;color:#555;">Trust {w.get("trust_score",40)}/100</td></tr>'
            for i, w in enumerate(sorted_lb, 1)
        )
        lb_html = (f'<div style="padding:16px;background:#f0f8ff;border-top:2px solid #0d1b2a;">'
                   f'<h3 style="margin:0 0 8px;">🏆 Top Whale Tracker</h3>'
                   f'<table style="width:100%;font-size:13px;">{lb_rows}</table></div>')

    demo_banner = ('<p style="background:#fff3cd;padding:8px;border-radius:4px;">'
                   '⚠️ Dati demo — nessun mercato reale trovato</p>') if is_demo else ""

    return f"""
    <html><body style="font-family:Arial,sans-serif;max-width:680px;margin:auto;color:#222;">
      <div style="background:#0d1b2a;padding:20px;border-radius:8px 8px 0 0;">
        <h2 style="color:#fff;margin:0;">🐋 Grandi Mosse su Polymarket</h2>
        <p style="color:#aaa;margin:4px 0 0;">{ts}</p>
      </div>
      <div style="padding:16px;background:#f5f5f5;">
        {demo_banner}{acc_html}
        <p>Analizzati <b>{len(results)}</b> movimenti da >${MIN_SIZE_USDC // 1000}k USDC.
           <b style="color:#1a7a3a;">{copy_count}</b> {'merita' if copy_count == 1 else 'meritano'} attenzione.</p>
      </div>
      <table width="100%" cellspacing="0" cellpadding="0">{rows}</table>
      {lb_html}
      <div style="padding:12px;background:#0d1b2a;border-radius:0 0 8px 8px;text-align:center;">
        <span style="color:#aaa;font-size:12px;">Polymarket Whale Tracker v3 — Bruno</span>
      </div>
    </body></html>"""


def send_email(results, state: dict = None, is_demo=False, best_skip=None):
    if not GMAIL_APP_PASSWORD:
        log("Email: GMAIL_APP_PASSWORD non impostata — salto invio email.", "WARN")
        return False
    try:
        msg = MIMEMultipart("alternative")
        ts  = datetime.now().strftime("%d/%m/%Y %H:%M")
        copy_count = sum(1 for t in results if t["verdict"] == "COPY")
        msg["Subject"] = f"🐋 Polymarket Whale Report {ts} — {copy_count} segnali"
        msg["From"]    = GMAIL_USER
        msg["To"]      = EMAIL_TO
        msg.attach(MIMEText(build_email_html(results, state, is_demo, best_skip), "html"))
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.starttls()
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.sendmail(GMAIL_USER, EMAIL_TO, msg.as_string())
        log("Email inviata!", "OK")
        return True
    except Exception as e:
        log(f"Email errore: {e}", "ERR")
        return False


# ── RUN SINGOLO ─────────────────────────────────────────────────────────────────
def run():
    state = load_state()
    state["run_count"] = state.get("run_count", 0) + 1
    run_n = state["run_count"]

    log("=" * 60)
    log(f"Run #{run_n} | Soglia: >${MIN_SIZE_USDC:,} | Max: {MAX_WHALES}")
    log("=" * 60)

    # 1. Controlla resolution dei mercati passati (self-improving)
    check_resolutions(state)

    # 2. Aggiorna leaderboard da Polymarket
    fetch_breaking_leaderboard(state)

    # 3. Reddit insights (ogni 10 run)
    reddit_context = fetch_reddit_insights(state)

    # 4. Fetch mercati whale
    ok, whales, total = fetch_polymarket_whales(MIN_SIZE_USDC, state)
    is_demo = not ok or not whales
    if is_demo:
        log("Nessun dato reale — uso dati demo.", "WARN")
        whales = [
            {"usdcSize": "125000", "price": "0.28", "side": "YES",
             "title": "Will the Fed cut rates in June 2026?",
             "userAddress": "0xdemo1", "whale_trust_score": 60, "whale_username": "demo_whale_1"},
            {"usdcSize": "180000", "price": "0.67", "side": "NO",
             "title": "Will BTC reach $150k before July 2026?",
             "userAddress": "0xdemo2", "whale_trust_score": 75, "whale_username": "demo_whale_2"},
            {"usdcSize": "220000", "price": "0.18", "side": "YES",
             "title": "Will Trump impose 50%+ tariffs on EU goods?",
             "userAddress": "0xdemo3", "whale_trust_score": 85, "whale_username": "demo_whale_3"},
        ]

    # 5. Analisi Claude
    results = []
    for i, trade in enumerate(whales[:MAX_WHALES]):
        name = trade.get("title") or trade.get("question") or "Mercato"
        log(f"Analisi [{i+1}/{min(MAX_WHALES, len(whales))}]: {name[:55]}...")
        try:
            result = analyze_with_claude(trade, state, reddit_context)
            results.append(result)
            log(f"→ {result['verdict']} | Rischio {result['risk_score']}/10 | "
                f"Trust {result.get('trust_score',40)} | Sospetto: {result.get('sospetto','No')}", "OK")
        except Exception as e:
            log(f"Errore analisi: {e}", "ERR")
            results.append({
                "market": name, "side": "N/D", "price": 0, "size": 0,
                "wallet": "—", "whale_name": "—", "tier": "—", "trust_score": 0,
                "verdict": "SKIP", "risk_score": 5,
                "vale_pena": "", "spiegazione": f"Errore: {str(e)[:100]}",
                "sospetto": "No",
            })
        if i < MAX_WHALES - 1:
            time.sleep(1)

    # 6. Salva COPY in watched_markets per tracking futuro
    update_watched_markets(state, results)

    # 7. Salva stato persistente
    save_state(state)

    # 8. Notifiche — best_skip esclude sport-flagged e sport nel titolo
    skips = [r for r in results
             if r["verdict"] == "SKIP"
             and not r.get("is_sport_flagged")
             and not _is_sport(r.get("market", ""))]
    best_skip = min(skips, key=lambda r: r["risk_score"]) if skips else None

    if results:
        log("Invio su Telegram...")
        try:
            if send_telegram(build_message(results, state, is_demo, best_skip)):
                log("Telegram inviato!", "OK")
        except Exception as e:
            log(f"Telegram: {e}", "ERR")

        log("Invio per email...")
        send_email(results, state, is_demo, best_skip)

    copy_count = sum(1 for r in results if r["verdict"] == "COPY")
    log(f"Run #{run_n} completato — {copy_count} COPY su {len(results)} analizzati.")
    return results


# ── LOOP CONTINUO ────────────────────────────────────────────────────────────────
def run_continuum():
    log("=" * 52)
    log("Modalità CONTINUUM attivata")
    log(f"Controllo ogni {CHECK_INTERVAL_MINUTES} minuti")
    log(f"Telegram solo su COPY: {ONLY_NOTIFY_ON_COPY}")
    log("Premi Ctrl+C per fermare")
    log("=" * 52)

    run_count = 0
    while True:
        run_count += 1
        log(f"─── Run #{run_count} ───────────────────────────────")
        try:
            results = run()
            copy_count = sum(1 for r in results if r.get("verdict") == "COPY")
            if copy_count:
                log(f"✓ {copy_count} COPY trovati — notifica inviata.", "OK")
            else:
                log(f"Nessun COPY — silenzio. Prossimo check tra {CHECK_INTERVAL_MINUTES} min.")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log(f"Errore nel run: {e}", "ERR")

        log(f"In attesa {CHECK_INTERVAL_MINUTES} min... (Ctrl+C per fermare)")
        try:
            time.sleep(CHECK_INTERVAL_MINUTES * 60)
        except KeyboardInterrupt:
            log("Fermato dall'utente.")
            break


# ── ENTRY POINT ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--continuum" in sys.argv or "-c" in sys.argv:
        run_continuum()
    else:
        run()
