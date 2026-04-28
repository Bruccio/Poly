#!/usr/bin/env python3
"""
Polymarket Whale Tracker v2 → Telegram + Email
- Soglia whale: $100k+ (solo Alpha/Insider)
- Leaderboard persistente con trust score aggiornato ogni run
- Filtro wash trading automatico
- Self-improving: traccia previsioni e verifica resolutions
- Reddit insights ogni run
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
import urllib.parse
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
SEND_HEARTBEAT         = os.environ.get("SEND_HEARTBEAT", "true").lower() != "false"
MAX_TRADE_AGE_HOURS    = 24   # scarta trade/mercati più vecchi di 24h

# ── STATO PERSISTENTE ────────────────────────────────────────────────────────────
STATE_FILE = "whale_state.json"

def _empty_state() -> dict:
    return {
        "leaderboard": {},
        "watched_markets": {},
        "resolved_archive": [],          # mercati risolti (rimossi da watched_markets)
        "algo_stats": {
            "total_copy_signals": 0,
            "resolved_copies": 0,
            "correct_copies": 0,
            "accuracy_pct": None,
            "last_updated": None,
            "shadow_profit_usdc": 0.0,   # ROI virtuale ($100 per segnale COPY)
            "shadow_roi_pct": None,       # ROI % cumulativo
        },
        "lessons_learned": [],           # errori passati → feedati a Claude per auto-miglioramento
        "reddit_cache": {
            "last_checked": None,
            "top_strategies": [],
        },
        "github_cache": {
            "last_checked": None,
            "insights": "",
            "repos": [],
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
    # NB: NON includere "win the " — è troppo comune in politica
    # ("Will Trump win the election", "Will X win the popular vote").
    # Per gli sport usiamo invece SPORT_PATTERNS che richiede contesto
    # (cup/championship/series/league/title/season) dopo "win".
    " vs ", " vs. ", " v ", " @ ", "beat ", "beats ",
    "score ", "scores ", "goal ", "match ", "game ",
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
    # Scommesse sportive esplicite (Polymarket vende handicap/moneyline)
    "spread:", "moneyline:", "total:", "1h spread:", "2h spread:",
    "1q spread:", "2q spread:", "3q spread:", "4q spread:",
    "over/under", "o/u", "puckline", "run line",
    # Nomi franchise NBA (spread/moneyline senza league nel titolo)
    "lakers", "celtics", "warriors", "nuggets", "heat", "bucks", "knicks",
    "76ers", "sixers", "nets", "suns", "mavericks", "mavs", "grizzlies",
    "clippers", "rockets", "thunder", "pelicans", "kings", "timberwolves",
    "wolves", "spurs", "trail blazers", "blazers", "jazz", "pistons",
    "cavaliers", "cavs", "raptors", "bulls", "hawks", "hornets", "magic",
    "pacers", "wizards",
    # Nomi franchise NFL
    "patriots", "chiefs", "eagles", "cowboys", "49ers", "packers",
    "bills", "ravens", "steelers", "bengals", "dolphins", "jets",
    "giants", "commanders", "vikings", "lions", "bears",
    # Nomi franchise MLB / NHL
    "yankees", "red sox", "dodgers", "mets", "cubs", "white sox",
    "maple leafs", "oilers", "bruins", "rangers", "canadiens",
]

# Pattern regex per catturare sport che sfuggono alle keyword
SPORT_PATTERNS = [
    r"will .+ win on \d{4}-\d{2}-\d{2}",       # "Will X win on 2026-04-05?" = partita
    r"will .+ win .+\b(season|series|cup|tournament|league|championship|title)\b",
    r"will .+ beat ",
    r"\b(home|away)\s+(win|team|game)\b",
    r"\b\d+\s+(goals?|points?|runs?|sets?|games?|touchdowns?)\b",
    # Nomi club: SOLO sigle UNIVOCHE per evitare falsi positivi.
    # "as", "ac", "cf", "sc", "rc" sono troppo comuni in inglese (es. "as next").
    r"\b(fc|afc|ssc|cska)\s+\w+",                # FC Barcelona, AFC Bournemouth, SSC Napoli
    r"\bfc\b",                                   # "Toulouse FC (-1.5)" — FC postfix
    r"\b(united|city|rovers|wanderers|athletic)\b.*\b(win|lose|draw|score)\b",
    r"\(-\d+(\.\d+)?\)",                        # spread notation "(-1.5)", "(-5.5)"
    r"\(\+\d+(\.\d+)?\)",                       # spread notation "(+2.5)"
    r"^\s*(1h|2h|1q|2q|3q|4q|ot)\s+",           # quarter/half period markers
]

def _is_sport(title: str) -> bool:
    t = title.lower()
    for kw in SPORT_KEYWORDS:
        k = kw.lower().strip()
        if not k:
            continue
        # Sigle corte (≤4 char) o solo alfanumeriche → word-boundary match
        # per evitare falsi positivi tipo NFL in "inflation".
        if len(k) <= 4 and k.isalnum():
            if re.search(rf"\b{re.escape(k)}\b", t):
                return True
        else:
            # keyword con spazi o punteggiatura → substring match OK
            if k in t:
                return True
    return any(re.search(p, t) for p in SPORT_PATTERNS)


# ── FILTRO MERCATI SCADUTI (title-based fallback) ────────────────────────────────
_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def _is_past_market(title: str) -> bool:
    """
    Fallback testuale: restituisce True se il titolo contiene una data già passata.
    Complementa is_future_market() che richiede il campo endDate strutturato.
    Es: "Fed decision in January?" → past in aprile 2026.
    """
    now = datetime.now(timezone.utc)
    t = title.lower()
    # ISO date: 2026-01-15
    for m in re.finditer(r'\b(\d{4})-(\d{2})-(\d{2})\b', title):
        try:
            if datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                        tzinfo=timezone.utc) < now:
                return True
        except ValueError:
            pass
    # Anno intero passato: 2025
    for m in re.finditer(r'\b(20\d{2})\b', title):
        if int(m.group(1)) < now.year:
            return True
    # "by/in/before <Mese> <Anno>"
    for m in re.finditer(
        r'\b(?:by|before|in|end of|through|until)\s+'
        r'(january|february|march|april|may|june|july|august|september|'
        r'october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)'
        r'\s+(\d{4})\b', t
    ):
        mn, yr = _MONTH_MAP.get(m.group(1), 0), int(m.group(2))
        if mn and datetime(yr, mn, 28, tzinfo=timezone.utc) < now:
            return True
    # "in/by <Mese>" senza anno → blocca SOLO se il mese è già passato quest'anno.
    # "December" senza anno potrebbe essere December 2026 → non bloccare.
    # Gamma API decide se il mercato è risolto; qui gestiamo solo i casi ovvi.
    for m in re.finditer(
        r'\b(?:by|before|in|end of)\s+'
        r'(january|february|march|april|may|june|july|august|september|'
        r'october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)'
        r'\b(?!\s+\d)', t
    ):
        mn = _MONTH_MAP.get(m.group(1), 0)
        if mn and mn < now.month:
            # Mese già passato nell'anno corrente (es. "in January" ad Aprile 2026)
            return True
        # mn >= now.month → potrebbe essere futuro (es. "in December" = December 2026)
        # Non bloccare: lascia decidere a Gamma API / is_market_resolved()
    # "by/before/on <Mese> <Giorno>, <Anno>" (es. "by April 5, 2026")
    for m in re.finditer(
        r'\b(?:by|before|on)\s+'
        r'(january|february|march|april|may|june|july|august|september|'
        r'october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)'
        r'\s+(\d{1,2})\s*,?\s*(\d{4})\b', t
    ):
        mn = _MONTH_MAP.get(m.group(1), 0)
        try:
            day, yr = int(m.group(2)), int(m.group(3))
        except ValueError:
            continue
        if mn and 1 <= day <= 31:
            try:
                if datetime(yr, mn, day, tzinfo=timezone.utc) < now:
                    return True
            except ValueError:
                pass
    # "by/before <Mese> <Giorno>" senza anno (es. "by March 31", "by Apr 15")
    # Assumi anno corrente; se già passato → blocca.
    for m in re.finditer(
        r'\b(?:by|before|on)\s+'
        r'(january|february|march|april|may|june|july|august|september|'
        r'october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)'
        r'\s+(\d{1,2})\b(?!\s*,?\s*\d{4})', t
    ):
        mn = _MONTH_MAP.get(m.group(1), 0)
        try:
            day = int(m.group(2))
        except ValueError:
            continue
        if mn and 1 <= day <= 31:
            try:
                candidate = datetime(now.year, mn, day, tzinfo=timezone.utc)
                if candidate < now:
                    return True
            except ValueError:
                pass
    # Q1-Q4 Anno
    for m in re.finditer(r'\bq([1-4])\s*(\d{4})\b', t):
        em, ed = _QUARTER_END[int(m.group(1))]
        if datetime(int(m.group(2)), em, ed, tzinfo=timezone.utc) < now:
            return True
    # "<Mese> <Anno>" senza preposizione
    for m in re.finditer(
        r'\b(january|february|march|april|may|june|july|august|september|'
        r'october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)'
        r'\s+(\d{4})\b', t
    ):
        mn, yr = _MONTH_MAP.get(m.group(1), 0), int(m.group(2))
        if mn and datetime(yr, mn, 28, tzinfo=timezone.utc) < now:
            return True
    return False


# ── FILTRO EVENTI GIÀ DECISI (world knowledge) ─────────────────────────────────
# Mercati il cui titolo non contiene una data esplicita ma l'esito è storicamente
# noto. Le whale possono ancora avere liquidità residua → i filtri di data non
# li catturano. Aggiornare questa lista quando nuovi eventi vengono decisi.
# Formato: (regex_pattern, descrizione breve per log)
_RESOLVED_EVENT_PATTERNS: list[tuple[str, str]] = [
    # ── Elezioni presidenziali USA 2024 (Trump ha vinto Nov 2024) ──
    (r"(republican|democrat).*(win|lose|carry).*presidential\s+election",
     "US Presidential Election 2024 (resolved Nov 2024)"),
    (r"presidential\s+election\s+winner",
     "US Presidential Election 2024 (resolved Nov 2024)"),
    (r"(win|lose|carry)\s+.*(pennsylvania|michigan|wisconsin|georgia|arizona|"
     r"nevada|north\s*carolina|ohio|florida|iowa|texas|virginia|minnesota|"
     r"new\s*hampshire|maine)\s*(presidential|election)",
     "US 2024 swing state (resolved Nov 2024)"),
    (r"will\s+(trump|harris|biden)\s+(win|be\s+elected|become\s+president)",
     "US Presidential Election 2024 (resolved Nov 2024)"),
    (r"next\s+president\s+of\s+the\s+united\s+states",
     "US Presidential Election 2024 (resolved Nov 2024)"),
    (r"electoral\s+college.*2024",
     "US Electoral College 2024 (resolved Nov 2024)"),
    (r"popular\s+vote.*2024",
     "US Popular Vote 2024 (resolved Nov 2024)"),
    (r"(kamala|harris|trump|biden).*popular\s+vote",
     "US Popular Vote 2024 (resolved Nov 2024)"),
    (r"popular\s+vote\s+(winner|win)",
     "US Popular Vote 2024 (resolved Nov 2024)"),
    (r"wins?\s+the\s+popular\s+vote",
     "US Popular Vote 2024 (resolved Nov 2024)"),
    # ── Elezioni/nomine USA 2024-2025 già avvenute ──
    (r"(speaker|majority\s+leader).*2024",
     "US Congress 2024 (resolved)"),
    (r"nyc\s+mayor|new\s+york\s+city\s+mayor",
     "NYC Mayoral 2025 (resolved — Mamdani)"),
    # ── Midterm / speciali già passati ──
    (r"(midterm|mid-term).*2022",
     "US Midterms 2022 (resolved)"),
    # ── Referenze temporali generiche già passate ──
    (r"(before|by)\s+end\s+of\s+2024",
     "Deadline 2024 (passed)"),
    (r"(before|by)\s+end\s+of\s+2025",
     "Deadline 2025 (passed)"),
]

def _is_known_resolved_event(title: str) -> bool:
    """Blocca mercati il cui esito è storicamente noto anche senza data nel titolo.
    Le whale possono piazzare ordini su mercati risolti per arbitraggio di liquidità
    residua — questo filtro impedisce che diventino segnali COPY/WATCH."""
    t = title.lower().strip()
    for pattern, _desc in _RESOLVED_EVENT_PATTERNS:
        if re.search(pattern, t):
            return True
    return False


# ── FILTRO MERCATI APERTI (Time Filter) ──────────────────────────────────────────
import dateutil.parser
def is_future_market(trade_data):
    """
    Verifica se il mercato è ancora aperto basandosi sulla endDate.
    Implementazione suggerita dalla guida Whale Tracking.
    """
    now = datetime.now(timezone.utc)
    if trade_data.get('resolved') is True:
        return False
    
    # Prova diversi campi possibili per la data di fine
    end_date = (trade_data.get('end_date_iso') or 
                trade_data.get('endDate') or 
                trade_data.get('end_timestamp') or
                trade_data.get('closedTime'))
    
    if not end_date:
        # Se non abbiamo info sulla data di fine, per sicurezza consideriamo il mercato aperto
        # a meno che non sia esplicitamente risolto.
        return True
        
    try:
        end_dt = dateutil.parser.isoparse(str(end_date))
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        return end_dt > now
    except Exception as e:
        log(f"Failed parsing end_date {end_date}: {e}", "WARN")
        return True # In caso di errore, meglio un falso positivo (mercato aperto)


# ── LOGGING ─────────────────────────────────────────────────────────────────────
# (definito prima così le funzioni successive possono usarlo)
def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    icon = {"OK": "✓", "ERR": "✗", "WARN": "⚠"}.get(level, "·")
    print(f"[{ts}] {icon} {msg}", flush=True)


# ── HTTP HELPER con RETRY + BACKOFF ─────────────────────────────────────────────
_H = {"Accept": "application/json", "User-Agent": "WhaleTracker/3.0"}

def _http_get(url: str, timeout: int = 12, retries: int = 3):
    """
    Wrapper per requests.get con retry esponenziale (2s/4s/8s).
    Gestisce 429 (rate limit), timeout e errori di rete.
    Restituisce requests.Response oppure None senza lanciare eccezioni.
    """
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=_H, timeout=timeout)
            if r.status_code == 429:
                wait = 2 ** (attempt + 1)
                log(f"Rate limit (429), attendo {wait}s... [{url[:55]}]", "WARN")
                time.sleep(wait)
                continue
            return r
        except requests.exceptions.Timeout:
            log(f"Timeout tentativo {attempt+1}: {url[:60]}", "WARN")
        except requests.exceptions.RequestException as e:
            log(f"Errore rete tentativo {attempt+1}: {str(e)[:80]}", "WARN")
        if attempt < retries - 1:
            time.sleep(2 ** attempt)
    log(f"Tutti i tentativi falliti: {url[:60]}", "ERR")
    return None


# ── GAMMA RESOLUTION CACHE ────────────────────────────────────────────────────────
_GAMMA_RESOLUTION_CACHE: dict = {}  # title_lower → market obj, svuotato a ogni run

def build_gamma_resolution_cache() -> dict:
    """Bulk-fetch mercati da Gamma API e indicizza per question (lowercase).
    Include sia mercati attivi che chiusi recenti.
    Chiamata una volta per run, riutilizzata da is_market_resolved() e check_resolutions()."""
    global _GAMMA_RESOLUTION_CACHE
    _GAMMA_RESOLUTION_CACHE.clear()
    today = datetime.now(timezone.utc)
    log(f"Data odierna: {today.strftime('%d/%m/%Y')} — costruisco cache Gamma...", "OK")

    all_markets = []
    # 1. Mercati attivi/recenti (top 1000 per volume)
    for offset in [0, 500]:
        r = _http_get(f"https://gamma-api.polymarket.com/markets?limit=500&offset={offset}")
        if r and r.status_code == 200:
            data = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
            all_markets.extend(data)
        time.sleep(0.3)
    # 2. Mercati chiusi recentemente — 1000 chiusi (2 pagine da 500)
    #    Cattura elezioni, decisioni Fed, eventi risolti negli ultimi 6-12 mesi
    for offset in [0, 500]:
        r = _http_get(f"https://gamma-api.polymarket.com/markets?limit=500&offset={offset}"
                      f"&closed=true&order=closeTime&ascending=false")
        if r and r.status_code == 200:
            data = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
            all_markets.extend(data)
        time.sleep(0.3)

    for m in all_markets:
        q = (m.get("question") or "").lower().strip()
        if q:
            _GAMMA_RESOLUTION_CACHE[q] = m
        # conditionId — lookup diretto, immune a differenze di titolo
        cid = m.get("conditionId") or m.get("id") or ""
        if cid:
            _GAMMA_RESOLUTION_CACHE[cid] = m
        # clobTokenIds — la Data API può restituire il tokenId dell'outcome (YES/NO)
        # invece del conditionId del market; indicizziamo anche quelli
        try:
            for tok in json.loads(m.get("clobTokenIds") or "[]"):
                if tok:
                    _GAMMA_RESOLUTION_CACHE[tok] = m
        except Exception:
            pass
    log(f"Gamma resolution cache: {len(_GAMMA_RESOLUTION_CACHE)} mercati indicizzati "
        f"(attivi + chiusi recenti, per titolo + conditionId + tokenId)", "OK")
    return _GAMMA_RESOLUTION_CACHE


def _resolved_from_cache_entry(m: dict) -> bool:
    """Dato un market object Gamma, ritorna True se il mercato è chiuso/risolto."""
    if m.get("closed") is True or m.get("isResolved") is True:
        return True
    end_date = m.get("endDate") or m.get("end_date_iso")
    if end_date:
        try:
            end_dt = dateutil.parser.isoparse(str(end_date))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            if end_dt < datetime.now(timezone.utc):
                return True
        except Exception:
            pass
    return False


def is_market_resolved(title: str, condition_id: str = "") -> bool:
    """Controlla se un mercato è risolto/chiuso usando la cache Gamma.
    condition_id (conditionId dalla Data API) permette un lookup diretto e affidabile,
    immune a differenze di titolo tra Data API e Gamma API.
    Gestisce titoli troncati dalla Data API (es. 'US strikes Iran by...?').
    Ritorna True = da escludere.
    Policy per titoli troncati: se non verificabile → blocca per sicurezza."""
    if not title and not condition_id:
        return False

    # Determina troncatura subito — usato in più punti della funzione
    is_truncated = bool(title) and ("..." in title or "\u2026" in title)

    # 0. Lookup per conditionId — SEMPRE prioritario sul text matching
    if condition_id:
        m = _GAMMA_RESOLUTION_CACHE.get(condition_id)
        if m is not None:
            return _resolved_from_cache_entry(m)
        # Non è nella cache bulk → chiama Gamma API direttamente per ID
        # Questo è il fix definitivo: ID-based lookup non dipende dal formato del titolo
        r = _http_get(
            f"https://gamma-api.polymarket.com/markets?conditionId={condition_id}&limit=1",
            timeout=8, retries=2,
        )
        if r and r.status_code == 200:
            items = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
            # Gamma API ignora conditionId sconosciuti e restituisce un market random
            # (verificato: GET /markets?conditionId=0xfake → ritorna il primo market
            # del feed). Filtriamo per accettare solo risposte che hanno davvero il
            # conditionId richiesto — altrimenti cacheremmo dati sbagliati sotto la
            # chiave e compromettere il fallback sul titolo.
            cid_lower = condition_id.lower()
            items = [m for m in items
                     if (m.get("conditionId") or "").lower() == cid_lower]
            if items:
                m = items[0]
                _GAMMA_RESOLUTION_CACHE[condition_id] = m
                q = (m.get("question") or "").lower().strip()
                if q:
                    _GAMMA_RESOLUTION_CACHE[q] = m
                return _resolved_from_cache_entry(m)
            # ID non trovato in Gamma → rimosso/risolto molto tempo fa.
            # Per titoli troncati non abbiamo altro modo per verificare → blocca.
            if is_truncated:
                log(f"Titolo troncato + conditionId non in Gamma → blocco: {title[:60]}", "WARN")
                return True

    if not title:
        return False

    # Rimuovi troncatura "..." dalla Data API — "US strikes Iran by...?" →
    # "US strikes Iran by" per trovare "Will the US strike Iran by Dec 31?"
    # (is_truncated è già calcolato sopra, non riassegnare)
    t_clean = (title.replace("...", "").replace("\u2026", "")
               .rstrip("?").strip())
    t_lower = title.lower().strip()
    t_clean_lower = t_clean.lower().strip()

    # 1. Match esatto per titolo nella cache
    match = _GAMMA_RESOLUTION_CACHE.get(t_lower)

    # Helper: estrae parole significative (> 2 char, non stopword)
    _SW = {"will", "the", "and", "that", "this", "are", "for", "not", "have",
           "with", "from", "into", "than", "more", "been", "their"}
    def _keywords(s: str) -> list:
        return [w for w in re.findall(r'[a-z0-9]+', s.lower())
                if len(w) > 2 and w not in _SW]

    def _fuzzy_overlap(qa: list, qb: list, min_match: int = 2) -> bool:
        """True se almeno min_match parole di qa hanno un prefisso comune (5 char)
        con una parola di qb — gestisce coniugazioni (strikes≈strike, ran≈run)."""
        matched = 0
        sb = set(qb)
        for w in qa:
            if w in sb or any(w[:5] == cw[:5] for cw in sb if len(cw) >= 5 and len(w) >= 5):
                matched += 1
                if matched >= min_match:
                    return True
        return False

    # 2. Match parziale — titoli normali: prefisso 40 char;
    #    titoli troncati: fuzzy overlap su parole chiave (handle: strikes≈strike)
    if not match:
        if is_truncated:
            kw_q = _keywords(t_clean_lower)
            for cached_q, cached_m in _GAMMA_RESOLUTION_CACHE.items():
                if isinstance(cached_q, str) and len(kw_q) >= 1:
                    if _fuzzy_overlap(kw_q, _keywords(cached_q), min_match=2):
                        match = cached_m
                        break
        else:
            key_prefix = t_lower[:40]
            for cached_q, cached_m in _GAMMA_RESOLUTION_CACHE.items():
                if isinstance(cached_q, str) and key_prefix in cached_q:
                    match = cached_m
                    break

    # 3. Fallback: ricerca mirata su Gamma API
    #    Titolo troncato → usa testo pulito (senza "...") come query
    if not match:
        search_query = t_clean if is_truncated else title
        encoded = urllib.parse.quote(search_query[:80])
        r = _http_get(
            f"https://gamma-api.polymarket.com/markets?search={encoded}&limit=5",
            timeout=8, retries=2,
        )
        if r and r.status_code == 200:
            results = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
            kw_q = _keywords(t_clean_lower)
            for candidate in results:
                cq = (candidate.get("question") or "").lower().strip()
                is_hit = (is_truncated and _fuzzy_overlap(kw_q, _keywords(cq), 2)
                          or not is_truncated and (t_lower[:40] in cq or cq[:40] in t_lower))
                if is_hit:
                    match = candidate
                    _GAMMA_RESOLUTION_CACHE[cq] = candidate
                    cid = candidate.get("conditionId") or candidate.get("id") or ""
                    if cid:
                        _GAMMA_RESOLUTION_CACHE[cid] = candidate
                    break

    # 4. Se il titolo è troncato e non abbiamo trovato niente → cerca anche
    #    sull'endpoint events (un event può coprire più market con un solo slug)
    if not match and is_truncated and t_clean:
        slug_guess = t_clean_lower.replace(" ", "-").replace("?", "")[:60]
        # Rimuovi articoli e preposizioni comuni per ottenere slug più pulito
        slug_guess = re.sub(r'\b(will|the|a|an|by|in|on|at|for|of|to|be)\b-?', '', slug_guess)
        slug_guess = re.sub(r'-+', '-', slug_guess).strip('-')
        if slug_guess:
            r = _http_get(
                f"https://gamma-api.polymarket.com/events?slug={urllib.parse.quote(slug_guess)}&limit=3",
                timeout=8, retries=2,
            )
            if r and r.status_code == 200:
                events = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
                for ev in events:
                    # L'evento è chiuso se tutti i suoi market sono chiusi
                    if ev.get("closed") or ev.get("resolved"):
                        # Usa dati evento come proxy per il mercato
                        match = {"closed": True, "isResolved": True,
                                 "winningOutcome": ev.get("outcome", "")}
                        break

    if not match:
        # Policy: titolo troncato non verificabile da nessun endpoint Gamma
        # → blocca per sicurezza. Un mercato attivo e liquido sarebbe nella cache bulk.
        # Se non lo troviamo da nessuna parte (cache + API ID + text search + slug),
        # quasi certamente è risolto/rimosso da tempo (es. "US strikes Iran by...?").
        if is_truncated:
            log(f"Titolo troncato non verificabile in Gamma → blocco: {title[:60]}", "WARN")
            return True
        return False

    return _resolved_from_cache_entry(match)


def _get_poly_url(condition_id: str, title: str = "") -> str:
    """Costruisce l'URL Polymarket per un market cercandolo nella Gamma cache.
    URL formato: https://polymarket.com/event/{groupSlug}/{slug}
    Fallback: https://polymarket.com/search?q={titolo}"""
    m = None
    if condition_id:
        m = _GAMMA_RESOLUTION_CACHE.get(condition_id)
    if not m and title:
        m = _GAMMA_RESOLUTION_CACHE.get(title.lower().strip())
    if m:
        slug = m.get("slug") or ""
        group_slug = (m.get("groupSlug") or m.get("group_slug") or
                      m.get("eventSlug") or m.get("event_slug") or "")
        if slug and group_slug:
            return f"https://polymarket.com/event/{group_slug}/{slug}"
        if slug:
            return f"https://polymarket.com/event/{slug}"
    # Fallback: cerca per titolo su Polymarket
    if title:
        return f"https://polymarket.com/search?q={urllib.parse.quote(title[:80])}"
    return ""


