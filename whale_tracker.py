#!/usr/bin/env python3
"""
Polymarket Whale Tracker → Telegram + Email
Monitora i mercati con più volume su Polymarket e avvisa quando
un grande investitore fa una mossa che vale la pena copiare.
"""

import os
import re
import sys
import time
import smtplib
import requests
from datetime import datetime
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
MIN_SIZE_USDC          = int(os.environ.get("MIN_WHALE_SIZE", "50000"))
MAX_WHALES             = int(os.environ.get("MAX_WHALES", "10"))
BANKROLL               = int(os.environ.get("BANKROLL", "10000"))
CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "30"))
ONLY_NOTIFY_ON_COPY    = os.environ.get("ONLY_NOTIFY_ON_COPY", "true").lower() != "false"

# ── FILTRO SPORT ─────────────────────────────────────────────────────────────────
SPORT_KEYWORDS = [
    " vs ", " vs. ", " v ", " @ ",
    "NBA", "NFL", "NHL", "MLB", "MLS", "WNBA", "NCAA",
    "Premier League", "Serie A", "La Liga", "Bundesliga", "Ligue 1",
    "Champions League", "Europa League", "World Cup", "Copa",
    "Super Bowl", "playoffs", "playoff",
    "UFC", "boxing", "MMA", "fight",
    "F1", "NASCAR", "Grand Prix",
    "tennis", "Open:", "ATP", "WTA", "Wimbledon",
    "golf", "PGA", "Masters",
    "Olympics", "Olimpiadi",
    "Oscar", "Grammy", "Emmy", "Eurovision",
]

def _is_sport(title: str) -> bool:
    t = title.lower()
    return any(kw.lower() in t for kw in SPORT_KEYWORDS)


# ── LOGGING ─────────────────────────────────────────────────────────────────────
def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    icon = {"OK": "✓", "ERR": "✗", "WARN": "⚠"}.get(level, "·")
    print(f"[{ts}] {icon} {msg}", flush=True)


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


def fetch_polymarket_whales(min_size):
    """
    Recupera i mercati/eventi Polymarket con volume >= min_size.
    Prova più endpoint in ordine di affidabilità.
    Restituisce (ok: bool, whales: list, total_count: int).
    """
    H = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}

    attempts = [
        ("GET",
         "https://gamma-api.polymarket.com/markets?limit=100&active=true&order=volume24hr&ascending=false",
         None),
        ("GET",
         "https://gamma-api.polymarket.com/events?limit=50&active=true",
         None),
        ("GET",
         "https://data-api.polymarket.com/activity?limit=200&type=TRADE",
         None),
        ("POST",
         "https://gateway-arbitrum.network.thegraph.com/api/f087f7244e56a2bc2d48c10e5a3c1bd3/subgraphs/id/Bx1W4S7kDVxs9gC3s2G6DS8kdNBJx2sYUiABH4RvGN46",
         '{"query":"{ orderFilledEvents(first:200,orderBy:matchedAmount,orderDirection:desc){matchedAmount price maker order{market{question}}} }"}'),
    ]

    for method, url, body in attempts:
        host = url.split("//")[1].split("/")[0]
        try:
            if method == "POST":
                r = requests.post(url, data=body,
                                  headers={**H, "Content-Type": "application/json"},
                                  timeout=15)
            else:
                r = requests.get(url, headers=H, timeout=12)

            if r.status_code != 200:
                log(f"{host} → HTTP {r.status_code}", "WARN")
                continue

            raw = r.json()

            if "thegraph" in url or "gateway-arbitrum" in url:
                events = raw.get("data", {}).get("orderFilledEvents", [])
                items = [{
                    "usdcSize":    str(float(e.get("matchedAmount", 0)) / 1e6),
                    "price":       e.get("price", "0"),
                    "side":        "YES",
                    "title":       e.get("order", {}).get("market", {}).get("question", "Mercato"),
                    "userAddress": e.get("maker", "0x???"),
                } for e in events]

            elif "markets" in url:
                mlist = raw if isinstance(raw, list) else raw.get("data", [])
                items = [{
                    "usdcSize":    str(float(m.get("volume24hr") or m.get("volume") or 0)),
                    "price":       str(m.get("bestAsk") or m.get("lastTradePrice") or 0.5),
                    "side":        "YES",
                    "title":       m.get("question") or m.get("title") or "Mercato",
                    "userAddress": "0xpool",
                } for m in mlist
                  if float(m.get("volume24hr") or m.get("volume") or 0) >= min_size]

            elif "events" in url:
                elist = raw if isinstance(raw, list) else raw.get("data", [])
                items = []
                for ev in elist:
                    vol = float(ev.get("volume") or ev.get("volumeNum") or 0)
                    if vol >= min_size:
                        items.append({
                            "usdcSize":    str(vol),
                            "price":       "0.5",
                            "side":        "YES",
                            "title":       ev.get("title") or ev.get("question") or "Evento",
                            "userAddress": "0xpool",
                        })

            else:
                items = raw if isinstance(raw, list) else raw.get("data", raw.get("activities", []))

            if not items:
                log(f"{host} → 0 elementi utili", "WARN")
                continue

            # Filtra sport e mercati sotto soglia
            filtered = [
                t for t in items
                if _sz(t) >= min_size and not _is_sport(t.get("title") or t.get("question") or "")
            ]
            filtered.sort(key=_sz, reverse=True)

            # Diversità: prende i top 30 e seleziona 10 con titoli diversi
            seen_words: set[str] = set()
            diverse: list = []
            for t in filtered[:30]:
                title = (t.get("title") or t.get("question") or "").lower()
                # Usa le prime 2 parole significative come "firma" del mercato
                words = [w for w in title.split() if len(w) > 3][:2]
                key = " ".join(words)
                if key not in seen_words:
                    seen_words.add(key)
                    diverse.append(t)
                if len(diverse) >= MAX_WHALES:
                    break

            whales = diverse or filtered[:MAX_WHALES]
            log(f"Polymarket OK ({host}): {len(items)} totali → "
                f"{len(filtered)} non-sport ≥${min_size:,} → {len(whales)} selezionati", "OK")
            return True, whales, len(items)

        except Exception as e:
            log(f"{host} → {str(e)[:90]}", "WARN")

    return False, [], 0


