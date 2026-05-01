"""
Pytest tests for whale_tracker.py critical functions.

Tests run in GitHub Actions after each deploy (continue-on-error=true).
They act as a safety net — a failing test is a warning, not a blocker.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from whale_tracker import (
    _is_sport, _parse_claude, compute_confidence, _is_past_market,
    is_market_resolved, _GAMMA_RESOLUTION_CACHE, _is_known_resolved_event,
    _activity_score, effective_trust,
    _classify_archive_entry, _migrate_archive_kinds,
    ARCHIVE_RESOLVED, ARCHIVE_FILTERED, ARCHIVE_STALE, ARCHIVE_UNKNOWN,
)


# ─────────────────────────────────────────────────────────────────────────────
# _is_sport() — SPORT must be caught (false negatives are the real risk)
# ─────────────────────────────────────────────────────────────────────────────

def test_sport_match_day_pattern():
    """The classic bypass: 'Will X win on YYYY-MM-DD?'"""
    assert _is_sport("Will Olympique Lyonnais win on 2026-04-05?") is True

def test_sport_beat_pattern():
    assert _is_sport("Will Arsenal beat Chelsea in the Premier League?") is True

def test_sport_nba_keyword():
    assert _is_sport("NBA: Lakers vs Celtics game 7?") is True

def test_sport_vs_separator():
    assert _is_sport("Manchester City vs Real Madrid — Champions League?") is True

def test_sport_championship_pattern():
    assert _is_sport("Will Brazil win the World Cup 2026?") is True

def test_sport_goals_pattern():
    assert _is_sport("Will Mbappe score 2+ goals against PSG?") is True

def test_sport_nfl_keyword():
    assert _is_sport("Will the Chiefs win Super Bowl LX?") is True

def test_sport_tennis_keyword():
    assert _is_sport("Will Sinner win Wimbledon 2026?") is True

def test_sport_f1_keyword():
    assert _is_sport("Will Verstappen win the Monaco GP?") is True


# ─────────────────────────────────────────────────────────────────────────────
# _is_sport() — POLITICS/ECONOMY must NOT be blocked (false positives)
# ─────────────────────────────────────────────────────────────────────────────

def test_not_sport_fed():
    assert _is_sport("Will the Fed cut rates in June 2026?") is False

def test_not_sport_trump_tariffs():
    assert _is_sport("Will Trump impose 50% tariffs on EU goods?") is False

def test_not_sport_btc():
    assert _is_sport("Will BTC reach $150k before July 2026?") is False

def test_not_sport_election():
    assert _is_sport("Will Joe Biden run for president in 2028?") is False

def test_not_sport_recession():
    assert _is_sport("Will the US enter recession in 2026?") is False

def test_not_sport_crypto():
    assert _is_sport("Will Ethereum ETF launch in Q2 2026?") is False


def test_not_sport_inflation_substring():
    """Regression: 'NFL' non deve matchare come substring dentro 'iNFLation'."""
    assert _is_sport("Will inflation reach 5% in 2027?") is False
    assert _is_sport("NFL won't match inflation keyword") is True  # vero NFL
    assert _is_sport("Will FBI indict politician?") is False  # FBI ≠ FB + I


def test_not_sport_win_the_political():
    """Regression CRITICO (28/04/26): 'win the' come keyword bloccava
    TUTTA la politica — 113/160 trade reali finivano in sport.
    Ora 'win the ' è rimosso; SPORT_PATTERNS richiede già contesto sportivo
    (cup/championship/series/league/title/season) dopo 'win'."""
    assert _is_sport("Will a Republican win the popular vote and the Presidency?") is False
    assert _is_sport("Will Donald Trump win the 2024 US Presidential Election?") is False
    assert _is_sport("Who will win the next presidential election?") is False
    assert _is_sport("Will Mamdani win the NYC mayoral?") is False
    # Sport veri col pattern restano bloccati:
    assert _is_sport("Will Lakers win the NBA championship?") is True
    assert _is_sport("Will Real Madrid win the Champions League?") is True


def test_not_sport_as_preposition():
    """Regression: la regex club (fc|cf|ac|as|sc|rc|...) catturava 'as next' in
    'announced as next James Bond' → falso positivo. Ora la regex è ristretta
    a sigle UNIVOCHE (fc|afc|ssc|cska) — 'as/ac/cf/sc/rc' rimossi."""
    assert _is_sport("Henry Cavill announced as next James Bond?") is False
    assert _is_sport("Will SpaceX go public as a company by 2026?") is False
    assert _is_sport("Will Trump act as president after term?") is False
    # Club veri (FC/AFC/SSC) restano bloccati:
    assert _is_sport("Will FC Barcelona win La Liga?") is True
    assert _is_sport("Will AFC Bournemouth stay up this season?") is True


# ─────────────────────────────────────────────────────────────────────────────
# _is_sport() — scommesse sportive esplicite su Polymarket (spread/moneyline)
# ─────────────────────────────────────────────────────────────────────────────
def test_sport_spread_notation():
    assert _is_sport("Spread: Toulouse FC (-1.5)") is True
    assert _is_sport("1H Spread: Nuggets (-3.5)") is True
    assert _is_sport("Spread: Rockets (-5.5)") is True
    assert _is_sport("Spread: Warriors (-6.5)") is True


def test_sport_moneyline_total():
    assert _is_sport("Moneyline: Lakers") is True
    assert _is_sport("Total: Heat vs Celtics") is True


def test_sport_team_name_without_league():
    assert _is_sport("Will Celtics beat Warriors tonight") is True
    assert _is_sport("Rockets playoff run") is True


# ─────────────────────────────────────────────────────────────────────────────
# _parse_claude() — verdict parsing
# ─────────────────────────────────────────────────────────────────────────────

FAKE_COPY = (
    "COPY\n"
    "Rischio: 3/10\n"
    "Vale la pena?: Sì, il prezzo sembra chiaramente sottovalutato.\n"
    "Cosa sta succedendo: Una whale da $4M ha appena comprato $180k di YES a 28¢. "
    "Il mercato politico ha un catalizzatore imminente.\n"
    "Sospetto?: No"
)

FAKE_WATCH = (
    "WATCH\n"
    "Rischio: 5/10\n"
    "Vale la pena?: Interessante ma serve conferma.\n"
    "Cosa sta succedendo: Whale con buon track record punta su evento incerto.\n"
    "Sospetto?: Forse"
)

FAKE_SKIP = (
    "SKIP\n"
    "Rischio: 8/10\n"
    "Vale la pena?: No, troppo rischioso.\n"
    "Cosa sta succedendo: Evento con poca liquidità e deadline lontana.\n"
    "Sospetto?: No"
)

FAKE_SPORT_IN_RESPONSE = (
    "COPY\n"
    "Rischio: 2/10\n"
    "Vale la pena?: Sì.\n"
    "Cosa sta succedendo: Questo mercato è una partita di calcio sport.\n"
    "Sospetto?: No"
)


def test_parse_copy_verdict():
    r = _parse_claude(FAKE_COPY, "Test market", "YES", 0.28, 180000, "0xabc", "Top Whale",
                      trust_score=90, whale_name="HorizonSplendidView")
    assert r["verdict"] == "COPY"


def test_parse_watch_verdict():
    r = _parse_claude(FAKE_WATCH, "Test market", "YES", 0.5, 100000, "0xdef", "Big Whale",
                      trust_score=75, whale_name="beachboy4")
    assert r["verdict"] == "WATCH"


def test_parse_skip_verdict():
    r = _parse_claude(FAKE_SKIP, "Test market", "NO", 0.8, 100000, "0xghi", "Whale",
                      trust_score=60, whale_name="testwhale")
    assert r["verdict"] == "SKIP"


def test_parse_sport_in_response_forces_skip():
    """If Claude mentions 'sport'/'calcio' the verdict must be forced to SKIP."""
    r = _parse_claude(FAKE_SPORT_IN_RESPONSE, "Test sport market", "YES", 0.3, 100000,
                      "0xjkl", "Whale", trust_score=70, whale_name="testwhale")
    assert r["verdict"] == "SKIP"


def test_parse_risk_score_extracted():
    r = _parse_claude(FAKE_COPY, "Test", "YES", 0.3, 150000, "0x1", "Top Whale")
    assert r["risk_score"] == 3


def test_parse_market_and_side_preserved():
    r = _parse_claude(FAKE_WATCH, "My Market", "NO", 0.7, 120000, "0x2", "Big Whale")
    assert r["market"] == "My Market"
    assert r["side"] == "NO"


# ─────────────────────────────────────────────────────────────────────────────
# compute_confidence() — scoring
# ─────────────────────────────────────────────────────────────────────────────

def test_confidence_base_score():
    trade = {"usdcSize": "100000", "price": "0.5", "whale_trust_score": 50}
    state = {"leaderboard": {}}
    score = compute_confidence(trade, state)
    assert 50 <= score <= 100


def test_confidence_high_trust_increases_score():
    low_trust  = {"usdcSize": "100000", "price": "0.5", "whale_trust_score": 30}
    high_trust = {"usdcSize": "100000", "price": "0.5", "whale_trust_score": 90}
    state = {"leaderboard": {}}
    assert compute_confidence(high_trust, state) > compute_confidence(low_trust, state)


def test_confidence_large_size_increases_score():
    small = {"usdcSize": "100000",  "price": "0.5", "whale_trust_score": 60}
    large = {"usdcSize": "600000",  "price": "0.5", "whale_trust_score": 60}
    state = {"leaderboard": {}}
    assert compute_confidence(large, state) > compute_confidence(small, state)


def test_confidence_extreme_price_increases_score():
    mid    = {"usdcSize": "150000", "price": "0.5",  "whale_trust_score": 60}
    corner = {"usdcSize": "150000", "price": "0.1",  "whale_trust_score": 60}
    state  = {"leaderboard": {}}
    assert compute_confidence(corner, state) > compute_confidence(mid, state)


def test_confidence_capped_at_100():
    trade = {"usdcSize": "1000000", "price": "0.05", "whale_trust_score": 100,
             "userAddress": "0xtopwhale"}
    state = {"leaderboard": {"0xtopwhale": {"total_volume_usd": 5_000_000}}}
    assert compute_confidence(trade, state) <= 100


# ─────────────────────────────────────────────────────────────────────────────
# _is_past_market() — titoli con date passate devono essere bloccati
# ─────────────────────────────────────────────────────────────────────────────

def test_past_market_iso_date():
    """Data ISO esplicitamente passata."""
    assert _is_past_market("Will X happen on 2026-01-15?") is True

def test_past_market_future_iso_date():
    """Data ISO nel futuro — non bloccare."""
    assert _is_past_market("Will X happen on 2026-12-31?") is False

def test_past_market_past_year():
    """Anno intero passato (2025)."""
    assert _is_past_market("Will Trump win the 2025 election?") is True

def test_past_market_by_month_year():
    """'by January 2026' quando siamo in aprile — passato."""
    assert _is_past_market("Will the Fed cut rates by January 2026?") is True

def test_past_market_future_month_year():
    """'by December 2026' — futuro — non bloccare."""
    assert _is_past_market("Will the Fed cut rates by December 2026?") is False

def test_past_market_month_only_past():
    """'in January' senza anno — assume anno corrente — passato in aprile."""
    assert _is_past_market("Fed decision in January?") is True

def test_past_market_month_only_december():
    """'in December' senza anno — potrebbe essere December 2026 → NON bloccare.
    È Gamma API che decide se è risolto, non il text filter."""
    assert _is_past_market("Fed decision in December?") is False

def test_past_market_month_only_near_future():
    """'in May' senza anno — May 2026 è tra 1 mese → non bloccare."""
    assert _is_past_market("Fed decision in May?") is False

def test_past_market_quarter_past():
    """'Q1 2026' finisce il 31 marzo — passato in aprile."""
    assert _is_past_market("Will Q1 2026 GDP exceed expectations?") is True

def test_past_market_quarter_future():
    """'Q4 2026' — futuro — non bloccare."""
    assert _is_past_market("Will Q4 2026 GDP disappoint?") is False

def test_past_market_bare_month_year():
    """'January 2026 Fed decision' senza preposizione — passato."""
    assert _is_past_market("January 2026 Fed decision outcome?") is True


def test_past_market_by_month_day_no_year():
    """'by March 31' senza anno — assumi anno corrente, se passato → blocca."""
    assert _is_past_market("US forces enter Iran by March 31?") is True
    assert _is_past_market("Will X happen by Feb 15?") is True


def test_past_market_by_month_day_with_year():
    """'by April 5, 2026' — passato il 19 aprile."""
    assert _is_past_market("Will X happen by April 5, 2026?") is True
    assert _is_past_market("Deal closes on March 3, 2026") is True


def test_past_market_by_month_day_future_year():
    """'by March 31, 2027' — futuro, non bloccare."""
    assert _is_past_market("Will it happen by March 31, 2027?") is False

def test_past_market_politics_not_blocked():
    """Mercato politico senza data — non bloccare."""
    assert _is_past_market("Will Trump impose 50% tariffs on EU?") is False

def test_past_market_crypto_future():
    """BTC target futuro — non bloccare."""
    assert _is_past_market("Will BTC reach $150k before July 2026?") is False


# ─────────────────────────────────────────────────────────────────────────────
# is_market_resolved() — uses Gamma resolution cache (no HTTP in tests)
# ─────────────────────────────────────────────────────────────────────────────

def test_resolved_market_detected():
    """Mercato chiuso nella cache Gamma — deve essere bloccato."""
    _GAMMA_RESOLUTION_CACHE.clear()
    _GAMMA_RESOLUTION_CACHE["will x happen?"] = {"closed": True, "isResolved": True}
    assert is_market_resolved("Will X happen?") is True

def test_open_market_not_blocked():
    """Mercato aperto nella cache — non bloccare."""
    _GAMMA_RESOLUTION_CACHE.clear()
    _GAMMA_RESOLUTION_CACHE["will y happen?"] = {"closed": False, "isResolved": False}
    assert is_market_resolved("Will Y happen?") is False

def test_unknown_market_not_blocked():
    """Mercato non in cache e nessun fallback — non bloccare."""
    _GAMMA_RESOLUTION_CACHE.clear()
    assert is_market_resolved("Some completely unknown market xyz") is False

def test_partial_match_resolved():
    """Match parziale (primi 40 char) — deve bloccare se risolto."""
    _GAMMA_RESOLUTION_CACHE.clear()
    _GAMMA_RESOLUTION_CACHE["will the federal reserve cut interest rates in june 2026?"] = {
        "closed": True, "isResolved": True
    }
    assert is_market_resolved("Will the Federal Reserve cut interest rates in June 2026?") is True

def test_partial_match_open():
    """Match parziale — mercato aperto — non bloccare."""
    _GAMMA_RESOLUTION_CACHE.clear()
    _GAMMA_RESOLUTION_CACHE["will btc reach $150k before july 2026?"] = {
        "closed": False, "isResolved": False
    }
    assert is_market_resolved("Will BTC reach $150k before July 2026?") is False

def test_empty_title_not_blocked():
    """Titolo vuoto — non bloccare."""
    _GAMMA_RESOLUTION_CACHE.clear()
    assert is_market_resolved("") is False

def test_resolved_with_end_date_past():
    """Mercato non esplicitamente chiuso ma con endDate passata."""
    _GAMMA_RESOLUTION_CACHE.clear()
    _GAMMA_RESOLUTION_CACHE["will event z occur?"] = {
        "closed": False, "isResolved": False,
        "endDate": "2026-01-01T00:00:00Z",
    }
    assert is_market_resolved("Will event Z occur?") is True


def test_condition_id_lookup_resolved():
    """conditionId nella cache → blocca anche se il titolo non combacia."""
    _GAMMA_RESOLUTION_CACHE.clear()
    fake_cid = "0xabc123conditionId"
    _GAMMA_RESOLUTION_CACHE[fake_cid] = {"closed": True, "isResolved": True}
    # Titolo volutamente diverso dalla question in cache — solo conditionId combacia
    assert is_market_resolved("US strikes Iran by December 31?",
                               condition_id=fake_cid) is True


def test_condition_id_lookup_open():
    """conditionId presente ma mercato aperto — non bloccare."""
    _GAMMA_RESOLUTION_CACHE.clear()
    fake_cid = "0xdef456conditionId"
    _GAMMA_RESOLUTION_CACHE[fake_cid] = {"closed": False, "isResolved": False}
    assert is_market_resolved("Some market title", condition_id=fake_cid) is False


def test_condition_id_not_in_cache_falls_back_to_title():
    """conditionId non in cache → fallback su titolo."""
    _GAMMA_RESOLUTION_CACHE.clear()
    _GAMMA_RESOLUTION_CACHE["will fallback work?"] = {"closed": True, "isResolved": True}
    # conditionId non noto, ma titolo combacia → deve bloccare
    assert is_market_resolved("Will fallback work?",
                               condition_id="0xunknownId") is True


def test_truncated_title_matches_full_gamma_question():
    """Titolo troncato dalla Data API deve matchare la domanda completa in Gamma cache."""
    _GAMMA_RESOLUTION_CACHE.clear()
    # Gamma cache ha il titolo completo
    _GAMMA_RESOLUTION_CACHE["will the us strike iran by december 31, 2025?"] = {
        "closed": True, "isResolved": True, "winningOutcome": "NO"
    }
    # Data API restituisce titolo troncato con "..." — deve comunque bloccare
    assert is_market_resolved("US strikes Iran by...?") is True


def test_truncated_title_open_market_not_blocked():
    """Titolo troncato ma mercato ancora aperto — non bloccare."""
    _GAMMA_RESOLUTION_CACHE.clear()
    _GAMMA_RESOLUTION_CACHE["will the us strike iran by december 31, 2026?"] = {
        "closed": False, "isResolved": False
    }
    assert is_market_resolved("US strikes Iran by...?") is False


def test_non_truncated_title_unaffected():
    """Titoli normali (senza '...') non vengono toccati dalla logica troncatura."""
    _GAMMA_RESOLUTION_CACHE.clear()
    _GAMMA_RESOLUTION_CACHE["will btc reach 200k?"] = {"closed": True, "isResolved": True}
    assert is_market_resolved("Will BTC reach 200k?") is True


# ─────────────────────────────────────────────────────────────────────────────
# _is_known_resolved_event() — eventi già decisi bloccati da world knowledge
# ─────────────────────────────────────────────────────────────────────────────
def test_known_resolved_presidential_election():
    """Elezioni presidenziali USA 2024 — esito noto (Trump ha vinto)."""
    assert _is_known_resolved_event("Will a Republican win Pennsylvania Presidential Election?")
    assert _is_known_resolved_event("Will a Republican win Michigan Presidential Election?")
    assert _is_known_resolved_event("Will a Republican win Wisconsin Presidential Election?")
    assert _is_known_resolved_event("Will Trump win the presidential election?")
    assert _is_known_resolved_event("Next President of the United States")


def test_known_resolved_nyc_mayor():
    """NYC Mayoral 2025 — già avvenuta."""
    assert _is_known_resolved_event("NYC Mayor race")
    assert _is_known_resolved_event("New York City Mayor election")


def test_known_resolved_popular_vote():
    """Popular vote 2024 — Trump ha vinto anche il voto popolare."""
    assert _is_known_resolved_event("Kamala Harris wins the popular vote?")
    assert _is_known_resolved_event("Will Kamala Harris win the popular vote?")
    assert _is_known_resolved_event("Trump wins the popular vote")
    assert _is_known_resolved_event("Popular vote winner")
    assert _is_known_resolved_event("Who wins the popular vote?")


def test_known_resolved_does_not_block_future():
    """Mercati futuri non devono essere bloccati dal filtro resolved events."""
    assert not _is_known_resolved_event("Will inflation reach 5% in 2027?")
    assert not _is_known_resolved_event("Fed rate cut June 2026")
    assert not _is_known_resolved_event("Will BTC reach $200k?")
    assert not _is_known_resolved_event("Trump tariffs on EU goods")


# ─────────────────────────────────────────────────────────────────────────────
# Active whale ranking — _activity_score / effective_trust
# ─────────────────────────────────────────────────────────────────────────────
from datetime import datetime, timedelta, timezone


def _ts_days_ago(n: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


def test_activity_score_decay():
    """Decay piecewise verificato: oggi=100, 7g=50, 30g=20, mai=50, 60g=10."""
    assert _activity_score(_ts_days_ago(0))   == 100
    assert _activity_score(_ts_days_ago(0.5)) == 100
    assert _activity_score(_ts_days_ago(1))   == 100
    assert 50 <= _activity_score(_ts_days_ago(7))  <= 51
    assert 18 <= _activity_score(_ts_days_ago(30)) <= 22
    assert _activity_score(_ts_days_ago(60))  == 10
    assert _activity_score(_ts_days_ago(365)) == 10
    assert _activity_score(None) == 50  # mai visto → neutro


def test_activity_score_robust_to_garbage():
    """Stringhe ISO malformate non devono crashare → fallback 50."""
    assert _activity_score("") == 50
    assert _activity_score("not-a-date") == 50
    assert _activity_score("2026-13-99") == 50  # mese 13 invalido


def test_effective_trust_active_beats_dormant():
    """Whale attiva con static=70 batte whale dormiente con static=100."""
    active = effective_trust({"trust_score": 70, "last_non_sport_trade": _ts_days_ago(0)})
    dormant = effective_trust({"trust_score": 100, "last_non_sport_trade": _ts_days_ago(60)})
    assert active > dormant, f"active={active} should beat dormant={dormant}"


def test_effective_trust_unknown_whale():
    """Whale con last_non_sport_trade=None (mai vista) → neutral baseline."""
    assert effective_trust({"trust_score": 100}) == 75   # 50% × 100 + 50% × 50 = 75
    assert effective_trust({"trust_score": 50})  == 50   # 50% × 50  + 50% × 50 = 50
    assert effective_trust({})                   == 50   # default static=50


def test_effective_trust_today_top():
    """Whale top con trade oggi → trust massimo (≈100)."""
    eff = effective_trust({"trust_score": 100, "last_non_sport_trade": _ts_days_ago(0)})
    assert eff == 100


# ─────────────────────────────────────────────────────────────────────────────
# Archive classification — separa risolti veri da filtered/stale/unknown
# ─────────────────────────────────────────────────────────────────────────────
def test_archive_kind_explicit_wins():
    """Se l'entry ha già archive_kind, viene rispettato."""
    assert _classify_archive_entry({"archive_kind": ARCHIVE_RESOLVED}) == ARCHIVE_RESOLVED
    assert _classify_archive_entry({"archive_kind": ARCHIVE_FILTERED}) == ARCHIVE_FILTERED
    assert _classify_archive_entry({"archive_kind": ARCHIVE_STALE}) == ARCHIVE_STALE