# ── CACHE IN-RUN PER ACTIVITY WALLET ────────────────────────────────────────────
_ACTIVITY_CACHE: dict = {}  # svuotato all'inizio di ogni run()

def _cached_activity(wallet: str, limit: int = 100) -> list:
    """
    Scarica (e memorizza per il run corrente) l'attività di trading di un wallet.
    Evita di chiamare lo stesso endpoint 2-3 volte per run.
    """
    url = (f"https://data-api.polymarket.com/activity"
           f"?user={wallet}&limit={limit}&type=TRADE")
    if url in _ACTIVITY_CACHE:
        return _ACTIVITY_CACHE[url]
    r = _http_get(url, timeout=10)
    if not r or r.status_code != 200:
        _ACTIVITY_CACHE[url] = []
        return []
    data = r.json()
    result = data if isinstance(data, list) else data.get("data", [])
    _ACTIVITY_CACHE[url] = result
    return result


# ── LEADERBOARD POLYMARKET ──────────────────────────────────────────────────────
def fetch_breaking_leaderboard(state: dict):
    """
    Scarica la leaderboard da data-api.polymarket.com/v1/leaderboard e aggiorna
    state["leaderboard"] con trust_score calcolato.

    Endpoint ufficiale (doc: docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings):
      GET https://data-api.polymarket.com/v1/leaderboard
      params: orderBy=PNL|VOL, timePeriod=DAY|WEEK|MONTH|ALL, limit=1-50
      fields:  rank, proxyWallet, userName, vol, pnl, profileImage, xUsername, verifiedBadge

    Chiamiamo due finestre/ordini per avere whale diverse:
      - PNL/ALL  → top profit storici (sticky, stabili)
      - PNL/WEEK → top profit settimanali (rotano)
      - VOL/WEEK → top volume settimanali (attivi davvero)
    """
    state.setdefault("leaderboard", {})

    # ── Pulisci seed stale: entries il cui key non è un wallet 0x valido.
    stale = [k for k in list(state["leaderboard"].keys())
             if not (isinstance(k, str) and k.startswith("0x") and len(k) >= 40)]
    for k in stale:
        del state["leaderboard"][k]
    if stale:
        log(f"Leaderboard: rimossi {len(stale)} entries stale (seed/fake wallet)", "OK")

    combos = [
        ("PNL", "ALL"),
        ("PNL", "WEEK"),
        ("VOL", "WEEK"),
    ]
    total_updated = 0
    for order_by, time_period in combos:
        url = (f"https://data-api.polymarket.com/v1/leaderboard"
               f"?orderBy={order_by}&timePeriod={time_period}&limit=50")
        try:
            r = _http_get(url)
            if not r or r.status_code != 200:
                log(f"Leaderboard {order_by}/{time_period}: "
                    f"HTTP {getattr(r,'status_code','?')}", "WARN")
                continue
            data = r.json()
            entries = data if isinstance(data, list) else data.get("data", [])
            if not entries:
                continue
            updated = 0
            for e in entries:
                wallet = (e.get("proxyWallet") or "").lower()
                if not wallet.startswith("0x") or len(wallet) < 40:
                    continue
                profit = float(e.get("pnl") or 0)
                volume = float(e.get("vol") or 0)
                username = e.get("userName") or wallet[:10]
                # Trust score: base 50 + bonus log-scala su profit e volume
                score = 50
                if profit > 0:
                    score += min(30, int(math.log10(max(profit, 1)) * 5))
                if volume > 0:
                    score += min(20, int(math.log10(max(volume, 1)) * 3))
                if e.get("verifiedBadge"):
                    score += 5
                score = min(100, score)
                existing = state["leaderboard"].get(wallet, {})
                # Mantieni il max di profit/volume tra le diverse finestre (ALL vince
                # per i top storici, WEEK per i recenti — così l'arricchimento con
                # trust_score resta coerente).
                state["leaderboard"][wallet] = {
                    "username":         username,
                    "wallet":           wallet,
                    "total_profit_usd": max(profit, existing.get("total_profit_usd", 0)),
                    "total_volume_usd": max(volume, existing.get("total_volume_usd", 0)),
                    "trust_score":      max(score,  existing.get("trust_score", 0)),
                    "times_seen":       existing.get("times_seen", 0),
                    "copy_accuracy":    existing.get("copy_accuracy", None),
                    "recent_bets":      existing.get("recent_bets", []),
                    "last_seen":        datetime.now(timezone.utc).isoformat(),
                    "source_windows":   sorted(set(
                        existing.get("source_windows", [])
                        + [f"{order_by}/{time_period}"]
                    )),
                }
                updated += 1
            total_updated += updated
            log(f"Leaderboard {order_by}/{time_period}: {updated} whale", "OK")
        except Exception as exc:
            log(f"Leaderboard {order_by}/{time_period}: {exc}", "WARN")

    if total_updated == 0:
        log("Leaderboard: nessuna whale scaricata (continuo senza)", "WARN")
    else:
        log(f"Leaderboard: {len(state['leaderboard'])} whale totali in cache", "OK")