# ── CLAUDE ──────────────────────────────────────────────────────────────────────
MODELS = [
    "claude-haiku-4-5-20251001",
    "claude-3-5-haiku-20241022",
    "claude-3-haiku-20240307",
]

SYSTEM_PROMPT = (
    "Sei un consulente finanziario che spiega le cose in modo semplice a un utente non esperto.\n"
    "Stai analizzando un grosso movimento di denaro su Polymarket (mercato predittivo) "
    "fatto da un grande investitore.\n"
    "Il tuo compito: capire se vale la pena copiarlo, e spiegarlo in italiano chiaro e diretto.\n\n"
    "Regole:\n"
    "- Escludi qualsiasi mercato sportivo (calcio, basket, tennis, F1, ecc.) → sempre SKIP\n"
    "- Se il trade sembra rischioso, speculativo o poco chiaro → SKIP\n"
    "- Se il trade sembra solido e con buone basi → COPY\n"
    "- Se COPY, suggerisci quanto investire (massimo 10% del bankroll, mai più)\n\n"
    "Rispondi SOLO in questo formato, in italiano semplice, zero tecnicismi:\n"
    "COPY\n"
    "Rischio: X/10\n"
    "Vale la pena?: [1 frase, es. 'Sì, un grande investitore scommette su X con alta fiducia']\n"
    "Cosa sta succedendo: [2 righe max, spiega il mercato come a un amico]\n"
    "Quanto investire: [es. 'Puoi mettere fino a $500, non di più']\n"
    "Sospetto?: No/Forse/Sì"
)


def _classify(size):
    if size >= 100_000: return "Alpha/Insider (>$100k)"
    if size >= 50_000:  return "Institutional ($50k–$100k)"
    return "Opportunistic (<$50k)"


def analyze_with_claude(trade):
    size   = _sz(trade)
    price  = float(trade.get("price") or trade.get("outcomePrice") or 0.5)
    side   = trade.get("side") or "YES"
    market = trade.get("title") or trade.get("question") or "Mercato"
    wallet = (trade.get("maker") or trade.get("userAddress") or "0x???")[:14] + "..."
    tier   = _classify(size)

    text = (
        f"Mercato: {market}\n"
        f"Direzione: {side}\n"
        f"Prezzo attuale: {price:.3f}\n"
        f"Volume/Size: ${size:,.0f} USDC\n"
        f"Tier: {tier}\n"
        f"Wallet: {wallet}\n"
        f"Bankroll utente: ${BANKROLL:,}"
    )

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
                    "max_tokens": 300,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": f"Analizza:\n\n{text}"}],
                },
                timeout=30,
            )
            if r.status_code == 200:
                raw = "".join(b.get("text", "") for b in r.json().get("content", []))
                log(f"Claude OK ({model})", "OK")
                return _parse_claude(raw, market, side, price, size, wallet, tier)

            err = r.json().get("error", {}).get("message", r.text[:100])
            log(f"Claude {r.status_code} ({model}): {err}", "WARN")
            last_err = err

        except Exception as e:
            log(f"Claude eccezione ({model}): {e}", "WARN")
            last_err = str(e)

    raise RuntimeError(last_err)


