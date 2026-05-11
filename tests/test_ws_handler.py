"""
Tests for src/ws_handler.py — price event handler.

Tests cover:
  - Delta threshold filter (< 3pp → skip)
  - Deduplicate window (same ticker within 30 min → skip)
  - No edge → skip (|model_prob - market_price| < 8pp)
  - Low confidence → skip
  - REST fetch error → error result
  - Happy path: edge ≥ 8pp + medium/high confidence → trade placed
  - Direction: BUY_YES when model > market, BUY_NO when model < market
"""

import time
import pytest
from unittest.mock import MagicMock, patch

from src.ws_handler import WSHandler, MIN_TRIGGER_DELTA, DEDUPE_WINDOW_S, MIN_EDGE


# ── Helpers ────────────────────────────────────────────────────────────────────

def _event(ticker="MKTX", yes_price=0.50, delta=0.05, prev=0.45):
    return {
        "ticker": ticker,
        "yes_price": yes_price,
        "prev_yes_price": prev,
        "delta": delta,
        "timestamp": time.time(),
    }


def _mock_market(ticker="MKTX", yes_price=0.50):
    return {
        "market": {
            "ticker": ticker,
            "title": "Test Market",
            "yes_ask_dollars": yes_price,
            "no_ask_dollars": 1 - yes_price,
            "yes_bid_dollars": yes_price - 0.02,
            "no_bid_dollars": 1 - yes_price - 0.02,
            "volume_fp": "5000",
            "close_time": "2026-12-31T00:00:00Z",
            "rules_primary": "Resolves YES if X happens.",
            "status": "open",
            "category": "politics",
        }
    }


def _mock_analysis(model_prob=0.65, confidence="medium"):
    return {
        "model_prob": model_prob,
        "confidence": confidence,
        "rationale": "Strong signals suggest YES.",
        "reasoning": "Based on recent data.",
        "market_source": "politics",
    }


# ── Delta threshold ────────────────────────────────────────────────────────────

def test_skip_when_delta_below_threshold():
    handler = WSHandler()
    small_delta = MIN_TRIGGER_DELTA - 0.001
    result = handler.handle(_event(delta=small_delta, yes_price=0.50, prev=0.50 - small_delta))
    assert result["action"] == "skip"
    assert result["reason"] == "delta_too_small"


def test_skip_negative_delta_below_threshold():
    handler = WSHandler()
    result = handler.handle(_event(delta=-0.01, yes_price=0.49, prev=0.50))
    assert result["action"] == "skip"
    assert result["reason"] == "delta_too_small"


def test_exactly_at_threshold_is_not_skipped():
    """A delta exactly equal to MIN_TRIGGER_DELTA should pass the threshold check."""
    handler = WSHandler()
    with patch("src.ws_handler.kalshi_get") as mock_get, \
         patch("src.ws_handler.estimate_probability") as mock_est:
        mock_get.return_value = _mock_market()
        mock_est.return_value = _mock_analysis(model_prob=0.30, confidence="low")
        result = handler.handle(_event(delta=MIN_TRIGGER_DELTA, yes_price=0.50, prev=0.47))
    # Should NOT be delta_too_small (may be skipped for other reasons)
    assert result["reason"] != "delta_too_small"


# ── Dedupe ─────────────────────────────────────────────────────────────────────

def test_skip_duplicate_within_window():
    handler = WSHandler()
    # Seed a recent evaluation
    handler._last_eval["MKTX"] = time.time() - 60  # 60s ago (within 30-min window)
    result = handler.handle(_event(delta=0.10))
    assert result["action"] == "skip"
    assert result["reason"] == "dedupe"
    assert result["cooldown_remaining_s"] > 0


def test_no_dedupe_after_window_expires():
    """Evaluation older than DEDUPE_WINDOW_S should not be deduped."""
    handler = WSHandler()
    handler._last_eval["MKTX"] = time.time() - (DEDUPE_WINDOW_S + 10)

    with patch("src.ws_handler.kalshi_get") as mock_get, \
         patch("src.ws_handler.estimate_probability") as mock_est:
        mock_get.return_value = _mock_market()
        mock_est.return_value = _mock_analysis(model_prob=0.30, confidence="low")
        result = handler.handle(_event(delta=0.10))

    assert result["reason"] != "dedupe"


# ── REST fetch error ───────────────────────────────────────────────────────────

