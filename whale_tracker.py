"""
Polymarket Whale Tracker — Daily Briefing Bot
Fetcha i trade più grandi, identifica le whale, analizza con Claude AI,
e invia il briefing giornaliero su Telegram.
"""

import os
import json
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from anthropic import Anthropic

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("whale-tracker")


# ─── Configurazione ────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]

MIN_WHALE_SIZE_USDC = int(os.environ.get("MIN_WHALE_SIZE", "10000"))   # $10k default
TOP_MARKETS         = int(os.environ.get("TOP_MARKETS", "15"))         # quanti mercati scandagliare
LOOKBACK_HOURS      = int(os.environ.get("LOOKBACK_HOURS", "24"))      # finestra temporale

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"


# ─── Fetcher ───────────────────────────────────────────────────────────────────
class PolymarketFetcher:
    """Chiama le API pubbliche Polymarket e aggrega i trade."""

    HEADERS = {"User-Agent": "whale-tracker-bot/1.0"}

    def _get(self, url: str, params: dict = None, retries: int = 3) -> dict | list:
        for attempt in range(retries):
            try:
                r = requests.get(url, params=params, headers=self.HEADERS, timeout=12)
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                log.warning(f"GET {url} tentativo {attempt+1}/{retries}: {e}")
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
        return []

    def fetch_top_markets(self) -> list[dict]:
        """Restituisce i mercati più attivi per volume 24h."""
        data = self._get(f"{GAMMA_API}/markets", params={
            "limit": TOP_MARKETS,
            "order": "volume24hr",
            "ascending": "false",
            "active": "true",
        })
        markets = data if isinstance(data, list) else data.get("data", [])
        log.info(f"Mercati attivi recuperati: {len(markets)}")
        return markets

    def fetch_trades_for_token(self, token_id: str, limit: int = 500) -> list[dict]:
        """Restituisce i trade recenti di un token (YES o NO)."""
        data = self._get(f"{CLOB_API}/trades", params={
            "market": token_id,
            "limit": limit,
        })
        if isinstance(data, dict):
            return data.get("data", [])
        return []

    def collect_all_trades(self, markets: list[dict]) -> list[dict]:
        """Raccoglie tutti i trade dai mercati top, attaching le info del mercato."""
        all_trades = []
        for mkt in markets:
            question = mkt.get("question", mkt.get("slug", "Mercato sconosciuto"))
            condition_id = mkt.get("conditionId", "")
            token_ids: list = mkt.get("clobTokenIds", [])

            # Alcuni mercati hanno i token come stringa JSON
            if isinstance(token_ids, str):
                try:
                    token_ids = json.loads(token_ids)
                except json.JSONDecodeError:
                    token_ids = [token_ids]

            for token_id in token_ids[:2]:   # YES e NO
                if not token_id:
                    continue
                trades = self.fetch_trades_for_token(str(token_id))
                for t in trades:
                    t["_question"]     = question
                    t["_condition_id"] = condition_id
                all_trades.extend(trades)
                time.sleep(0.15)   # cortesia verso le API

        log.info(f"Trade totali raccolti: {len(all_trades)}")
        return all_trades


# ─── Whale Analyzer ────────────────────────────────────────────────────────────
class WhaleAnalyzer:
    """Filtra e raggruppa i trade per wallet, identificando le whale."""

    def __init__(self, min_size: int = MIN_WHALE_SIZE_USDC, lookback_hours: int = LOOKBACK_HOURS):
        self.min_size      = min_size
        self.cutoff_epoch  = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).timestamp()