def _parse_claude(raw, market, side, price, size, wallet, tier):
    def g(pat, flags=re.I):
        m = re.search(pat, raw, flags)
        return m.group(1).strip() if m else None

    return {
        "market":      market,
        "side":        side,
        "price":       price,
        "size":        size,
        "wallet":      wallet,
        "tier":        tier,
        "verdict":     "COPY" if re.search(r"^COPY", raw, re.M) else "SKIP",
        "risk_score":  int(g(r"Rischio[:\s]+(\d+)") or 5),
        "vale_pena":   g(r"Vale la pena\?[:\s]*(.+?)(?:\n|$)") or "",
        "spiegazione": (g(r"Cosa sta succedendo[:\s]*(.+?)(?:\nQuanto|\nSosp|$)",
                          re.I | re.S) or raw[:200])[:250],
        "quanto":      g(r"Quanto investire[:\s]*(.+?)(?:\n|$)") or "N/A",
        "sospetto":    g(r"Sospetto\?[:\s]*(.+?)(?:\n|$)") or "No",
    }


# ── TELEGRAM ────────────────────────────────────────────────────────────────────
def build_message(results, is_demo=False, best_skip=None):
    ts  = datetime.now().strftime("%d/%m/%Y %H:%M")
    msg = f"🐋 *Grandi Mosse su Polymarket*{' _(DEMO)_' if is_demo else ''}\n_{ts}_\n\n"

    copy_count = sum(1 for t in results if t["verdict"] == "COPY")
    msg += f"Analizzati {len(results)} mercati non\\-sportivi da >\\${MIN_SIZE_USDC // 1000}k.\n"

    if copy_count:
        msg += f"*{copy_count}* {'merita' if copy_count == 1 else 'meritano'} attenzione. 👇\n\n"
    else:
        msg += "Nessun COPY oggi — ecco il *meno peggio* tra quelli analizzati. 👇\n\n"

    # Mostra prima i COPY, poi solo il best_skip se non ci sono COPY
    to_show = [t for t in results if t["verdict"] == "COPY"]
    if not to_show and best_skip:
        to_show = [best_skip]
    if not to_show:
        to_show = results[:3]

    for t in to_show:
        m = t["market"][:70] + ("..." if len(t["market"]) > 70 else "")
        is_copy = t["verdict"] == "COPY"
        msg += "✅ *DA VALUTARE*\n" if is_copy else "⭐ *IL MENO PEGGIO DI OGGI*\n"
        msg += f"📌 _{m}_\n"
        if t.get("vale_pena"):
            msg += f"💡 {t['vale_pena']}\n"
        if t.get("spiegazione"):
            msg += f"📖 {t['spiegazione'][:200]}\n"
        risk = t["risk_score"]
        icon = "🟢" if risk <= 3 else "🟡" if risk <= 6 else "🔴"
        msg += f"{icon} Rischio: {risk}/10"
        if is_copy and t.get("quanto", "N/A") != "N/A":
            msg += f"  |  💰 {t['quanto']}\n"
        else:
            msg += "\n"
        if t.get("sospetto", "No") not in ("No", "N/A"):
            msg += "⚠️ Potrebbe essere gonfiato artificialmente\n"
        msg += "\n"

    return msg + "_Polymarket Whale Tracker — Bruno_"


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
def build_email_html(results, is_demo=False, best_skip=None):
    ts = datetime.now().strftime("%d/%m/%Y %H:%M")
    copy_count = sum(1 for t in results if t["verdict"] == "COPY")

    copy_results = [t for t in results if t["verdict"] == "COPY"]
    to_show = copy_results or ([best_skip] if best_skip else results[:3])

    rows = ""
    for t in to_show:
        is_copy = t["verdict"] == "COPY"
        color   = "#1a7a3a" if is_copy else "#555"
        bg      = "#f0fff4" if is_copy else "#fafafa"
        badge   = "✅ DA VALUTARE" if is_copy else "⭐ Il meno peggio di oggi"
        risk    = t["risk_score"]
        risk_color = "#2a9d2a" if risk <= 3 else "#e6a817" if risk <= 6 else "#d9534f"

        rows += f"""
        <tr style="background:{bg};border-bottom:1px solid #e0e0e0;">
          <td style="padding:16px;">
            <span style="font-weight:bold;color:{color};">{badge}</span><br>
            <span style="font-size:15px;font-weight:600;">{t['market'][:90]}</span><br><br>
            {f"<b>💡 {t['vale_pena']}</b><br>" if t.get('vale_pena') else ""}
            {f"📖 {t['spiegazione'][:200]}<br>" if t.get('spiegazione') else ""}
            <br>
            <span style="color:{risk_color};font-weight:bold;">Rischio: {risk}/10</span>
            {"&nbsp;&nbsp;|&nbsp;&nbsp;💰 <b>" + t['quanto'] + "</b>" if is_copy and t.get('quanto','N/A') != 'N/A' else ""}
            {"<br>⚠️ <i>Potrebbe essere gonfiato artificialmente</i>" if t.get('sospetto','No') not in ('No','N/A') else ""}
          </td>
        </tr>"""

    demo_banner = '<p style="background:#fff3cd;padding:8px;border-radius:4px;">⚠️ Dati demo — nessun mercato reale trovato</p>' if is_demo else ""

    return f"""
    <html><body style="font-family:Arial,sans-serif;max-width:680px;margin:auto;color:#222;">
      <div style="background:#0d1b2a;padding:20px;border-radius:8px 8px 0 0;">
        <h2 style="color:#fff;margin:0;">🐋 Grandi Mosse su Polymarket</h2>
        <p style="color:#aaa;margin:4px 0 0;">{ts}</p>
      </div>
      <div style="padding:16px;background:#f5f5f5;">
        {demo_banner}
        <p>Analizzati <b>{len(results)}</b> movimenti da >${MIN_SIZE_USDC // 1000}k USDC.
           <b style="color:#1a7a3a;">{copy_count}</b> {'merita' if copy_count == 1 else 'meritano'} attenzione.</p>
      </div>
      <table width="100%" cellspacing="0" cellpadding="0">{rows}</table>
      <div style="padding:12px;background:#0d1b2a;border-radius:0 0 8px 8px;text-align:center;">
        <span style="color:#aaa;font-size:12px;">Polymarket Whale Tracker — Bruno</span>
      </div>
    </body></html>"""