# ── WHALE-FIRST AUTO DETECTION ───────────────────────────────────────────────────
def fetch_whale_trades(state: dict) -> list:
    """
    Whale-first approach: scarica i trade recenti dei top wallet dalla leaderboard.
    Non usa soglie manuali — le whale sono già identificate per profitto storico.
    Restituisce lista di trade pronti per analisi Claude.
    """
    leaderboard = state.get("leaderboard", {})
    if not leaderboard:
        log("fetch_whale_trades: leaderboard vuota, skip", "WARN")
        return []

    # Prendi top 40 wallet ordinati per trust_score.
    # Le top 10-20 storiche sono spesso "dormienti" (whale dell'elezione 2024
    # che ora tradano solo sport): allargare il pool dà ossigeno alle whale
    # ancora attive su mercati politici/macro/crypto.
    sorted_wallets = sorted(
        leaderboard.items(),
        key=lambda kv: kv[1].get("trust_score", 0),
        reverse=True,
    )[:40]

    all_trades: list = []
    seen_titles: set = set()

    for wallet_key, lb_entry in sorted_wallets:
        wallet = lb_entry.get("wallet") or wallet_key
        # Salta wallet demo/pool
        if wallet.startswith("0x") and len(wallet) < 15:
            continue
        username = lb_entry.get("username", wallet[:10])
        trust_score = lb_entry.get("trust_score", 50)
        log(f"  Scarico trade da wallet {username} (trust {trust_score})...")
        try:
            trades = _cached_activity(wallet, limit=20)
            added = 0
            wallet_bets: list = []  # per recent_bets di questo wallet
            # Aggiorna last_seen ogni volta che scarico l'attività della whale
            if wallet_key in state.get("leaderboard", {}):
                state["leaderboard"][wallet_key]["last_seen"] = datetime.now(timezone.utc).isoformat()
            for t in trades:
                raw_title = (t.get("title") or t.get("question") or "").strip()
                market_id  = (t.get("conditionId") or t.get("market") or
                              t.get("asset_id") or t.get("tokenId") or "")
                # Se il titolo è un hex conditionId o manca, prova dalla Gamma cache
                if not raw_title or raw_title.startswith("0x"):
                    cached = _GAMMA_RESOLUTION_CACHE.get(market_id) if market_id else None
                    if cached:
                        raw_title = cached.get("question") or cached.get("title") or raw_title
                title = raw_title.strip()
                if not title or title.startswith("0x"):
                    continue
                size = float(t.get("usdcSize") or t.get("size") or
                             t.get("amount") or 0)
                price = float(t.get("price") or t.get("outcomePrice") or 0.5)
                side = t.get("side") or t.get("type") or "YES"
                # Salva nelle recenti del wallet (max 5, SOLO mercati non sport
                # e non già risolti — così la dashboard non mostra residui stale).
                if (len(wallet_bets) < 5
                        and not _is_sport(title)
                        and not _is_past_market(title)
                        and not _is_known_resolved_event(title)):
                    cond_id = (t.get("conditionId") or t.get("market") or "")
                    bet = {
                        "title": title,
                        "side":  side,
                        "size":  size,
                        "price": price,
                        "conditionId": cond_id,
                        "poly_url": _get_poly_url(cond_id, title),
                    }
                    bet["copy_advice"] = classify_bet(bet, whale_trust=trust_score)
                    wallet_bets.append(bet)
                title_key = title.lower()
                if title_key in seen_titles:
                    continue
                if _is_sport(title):
                    continue
                if _is_past_market(title):
                    continue
                if _is_known_resolved_event(title):
                    log(f"  Evento già deciso: {title[:55]}", "WARN")
                    continue
                # Prova più chiavi: conditionId, market, asset_id, tokenId
                cond_id = (t.get("conditionId") or t.get("market") or
                           t.get("asset_id") or t.get("tokenId") or "")
                # Se il trade non ha endDate, prova a ottenerla dalla Gamma cache
                if not t.get("endDate") and cond_id:
                    cached = _GAMMA_RESOLUTION_CACHE.get(cond_id)
                    if cached and cached.get("endDate"):
                        t["endDate"] = cached["endDate"]
                if is_market_resolved(title, condition_id=cond_id):
                    continue
                if not is_future_market(t):
                    continue
                # Filtro size minimo: trade <$1k = retail noise, non whale signal.
                # Risparmia API call Claude e tiene il pool focalizzato.
                if size < 1000:
                    continue
                seen_titles.add(title_key)
                all_trades.append({
                    "usdcSize":         str(size),
                    "price":            str(price),
                    "side":             side,
                    "title":            title,
                    "userAddress":      wallet,
                    "whale_trust_score": trust_score,
                    "whale_username":   username,
                    "_source":          "whale-first",
                })
                added += 1
            # Aggiorna recent_bets nel leaderboard per la dashboard
            if wallet_bets:
                state["leaderboard"][wallet_key]["recent_bets"] = wallet_bets
            if added:
                log(f"    → {added} trade da {username}", "OK")
            time.sleep(0.5)  # rate limit cortesia
        except Exception as e:
            log(f"  fetch_whale_trades {username}: {str(e)[:80]}", "WARN")

    # Ordina per trust_score × size (trade più significativi prima)
    all_trades.sort(
        key=lambda x: x["whale_trust_score"] * float(x.get("usdcSize") or 0),
        reverse=True,
    )
    log(f"fetch_whale_trades: {len(all_trades)} trade da {len(sorted_wallets)} wallet", "OK")
    return all_trades


# ── WASH TRADING DETECTION ──────────────────────────────────────────────────────
def is_wash_trader(wallet: str) -> bool:
    """
    Heuristica: un wallet che compra E vende la stessa posizione più volte
    sullo stesso mercato è probabilmente wash trader.
    """
    if not wallet or wallet.startswith("0xpool") or wallet.startswith("0xdemo"):
        return False
    try:
        trades = _cached_activity(wallet, limit=100)
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
    """Ogni run scarica i top post di r/Polymarket e cerca strategie."""
    cache = state.get("reddit_cache", {})
    try:
        r = requests.get(
            "https://www.reddit.com/r/Polymarket/hot.json?limit=10",
            headers={"Accept": "application/json",
                     "User-Agent": "WhaleTracker/3.0 (by /u/polymarket_bot)"},
            timeout=12)
        if not r or r.status_code != 200:
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


