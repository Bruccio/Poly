"""
Polymarket Whale Tracker — Daily Briefing Bot v2
Usa data-api.polymarket.com (pubblico, no auth required).
"""

import os
import json
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from anthropic import Anthropic

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("whale-tracker")

ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]

MIN_WHALE_SIZE_USDC = int(os.environ.get("MIN_WHALE_SIZE", "5000"))
LOOKBACK_HOURS      = int(os.environ.get("LOOKBACK_HOURS", "24"))
TOP_MARKETS         = int(os.environ.get("TOP_MARKETS", "15"))

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API  = "https://data-api.polymarket.com"


class PolymarketFetcher:

    HEADERS = {"User-Agent": "Mozilla/5.0 whale-tracker/2.0"}

    def _get(self, url, params=None, retries=3):
        for attempt in range(retries):
            try:
                r = requests.get(url, params=params, headers=self.HEADERS, timeout=15)
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                log.warning(f"GET {url} tentativo {attempt+1}/{retries}: {e}")
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
        return []

    def fetch_top_markets(self):
        data = self._get(f"{GAMMA_API}/markets", params={
            "limit": TOP_MARKETS,
            "order": "volume24hr",
            "ascending": "false",
            "active": "true",
        })
        markets = data if isinstance(data, list) else data.get("data", [])
        log.info(f"Mercati attivi: {len(markets)}")
        return markets

    def fetch_activity(self, limit=1000):
        """Usa il Data API pubblico — nessuna autenticazione richiesta."""
        data = self._get(f"{DATA_API}/activity", params={"limit": limit})
        items = data if isinstance(data, list) else data.get("data", [])
        log.info(f"Activity items recuperati: {len(items)}")
        return items

    def fetch_market_trades(self, condition_id, limit=200):
        """Trade per singolo mercato via Data API."""
        data = self._get(f"{DATA_API}/activity", params={
            "market": condition_id,
            "limit": limit,
        })
        return data if isinstance(data, list) else data.get("data", [])

    def collect_all_trades(self, markets):
        """Raccoglie trade dall'activity feed globale + per i top mercati."""
        # Feed globale
        all_trades = self.fetch_activity(limit=1000)

        # Integra con trade per singolo mercato (top 5 per volume)
        for mkt in markets[:5]:
            condition_id = mkt.get("conditionId", "")
            if not condition_id:
                continue
            trades = self.fetch_market_trades(condition_id)
            question = mkt.get("question", mkt.get("slug", ""))
            for t in trades:
                if "_question" not in t:
                    t["_question"] = question
                    t["_condition_id"] = condition_id
            all_trades.extend(trades)
            time.sleep(0.2)

        # Deduplicazione per id
        seen = set()
        unique = []
        for t in all_trades:
            tid = t.get("id") or t.get("transactionHash") or str(t)
            if tid not in seen:
                seen.add(tid)
                unique.append(t)

        log.info(f"Trade unici raccolti: {len(unique)}")
        return unique