def send_email(results, is_demo=False, best_skip=None):
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
        msg.attach(MIMEText(build_email_html(results, is_demo, best_skip), "html"))

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
    log("=" * 52)
    log(f"Soglia: >${MIN_SIZE_USDC:,} | Max: {MAX_WHALES} | Bankroll: ${BANKROLL:,}")
    log("=" * 52)

    ok, whales, total = fetch_polymarket_whales(MIN_SIZE_USDC)
    is_demo = not ok or not whales
    if is_demo:
        log("Nessun dato reale — uso dati demo.", "WARN")
        whales = [
            {"usdcSize": "85000",  "price": "0.33", "side": "YES",
             "title": "Will the Fed cut rates in May 2026?",         "userAddress": "0xdemo1"},
            {"usdcSize": "65000",  "price": "0.67", "side": "NO",
             "title": "Will BTC reach $120k before June 2026?",      "userAddress": "0xdemo2"},
            {"usdcSize": "120000", "price": "0.24", "side": "YES",
             "title": "Will Trump impose 50%+ tariffs on EU?",       "userAddress": "0xdemo3"},
        ]

    results = []
    for i, trade in enumerate(whales[:MAX_WHALES]):
        name = trade.get("title") or trade.get("question") or "Mercato"
        log(f"Analisi [{i+1}/{min(MAX_WHALES, len(whales))}]: {name[:50]}...")
        try:
            result = analyze_with_claude(trade)
            results.append(result)
            log(f"→ {result['verdict']} | Rischio {result['risk_score']}/10 | Sospetto: {result.get('sospetto','No')}", "OK")
        except Exception as e:
            log(f"Errore: {e}", "ERR")
            results.append({
                "market": name, "side": "N/D", "price": 0, "size": 0,
                "wallet": "—", "tier": "—", "verdict": "SKIP", "risk_score": 5,
                "vale_pena": "", "spiegazione": f"Errore durante l'analisi: {str(e)[:100]}",
                "quanto": "N/A", "sospetto": "No",
            })
        if i < MAX_WHALES - 1:
            time.sleep(1)

    has_copy = any(r["verdict"] == "COPY" for r in results)

    # Trova il "meno peggio" tra i SKIP (rischio più basso)
    skips = [r for r in results if r["verdict"] == "SKIP"]
    best_skip = min(skips, key=lambda r: r["risk_score"]) if skips else None

    if results:
        log("Invio su Telegram...")
        try:
            if send_telegram(build_message(results, is_demo, best_skip)):
                log("Telegram inviato!", "OK")
        except Exception as e:
            log(f"Telegram: {e}", "ERR")

        log("Invio per email...")
        send_email(results, is_demo, best_skip)

    log("Run completato.")
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
