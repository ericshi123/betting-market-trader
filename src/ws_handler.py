"""
Real-time price event handler — filters, deduplicates, analyzes, and places trades.

Decision flow for each price update:
  1. Skip if |delta| < MIN_TRIGGER_DELTA (3pp)
  2. Skip if same ticker was evaluated in the last 30 minutes (dedupe)
  3. Fetch full market details via REST
  4. Ask Claude for probability estimate
  5. Skip if edge < MIN_EDGE (8pp) or confidence is low
  6. Open a momentum paper position
"""

import logging
import time
from typing import Optional

from src.analyzer import estimate_probability
from src.client import kalshi_get
from src.markets import _parse_market
from src.momentum_portfolio import load_portfolio, open_position

logger = logging.getLogger(__name__)

MIN_TRIGGER_DELTA = 0.03   # 3pp move required to trigger evaluation
DEDUPE_WINDOW_S = 1800     # 30-minute cooldown per ticker
MIN_EDGE = 0.08            # 8pp minimum model vs. market divergence
_HIGH_CONFIDENCE = {"medium", "high"}


class WSHandler:
    """Stateful handler that evaluates price update events and places paper trades."""

    def __init__(self):
        self._last_eval: dict[str, float] = {}  # ticker -> epoch seconds of last evaluation

    def handle(self, event: dict) -> dict:
        """
        Process one price update event. Returns a result dict with at minimum
        'action' (skip | trade | error) and 'reason'.
        """
        ticker = event["ticker"]
        delta = event["delta"]
        yes_price = event["yes_price"]
        prev_yes_price = event.get("prev_yes_price", yes_price)

        # ── 1. Delta threshold ────────────────────────────────────────────────
        if abs(delta) < MIN_TRIGGER_DELTA:
            logger.debug("SKIP %s: delta %.4f below threshold %.2f", ticker, delta, MIN_TRIGGER_DELTA)
            return {"action": "skip", "reason": "delta_too_small", "ticker": ticker, "delta": delta}

        # ── 2. Dedupe ──────────────────────────────────────────────────────────
        now = time.time()
        last = self._last_eval.get(ticker, 0.0)
        elapsed = now - last
        if elapsed < DEDUPE_WINDOW_S:
            cooldown = int(DEDUPE_WINDOW_S - elapsed)
            logger.info("SKIP %s: evaluated %.0fs ago — dedupe cooldown %ds", ticker, elapsed, cooldown)
            return {
                "action": "skip", "reason": "dedupe",
                "ticker": ticker, "cooldown_remaining_s": cooldown,
            }

        # Record before REST fetch so errors don't allow rapid hammering of the API
        self._last_eval[ticker] = now

        # ── 3. Fetch full market ───────────────────────────────────────────────
        try:
            data = kalshi_get(f"/markets/{ticker}")
            market_raw = data.get("market", {})
            market = _parse_market(market_raw)
            # Override with live WS price — REST may lag by seconds
            market["yes_price"] = yes_price
        except Exception as exc:
            logger.error("Failed to fetch market %s: %s", ticker, exc)
            return {"action": "error", "reason": str(exc), "ticker": ticker}

        # ── 4. Claude analysis ─────────────────────────────────────────────────
        try:
            analysis = estimate_probability(market)
        except Exception as exc:
            logger.error("Analyzer error for %s: %s", ticker, exc)
            return {"action": "error", "reason": str(exc), "ticker": ticker}

        model_prob: Optional[float] = analysis.get("model_prob")
        confidence: str = analysis.get("confidence", "low")

        if model_prob is None:
            logger.info("SKIP %s: analyzer returned no probability", ticker)
            return {"action": "skip", "reason": "no_model_prob", "ticker": ticker, "analysis": analysis}

        edge = abs(model_prob - yes_price)

        # ── 5a. Edge check ─────────────────────────────────────────────────────
        if edge < MIN_EDGE:
            logger.info(
                "SKIP %s: edge %.3f < %.2f (model=%.2f market=%.2f conf=%s)",
                ticker, edge, MIN_EDGE, model_prob, yes_price, confidence,
            )
            return {
                "action": "skip", "reason": "no_edge",
                "ticker": ticker, "edge": edge, "model_prob": model_prob,
                "yes_price": yes_price, "confidence": confidence, "analysis": analysis,
            }

        # ── 5b. Confidence check ───────────────────────────────────────────────
        if confidence not in _HIGH_CONFIDENCE:
            logger.info("SKIP %s: confidence=%s (need medium/high)", ticker, confidence)
            return {
                "action": "skip", "reason": "low_confidence",
                "ticker": ticker, "edge": edge, "confidence": confidence, "analysis": analysis,
            }

        # ── 6. Place paper trade ──────────────────────────────────────────────
        direction = "BUY_YES" if model_prob > yes_price else "BUY_NO"
        entry_price = yes_price if direction == "BUY_YES" else round(1.0 - yes_price, 4)

        signal = {
            "ticker": ticker,
            "market_id": ticker,
            "question": market.get("question", ""),
            "direction": direction,
            "yes_price": yes_price,
            "baseline_price": prev_yes_price,
            "delta": round(delta, 4),
            "abs_delta": round(abs(delta), 4),
            "entry_price": round(entry_price, 4),
        }

        portfolio = load_portfolio()
        position = open_position(portfolio, signal)

        if position is None:
            logger.info(
                "SKIP %s: %s edge=%.3f — portfolio full or bankroll insufficient",
                ticker, direction, edge,
            )
            return {
                "action": "skip", "reason": "portfolio_full",
                "ticker": ticker, "edge": edge, "analysis": analysis,
            }

        logger.info(
            "TRADE %s %s: edge=%.3f conf=%s amount=$%.2f entry=%.2f",
            direction, ticker, edge, confidence, position["amount"], entry_price,
        )
        return {
            "action": "trade",
            "direction": direction,
            "ticker": ticker,
            "edge": edge,
            "confidence": confidence,
            "position": position,
            "analysis": analysis,
        }