# ── GITHUB ALGORITHM SCOUT ──────────────────────────────────────────────────────
def fetch_github_insights(state: dict) -> str:
    """Ogni run cerca su GitHub i repo Polymarket/prediction-market più aggiornati.
    Estrae pattern algoritmici dai README e li passa come contesto a Claude.
    Usa GITHUB_TOKEN (disponibile in GitHub Actions) per evitare rate limit."""
    token = os.environ.get("GITHUB_TOKEN", "")
    hdrs = {"Accept": "application/vnd.github+json", "User-Agent": "PolyWhaleBot/3.0"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"

    queries = [
        "polymarket whale tracker language:python",
        "polymarket analytics prediction market",
        "prediction market insider trading detection",
    ]

    repos: dict = {}
    for q in queries:
        try:
            url = (f"https://api.github.com/search/repositories"
                   f"?q={urllib.parse.quote(q)}&sort=updated&order=desc&per_page=5")
            r = requests.get(url, headers=hdrs, timeout=10)
            if r.status_code == 403:
                log("GitHub API rate limit", "WARN")
                break
            if r.status_code != 200:
                continue
            for item in r.json().get("items", [])[:5]:
                name = item.get("full_name", "")
                if name and name not in repos:
                    repos[name] = {
                        "stars": item.get("stargazers_count", 0),
                        "desc": (item.get("description") or "")[:150],
                        "updated": (item.get("updated_at") or "")[:10],
                        "url": item.get("html_url", ""),
                    }
        except Exception as e:
            log(f"GitHub search: {e}", "WARN")
        time.sleep(0.8)

    if not repos:
        return state.get("github_cache", {}).get("insights", "")

    # Top 4 repo per stars — leggi README per estrarre pattern
    top = sorted(repos.items(), key=lambda x: x[1]["stars"], reverse=True)[:4]
    parts = []
    for name, meta in top:
        snippet = ""
        for branch in ("main", "master"):
            try:
                raw = f"https://raw.githubusercontent.com/{name}/{branch}/README.md"
                rr = requests.get(raw, timeout=6, headers={"User-Agent": "PolyWhaleBot/3.0"})
                if rr.status_code == 200:
                    # Estrai le prime 600 char del README (di solito contengono la descrizione algo)
                    snippet = rr.text[:600].replace("\n", " ").strip()
                    break
            except Exception:
                pass
        line = f"• {name} ({meta['stars']}★, {meta['updated']}): {meta['desc']}"
        if snippet:
            line += f"\n  README: {snippet[:300]}"
        parts.append(line)
        time.sleep(0.3)

    summary = "Repo GitHub Polymarket/Prediction-Market (aggiornati oggi):\n" + "\n\n".join(parts)
    state["github_cache"] = {
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "insights": summary,
        "repos": [n for n, _ in top],
    }
    log(f"GitHub scout: {len(repos)} repo trovati, {len(top)} analizzati", "OK")
    return summary


# ── SELF-IMPROVING: CONTROLLA RESOLUTION ────────────────────────────────────────
def check_resolutions(state: dict):
    """Controlla se i mercati watched hanno avuto una resolution.
    I mercati risolti vengono spostati in resolved_archive e rimossi da watched_markets
    (così non compaiono più nella dashboard).
    """
    watched = state.get("watched_markets", {})
    archive = state.setdefault("resolved_archive", [])
    now = datetime.now(timezone.utc)

    # 0. Migrazione one-shot: se ci sono mercati già risolti in watched (da vecchie versioni),
    #    spostali subito in archive
    for key, market in list(watched.items()):
        if market.get("resolved"):
            archive.append(market)
            del watched[key]

    # 1. Pulizia mercati obsoleti (stale) > 30 giorni non risolti
    removed_stale = 0
    for key, market in list(watched.items()):
        df_str = market.get("date_flagged")
        if df_str:
            try:
                df_dt = datetime.strptime(df_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if (now - df_dt).days > 30:
                    del watched[key]
                    removed_stale += 1
            except: pass
    if removed_stale:
        log(f"Cleanup: rimossi {removed_stale} mercati obsoleti (>30gg)", "OK")

    # 2. Controlla ogni watched market usando is_market_resolved() — unica fonte di verità.
    #    Quella funzione gestisce: cache bulk → conditionId API → fuzzy title → slug event.
    #    Dopo che ritorna True, il market object è già in _GAMMA_RESOLUTION_CACHE.
    if not _GAMMA_RESOLUTION_CACHE:
        log("check_resolutions: cache Gamma vuota, skip verifica", "WARN")
    newly_resolved = 0

    for key, market in list(watched.items()):
        question = market.get("question", "")
        cond_id  = market.get("conditionId") or market.get("condition_id") or ""

        # Delega tutto il lookup (cache + API ID + fuzzy + search + slug) a is_market_resolved()
        if not is_market_resolved(question, condition_id=cond_id):
            continue  # ancora aperto o non trovato

        # Market risolto — recupera l'oggetto dalla cache (è stato popolato da is_market_resolved)
        q_lower = question.lower().strip()
        t_clean = q_lower.replace("...", "").replace("\u2026", "").rstrip("?").strip()
        match = (_GAMMA_RESOLUTION_CACHE.get(cond_id)
                 or _GAMMA_RESOLUTION_CACHE.get(q_lower)
                 or _GAMMA_RESOLUTION_CACHE.get(t_clean)
                 or next((v for k, v in _GAMMA_RESOLUTION_CACHE.items()
                          if isinstance(k, str) and t_clean[:20] and t_clean[:20] in k), None))
        if not match:
            # Nessun dato di outcome disponibile — archivia comunque senza outcome
            match = {}


        winning = (match.get("winningOutcome") or "").upper()
        if not winning and match.get("closed"):
            try:
                prices = json.loads(match.get("outcomePrices", "[]"))
                if prices and any(float(p) > 0.98 for p in prices):
                    idx = [float(p) > 0.98 for p in prices].index(True)
                    outcomes = json.loads(match.get("outcomes", "[]"))
                    winning = outcomes[idx].upper() if idx < len(outcomes) else ""
            except: pass

        our_side = market.get("side", "YES").upper()
        correct = (winning == our_side or
                   (winning in ("YES", "1") and our_side == "YES") or
                   (winning in ("NO", "0") and our_side == "NO")) if winning else None
        market.update({"resolved": True, "resolution": winning or "?", "correct": correct,
                       "resolution_date": now.isoformat()})

        # ── Self-correction: salva errori COPY per feedarli a Claude
        if market.get("our_verdict") == "COPY" and correct is False:
            lesson = {
                "question":    question[:80],
                "our_side":    our_side,
                "outcome":     winning or "?",
                "entry_price": market.get("entry_price", 0),
                "why":         market.get("why_snippet", ""),
                "date":        now.strftime("%Y-%m-%d"),
            }
            state.setdefault("lessons_learned", []).append(lesson)
            state["lessons_learned"] = state["lessons_learned"][-10:]  # max 10
            log(f"  📚 Lezione salvata: '{question[:45]}' → previsto {our_side}, realtà {winning}", "WARN")

        # Sposta in archive, rimuovi da watched → non compare più in dashboard
        archive.append(market)
        del watched[key]
        newly_resolved += 1
        log(f"  ✓ Risolto: {question[:55]}... → {winning or '?'} "
            f"({'✓' if correct else '✗' if correct is False else '?'})", "OK")

    # 3. Limita archive a 200 voci (le più recenti)
    state["resolved_archive"] = archive[-200:]

    # 4. Ricalcola algo_stats dall'archive (tutti i mercati risolti storicamente)
    all_resolved = [m for m in state["resolved_archive"]
                    if m.get("correct") is not None]
    correct_count = sum(1 for m in all_resolved if m.get("correct"))
    # total_copy_signals = segnali COPY attivi + archiviati
    active_copies = sum(1 for m in watched.values() if m.get("our_verdict") == "COPY")
    archived_copies = sum(1 for m in state["resolved_archive"] if m.get("our_verdict") == "COPY")

    # Shadow wallet: $100 virtuale per ogni segnale COPY risolto
    # Se corretto: guadagno = $100 / entry_price - $100 (payoff binario)
    # Se sbagliato: perdi $100 (scommessa persa)
    shadow_profit = 0.0
    for m in state["resolved_archive"]:
        if m.get("our_verdict") != "COPY" or m.get("correct") is None:
            continue
        ep = float(m.get("entry_price") or 0.5)
        if ep <= 0:
            ep = 0.5
        if m["correct"]:
            shadow_profit += (100.0 / ep) - 100.0   # payoff netto
        else:
            shadow_profit -= 100.0                    # perdi la posta
    total_copy_resolved = sum(
        1 for m in state["resolved_archive"] if m.get("our_verdict") == "COPY" and m.get("correct") is not None
    )
    shadow_roi = round(shadow_profit / (total_copy_resolved * 100) * 100, 1) if total_copy_resolved else None

    state["algo_stats"] = {
        "total_copy_signals": active_copies + archived_copies,
        "resolved_copies": len(all_resolved),
        "correct_copies": correct_count,
        "accuracy_pct": round(correct_count / len(all_resolved) * 100, 1) if all_resolved else None,
        "last_updated": now.isoformat(),
        "shadow_profit_usdc": round(shadow_profit, 2),
        "shadow_roi_pct": shadow_roi,
    }
    if newly_resolved:
        log(f"Resolution: {newly_resolved} mercati risolti e rimossi dalla dashboard, "
            f"accuracy: {state['algo_stats']['accuracy_pct']}%, "
            f"shadow ROI: {shadow_roi}%", "OK")


# ── SPORT TAG FILTER (Gamma API) ─────────────────────────────────────────────────
_SPORT_TAGS = {
    "soccer", "football", "basketball", "nba", "nfl", "mlb", "nhl", "tennis",
    "formula-1", "f1", "mma", "ufc", "boxing", "rugby", "cricket", "golf",
    "sports", "sport", "esports", "olympics", "volleyball", "baseball",
    "hockey", "cycling", "atletica", "nuoto", "swimming", "wrestling",
}

def _has_sport_tag(market_obj: dict) -> bool:
    """Controlla se un market Gamma ha tag o categoria sportiva."""
    tags = market_obj.get("tags") or []
    for tag in tags:
        slug = (tag.get("slug") or tag.get("id") or tag if isinstance(tag, str) else "").lower()
        label = (tag.get("label") or tag.get("name") or "").lower()
        if slug in _SPORT_TAGS or label in _SPORT_TAGS:
            return True
    cat = (market_obj.get("category") or market_obj.get("groupItemTitle") or "").lower()
    return any(s in cat for s in _SPORT_TAGS)


# ── NEWS CONTEXT (Google News RSS) ───────────────────────────────────────────────
def fetch_market_context(title: str) -> str:
    """
    Cerca le ultime 3 notizie correlate al mercato via Google News RSS.
    Restituisce stringa con i titoli separati da ' | '.
    Gratis, nessuna API key, usato per dare a Claude contesto reale.
    """
    # Keyword principali (ignora stop words e punteggiatura)
    stop = {
        "will", "the", "a", "an", "in", "on", "at", "to", "for", "of",
        "is", "be", "are", "was", "has", "have", "by", "from", "with",
        "that", "this", "or", "and", "its", "their"
    }
    words = [
        w.strip("?.,!") for w in title.split()
        if w.lower().strip("?.,!") not in stop and len(w) > 2
    ][:6]
    query = urllib.parse.quote(" ".join(words))
    url = f"https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"
    try:
        r = _http_get(url, timeout=8, retries=2)
        if not r or r.status_code != 200:
            return ""
        # Estrai titoli da RSS XML — CDATA o testo semplice
        items = re.findall(
            r'<item>.*?<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>',
            r.text, re.S
        )
        headlines = []
        for raw_title in items[:4]:
            clean = raw_title.strip()
            # Rimuovi " - NomeTestate" alla fine
            clean = re.sub(r'\s*[-–]\s*[^-–]+$', '', clean).strip()
            if clean and len(clean) > 15:
                headlines.append(clean)
        return " | ".join(headlines[:3])
    except Exception:
        return ""


# ── AGGIORNA WATCHED MARKETS ─────────────────────────────────────────────────────
def update_watched_markets(state: dict, results: list):
    """Salva in watched_markets i mercati con verdict COPY o WATCH."""
    for res in results:
        if res.get("verdict") not in ("COPY", "WATCH"):
            continue
        # Filtro difensivo: non salvare mai segnali con wallet placeholder
        # (0xpool viene usato come default per sorgenti market-level senza wallet reale).
        wallet_s = (res.get("wallet") or "").lower()
        whale_s  = (res.get("whale_name") or "").lower()
        wallet_f = (res.get("wallet_full") or "").lower()
        if ("0xpool" in wallet_s or "0xpool" in whale_s or "0xpool" in wallet_f
                or "0xdemo" in wallet_s or "0xdemo" in whale_s or "0xdemo" in wallet_f):
            log(f"Scarto segnale senza wallet reale: {res.get('market','?')[:50]}", "WARN")
            continue
        key = hashlib.md5(res["market"].encode()).hexdigest()[:12]
        if key not in state["watched_markets"]:
            state["watched_markets"][key] = {
                "question": res["market"],
                "our_verdict": res.get("verdict", "COPY"),
                "whale_wallet": res.get("wallet", ""),
                "whale_name": res.get("whale_name", ""),
                "tier": res.get("tier", "Whale"),
                "confidence": res.get("confidence", 0),
                "entry_price": res.get("price", 0),
                "side": res.get("side", "YES"),
                "vale_pena": res.get("vale_pena", ""),
                "spiegazione": res.get("spiegazione", ""),
                "date_flagged": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "conditionId": res.get("conditionId", ""),   # per lookup diretto in check_resolutions
                "poly_url": res.get("poly_url", ""),         # link diretto al mercato su Polymarket
                "why_snippet": (res.get("vale_pena") or "")[:80],  # micro-ragionamento per la card
                "resolved": False,
                "resolution": None,
                "correct": None,
            }
            # Incrementa times_seen nella leaderboard per questa whale
            wf = res.get("wallet_full", "")
            if wf:
                for addr, entry in state.get("leaderboard", {}).items():
                    if addr[:8] == wf[:8]:
                        entry["times_seen"] = entry.get("times_seen", 0) + 1
                        break


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

    total_volume = 0.0
    markets_seen = set()
    try:
        for page_offset in [0, 500]:  # max 2 pagine = 1000 trade
            url = (f"https://data-api.polymarket.com/activity"
                   f"?user={wallet}&limit=500&offset={page_offset}&type=TRADE")
            r = _http_get(url, timeout=12)
            if not r or r.status_code != 200:
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


def classify_bet(bet: dict, whale_trust: int = 50) -> dict:
    """Decide se una singola scommessa di una whale vale la pena copiare.

    Ritorna {verdict, reason, color} dove:
      verdict ∈ {"COPY", "WATCH", "SKIP"}
      reason  = frase breve italiana da mostrare in UI / report
      color   = hex per badge ("#00ff9f" / "#fcee0c" / "#777")

    Logica:
      SKIP  – mercato sport / passato / evento risolto / size troppo piccola
      COPY  – trust ≥80, size ≥$50k, prezzo non estremo, mercato attivo
      WATCH – tutti gli altri casi (interessante ma non conviction play)
    """
    title = (bet.get("title") or "").strip()
    size = float(bet.get("size") or 0)
    price = float(bet.get("price") or 0.5)

    # Blocchi hard → SKIP
    if not title:
        return {"verdict": "SKIP", "reason": "titolo mancante", "color": "#777"}
    if _is_sport(title):
        return {"verdict": "SKIP", "reason": "sport/entertainment", "color": "#777"}
    if _is_past_market(title):
        return {"verdict": "SKIP", "reason": "deadline già passata", "color": "#777"}
    if _is_known_resolved_event(title):
        return {"verdict": "SKIP", "reason": "evento già deciso", "color": "#777"}
    if size < 5_000:
        return {"verdict": "SKIP", "reason": "size troppo bassa (<$5k)", "color": "#777"}

    # Prezzo estremo → upside limitato
    if price >= 0.92:
        return {"verdict": "WATCH",
                "reason": f"prezzo {int(price*100)}¢: upside molto limitato",
                "color": "#fcee0c"}
    if price <= 0.08 and price > 0:
        return {"verdict": "WATCH",
                "reason": f"prezzo {int(price*100)}¢: tail-risk puro",
                "color": "#fcee0c"}

    # Core rules copy / watch / skip
    if whale_trust >= 80 and size >= 50_000 and 0.12 <= price <= 0.88:
        return {"verdict": "COPY",
                "reason": f"trust {whale_trust} × ${size/1000:.0f}k @ {int(price*100)}¢ → convinzione alta",
                "color": "#00ff9f"}
    if whale_trust >= 70 and size >= 20_000:
        return {"verdict": "WATCH",
                "reason": f"trust {whale_trust}, size medio — tieni d'occhio",
                "color": "#fcee0c"}
    if whale_trust < 50:
        return {"verdict": "SKIP",
                "reason": f"whale trust basso ({whale_trust})",
                "color": "#777"}
    return {"verdict": "WATCH",
            "reason": f"size $%dk su mercato attivo" % int(size/1000),
            "color": "#fcee0c"}


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
def _gamma_price(m: dict) -> str:
    """Estrae il prezzo disponibile da un oggetto Gamma market.
    Prova: bestAsk → lastTradePrice → outcomePrices[0] → tokens[0].price
    Rifiuta 0.5 esatto (placeholder Gamma = nessun dato reale).
    Ritorna "" se non trovato."""
    def _valid(f: float) -> bool:
        return 0.01 < f < 0.99 and abs(f - 0.5) > 0.005  # esclude 0.5 placeholder

    for key in ("bestAsk", "lastTradePrice"):
        v = m.get(key)
        if v is not None:
            try:
                f = float(v)
                if _valid(f):
                    return str(round(f, 6))
            except (ValueError, TypeError):
                pass
    # outcomePrices: ["0.65", "0.35"] — usa YES (indice 0)
    op = m.get("outcomePrices")
    if isinstance(op, list) and op:
        try:
            f = float(op[0])
            if _valid(f):
                return str(round(f, 6))
        except (ValueError, TypeError):
            pass
    # tokens: [{"price": "0.65"}, ...]
    tokens = m.get("tokens")
    if isinstance(tokens, list) and tokens:
        try:
            f = float(tokens[0].get("price") or 0)
            if _valid(f):
                return str(round(f, 6))
        except (ValueError, TypeError):
            pass
    return ""   # no real price found


# ── CLOB API (prezzi reali dall'orderbook) ───────────────────────────────────────
_CLOB_PRICE_CACHE: dict = {}  # token_id → price str, svuotato a ogni run


def _clob_price(token_id: str) -> str:
    """Legge il prezzo midpoint dalla CLOB API di Polymarket.
    Endpoint: GET https://clob.polymarket.com/midpoint?token_id={id}
    Cache per run — un token interrogato non si richiama."""
    if not token_id or len(token_id) < 10:
        return ""
    if token_id in _CLOB_PRICE_CACHE:
        return _CLOB_PRICE_CACHE[token_id]
    try:
        r = _http_get(
            f"https://clob.polymarket.com/midpoint?token_id={token_id}",
            timeout=5, retries=1,
        )
        if r and r.status_code == 200:
            data = r.json()
            mid = data.get("mid") or data.get("midpoint") or ""
            if mid:
                try:
                    f = float(mid)
                    if 0.01 < f < 0.99 and abs(f - 0.5) > 0.005:
                        result = str(round(f, 4))
                        _CLOB_PRICE_CACHE[token_id] = result
                        return result
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        log(f"CLOB price {token_id[:20]}: {e}", "DEBUG")
    _CLOB_PRICE_CACHE[token_id] = ""
    return ""


def _best_price(m: dict) -> str:
    """Prezzo definitivo: Gamma prima (veloce), CLOB come fallback (preciso).
    Usa clobTokenIds o tokens[].token_id per interrogare la CLOB.
    Ritorna "" se nessuna fonte ha un prezzo reale (≠ 0.5 placeholder)."""
    # 1. Gamma API (istantaneo, dalla cache bulk)
    p = _gamma_price(m)
    if p:
        return p
    # 2. CLOB API: prova clobTokenIds (campo JSON stringificato da Gamma)
    try:
        clob_ids = json.loads(m.get("clobTokenIds") or "[]")
        if clob_ids:
            p = _clob_price(str(clob_ids[0]))
            if p:
                return p
    except (json.JSONDecodeError, TypeError, IndexError):
        pass
    # 3. CLOB API: prova tokens[].token_id
    tokens = m.get("tokens") or []
    for tok in (tokens if isinstance(tokens, list) else [])[:1]:
        tid = (tok.get("token_id") or tok.get("tokenId") or
               tok.get("id") or "")
        if tid:
            p = _clob_price(str(tid))
            if p:
                return p
    return ""


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


def _is_stale_trade(trade: dict) -> bool:
    """True se il trade è più vecchio di MAX_TRADE_AGE_HOURS.
    Controlla campi timestamp standard dalla Data API / Gamma API."""
    ts_raw = (trade.get("createdAt") or trade.get("timestamp") or
              trade.get("created_at") or trade.get("updatedAt") or
              trade.get("transactionHash") and None)  # transactionHash non è un timestamp
    if not ts_raw:
        return False  # nessun timestamp disponibile → non scartare
    try:
        ts = dateutil.parser.isoparse(str(ts_raw))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        if age_hours > MAX_TRADE_AGE_HOURS:
            log(f"Scartato trade obsoleto (età: {age_hours:.1f}h): "
                f"{(trade.get('title') or trade.get('question') or '')[:50]}", "DEBUG")
            return True
    except Exception:
        pass
    return False


# Cache in-run per verifica real-time mercati (evita chiamate duplicate)
_VERIFIED_OPEN_CACHE: dict = {}  # conditionId → True=aperto, False=chiuso


def _verify_market_open(condition_id: str, title: str = "") -> bool:
    """Verifica real-time che un mercato sia ancora aperto via Gamma API.
    Controlla: closed=false, resolved=false, nessun winnerIndex assegnato.
    Cache per run per evitare rate limiting (un mercato verificato non si richiama)."""
    if not condition_id:
        return True  # senza ID non possiamo verificare, lascia passare
    if condition_id in _VERIFIED_OPEN_CACHE:
        return _VERIFIED_OPEN_CACHE[condition_id]

    # Controlla prima la cache bulk già costruita
    cached = _GAMMA_RESOLUTION_CACHE.get(condition_id)
    if cached is not None:
        is_open = not _resolved_from_cache_entry(cached)
        _VERIFIED_OPEN_CACHE[condition_id] = is_open
        return is_open

    # Chiamata API diretta per questo conditionId specifico
    try:
        r = _http_get(
            f"https://gamma-api.polymarket.com/markets?conditionId={condition_id}&limit=1",
            timeout=6, retries=1,
        )
        if r and r.status_code == 200:
            items = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
            if items:
                m = items[0]
                _GAMMA_RESOLUTION_CACHE[condition_id] = m
                closed = bool(m.get("closed") or m.get("isResolved"))
                # Controlla anche winnerIndex assegnato nei tokens
                tokens = m.get("tokens") or m.get("outcomes") or []
                has_winner = any(t.get("winnerIndex") is not None
                                 or t.get("winner") is True
                                 for t in (tokens if isinstance(tokens, list) else []))
                is_open = not closed and not has_winner
                if not is_open:
                    log(f"Mercato STALE (closed/resolved/winner): {title[:60] or condition_id[:14]}", "WARN")
                _VERIFIED_OPEN_CACHE[condition_id] = is_open
                return is_open
            else:
                # conditionId non trovato in Gamma → rimosso/risolto
                log(f"Mercato non trovato in Gamma (STALE): {title[:60] or condition_id[:14]}", "WARN")
                _VERIFIED_OPEN_CACHE[condition_id] = False
                return False
    except Exception as e:
        log(f"_verify_market_open {condition_id[:14]}: {e}", "WARN")

    _VERIFIED_OPEN_CACHE[condition_id] = True  # in caso di errore rete, lascia passare
    return True


def fetch_polymarket_whales(min_size, state: dict = None):
    """
    Multi-source crawling: aggrega dati da TUTTI gli endpoint Polymarket disponibili.
    Non si ferma al primo — prende tutto e deduplica.
    """
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

    # ── Source 1: Gamma API Markets (volume 24h) — solo mercati aperti ──
    try:
        r = _http_get("https://gamma-api.polymarket.com/markets?limit=100"
                      "&active=true&closed=false&order=volume24hr&ascending=false")
        if r and r.status_code == 200:
            mlist = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
            candidates = []
            for m in mlist:
                if float(m.get("volume24hr") or m.get("volume") or 0) < min_size:
                    continue
                if _has_sport_tag(m):
                    continue
                cid = m.get("conditionId") or ""
                # Scudo anti-latenza: verifica real-time per ogni mercato
                if not _verify_market_open(cid, m.get("question") or ""):
                    continue
                price_str = _best_price(m) or "0.5"
                candidates.append({
                    "usdcSize":    str(float(m.get("volume24hr") or m.get("volume") or 0)),
                    "price":       price_str,
                    "side":        "YES",
                    "title":       m.get("question") or m.get("title") or "Mercato",
                    "userAddress": "0xpool",
                    "conditionId": cid,
                })
            _add_items(candidates, "gamma-markets")
    except Exception as e:
        log(f"  gamma-markets: {str(e)[:80]}", "WARN")

    # ── Source 2: Gamma API Events (volume totale) — solo eventi aperti ──
    try:
        r = _http_get("https://gamma-api.polymarket.com/events?limit=80"
                      "&active=true&closed=false&order=volume&ascending=false")
        if r and r.status_code == 200:
            elist = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
            candidates = []
            for ev in elist:
                if float(ev.get("volume") or ev.get("volumeNum") or 0) < min_size:
                    continue
                if _has_sport_tag(ev):
                    continue
                # Gli eventi non hanno conditionId — usa closed/resolved del campo evento
                if ev.get("closed") or ev.get("resolved"):
                    log(f"Evento chiuso scartato: {str(ev.get('title',''))[:50]}", "WARN")
                    continue
                # Prova a ottenere il prezzo dal primo mercato dell'evento (Gamma + CLOB)
                ev_markets = ev.get("markets") or []
                ev_price = ""
                first_cid = ""
                for em in (ev_markets if isinstance(ev_markets, list) else []):
                    ev_price = _best_price(em)
                    if not first_cid:
                        first_cid = em.get("conditionId") or ""
                    if ev_price:
                        break
                # Se i mercati interni non avevano prezzo, prova CLOB sull'evento stesso
                if not ev_price and first_cid:
                    ev_price = _clob_price(first_cid)
                candidates.append({
                    "usdcSize":    str(float(ev.get("volume") or ev.get("volumeNum") or 0)),
                    "price":       ev_price or "0.5",
                    "side":        "YES",
                    "title":       ev.get("title") or ev.get("question") or "Evento",
                    "userAddress": "0xpool",
                    "conditionId": first_cid,
                })
            _add_items(candidates, "gamma-events")
    except Exception as e:
        log(f"  gamma-events: {str(e)[:80]}", "WARN")

    # ── Source 3: Gamma API Trending/Popular markets — solo mercati aperti ──
    for tag in ["trending", "popular"]:
        try:
            r = _http_get(f"https://gamma-api.polymarket.com/markets?limit=50"
                          f"&active=true&closed=false&tag={tag}")
            if r and r.status_code == 200:
                mlist = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
                candidates = []
                for m in mlist:
                    if float(m.get("volume24hr") or m.get("volume") or 0) < min_size:
                        continue
                    if _has_sport_tag(m):
                        continue
                    cid = m.get("conditionId") or ""
                    if not _verify_market_open(cid, m.get("question") or ""):
                        continue
                    price_str = _best_price(m) or "0.5"
                    candidates.append({
                        "usdcSize":    str(float(m.get("volume24hr") or m.get("volume") or 0)),
                        "price":       price_str,
                        "side":        "YES",
                        "title":       m.get("question") or m.get("title") or "Mercato",
                        "userAddress": "0xpool",
                        "conditionId": cid,
                    })
                _add_items(candidates, f"gamma-{tag}")
        except Exception as e:
            log(f"  gamma-{tag}: {str(e)[:80]}", "WARN")

    # ── Source 4: Data API Activity (trade reali con wallet) ──
    try:
        r = _http_get("https://data-api.polymarket.com/activity?limit=200&type=TRADE")
        if r and r.status_code == 200:
            trades = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
            _add_items([{
                "usdcSize":    str(float(t.get("usdcSize") or t.get("amount") or 0)),
                "price":       str(t.get("price") or 0.5),
                "side":        t.get("side") or "YES",
                "title":       t.get("title") or t.get("question") or "Trade",
                "userAddress": t.get("user") or t.get("proxyWallet") or "0x???",
                "createdAt":   t.get("createdAt") or t.get("timestamp") or "",
            } for t in trades
              if float(t.get("usdcSize") or t.get("amount") or 0) >= min_size
              and not _is_stale_trade(t)],
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
            headers={**_H, "Content-Type": "application/json"},
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
            r = _http_get(f"https://data-api.polymarket.com/activity?user={wallet_addr}&limit=20&type=TRADE", timeout=10)
            if r and r.status_code == 200:
                trades = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
                _add_items([{
                    "usdcSize":    str(float(t.get("usdcSize") or t.get("amount") or 0)),
                    "price":       str(t.get("price") or 0.5),
                    "side":        t.get("side") or "YES",
                    "title":       t.get("title") or t.get("question") or "Trade",
                    "userAddress": wallet_addr,
                    "createdAt":   t.get("createdAt") or t.get("timestamp") or "",
                } for t in trades
                  if float(t.get("usdcSize") or t.get("amount") or 0) >= min_size * 0.5
                  and not _is_stale_trade(t)],
                f"whale-{wdata.get('username', wallet_addr[:8])}")
            time.sleep(0.3)
        except Exception as e:
            log(f"  whale-{wallet_addr[:8]}: {str(e)[:60]}", "WARN")

    if not all_items:
        return False, [], 0

    log(f"Multi-source totale: {len(all_items)} mercati unici raccolti", "OK")

    # ── Filtra sport, sotto soglia, e eventi già decisi ──
    filtered = [
        t for t in all_items
        if _sz(t) >= min_size * 0.5  # più permissivo per whale top
        and not _is_sport(t.get("title") or t.get("question") or "")
        and not _is_past_market(t.get("title") or t.get("question") or "")
        and not _is_known_resolved_event(t.get("title") or t.get("question") or "")
        and not is_market_resolved(
            t.get("title") or t.get("question") or "",
            condition_id=t.get("conditionId") or t.get("market") or "",
        )
    ]

    # ── Arricchisci con trust_score e filtra wash trader ──
    leaderboard = (state or {}).get("leaderboard", {})
    enriched = []
    for t in filtered:
        wallet = (t.get("userAddress") or t.get("maker") or "0xpool").lower()
        
        # ESCLUDI 0XPOOL E DEMO WALLETS
        if wallet.startswith("0xpool") or wallet.startswith("0xdemo") or "0xpool" in wallet:
            continue
            
        lb_entry = leaderboard.get(wallet, {})
        t["whale_trust_score"] = lb_entry.get("trust_score", 40)
        t["whale_username"] = lb_entry.get("username", wallet[:10])
        
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

def _build_system_prompt(lessons: list = None) -> str:
    today = datetime.now(timezone.utc).strftime("%d %B %Y")
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = (
        f"Oggi è {today} ({today_iso}). Usa questa data come riferimento assoluto.\n\n"
        "REGOLA N°0 — MERCATO GIÀ RISOLTO → SKIP IMMEDIATO (priorità massima assoluta):\n"
        "Devi fare SKIP se UNA delle seguenti condizioni è vera:\n"
        "  a) La deadline del mercato è PRIMA di oggi (es: 'by January 2026', 'Q1 2026' siamo in aprile 2026)\n"
        "  b) L'evento descritto è già ACCADUTO o il risultato è già NOTO — usa la tua conoscenza del mondo:\n"
        "     • Elezioni già svolte (es: NYC Mayor 2025 → Mamdani ha vinto, già sindaco)\n"
        "     • Decisioni Fed già annunciate (es: 'Fed decision in December?' → dicembre 2025 è passato)\n"
        "     • Risultati sportivi/politici/economici già noti\n"
        "     • Qualsiasi mercato dove il risultato è già un fatto storico\n"
        "  c) Il titolo contiene '...' o è troncato e non riesci a determinare la deadline\n"
        "ATTENZIONE: un volume alto ($100M+) su un mercato già risolto indica wash trading o "
        "liquidità residua — NON è un segnale di insider trading. SKIP senza eccezioni.\n\n"
        "REGOLA N°1 — SPORT/ENTERTAINMENT → SKIP IMMEDIATO (nessuna analisi):\n"
        "Calcio, basket, tennis, F1, NFL, NBA, Oscar, Grammy, Eurovision → SKIP.\n\n"
        "REGOLA N°2 — COPY DI DEFAULT (la più importante):\n"
        "Se Trust Score ≥ 80 AND size ≥ $100k AND NON è sport AND deadline FUTURA → COPY "
        "a MENO CHE tu non abbia una ragione specifica e concreta per non farlo "
        "(es: mercato già a 95¢, whale nota per wash trading, evento già risolto).\n"
        "Il dubbio va a favore del COPY, non dello SKIP.\n\n"
        "REGOLA N°3 — WATCH se incerto:\n"
        "Trust 60-79 OR size $50k-$99k OR contesto ambiguo → WATCH. "
        "Non usare SKIP quando hai incertezza — usa WATCH.\n\n"
        "CALIBRAZIONE PREZZI — guida rapida:\n"
        "- Prezzo < 20¢: evento improbabile MA la whale sa qualcosa? → spesso COPY\n"
        "- Prezzo 20-45¢: zona interessante, mercato sotto-prezzato → COPY o WATCH\n"
        "- Prezzo 50-75¢: neutro, dipende dal contesto → WATCH o COPY se forte segnale\n"
        "- Prezzo > 85¢: mercato quasi certo, poco valore → SKIP o WATCH\n\n"
        "CONTESTO NEWS: Se vengono fornite notizie recenti correlate, usale come "
        "conferma del movimento whale. Una whale che muove $200k+ DOPO una notizia "
        "rilevante è quasi certamente un COPY.\n\n"
        "TRACK RECORD: Se il sistema ha >60% accuracy → fidati ancora di più dei segnali.\n\n"
        "Rispondi SOLO in questo formato, in italiano:\n"
        "COPY (o WATCH o SKIP)\n"
        "Rischio: X/10\n"
        "Vale la pena?: [1 frase diretta e concisa]\n"
        "Cosa sta succedendo: [2-3 righe MAX — spiega il mercato come a un amico]\n"
        "Sospetto?: No/Forse/Sì"
    )
    if lessons:
        lesson_lines = []
        for l in lessons[-5:]:  # ultimi 5 errori
            lesson_lines.append(
                f"  • '{l.get('question','?')}' → previsto {l.get('our_side','?')}, "
                f"realtà {l.get('outcome','?')} @ {l.get('entry_price',0)*100:.0f}¢ "
                f"({l.get('date','')})"
            )
        base += (
            "\n\nLEZIONI APPRESE (errori passati — NON ripetere questi pattern):\n"
            + "\n".join(lesson_lines)
            + "\nSe il mercato attuale è simile a uno di questi, sii più cauto (WATCH invece di COPY)."
        )
    return base

SYSTEM_PROMPT = _build_system_prompt()


def analyze_with_claude(trade, state: dict = None, reddit_context: str = "", lessons: list = None):
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
                    "system": _build_system_prompt(lessons),  # ricostruito con data odierna + lezioni
                    "messages": [{"role": "user", "content": f"Analizza:\n\n{text}"}],
                },
                timeout=30,
            )
            if r.status_code == 200:
                raw = "".join(b.get("text", "") for b in r.json().get("content", []))
                log(f"Claude OK ({model})", "OK")
                result = _parse_claude(raw, market, side, price, size, wallet, tier,
                                       trust_score, whale_name, confidence)
                # Salva conditionId per check_resolutions() — lookup diretto per ID
                cond_id = (trade.get("conditionId") or
                           trade.get("market") or
                           trade.get("asset_id") or "")
                result["conditionId"] = cond_id
                result["wallet_full"] = wallet_full
                result["poly_url"] = _get_poly_url(cond_id, market)
                return result
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


# ── BATCH CLAUDE — top 5 mercati in una sola chiamata ───────────────────────────
BATCH_SYSTEM = (
    "Sei un analista aggressivo di mercati predittivi. "
    "Analizza i mercati in batch e rispondi nel formato esatto richiesto. "
    "Sport/entertainment → SKIP immediato. "
    "Trust ≥ 80 + size ≥ $100k + non sport → COPY di default. "
    "Usa WATCH per i casi incerti, non SKIP."
)

def _build_trade_text(trade: dict, state: dict, news: str = "") -> str:
    """Testo sintetico di un singolo trade per il prompt batch."""
    size       = _sz(trade)
    price      = float(trade.get("price") or 0.5)
    side       = trade.get("side") or "YES"
    market     = trade.get("title") or trade.get("question") or "Mercato"
    trust      = trade.get("whale_trust_score", 40)
    wname      = trade.get("whale_username", "?")
    wallet_f   = (trade.get("maker") or trade.get("userAddress") or "").lower()
    profile    = profile_whale(wallet_f, state)
    total_vol  = profile.get("total_volume_usd", 0)
    tier       = _classify(size, total_vol)
    confidence = compute_confidence(trade, state)
    lines = [
        f"Titolo: {market}",
        f"Direzione: {side} @ {price:.2f} ({price*100:.0f}¢)",
        f"Size: ${size:,.0f} | Vol storico wallet: ${total_vol:,.0f}",
        f"Whale: {wname} | Trust: {trust}/100 | Tier: {tier}",
        f"Confidence pre-analisi: {confidence}/100",
    ]
    if news:
        lines.append(f"Notizie correlate: {news}")
    return "\n".join(lines)


def analyze_batch_claude(trades: list, state: dict, reddit_context: str = "", lessons: list = None) -> list:
    """
    Analizza fino a 5 mercati in una singola chiamata Claude.
    Riduce il numero di API call da N a 1, con parsing batch del risultato.
    Ritorna lista di result dict (stesso formato di _parse_claude).
    """
    batch = trades[:5]
    if not batch:
        return []

    # Prepara testo strutturato per ogni trade (con news context)
    sections = []
    trade_meta = []  # dati per parsing post-risposta
    for i, trade in enumerate(batch, 1):
        news = fetch_market_context(trade.get("title") or "")
        if news:
            log(f"  News [{i}]: {news[:80]}", "OK")
        text = _build_trade_text(trade, state, news)
        sections.append(f"=== MERCATO {i} ===\n{text}")
        wallet_f = (trade.get("maker") or trade.get("userAddress") or "").lower()
        profile  = profile_whale(wallet_f, state)
        trade_meta.append({
            "market":     trade.get("title") or trade.get("question") or "Mercato",
            "side":       trade.get("side") or "YES",
            "price":      float(trade.get("price") or 0.5),
            "size":       _sz(trade),
            "wallet":     wallet_f[:14] + "...",
            "whale_name": trade.get("whale_username", "?"),
            "tier":       _classify(_sz(trade), profile.get("total_volume_usd", 0)),
            "trust":      trade.get("whale_trust_score", 40),
            "confidence": compute_confidence(trade, state),
        })

    acc_str = ""
    algo    = state.get("algo_stats", {})
    if algo.get("accuracy_pct") is not None:
        acc_str = f"\nTrack record sistema: {algo['accuracy_pct']}% accuracy."
    if reddit_context:
        acc_str += f"\nInsight r/Polymarket: {reddit_context}"
    if lessons:
        lesson_lines = [
            f"  • '{l.get('question','?')}' → previsto {l.get('our_side','?')}, "
            f"realtà {l.get('outcome','?')} ({l.get('date','')})"
            for l in lessons[-5:]
        ]
        acc_str += (
            "\nLEZIONI APPRESE (non ripetere questi errori):\n"
            + "\n".join(lesson_lines)
        )

    prompt = (
        f"Analizza questi {len(batch)} mercati Polymarket.{acc_str}\n\n"
        + "\n\n".join(sections)
        + "\n\nRispondi ESATTAMENTE in questo formato (nessun testo extra tra i mercati):\n\n"
        + "\n\n".join(
            f"=== MERCATO {i} ===\nCOPY/WATCH/SKIP\nRischio: X/10\n"
            "Vale la pena?: ...\nCosa sta succedendo: ...\nSospetto?: No/Forse/Sì"
            for i in range(1, len(batch) + 1)
        )
    )

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
                    "max_tokens": 800,
                    "system": _build_system_prompt(lessons),
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=45,
            )
            if r.status_code != 200:
                log(f"Batch Claude {r.status_code} ({model})", "WARN")
                continue
            raw = "".join(b.get("text", "") for b in r.json().get("content", []))
            log(f"Batch Claude OK ({model}) — {len(batch)} mercati in 1 call", "OK")

            # Parser batch: dividi per === MERCATO N ===
            parts = re.split(r'===\s*MERCATO\s+\d+\s*===', raw)
            parts = [p.strip() for p in parts if p.strip()]
            results = []
            for idx, (chunk, meta) in enumerate(zip(parts, trade_meta)):
                try:
                    res = _parse_claude(
                        chunk,
                        meta["market"], meta["side"], meta["price"], meta["size"],
                        meta["wallet"], meta["tier"], meta["trust"], meta["whale_name"],
                        meta["confidence"],
                    )
                    results.append(res)
                    log(f"  [{idx+1}] {meta['market'][:45]}: {res['verdict']} "
                        f"R{res['risk_score']}/10", "OK")
                except Exception as e:
                    log(f"  Batch parse errore mercato {idx+1}: {e}", "WARN")
                    results.append({
                        "market": meta["market"], "side": meta["side"],
                        "price": meta["price"], "size": meta["size"],
                        "wallet": meta["wallet"], "whale_name": meta["whale_name"],
                        "tier": meta["tier"], "trust_score": meta["trust"],
                        "confidence": meta["confidence"],
                        "verdict": "SKIP", "risk_score": 5, "is_sport_flagged": False,
                        "vale_pena": "", "spiegazione": chunk[:200], "sospetto": "No",
                    })
            # Completa risultati mancanti (se Claude ha risposto per meno mercati)
            while len(results) < len(batch):
                m = trade_meta[len(results)]
                results.append({
                    "market": m["market"], "side": m["side"],
                    "price": m["price"], "size": m["size"],
                    "wallet": m["wallet"], "whale_name": m["whale_name"],
                    "tier": m["tier"], "trust_score": m["trust"],
                    "confidence": m["confidence"],
                    "verdict": "SKIP", "risk_score": 5, "is_sport_flagged": False,
                    "vale_pena": "", "spiegazione": "Parsing batch fallito", "sospetto": "No",
                })
            return results
        except Exception as e:
            log(f"Batch Claude eccezione ({model}): {e}", "WARN")

    log("Batch Claude fallito — fallback su chiamate singole", "WARN")
    return []


