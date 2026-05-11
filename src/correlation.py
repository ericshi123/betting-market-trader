"""
Correlation arbitrage for Kalshi prediction markets.

When two semantically related markets diverge in price by more than a threshold,
there's a potential arbitrage opportunity: the cheaper side is likely underpriced
relative to its correlated counterpart.

Usage:
    divergences = detect_divergence(market_dicts)
    for d in divergences:
        amt = size_position(d['divergence_pp'], bankroll)
        # open paper position on d['arb_side'] in d['ticker_a'] or d['ticker_b']
"""

import time
from typing import Dict, List, Optional, Tuple

# ── Correlated pairs ──────────────────────────────────────────────────────────
# Maps (ticker_a, ticker_b) where both tickers trading in the same direction
# implies a correlated outcome. Format: (canonical, related).
#
# The market tickers below are examples of semantically linked contracts on
# Kalshi (presidential outcome + congressional composition move together, etc.).
CORRELATED_PAIRS: Dict[Tuple[str, str], str] = {
    # Presidential race ↔ Senate control
    ("PRES-2024-DJT", "SENATE-REP-2024"): "Trump win implies Republican Senate",
    ("PRES-2024-DJT", "HOUSE-REP-2024"): "Trump win implies Republican House",
    # Senate ↔ House (unified Congress)
    ("SENATE-REP-2024", "HOUSE-REP-2024"): "Republican Senate implies Republican House",
    # Generic pattern: these are discovered dynamically by prefix matching below
}

# Prefix patterns that indicate correlation (both tickers share a root)
_CORRELATED_PREFIXES: List[str] = [
    "PRES-",
    "SENATE-",
    "HOUSE-",
    "FED-",
    "GDP-",
    "CPI-",
    "UNEMP-",
    "BTCUSD-",
    "ETHUSD-",
]

# Minimum divergence in percentage points to flag as an opportunity
MIN_DIVERGENCE_PP: float = 10.0


def _pairs_from_markets(markets: List[dict]) -> List[Tuple[dict, dict]]:
    """
    Build candidate pairs from CORRELATED_PAIRS plus dynamic prefix matching.
    Returns list of (market_a, market_b) tuples.
    """
    by_ticker: Dict[str, dict] = {
        m.get("ticker") or m.get("market_id", ""): m for m in markets
    }

    pairs: List[Tuple[dict, dict]] = []
    seen: set = set()

    # Explicit pairs from CORRELATED_PAIRS
    for (ta, tb) in CORRELATED_PAIRS:
        if ta in by_ticker and tb in by_ticker:
            key = (min(ta, tb), max(ta, tb))
            if key not in seen:
                seen.add(key)
                pairs.append((by_ticker[ta], by_ticker[tb]))

    # Dynamic prefix-based pairing
    tickers = list(by_ticker.keys())
    for i, t1 in enumerate(tickers):
        for t2 in tickers[i + 1:]:
            # Match tickers that share a long common prefix (first 8+ chars)
            prefix_len = 0
            for c1, c2 in zip(t1, t2):
                if c1 == c2:
                    prefix_len += 1
                else:
                    break
            if prefix_len >= 6:
                key = (min(t1, t2), max(t1, t2))
                if key not in seen:
                    seen.add(key)
                    pairs.append((by_ticker[t1], by_ticker[t2]))

    return pairs


def detect_divergence(markets: List[dict]) -> List[dict]:
    """
    Scan a list of market dicts for correlated pairs where yes_price diverges
    by more than MIN_DIVERGENCE_PP.

    Each market dict must have a 'yes_price' (float 0–1) and 'ticker' or 'market_id'.

    Returns list of divergence dicts:
    {
        ticker_a: str,
        ticker_b: str,
        price_a: float,
        price_b: float,
        divergence_pp: float,   # absolute difference in percentage points
        arb_side: str,          # 'a' (buy ticker_a) or 'b' (buy ticker_b)
        timestamp: float,
    }
    Sorted by divergence_pp descending.
    """
    if len(markets) < 2:
        return []

    pairs = _pairs_from_markets(markets)
    results = []

    for ma, mb in pairs:
        price_a = ma.get("yes_price")
        price_b = mb.get("yes_price")
        if price_a is None or price_b is None:
            continue

        ticker_a = ma.get("ticker") or ma.get("market_id", "")
        ticker_b = mb.get("ticker") or mb.get("market_id", "")

        divergence_pp = abs(price_a - price_b) * 100.0

        if divergence_pp < MIN_DIVERGENCE_PP:
            continue

        # arb_side: buy the cheaper side (lower yes_price → more underpriced)
        arb_side = "a" if price_a < price_b else "b"

        results.append({
            "ticker_a": ticker_a,
            "ticker_b": ticker_b,
            "price_a": round(price_a, 4),
            "price_b": round(price_b, 4),
            "divergence_pp": round(divergence_pp, 2),
            "arb_side": arb_side,
            "timestamp": time.time(),
        })

    results.sort(key=lambda x: x["divergence_pp"], reverse=True)
    return results


def size_position(divergence_pp: float, balance: float) -> float:
    """
    Size a position based on divergence magnitude.

    Rule: 1 pp of divergence = 1% of balance, capped at 10% of balance.

    Args:
        divergence_pp: divergence in percentage points (e.g. 15.0 for 15pp)
        balance: current account balance in dollars

    Returns:
        Dollar amount to risk (rounded to 2 dp).
    """
    fraction = min(divergence_pp / 100.0, 0.10)   # 1pp → 1%, max 10%
    return round(balance * fraction, 2)
