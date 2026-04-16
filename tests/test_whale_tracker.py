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


def test_known_resolved_does_not_block_future():
    """Mercati futuri non devono essere bloccati dal filtro resolved events."""
    assert not _is_known_resolved_event("Will inflation reach 5% in 2027?")
    assert not _is_known_resolved_event("Fed rate cut June 2026")
    assert not _is_known_resolved_event("Will BTC reach $200k?")
    assert not _is_known_resolved_event("Trump tariffs on EU goods")