def _trade_size(self, trade: dict) -> float:
    """Estrae la size in USDC: shares × price."""
    price = 0.0
    try:
        price = float(trade.get("price", 0) or 0)
    except (ValueError, TypeError):
        pass

    for field in ("usdcSize", "size", "makerAmountFilled", "takerAmountFilled"):
        val = trade.get(field)
        if val:
            try:
                shares = float(val)
                # Se il campo è già in USDC (usdcSize) non moltiplicare
                if field == "usdcSize":
                    return shares
                # Altrimenti è in shares → converti in USDC
                return shares * price if price > 0 else shares
            except (ValueError, TypeError):
                pass
    return 0.0

    def _trade_timestamp(self, trade: dict) -> float:
        for field in ("match_time", "timestamp", "created_at"):
            val = trade.get(field)
            if val:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass
        return 0.0

    def filter_and_group(self, trades: list[dict]) -> list[dict]:
        """
        Restituisce una lista di whale ordenate per volume decrescente.
        Ogni whale è un dict con address, trades, total_volume, markets.
        """
        wallets: dict[str, dict] = {}

        for trade in trades:
            ts = self._trade_timestamp(trade)
            if ts and ts < self.cutoff_epoch:
                continue                        # trade troppo vecchio

            size = self._trade_size(trade)
            if size < self.min_size:
                continue                        # sotto soglia

            maker = (
                trade.get("maker_address")
                or trade.get("owner")
                or trade.get("maker")
                or ""
            ).lower().strip()
            if not maker or maker in ("", "0x0000000000000000000000000000000000000000"):
                continue

            if maker not in wallets:
                wallets[maker] = {
                    "address":      maker,
                    "trades":       [],
                    "total_volume": 0.0,
                    "markets":      set(),
                    "sides":        {"BUY": 0, "SELL": 0},
                }

            wallets[maker]["trades"].append(trade)
            wallets[maker]["total_volume"] += size

            question = trade.get("_question", trade.get("_condition_id", "?"))
            wallets[maker]["markets"].add(question)

            side = str(trade.get("side", "")).upper()
            if side in wallets[maker]["sides"]:
                wallets[maker]["sides"][side] += 1

        # Converti set in lista per serializzazione
        result = []
        for w in wallets.values():
            w["markets"] = list(w["markets"])
            result.append(w)

        result.sort(key=lambda x: x["total_volume"], reverse=True)
        log.info(f"Whale identificate (>={self.min_size:,} USDC): {len(result)}")
        return result

    def build_summary_text(self, whales: list[dict]) -> str:
        """Costruisce il testo da inviare a Claude per l'analisi."""
        now = datetime.now().strftime("%d/%m/%Y %H:%M")
        lines = [
            f"POLYMARKET WHALE ACTIVITY — {now}",
            f"Finestra: ultime {LOOKBACK_HOURS}h | Soglia: ≥${self.min_size:,} USDC\n",
        ]

        for i, w in enumerate(whales[:6], 1):   # analizza top 6
            addr = w["address"]
            short = f"{addr[:6]}...{addr[-4:]}"
            buy_count  = w["sides"].get("BUY", 0)
            sell_count = w["sides"].get("SELL", 0)
            bias = "prevalentemente long (BUY)" if buy_count > sell_count else "prevalentemente short (SELL)" if sell_count > buy_count else "bilanciato"

            lines += [
                f"\n--- WHALE #{i} ---",
                f"Wallet: {short}",
                f"Volume totale 24h: ${w['total_volume']:,.0f} USDC",
                f"Mercati attivi: {len(w['markets'])}",
                f"Bias direzionale: {bias} ({buy_count} BUY / {sell_count} SELL)",
                f"Trade count: {len(w['trades'])}",
                "Trade rilevanti:",
            ]

            seen_markets = set()
            for t in sorted(w["trades"], key=lambda x: self._trade_size(x), reverse=True)[:4]:
                mkt = t.get("_question", t.get("_condition_id", "Sconosciuto"))[:60]
                if mkt in seen_markets:
                    continue
                seen_markets.add(mkt)

                size     = self._trade_size(t)
                price    = t.get("price", "?")
                side     = t.get("side", "?")
                outcome  = t.get("outcome", "?")
                lines.append(f"  · {mkt}")
                lines.append(f"    {side} {outcome} @ {price} — ${size:,.0f} USDC")

        return "\n".join(lines)