def test_rest_fetch_error_returns_error():
    handler = WSHandler()
    with patch("src.ws_handler.kalshi_get", side_effect=Exception("connection refused")):
        result = handler.handle(_event(delta=0.10))
    assert result["action"] == "error"
    assert "connection refused" in result["reason"]


# ── No edge ────────────────────────────────────────────────────────────────────

def test_skip_when_edge_below_threshold():
    handler = WSHandler()
    # model_prob=0.52, yes_price=0.50 → edge=0.02 < 0.08
    with patch("src.ws_handler.kalshi_get", return_value=_mock_market()), \
         patch("src.ws_handler.estimate_probability",
               return_value=_mock_analysis(model_prob=0.52, confidence="high")):
        result = handler.handle(_event(delta=0.05))
    assert result["action"] == "skip"
    assert result["reason"] == "no_edge"
    assert result["edge"] == pytest.approx(0.02)


# ── Low confidence ─────────────────────────────────────────────────────────────

def test_skip_when_confidence_low():
    handler = WSHandler()
    # edge=0.20, but confidence=low → skip
    with patch("src.ws_handler.kalshi_get", return_value=_mock_market(yes_price=0.50)), \
         patch("src.ws_handler.estimate_probability",
               return_value=_mock_analysis(model_prob=0.70, confidence="low")):
        result = handler.handle(_event(delta=0.05, yes_price=0.50))
    assert result["action"] == "skip"
    assert result["reason"] == "low_confidence"


# ── Trade placed ───────────────────────────────────────────────────────────────

def _trade_setup(model_prob=0.65, yes_price=0.50, confidence="medium", delta=0.05):
    """Shared setup for happy-path trade tests."""
    mock_position = {
        "id": "pos-123",
        "ticker": "MKTX",
        "direction": "BUY_YES",
        "amount": 10.0,
        "entry_price": yes_price,
        "question": "Test Market",
        "status": "open",
    }
    mock_portfolio = {"bankroll": 1000.0, "positions": [], "total_trades": 0}

    with patch("src.ws_handler.kalshi_get", return_value=_mock_market(yes_price=yes_price)), \
         patch("src.ws_handler.estimate_probability",
               return_value=_mock_analysis(model_prob=model_prob, confidence=confidence)), \
         patch("src.ws_handler.load_portfolio", return_value=mock_portfolio), \
         patch("src.ws_handler.open_position", return_value=mock_position) as mock_open:
        handler = WSHandler()
        result = handler.handle(_event(delta=delta, yes_price=yes_price, prev=yes_price - delta))

    return result, mock_open


def test_trade_placed_buy_yes():
    """model_prob > yes_price → BUY_YES trade."""
    result, mock_open = _trade_setup(model_prob=0.65, yes_price=0.50)
    assert result["action"] == "trade"
    assert result["direction"] == "BUY_YES"
    assert result["edge"] == pytest.approx(0.15)
    assert result["confidence"] == "medium"
    assert "position" in result
    mock_open.assert_called_once()


def test_trade_placed_buy_no():
    """model_prob < yes_price → BUY_NO trade (market overprices yes)."""
    result, _ = _trade_setup(model_prob=0.30, yes_price=0.50)
    assert result["action"] == "trade"
    assert result["direction"] == "BUY_NO"


def test_trade_placed_high_confidence():
    """high confidence also triggers a trade."""
    result, _ = _trade_setup(confidence="high")
    assert result["action"] == "trade"


def test_signal_entry_price_buy_yes():
    """For BUY_YES, entry_price should equal yes_price."""
    handler = WSHandler()
    yes_price = 0.50
    mock_portfolio = {"bankroll": 1000.0, "positions": [], "total_trades": 0}
    captured_signal = {}

    def capture_open(portfolio, signal):
        captured_signal.update(signal)
        return {"id": "p1", "amount": 10.0, "entry_price": signal["entry_price"],
                "ticker": "MKTX", "question": "", "direction": signal["direction"], "status": "open"}

    with patch("src.ws_handler.kalshi_get", return_value=_mock_market(yes_price=yes_price)), \
         patch("src.ws_handler.estimate_probability",
               return_value=_mock_analysis(model_prob=0.65, confidence="medium")), \
         patch("src.ws_handler.load_portfolio", return_value=mock_portfolio), \
         patch("src.ws_handler.open_position", side_effect=capture_open):
        result = handler.handle(_event(delta=0.05, yes_price=yes_price))

    assert result["action"] == "trade"
    assert captured_signal["direction"] == "BUY_YES"
    assert captured_signal["entry_price"] == pytest.approx(yes_price)


