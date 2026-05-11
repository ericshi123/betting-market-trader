"""
Tests for src/correlation.py — detect_divergence and size_position.
"""

import pytest

from src.correlation import (
    MIN_DIVERGENCE_PP,
    detect_divergence,
    size_position,
    CORRELATED_PAIRS,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _market(ticker: str, yes_price: float) -> dict:
    return {"ticker": ticker, "market_id": ticker, "yes_price": yes_price}


# ── detect_divergence — basic cases ───────────────────────────────────────────

def test_no_divergence_when_below_threshold():
    """Pairs within MIN_DIVERGENCE_PP should not appear in results."""
    m1 = _market("PRES-2024-DJT", 0.55)
    m2 = _market("SENATE-REP-2024", 0.60)  # 5pp apart — below 10pp threshold
    result = detect_divergence([m1, m2])
    assert result == []


def test_divergence_detected_for_explicit_pair():
    """An explicit CORRELATED_PAIRS entry with >10pp divergence should be returned."""
    m1 = _market("PRES-2024-DJT", 0.40)
    m2 = _market("SENATE-REP-2024", 0.60)  # 20pp divergence
    result = detect_divergence([m1, m2])
    assert len(result) == 1
    div = result[0]
    assert div["divergence_pp"] == pytest.approx(20.0)
    assert div["ticker_a"] in ("PRES-2024-DJT", "SENATE-REP-2024")
    assert div["ticker_b"] in ("PRES-2024-DJT", "SENATE-REP-2024")


def test_arb_side_is_cheaper():
    """arb_side='a' means ticker_a is the cheaper (more underpriced) side."""
    m1 = _market("PRES-2024-DJT", 0.35)     # cheaper → arb_side='a'
    m2 = _market("SENATE-REP-2024", 0.60)
    result = detect_divergence([m1, m2])
    assert len(result) == 1
    div = result[0]
    if div["ticker_a"] == "PRES-2024-DJT":
        assert div["arb_side"] == "a"
    else:
        assert div["arb_side"] == "b"


def test_arb_side_b_when_b_is_cheaper():
    """When ticker_b has the lower price, arb_side should be 'b'."""
    m1 = _market("PRES-2024-DJT", 0.65)
    m2 = _market("SENATE-REP-2024", 0.40)   # cheaper → arb_side='b'
    result = detect_divergence([m1, m2])
    assert len(result) == 1
    div = result[0]
    if div["ticker_a"] == "PRES-2024-DJT":
        assert div["arb_side"] == "b"
    else:
        assert div["arb_side"] == "a"


def test_divergence_exactly_at_threshold_not_included():
    """Divergence exactly at MIN_DIVERGENCE_PP should not be included (strict <)."""
    price_a = 0.50
    price_b = round(price_a + MIN_DIVERGENCE_PP / 100.0 - 0.001, 4)
    m1 = _market("PRES-2024-DJT", price_a)
    m2 = _market("SENATE-REP-2024", price_b)
    result = detect_divergence([m1, m2])
    assert result == []


def test_divergence_just_above_threshold_included():
    """Divergence just over MIN_DIVERGENCE_PP should appear."""
    price_a = 0.50
    price_b = round(price_a + MIN_DIVERGENCE_PP / 100.0 + 0.001, 4)
    m1 = _market("PRES-2024-DJT", price_a)
    m2 = _market("SENATE-REP-2024", price_b)
    result = detect_divergence([m1, m2])
    assert len(result) == 1


def test_sorted_by_divergence_descending():
    """Results should be sorted with largest divergence first."""
    # Two pairs from explicit CORRELATED_PAIRS
    markets = [
        _market("PRES-2024-DJT", 0.30),      # 30pp from SENATE
        _market("SENATE-REP-2024", 0.60),
        _market("HOUSE-REP-2024", 0.80),      # 20pp from SENATE, 50pp from PRES
    ]
    result = detect_divergence(markets)
    assert len(result) >= 2
    # First result should have the largest divergence
    assert result[0]["divergence_pp"] >= result[1]["divergence_pp"]


def test_returns_empty_for_single_market():
    result = detect_divergence([_market("PRES-2024-DJT", 0.50)])
    assert result == []


def test_returns_empty_for_empty_input():
    result = detect_divergence([])
    assert result == []


def test_skips_market_without_yes_price():
    m1 = {"ticker": "PRES-2024-DJT", "market_id": "PRES-2024-DJT"}  # no yes_price
    m2 = _market("SENATE-REP-2024", 0.60)
    result = detect_divergence([m1, m2])
    assert result == []


def test_divergence_result_structure():
    """Each divergence result must contain required keys."""
    m1 = _market("PRES-2024-DJT", 0.30)
    m2 = _market("SENATE-REP-2024", 0.55)
    result = detect_divergence([m1, m2])
    assert len(result) == 1
    div = result[0]
    required_keys = {"ticker_a", "ticker_b", "price_a", "price_b", "divergence_pp", "arb_side", "timestamp"}
    assert required_keys.issubset(div.keys())
    assert div["arb_side"] in ("a", "b")
    assert isinstance(div["divergence_pp"], float)
    assert isinstance(div["timestamp"], float)


def test_prefix_based_pair_detection():
    """Markets sharing a >=6-char prefix should be paired dynamically."""
    m1 = _market("BTCUSD-JAN-ABOVE-90K", 0.30)
    m2 = _market("BTCUSD-JAN-ABOVE-80K", 0.55)  # 25pp divergence, same BTCUSD prefix
    result = detect_divergence([m1, m2])
    assert len(result) == 1
    assert result[0]["divergence_pp"] == pytest.approx(25.0, abs=0.1)


# ── size_position ─────────────────────────────────────────────────────────────

def test_size_position_1pp():
    """1pp divergence → 1% of balance."""
    assert size_position(1.0, 1000.0) == pytest.approx(10.0)


def test_size_position_5pp():
    """5pp divergence → 5% of balance."""
    assert size_position(5.0, 1000.0) == pytest.approx(50.0)


def test_size_position_10pp():
    """10pp divergence → 10% of balance (at the cap)."""
    assert size_position(10.0, 1000.0) == pytest.approx(100.0)


def test_size_position_capped_at_10pct():
    """50pp divergence still capped at 10% of balance."""
    assert size_position(50.0, 1000.0) == pytest.approx(100.0)


def test_size_position_zero_balance():
    assert size_position(20.0, 0.0) == 0.0


def test_size_position_rounding():
    """Result should be rounded to 2 decimal places."""
    result = size_position(3.3, 333.0)
    assert result == round(result, 2)