# ─── Claude Analyzer ───────────────────────────────────────────────────────────
CLAUDE_SYSTEM_PROMPT = """Sei un analista quantitativo specializzato in prediction markets (Polymarket).
Analizza i dati whale forniti e produci un DAILY BRIEFING strutturato.

Applica questi criteri:
- TIER A: volume >$100k o accumulo frazionato sofisticato, trade early, mercati illiquidi/contrarian
- TIER B: $25k-$100k, istituzionale, limit orders, buona diversificazione  
- TIER C: $10k-$25k, reattivo a news pubbliche, edge basso
- COPY se: OI in aumento, timing early, mercato liquido, whale tier A/B, risk_score ≤ 5
- SKIP se: prezzo già mosso >5%, OI in calo, wash trading, timing tardivo
- WATCH se: segnale interessante ma da confermare
- Kelly frazionario Quarter-Kelly: f* = 0.25 × (p - q) / (1 - q), tetto 10%
- Segnala wash trading se vedi pattern simmetrici BUY/SELL dallo stesso wallet

Rispondi SOLO con JSON valido (nessun testo prima/dopo, nessun markdown):
{
  "summary": {
    "whales_tracked": <int>,
    "copy_signals": <int>,
    "skip_signals": <int>,
    "watch_signals": <int>,
    "risk_level": "basso|medio|alto",
    "market_sentiment": "<frase breve 5-8 parole>"
  },
  "whales": [
    {
      "address": "<0x...abbreviato>",
      "tier": "A|B|C",
      "style": "Early Narrative Bettor|Momentum Follower|Macro Institutional|Contrarian Alpha|Casual Bettor|Wash Trader",
      "volume_24h": "<es. $87,000>",
      "best_markets": "<categorie>",
      "note": "<insight chiave 1 riga>",
      "copy_worthy": true|false
    }
  ],
  "trades": [
    {
      "market": "<nome mercato>",
      "direction": "YES|NO",
      "entry_price": "<es. 0.34>",
      "whale_tier": "A|B|C",
      "decision": "COPY|SKIP|WATCH",
      "risk_score": <1-10>,
      "edge": "basso|medio|alto",
      "kelly_fraction": "<es. 4%>",
      "entry_window": "<es. valido se prezzo <0.40>",
      "reason": "<motivazione 2 righe>"
    }
  ],
  "risk_alerts": ["<alert 1>", "<alert 2>"],
  "daily_insight": "<osservazione macro 1-2 frasi sul sentiment generale del mercato oggi>"
}"""


class ClaudeAnalyzer:
    def __init__(self):
        self.client = Anthropic(api_key=ANTHROPIC_API_KEY)

    def analyze(self, whale_text: str) -> dict:
        log.info("Invio dati a Claude per analisi...")
        response = self.client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2000,
            system=CLAUDE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": whale_text}],
        )
        raw = response.content[0].text.strip()

        # Rimuovi eventuali backtick markdown
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip().rstrip("`").strip()

        briefing = json.loads(raw)
        log.info("Analisi Claude completata")
        return briefing