def test_signal_entry_price_buy_no():
    """For BUY_NO, entry_price should equal 1 - yes_price."""
    handler = WSHandler()
    yes_price = 0.50
    mock_portfolio = {"bankroll": 1000.0, "positions": [], "total_trades": 0}
    captured_signal = {}

    def capture_open(portfolio, signal):
        captured_signal.update(signal)
        return {"id": "p2", "amount": 10.0, "entry_price": signal["entry_price"],
                "ticker": "MKTX", "question": "", "direction": signal["direction"], "status": "open"}

    with patch("src.ws_handler.kalshi_get", return_value=_mock_market(yes_price=yes_price)), \
         patch("src.ws_handler.estimate_probability",
               return_value=_mock_analysis(model_prob=0.30, confidence="high")), \
         patch("src.ws_handler.load_portfolio", return_value=mock_portfolio), \
         patch("src.ws_handler.open_position", side_effect=capture_open):
        result = handler.handle(_event(delta=0.05, yes_price=yes_price))

    assert result["action"] == "trade"
    assert captured_signal["direction"] == "BUY_NO"
    assert captured_signal["entry_price"] == pytest.approx(1.0 - yes_price)


# ── Portfolio full ─────────────────────────────────────────────────────────────

def test_skip_when_portfolio_full():
    """open_position returns None when portfolio is full."""
    handler = WSHandler()
    mock_portfolio = {"bankroll": 1000.0, "positions": [], "total_trades": 0}

    with patch("src.ws_handler.kalshi_get", return_value=_mock_market()), \
         patch("src.ws_handler.estimate_probability",
               return_value=_mock_analysis(model_prob=0.65, confidence="medium")), \
         patch("src.ws_handler.load_portfolio", return_value=mock_portfolio), \
         patch("src.ws_handler.open_position", return_value=None):
        result = handler.handle(_event(delta=0.05))

    assert result["action"] == "skip"
    assert result["reason"] == "portfolio_full"


# ── Dedupe state update ────────────────────────────────────────────────────────

def test_dedupe_timestamp_updated_after_evaluation():
    """After a qualifying event, _last_eval is updated so the next call is deduped."""
    handler = WSHandler()
    mock_portfolio = {"bankroll": 1000.0, "positions": [], "total_trades": 0}
    mock_position = {"id": "p", "amount": 10.0, "entry_price": 0.5,
                     "ticker": "MKTX", "question": "", "direction": "BUY_YES", "status": "open"}

    with patch("src.ws_handler.kalshi_get", return_value=_mock_market()), \
         patch("src.ws_handler.estimate_probability",
               return_value=_mock_analysis(model_prob=0.65, confidence="medium")), \
         patch("src.ws_handler.load_portfolio", return_value=mock_portfolio), \
         patch("src.ws_handler.open_position", return_value=mock_position):
        handler.handle(_event(delta=0.10))

    # Immediately re-evaluate — should be deduped now
    result2 = handler.handle(_event(delta=0.10))
    assert result2["reason"] == "dedupe"


def test_dedupe_also_set_on_rest_error():
    """Even when REST fetch fails, we update _last_eval to avoid hammering the API."""
    handler = WSHandler()
    with patch("src.ws_handler.kalshi_get", side_effect=Exception("timeout")):
        handler.handle(_event(delta=0.10))

    # Second call immediately → deduped
    result = handler.handle(_event(delta=0.10))
    # The REST error sets _last_eval before failing, so second call should dedupe
    assert result["reason"] == "dedupe"


# ── Analyzer error ─────────────────────────────────────────────────────────────

def test_analyzer_error_returns_error():
    handler = WSHandler()
    with patch("src.ws_handler.kalshi_get", return_value=_mock_market()), \
         patch("src.ws_handler.estimate_probability", side_effect=Exception("API down")):
        result = handler.handle(_event(delta=0.10))
    assert result["action"] == "error"
    assert "API down" in result["reason"]


def test_no_model_prob_returns_skip():
    """Analyzer returning model_prob=None → skip."""
    handler = WSHandler()
    with patch("src.ws_handler.kalshi_get", return_value=_mock_market()), \
         patch("src.ws_handler.estimate_probability",
               return_value={"model_prob": None, "confidence": "low", "rationale": "", "reasoning": ""}):
        result = handler.handle(_event(delta=0.10))
    assert result["action"] == "skip"
    assert result["reason"] == "no_model_prob"