def test_archive_kind_inferred_filtered():
    """Auto-removed dai filtri → filtered (anche senza campo archive_kind)."""
    e = {"resolution": "Auto-removed: US Presidential Election 2024",
         "correct": None}
    assert _classify_archive_entry(e) == ARCHIVE_FILTERED


def test_archive_kind_inferred_resolved():
    """Mercato chiuso da Polymarket con outcome known → resolved."""
    assert _classify_archive_entry({"correct": True,
                                    "resolution": "YES",
                                    "resolution_date": "2026-04-15"}) == ARCHIVE_RESOLVED
    assert _classify_archive_entry({"correct": False,
                                    "resolution": "NO",
                                    "resolution_date": "2026-04-15"}) == ARCHIVE_RESOLVED


def test_archive_kind_inferred_unknown():
    """Entry incompleta (no auto-removed, no correct, no resolution proper)."""
    assert _classify_archive_entry({"resolution": "?"}) == ARCHIVE_UNKNOWN
    assert _classify_archive_entry({}) == ARCHIVE_UNKNOWN


def test_migrate_archive_kinds_idempotent():
    """Migrate è idempotente — non altera entry già marcate."""
    state = {"resolved_archive": [
        {"archive_kind": ARCHIVE_RESOLVED, "correct": True},
        {"resolution": "Auto-removed: x"},   # → filtered
        {"correct": False, "resolution": "NO", "resolution_date": "2026-04-01"},  # → resolved
    ]}
    n = _migrate_archive_kinds(state)
    assert n == 2  # solo le ultime due, la prima era già marcata
    assert state["resolved_archive"][1]["archive_kind"] == ARCHIVE_FILTERED
    assert state["resolved_archive"][2]["archive_kind"] == ARCHIVE_RESOLVED
    # Seconda chiamata: nessun cambio
    assert _migrate_archive_kinds(state) == 0


def test_effective_trust_silent_demoting():
    """Whale pingata oggi (last_seen<24h) ma zero trade non-sport → demoted.
    Senza demoting silenzioso resterebbe a static/2 + 50 = 75.
    Con demoting → static/2 + 25 = 62 (per static=100). Le 'whale 2024'
    dormienti emergono entro 1 giorno invece di 30."""
    today = _ts_days_ago(0.5)
    # Caso A: last_seen oggi MA mai trade non-sport → demoted
    demoted = effective_trust({"trust_score": 100, "last_seen": today,
                                "last_non_sport_trade": None})
    assert demoted == 62, f"got {demoted}"
    # Caso B: mai pingata né visto → resta neutro
    neutral = effective_trust({"trust_score": 100, "last_non_sport_trade": None})
    assert neutral == 75
    # Caso C: pingata ma con trade non-sport recente → resta alta
    fresh = effective_trust({"trust_score": 100, "last_seen": today,
                              "last_non_sport_trade": today})
    assert fresh == 100