# ── TELEGRAM ────────────────────────────────────────────────────────────────────
def build_message(results, state: dict = None, is_demo=False, best_skip=None):
    # results qui contiene SOLO COPY/WATCH (i SKIP sono già filtrati da run())
    ts  = datetime.now().strftime("%d/%m/%Y %H:%M")
    msg = f"🐋 *Grandi Mosse su Polymarket*{' _(DEMO)_' if is_demo else ''}\n_{ts}_\n\n"

    copy_count = sum(1 for t in results if t["verdict"] == "COPY")
    watch_count = sum(1 for t in results if t["verdict"] == "WATCH")

    # Track record accuracy
    algo_stats = (state or {}).get("algo_stats", {})
    acc = algo_stats.get("accuracy_pct")
    resolved = algo_stats.get("resolved_copies", 0)
    if acc is not None:
        msg += f"📊 Track record: *{acc}%* accuracy ({resolved} segnali risolti)\n"

    parts = []
    if copy_count:
        parts.append(f"*{copy_count}* COPY")
    if watch_count:
        parts.append(f"*{watch_count}* WATCH")
    msg += f"{', '.join(parts)} — da seguire 👇\n\n"

    # Mostra COPY prima, poi WATCH
    to_show = [t for t in results if t["verdict"] == "COPY"]
    to_show += [t for t in results if t["verdict"] == "WATCH"]

    for t in to_show:
        m_title = t["market"][:70] + ("..." if len(t["market"]) > 70 else "")
        v = t["verdict"]
        conf = t.get("confidence", 50)
        price = float(t.get("price") or 0)
        side  = (t.get("side") or "YES").upper()

        if v == "COPY":
            header = "✅ *COPY — ENTRA ORA*" if conf >= 80 else "✅ *COPY — DA VALUTARE*"
        elif v == "WATCH":
            header = "👁️ *WATCH — TIENI D'OCCHIO*"
        else:
            header = "⭐ *IL MENO PEGGIO DI OGGI*"
        msg += f"{header}\n"
        msg += f"📌 _{m_title}_\n"

        # Azione esplicita: cosa comprare e a quale prezzo
        if v == "COPY" and price > 0:
            pr_pct = round(price * 100, 1)
            price_str = f"{pr_pct}¢" if pr_pct != 50.0 else "?"
            msg += f"👉 *Azione: Compra {side} a {price_str}*\n"
        elif v == "COPY":
            msg += f"👉 *Azione: Compra {side}*\n"

        # Whale info
        wname = t.get("whale_name", "")
        ts_val = t.get("trust_score", 40)
        if wname and "0xpool" not in wname and "0xdemo" not in wname:
            trust_icon = "🟢" if ts_val >= 70 else "🟡" if ts_val >= 50 else "🔴"
            msg += f"🐋 {wname} {trust_icon} Trust: {ts_val}/100 | {t.get('tier','')}\n"
        msg += f"🎯 Confidence: {conf}/100\n"

        if t.get("vale_pena"):
            msg += f"💡 {t['vale_pena']}\n"
        if t.get("spiegazione"):
            msg += f"📖 {t['spiegazione']}\n"
        
        # Indicazione su cosa puntare
        msg += f"💡 *COSA FARE:* Punta su *{t['side']}* se il prezzo è vicino a {t['price']} USDC.\n"

        risk = t["risk_score"]
        icon = "🟢" if risk <= 3 else "🟡" if risk <= 6 else "🔴"
        msg += f"{icon} Rischio: {risk}/10\n"
        if t.get("sospetto", "No") not in ("No", "N/A"):
            msg += "⚠️ Potrebbe essere gonfiato artificialmente\n"

        # Link diretto Polymarket — CTA esplicita sul cosa fare
        poly_url = t.get("poly_url") or ""
        if poly_url:
            cta = "🚀 COPY SU POLYMARKET" if v == "COPY" else "👁️ APRI IL MERCATO"
            msg += f"\n👉 [{cta} ↗]({poly_url})\n"
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
            msg += f"\n*{i}. {name}* +${profit:,.0f} (trust {ts_val})\n"
            # Mostra le 2 bet più recenti con copy-advice + link al mercato
            bets = (w.get("recent_bets") or [])[:2]
            for b in bets:
                adv = b.get("copy_advice") or {}
                v = adv.get("verdict", "—")
                icon = "🟢" if v == "COPY" else "🟡" if v == "WATCH" else "⚪"
                side = (b.get("side") or "YES").upper()
                side = "YES" if "YES" in side or "BUY" in side else "NO"
                t_short = (b.get("title") or "")[:45]
                sz = float(b.get("size") or 0)
                pr = float(b.get("price") or 0.5)
                bet_url = b.get("poly_url") or ""
                msg += (f"  {icon} *{v}* {side} {t_short}\n"
                        f"     ${sz/1000:.0f}k @ {int(pr*100)}¢ — _{adv.get('reason','')}_\n")
                if bet_url:
                    msg += f"     [→ apri mercato]({bet_url})\n"
        msg += "\n"

    return msg + "_Polymarket Whale Tracker v3 — Bruno_"