# ─── Telegram Sender ───────────────────────────────────────────────────────────
class TelegramSender:
    BASE = "https://api.telegram.org/bot"

    def __init__(self):
        self.token   = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID

    def send(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Invia un messaggio Telegram. Spezza i messaggi >4096 char."""
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            r = requests.post(
                f"{self.BASE}{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": chunk, "parse_mode": parse_mode},
                timeout=15,
            )
            if not r.ok:
                log.error(f"Telegram error: {r.status_code} — {r.text}")
                return False
            time.sleep(0.3)
        return True

    def format_briefing(self, briefing: dict) -> str:
        """Trasforma il dict JSON del briefing in un messaggio Telegram leggibile."""
        s       = briefing.get("summary", {})
        whales  = briefing.get("whales", [])
        trades  = briefing.get("trades", [])
        alerts  = briefing.get("risk_alerts", [])
        insight = briefing.get("daily_insight", "")

        risk_emoji = {"basso": "🟢", "medio": "🟡", "alto": "🔴"}.get(s.get("risk_level", ""), "⚪")
        date_str   = datetime.now().strftime("%d/%m/%Y")

        lines = [
            f"🐋 *WHALE BRIEFING — {date_str}*",
            "",
            f"📊 *Panoramica mercato*",
            f"Whale tracciate: *{s.get('whales_tracked', 0)}*  |  "
            f"✅ COPY: *{s.get('copy_signals', 0)}*  |  "
            f"❌ SKIP: *{s.get('skip_signals', 0)}*  |  "
            f"👁 WATCH: *{s.get('watch_signals', 0)}*",
            f"Rischio globale: {risk_emoji} *{s.get('risk_level', '—').upper()}*",
            f"Sentiment: _{s.get('market_sentiment', '—')}_",
        ]

        if insight:
            lines += ["", f"💡 _{insight}_"]

        # Whale da seguire
        top_whales = [w for w in whales if w.get("copy_worthy")] or whales[:3]
        if top_whales:
            lines += ["", "─" * 28, "🐋 *Whale da seguire oggi*"]
            for w in top_whales[:4]:
                tier_badge = {"A": "🟢 A", "B": "🟡 B", "C": "⚪ C"}.get(w.get("tier", ""), "❓")
                lines.append(
                    f"{tier_badge}  `{w.get('address', '—')}`"
                    f"  —  {w.get('volume_24h', '')}  —  {w.get('style', '')}"
                )
                if w.get("note"):
                    lines.append(f"   _{w['note']}_")

        # Raccomandazioni trade
        if trades:
            lines += ["", "─" * 28, "📈 *Trade raccomandati*"]
            for t in trades:
                dec       = t.get("decision", "")
                dec_emoji = {"COPY": "✅", "SKIP": "❌", "WATCH": "👁"}.get(dec, "❓")
                risk_val  = t.get("risk_score", "?")
                lines.append(
                    f"\n{dec_emoji} *{t.get('market', '—')}*"
                )
                lines.append(
                    f"   {t.get('direction', '')} @ {t.get('entry_price', '?')}  "
                    f"|  Risk {risk_val}/10  |  Kelly {t.get('kelly_fraction', '—')}  "
                    f"|  Tier {t.get('whale_tier', '?')}"
                )
                if t.get("entry_window"):
                    lines.append(f"   🪟 Finestra: _{t['entry_window']}_")
                if t.get("reason"):
                    lines.append(f"   _{t['reason'][:140]}_")

        # Alert
        if alerts:
            lines += ["", "─" * 28, "⚠️ *Alert*"]
            for a in alerts:
                lines.append(f"• {a}")

        lines += [
            "",
            f"─" * 28,
            f"_Generato {datetime.now().strftime('%H:%M')} · Polymarket Whale Bot_",
        ]

        return "\n".join(lines)


# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 50)
    log.info("POLYMARKET WHALE TRACKER — avvio")
    log.info(f"Soglia: ≥${MIN_WHALE_SIZE_USDC:,} USDC | Finestra: {LOOKBACK_HOURS}h | Mercati: {TOP_MARKETS}")
    log.info("=" * 50)

    telegram = TelegramSender()

    try:
        # 1. Fetch mercati
        fetcher = PolymarketFetcher()
        markets = fetcher.fetch_top_markets()
        if not markets:
            raise RuntimeError("Nessun mercato recuperato dall'API Polymarket")

        # 2. Raccolta trade
        all_trades = fetcher.collect_all_trades(markets)

        # 3. Identificazione whale
        analyzer = WhaleAnalyzer()
        whales   = analyzer.filter_and_group(all_trades)

        if not whales:
            msg = (
                f"🐋 *Whale Briefing — {datetime.now().strftime('%d/%m/%Y')}*\n\n"
                f"Nessuna whale rilevata nelle ultime {LOOKBACK_HOURS}h "
                f"con size ≥${MIN_WHALE_SIZE_USDC:,} USDC.\n"
                f"Mercati relativamente calmi — nessuna raccomandazione trade oggi."
            )
            telegram.send(msg)
            log.info("Nessuna whale trovata — notifica inviata")
            return

        # 4. Analisi AI
        whale_text = analyzer.build_summary_text(whales)
        log.debug(f"Testo inviato a Claude:\n{whale_text[:500]}...")

        claude   = ClaudeAnalyzer()
        briefing = claude.analyze(whale_text)

        # 5. Formattazione e invio Telegram
        message = telegram.format_briefing(briefing)
        ok      = telegram.send(message)
        if ok:
            log.info("✅ Briefing inviato su Telegram con successo")
        else:
            log.error("❌ Invio Telegram fallito")

        # 6. Log locale
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "whales_found": len(whales),
            "briefing": briefing,
        }
        with open("whale_log.jsonl", "a") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    except Exception as e:
        log.exception(f"Errore fatale: {e}")
        telegram.send(f"⚠️ *Whale Tracker Error*\n`{type(e).__name__}: {str(e)[:200]}`")
        raise


if __name__ == "__main__":
    main()