class WhaleAnalyzer:

    def __init__(self, min_size=None, lookback_hours=None):
        self.min_size     = min_size or MIN_WHALE_SIZE_USDC
        self.cutoff_epoch = (
            datetime.now(timezone.utc) - timedelta(hours=lookback_hours or LOOKBACK_HOURS)
        ).timestamp()

    def _trade_size(self, trade):
        """Size in USDC: prova campi diretti, poi shares*price."""
        # Campo USDC diretto
        for field in ("usdcSize", "amount", "cashPayout", "cashAmount"):
            val = trade.get(field)
            if val:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass

        # shares * price
        price = 0.0
        try:
            price = float(trade.get("price", 0) or 0)
        except (ValueError, TypeError):
            pass

        for field in ("size", "shares", "makerAmountFilled", "takerAmountFilled"):
            val = trade.get(field)
            if val:
                try:
                    shares = float(val)
                    return shares * price if price > 0 else shares
                except (ValueError, TypeError):
                    pass
        return 0.0

    def _trade_timestamp(self, trade):
        for field in ("timestamp", "createdAt", "created_at", "match_time", "blockTimestamp"):
            val = trade.get(field)
            if val:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass
        return 0.0

    def _wallet_address(self, trade):
        for field in ("user", "maker", "owner", "maker_address", "transactor", "funder"):
            val = trade.get(field)
            if val and isinstance(val, str) and val.startswith("0x") and len(val) > 10:
                return val.lower()
        return ""

    def filter_and_group(self, trades):
        """Filtra per size e finestra temporale, aggrega per wallet."""
        wallets = {}

        for trade in trades:
            ts = self._trade_timestamp(trade)
            if ts and ts < self.cutoff_epoch:
                continue

            size = self._trade_size(trade)
            if size < self.min_size:
                continue

            wallet = self._wallet_address(trade)
            if not wallet:
                continue

            if wallet not in wallets:
                wallets[wallet] = {
                    "address":      wallet,
                    "trades":       [],
                    "total_volume": 0.0,
                    "markets":      set(),
                    "sides":        {"buy": 0, "sell": 0},
                }

            wallets[wallet]["trades"].append(trade)
            wallets[wallet]["total_volume"] += size

            question = (
                trade.get("_question")
                or trade.get("title")
                or trade.get("market")
                or trade.get("conditionId", "?")
            )
            wallets[wallet]["markets"].add(str(question)[:60])

            side = str(trade.get("side", trade.get("type", ""))).lower()
            if "sell" in side:
                wallets[wallet]["sides"]["sell"] += 1
            else:
                wallets[wallet]["sides"]["buy"] += 1

        result = []
        for w in wallets.values():
            w["markets"] = list(w["markets"])
            result.append(w)

        result.sort(key=lambda x: x["total_volume"], reverse=True)
        log.info(f"Whale identificate (>=${self.min_size:,}): {len(result)}")
        return result

    def build_summary_text(self, whales):
        now = datetime.now().strftime("%d/%m/%Y %H:%M")
        lines = [
            f"POLYMARKET WHALE ACTIVITY — {now}",
            f"Finestra: ultime {LOOKBACK_HOURS}h | Soglia: >=${self.min_size:,} USDC\n",
        ]

        for i, w in enumerate(whales[:6], 1):
            addr  = w["address"]
            short = f"{addr[:6]}...{addr[-4:]}"
            buys  = w["sides"]["buy"]
            sells = w["sides"]["sell"]
            bias  = (
                "prevalentemente long (BUY)"  if buys > sells else
                "prevalentemente short (SELL)" if sells > buys else
                "bilanciato"
            )

            lines += [
                f"\n--- WHALE #{i} ---",
                f"Wallet: {short}",
                f"Volume 24h: ${w['total_volume']:,.0f} USDC",
                f"Mercati: {len(w['markets'])} | Trade: {len(w['trades'])}",
                f"Bias: {bias} ({buys} BUY / {sells} SELL)",
                "Trade principali:",
            ]

            seen = set()
            for t in sorted(w["trades"], key=lambda x: self._trade_size(x), reverse=True)[:4]:
                mkt  = (t.get("_question") or t.get("title") or t.get("conditionId", "?"))[:55]
                if mkt in seen:
                    continue
                seen.add(mkt)
                size    = self._trade_size(t)
                price   = t.get("price", "?")
                outcome = t.get("outcome", t.get("side", "?"))
                lines.append(f"  · {mkt}")
                lines.append(f"    {outcome} @ {price} — ${size:,.0f} USDC")

        return "\n".join(lines)


CLAUDE_SYSTEM = """Sei un analista quantitativo specializzato in prediction markets (Polymarket).
Analizza i dati whale e produci un DAILY BRIEFING.

Criteri di classificazione:
- TIER A: volume >$100k o accumulo frazionato sofisticato, timing early, mercati contrarian
- TIER B: $25k-$100k, istituzionale, limit orders
- TIER C: $5k-$25k, reattivo a news, edge basso
- COPY: OI crescente, timing early, mercato liquido, whale A/B, risk_score <=5
- SKIP: prezzo mosso >5%, OI calante, wash trading, timing tardivo
- WATCH: segnale interessante ma da confermare
- Kelly Quarter: f* = 0.25 x (p - q) / (1 - q), tetto 10%

Rispondi SOLO con JSON valido, nessun testo extra:
{
  "summary": {
    "whales_tracked": 0,
    "copy_signals": 0,
    "skip_signals": 0,
    "watch_signals": 0,
    "risk_level": "basso|medio|alto",
    "market_sentiment": "frase breve"
  },
  "whales": [
    {
      "address": "0x...abbreviato",
      "tier": "A|B|C",
      "style": "Early Narrative Bettor|Momentum Follower|Macro Institutional|Contrarian Alpha|Casual Bettor",
      "volume_24h": "$XX,000",
      "best_markets": "categorie",
      "note": "insight chiave 1 riga",
      "copy_worthy": true
    }
  ],
  "trades": [
    {
      "market": "nome mercato",
      "direction": "YES|NO",
      "entry_price": "0.XX",
      "whale_tier": "A|B|C",
      "decision": "COPY|SKIP|WATCH",
      "risk_score": 5,
      "edge": "basso|medio|alto",
      "kelly_fraction": "X%",
      "entry_window": "valido se prezzo <0.XX",
      "reason": "motivazione 2 righe"
    }
  ],
  "risk_alerts": ["alert"],
  "daily_insight": "osservazione macro 1-2 frasi"
}"""


class ClaudeAnalyzer:
    def __init__(self):
        self.client = Anthropic(api_key=ANTHROPIC_API_KEY)

    def analyze(self, whale_text):
        log.info("Analisi Claude in corso...")
        resp = self.client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2000,
            system=CLAUDE_SYSTEM,
            messages=[{"role": "user", "content": whale_text}],
        )
        raw = resp.content[0].text.strip()
        raw = raw.split("```json")[-1].split("```")[0].strip() if "```" in raw else raw
        return json.loads(raw)