def send_telegram(message, silent: bool = False):
    """Invia un messaggio Telegram.
    silent=True → notifica silenziosa (vibrazione/suono disattivati) per WATCH.
    silent=False → notifica normale con suono per COPY."""
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id":            TELEGRAM_USER_ID,
            "text":               message,
            "parse_mode":         "Markdown",
            "disable_notification": silent,
        },
        timeout=15,
    )
    d = r.json()
    if not d.get("ok"):
        log(f"Telegram: {d.get('description', 'errore')}", "ERR")
    return d.get("ok", False)


# ── EMAIL ───────────────────────────────────────────────────────────────────────
def build_email_html(results, state: dict = None, is_demo=False, best_skip=None):
    # results qui contiene SOLO COPY/WATCH (i SKIP sono già filtrati da run())
    ts = datetime.now().strftime("%d/%m/%Y %H:%M")
    copy_count = sum(1 for t in results if t["verdict"] == "COPY")
    watch_count = sum(1 for t in results if t["verdict"] == "WATCH")
    to_show = results  # già filtrati

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
        badge   = "✅ COPY — DA VALUTARE" if v == "COPY" else "👁️ WATCH — DA SEGUIRE"
        risk    = t["risk_score"]
        risk_color = "#2a9d2a" if risk <= 3 else "#e6a817" if risk <= 6 else "#d9534f"
        wname = t.get("whale_name", "")
        ts_val = t.get("trust_score", 40)
        trust_html = ""
        if wname and "0xpool" not in wname and "0xdemo" not in wname:
            tc = "#2a9d2a" if ts_val >= 70 else "#e6a817" if ts_val >= 50 else "#d9534f"
            trust_html = f'<span style="color:{tc};font-size:12px;">🐋 {wname} — Trust {ts_val}/100</span><br>'

        # Bottone grande e colorato: link diretto al mercato su Polymarket
        poly_url = t.get("poly_url") or ""
        if not poly_url:
            poly_url = ("https://polymarket.com/search?q=" +
                        urllib.parse.quote(t.get("market", "")[:80]))
        btn_bg  = "#1a7a3a" if v == "COPY" else "#2a6dd9"
        btn_txt = "🚀 COPY SU POLYMARKET →" if v == "COPY" else "👁️ APRI IL MERCATO →"
        poly_btn_html = (
            f'<div style="margin-top:14px;text-align:center;">'
            f'<a href="{poly_url}" target="_blank" '
            f'style="display:inline-block;background:{btn_bg};color:#fff;'
            f'padding:12px 22px;border-radius:6px;font-weight:700;'
            f'text-decoration:none;font-size:14px;letter-spacing:0.5px;">'
            f'{btn_txt}</a></div>'
        )

        rows += f"""
        <tr style="background:{bg};border-bottom:1px solid #e0e0e0;">
          <td style="padding:16px;">
            <span style="font-weight:bold;color:{color};">{badge}</span><br>
            {trust_html}
            <span style="font-size:15px;font-weight:600;">{t['market']}</span><br><br>
            {f"<b>💡 {t['vale_pena']}</b><br>" if t.get('vale_pena') else ""}
            {f"📖 {t['spiegazione']}<br>" if t.get('spiegazione') else ""}
            <div style="margin-top:10px; padding:10px; background:#e8f4fd; border-left:4px solid #2a6dd9;">
                <b>🎯 COSA FARE:</b> Punta su <b>{t['side']}</b> se il prezzo è vicino a {t['price']} USDC.
            </div>
            <br>
            <span style="color:{risk_color};font-weight:bold;">Rischio: {risk}/10</span>
            {"<br>⚠️ <i>Potrebbe essere gonfiato artificialmente</i>" if t.get('sospetto','No') not in ('No','N/A') else ""}
            {poly_btn_html}
          </td>
        </tr>"""

    leaderboard = (state or {}).get("leaderboard", {})
    lb_html = ""
    if leaderboard:
        sorted_lb = sorted(leaderboard.values(),
                           key=lambda x: x.get("total_profit_usd", 0), reverse=True)[:5]
        lb_rows_html = ""
        for i, w in enumerate(sorted_lb, 1):
            username = w.get("username", "?")[:22]
            profit = w.get("total_profit_usd", 0)
            ts_val = w.get("trust_score", 40)
            bets = (w.get("recent_bets") or [])[:3]
            # Rendi le 3 bet più recenti con advice badge
            bets_html = ""
            if bets:
                for b in bets:
                    adv = b.get("copy_advice") or {}
                    v = adv.get("verdict", "—")
                    v_color = adv.get("color", "#888")
                    reason = adv.get("reason", "")
                    title = (b.get("title") or "")[:55]
                    side = (b.get("side") or "YES").upper()
                    side = "YES" if "YES" in side or "BUY" in side else "NO"
                    side_color = "#1a7a3a" if side == "YES" else "#d9534f"
                    sz = float(b.get("size") or 0)
                    pr = float(b.get("price") or 0.5)
                    bet_url = b.get("poly_url") or (
                        "https://polymarket.com/search?q=" +
                        urllib.parse.quote((b.get("title") or "")[:80])
                    )
                    bets_html += (
                        f'<div style="margin:6px 0;padding:8px;background:#fff;'
                        f'border-left:3px solid {v_color};border-radius:0 4px 4px 0;font-size:12px;">'
                        f'<span style="background:{v_color};color:#0a0f1a;padding:1px 6px;'
                        f'border-radius:3px;font-weight:700;font-size:10px;">{v}</span> '
                        f'<span style="color:{side_color};font-weight:600;">{side}</span> '
                        f'<span style="color:#222;">{title}</span>'
                        f'<div style="color:#888;font-size:11px;margin-top:2px;">'
                        f'${sz/1000:.0f}k @ {int(pr*100)}¢ — <i>{reason}</i></div>'
                        f'<a href="{bet_url}" target="_blank" style="display:inline-block;'
                        f'margin-top:4px;color:#2a6dd9;font-size:11px;text-decoration:none;">'
                        f'→ apri mercato</a></div>'
                    )
            else:
                bets_html = ('<div style="color:#888;font-size:11px;font-style:italic;">'
                             'nessuna bet recente</div>')
            lb_rows_html += (
                f'<div style="padding:12px;border-bottom:1px solid #e0e0e0;background:#fafcff;">'
                f'<div style="display:flex;justify-content:space-between;align-items:baseline;">'
                f'<span style="font-weight:700;font-size:14px;">{i}. {username}</span>'
                f'<span style="color:#1a7a3a;font-weight:700;">+${profit:,.0f}</span></div>'
                f'<div style="color:#666;font-size:11px;margin-top:2px;">Trust {ts_val}/100</div>'
                f'{bets_html}</div>'
            )
        lb_html = (f'<div style="padding:16px;background:#f0f8ff;border-top:2px solid #0d1b2a;">'
                   f'<h3 style="margin:0 0 8px;">🏆 Top Whale Tracker — con Copy Advice</h3>'
                   f'{lb_rows_html}</div>')

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
        msg.attach(MIMEText(build_email_html(results, state, is_demo), "html"))
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
    _ACTIVITY_CACHE.clear()
    _GAMMA_RESOLUTION_CACHE.clear()
    _VERIFIED_OPEN_CACHE.clear()
    _CLOB_PRICE_CACHE.clear()
    state = load_state()

    # ── DEDUP RUN (backup cron) ────────────────────────────────────────────────
    # Il workflow GitHub Actions ha 2 cron (primario + backup 15 min dopo) per
    # resistere agli skip casuali dello scheduler GitHub. Se l'ultimo run è
    # finito da meno di ~3 ore, il secondo cron è ridondante → esce silenzioso
    # senza spammare email/telegram. Override via env FORCE_RUN=1 o
    # workflow_dispatch (che setta GITHUB_EVENT_NAME=workflow_dispatch).
    last_run_ts = state.get("last_run_ts")
    is_manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    force = os.environ.get("FORCE_RUN") == "1"
    if last_run_ts and not is_manual and not force:
        try:
            last_dt = datetime.fromisoformat(last_run_ts.replace("Z", "+00:00"))
            age_min = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
            if age_min < 180:  # 3 ore
                log(f"DEDUP: ultimo run {age_min:.0f} min fa — skip backup cron", "WARN")
                return
        except Exception as e:
            log(f"DEDUP: parse last_run_ts fallito ({e}), procedo", "WARN")

    state["run_count"] = state.get("run_count", 0) + 1
    state["last_run_ts"] = datetime.now(timezone.utc).isoformat()
    run_n = state["run_count"]

    today = datetime.now(timezone.utc)
    log("=" * 60)
    log(f"Run #{run_n} | {today.strftime('%d/%m/%Y %H:%M')} UTC | Max: {MAX_WHALES}")
    log("=" * 60)

    # 1. Build Gamma resolution cache (include mercati chiusi recenti)
    # Calcola oggi una volta sola — usata da tutti i filtri date
    build_gamma_resolution_cache()

     # 1. Controlla resolution dei mercati passati (self-improving)
    check_resolutions(state)
    
    # 1b. WASH STORICO (Ogni 4 run = circa 2 giorni se run 2 volte al giorno)
    if run_n % 4 == 0:
        log(f"WASH STORICO: Pulizia watched_markets (Run #{run_n})", "WARN")
        state["watched_markets"] = {}
        state["algo_stats"]["total_copy_signals"] = 0
        state["algo_stats"]["resolved_copies"] = 0
        state["algo_stats"]["correct_copies"] = 0
        state["algo_stats"]["accuracy_pct"] = None

    # 1c. Cleanup proattivo: rimuovi segnali senza wallet reale (0xpool/0xdemo)
    # E segnali su eventi già decisi (es. elezioni USA 2024).
    stale_keys = []
    for k, m in list(state.get("watched_markets", {}).items()):
        wallet_s = (m.get("whale_wallet") or "").lower()
        whale_s  = (m.get("whale_name") or "").lower()
        question = m.get("question") or ""
        if ("0xpool" in wallet_s or "0xpool" in whale_s
                or "0xdemo" in wallet_s or "0xdemo" in whale_s):
            stale_keys.append((k, "wallet placeholder"))
        elif _is_known_resolved_event(question):
            stale_keys.append((k, "evento già deciso"))
    for k, reason in stale_keys:
        removed = state["watched_markets"].pop(k)
        state.setdefault("resolved_archive", []).append({
            **removed,
            "resolved": True,
            "resolution": f"Auto-removed: {reason}",
            "removed_at": datetime.now(timezone.utc).isoformat(),
        })
    if stale_keys:
        reasons = ", ".join(f"{r}" for _, r in stale_keys)
        log(f"Cleanup: rimossi {len(stale_keys)} segnali stale ({reasons})", "OK")

    # 1d. Wash AUTOMATICO delle recent_bets di ogni whale nella leaderboard.
    # Rimuove scommesse su sport, mercati passati e eventi già decisi — così la
    # Top Whale Leaderboard nella dashboard non mostra mai bet obsolete, senza
    # che l'utente debba chiedere pulizia manuale.
    bets_purged = 0
    whales_touched = 0
    for wallet_k, entry in state.get("leaderboard", {}).items():
        bets = entry.get("recent_bets") or []
        if not bets:
            continue
        cleaned = []
        for b in bets:
            t = (b.get("title") or "").strip()
            if not t:
                continue
            if _is_sport(t) or _is_past_market(t) or _is_known_resolved_event(t):
                bets_purged += 1
                continue
            # Ri-classifica la bet con il trust corrente (trust può essere cambiato)
            b["copy_advice"] = classify_bet(b, whale_trust=entry.get("trust_score", 50))
            cleaned.append(b)
        if len(cleaned) != len(bets):
            whales_touched += 1
        entry["recent_bets"] = cleaned
    if bets_purged:
        log(f"Cleanup recent_bets: {bets_purged} bet stale rimosse da "
            f"{whales_touched} whale", "OK")

    # 2. Aggiorna leaderboard da Polymarket (chi sono le whale?)
    fetch_breaking_leaderboard(state)

    # 3. Reddit insights (ogni run) + GitHub algorithm scout (ogni run)
    reddit_context = fetch_reddit_insights(state)
    github_context = fetch_github_insights(state)
    extra_context = "\n\n".join(filter(None, [reddit_context, github_context]))

    # 4. Whale-first: prendi i trade recenti dei top wallet
    log("Scarico trade recenti delle top whale...")
    whales = fetch_whale_trades(state)

    # 4b. Fallback: se nessun trade da wallet, usa mercati per volume
    if not whales:
        log("Nessun trade da wallet — fallback su volume mercati", "WARN")
        ok, whales, total = fetch_polymarket_whales(MIN_SIZE_USDC, state)
    else:
        ok = True

    is_demo = not whales
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

    # 5. Analisi Claude — BATCH per i primi 5, singola per i rimanenti
    to_analyze = whales[:MAX_WHALES]
    results: list = []

    # Lezioni apprese — passate a Claude per auto-correzione
    lessons = state.get("lessons_learned", [])
    if lessons:
        log(f"Self-correction: {len(lessons)} lezioni disponibili per Claude", "OK")

    # 5a. Batch (top 5) — 1 call invece di 5, include news context
    log(f"Analisi batch top 5 mercati (1 chiamata Claude)...")
    batch_results = analyze_batch_claude(to_analyze[:5], state, extra_context, lessons=lessons)
    if batch_results:
        results.extend(batch_results)
    else:
        # Fallback: analisi singola per i primi 5
        for trade in to_analyze[:5]:
            name = trade.get("title") or "Mercato"
            log(f"Analisi singola fallback: {name[:55]}...")
            try:
                news = fetch_market_context(name)
                result = analyze_with_claude(trade, state, extra_context + (f" | News: {news}" if news else ""), lessons=lessons)
                results.append(result)
            except Exception as e:
                log(f"Errore analisi: {e}", "ERR")
                results.append({
                    "market": name, "side": "N/D", "price": 0, "size": 0,
                    "wallet": "—", "whale_name": "—", "tier": "—", "trust_score": 0,
                    "verdict": "SKIP", "risk_score": 5, "is_sport_flagged": False,
                    "vale_pena": "", "spiegazione": f"Errore: {str(e)[:100]}", "sospetto": "No",
                })
            time.sleep(1)

    # 5b. Analisi singola per i mercati rimanenti (6-MAX_WHALES)
    for i, trade in enumerate(to_analyze[5:], 6):
        name = trade.get("title") or "Mercato"
        log(f"Analisi [{i}/{len(to_analyze)}]: {name[:55]}...")
        try:
            news = fetch_market_context(name)
            result = analyze_with_claude(
                trade, state,
                extra_context + (f" | News: {news}" if news else ""),
                lessons=lessons,
            )
            results.append(result)
            log(f"→ {result['verdict']} | R{result['risk_score']}/10 | "
                f"Trust {result.get('trust_score',40)}", "OK")
        except Exception as e:
            log(f"Errore analisi: {e}", "ERR")
            results.append({
                "market": name, "side": "N/D", "price": 0, "size": 0,
                "wallet": "—", "whale_name": "—", "tier": "—", "trust_score": 0,
                "verdict": "SKIP", "risk_score": 5, "is_sport_flagged": False,
                "vale_pena": "", "spiegazione": f"Errore: {str(e)[:100]}", "sospetto": "No",
            })
        time.sleep(1)

    # Log lezioni apprese
    copy_n  = sum(1 for r in results if r["verdict"] == "COPY")
    watch_n = sum(1 for r in results if r["verdict"] == "WATCH")
    skip_n  = sum(1 for r in results if r["verdict"] == "SKIP")
    log(f"Analisi completata: {copy_n} COPY | {watch_n} WATCH | {skip_n} SKIP "
        f"su {len(results)} mercati", "OK")
    acc = state.get("algo_stats", {}).get("accuracy_pct")
    if acc is not None:
        log(f"Track record sistema: {acc}% accuracy su {state['algo_stats']['resolved_copies']} segnali risolti", "OK")

    # 6. Salva COPY in watched_markets per tracking futuro
    update_watched_markets(state, results)

    # 7. Salva stato persistente
    save_state(state)

    # 8. Notifiche — solo se c'è almeno un COPY o WATCH (mai mostrare SKIP)
    actionable = [r for r in results if r["verdict"] in ("COPY", "WATCH")]
    copy_count = sum(1 for r in actionable if r["verdict"] == "COPY")

    if actionable:
        copies  = [r for r in actionable if r["verdict"] == "COPY"]
        watches = [r for r in actionable if r["verdict"] == "WATCH"]
        log(f"Invio notifiche: {len(copies)} COPY (con suono) + {len(watches)} WATCH (silenziosi)...")
        try:
            # ── COPY: notifica CON suono (urgente, da vedere subito)
            if copies:
                silent_copy = all(r.get("confidence", 50) < 86 for r in copies)
                if send_telegram(build_message(copies, state, is_demo), silent=silent_copy):
                    log(f"Telegram COPY inviato ({'silenzioso' if silent_copy else 'CON suono'})!", "OK")
            # ── WATCH: notifica SILENZIOSA (solo informativa)
            if watches:
                if send_telegram(build_message(watches, state, is_demo), silent=True):
                    log("Telegram WATCH inviato (silenzioso).", "OK")
        except Exception as e:
            log(f"Telegram: {e}", "ERR")
        send_email(actionable, state, is_demo)
    else:
        log(f"Nessun COPY/WATCH — notifiche dei segnali soppresse (tutti SKIP).", "OK")
        # ── HEARTBEAT: conferma che il bot sta girando anche senza segnali ───
        # Altrimenti l'utente non distingue "bot down" da "bot vivo senza segnali".
        if SEND_HEARTBEAT:
            try:
                lb_size = len(state.get("leaderboard", {}))
                skips   = sum(1 for r in results if r.get("verdict") == "SKIP")
                hb_msg  = (
                    f"🐋 <b>Whale Tracker — run #{run_n}</b>\n"
                    f"<i>{today.strftime('%d/%m/%Y %H:%M')} UTC</i>\n\n"
                    f"✅ Bot vivo, nessun segnale attivo\n"
                    f"• Whale monitorate: {lb_size}\n"
                    f"• Mercati analizzati: {len(results)} ({skips} SKIP)\n\n"
                    f"📊 Dashboard: https://bruccio.github.io/Poly/"
                )
                if send_telegram(hb_msg, silent=True):
                    log("Heartbeat Telegram inviato (silenzioso).", "OK")
            except Exception as e:
                log(f"Heartbeat Telegram fallito: {e}", "WARN")

    log(f"Run #{run_n} completato — {copy_count} COPY su {len(results)} analizzati.")


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