class TelegramSender:
    BASE = "https://api.telegram.org/bot"

    def __init__(self):
        self.token   = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID

    def send(self, text):
        for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
            r = requests.post(
                f"{self.BASE}{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": chunk, "parse_mode": "Markdown"},
                timeout=15,
            )
            if not r.ok:
                log.error(f"Telegram error: {r.status_code} {r.text}")
                return False
            time.sleep(0.3)
        return True

    def format_briefing(self, briefing):
        s      = briefing.get("summary", {})
        whales = briefing.get("whales", [])
        trades = briefing.get("trades", [])
        alerts = briefing.get("risk_alerts", [])
        insight = briefing.get("daily_insight", "")

        risk_e = {"basso": "🟢", "medio": "🟡", "alto": "🔴"}.get(s.get("risk_level", ""), "⚪")
        date_s = datetime.now().strftime("%d/%m/%Y")

        lines = [
            f"🐋 *WHALE BRIEFING — {date_s}*", "",
            f"📊 *Panoramica*",
            f"Whale: *{s.get('whales_tracked',0)}* | ✅ COPY: *{s.get('copy_signals',0)}* | "
            f"❌ SKIP: *{s.get('skip_signals',0)}* | 👁 WATCH: *{s.get('watch_signals',0)}*",
            f"Rischio: {risk_e} *{s.get('risk_level','—').upper()}* | _{s.get('market_sentiment','')}_",
        ]

        if insight:
            lines += ["", f"💡 _{insight}_"]

        top = [w for w in whales if w.get("copy_worthy")] or whales[:3]
        if top:
            lines += ["", "─"*26, "🐋 *Whale da seguire*"]
            for w in top[:4]:
                badge = {"A":"🟢 A","B":"🟡 B","C":"⚪ C"}.get(w.get("tier",""),"❓")
                lines.append(f"{badge}  `{w.get('address','—')}`  {w.get('volume_24h','')}  —  {w.get('style','')}")
                if w.get("note"):
                    lines.append(f"   _{w['note']}_")

        if trades:
            lines += ["", "─"*26, "📈 *Trade*"]
            for t in trades:
                e = {"COPY":"✅","SKIP":"❌","WATCH":"👁"}.get(t.get("decision",""),"❓")
                lines.append(f"\n{e} *{t.get('market','—')}*")
                lines.append(
                    f"   {t.get('direction','')} @ {t.get('entry_price','?')} | "
                    f"Risk {t.get('risk_score','?')}/10 | Kelly {t.get('kelly_fraction','—')} | Tier {t.get('whale_tier','?')}"
                )
                if t.get("entry_window"):
                    lines.append(f"   🪟 _{t['entry_window']}_")
                if t.get("reason"):
                    lines.append(f"   _{t['reason'][:140]}_")

        if alerts:
            lines += ["", "─"*26, "⚠️ *Alert*"]
            for a in alerts:
                lines.append(f"• {a}")

        lines += ["", f"_Generato {datetime.now().strftime('%H:%M')} · Polymarket Whale Bot_"]
        return "\n".join(lines)


def main():
    log.info("="*50)
    log.info("POLYMARKET WHALE TRACKER v2 — avvio")
    log.info(f"Soglia: >=${MIN_WHALE_SIZE_USDC:,} | Finestra: {LOOKBACK_HOURS}h | Mercati: {TOP_MARKETS}")
    log.info("="*50)

    telegram = TelegramSender()

    try:
        fetcher  = PolymarketFetcher()
        markets  = fetcher.fetch_top_markets()
        if not markets:
            raise RuntimeError("Nessun mercato recuperato")

        trades   = fetcher.collect_all_trades(markets)

        analyzer = WhaleAnalyzer()
        whales   = analyzer.filter_and_group(trades)

        if not whales:
            telegram.send(
                f"🐋 *Whale Briefing — {datetime.now().strftime('%d/%m/%Y')}*\n\n"
                f"Nessuna whale rilevata nelle ultime {LOOKBACK_HOURS}h "
                f"con size >=${MIN_WHALE_SIZE_USDC:,} USDC."
            )
            log.info("Nessuna whale — notifica inviata")
            return

        whale_text = analyzer.build_summary_text(whales)
        briefing   = ClaudeAnalyzer().analyze(whale_text)
        message    = telegram.format_briefing(briefing)
        telegram.send(message)
        log.info("✅ Briefing inviato!")

        with open("whale_log.jsonl", "a") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(), "briefing": briefing}) + "\n")

    except Exception as e:
        log.exception(f"Errore: {e}")
        telegram.send(f"⚠️ *Whale Tracker Error*\n`{type(e).__name__}: {str(e)[:200]}`")
        raise


if __name__ == "__main__":
    main()
