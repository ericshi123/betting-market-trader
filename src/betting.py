from typing import Optional
"""
Betting helpers: Kelly criterion sizing and bet recommendation.
"""


def kelly_fraction(edge: float, model_prob: float, market_prob: float) -> float:
    """
    Compute quarter-Kelly fraction for a binary YES bet.

    Kelly formula: f* = (b*p - q) / b
      b = odds received on a $1 bet = (1 - market_prob) / market_prob
      p = model probability of YES
      q = 1 - p

    Returns quarter-Kelly, capped at 0.20.  Returns 0 if edge <= 0.
    """
    if edge <= 0:
        return 0.0
    if market_prob <= 0 or market_prob >= 1:
        return 0.0

    b = (1 - market_prob) / market_prob
    p = model_prob
    q = 1 - model_prob

    f_star = (b * p - q) / b
    quarter_kelly = f_star / 4

    return min(max(quarter_kelly, 0.0), 0.20)


def recommend_bet(market: dict, analyzed: dict, bankroll: float) -> Optional[dict]:
    """
    Return a bet recommendation dict or None if criteria not met.

    Criteria:
      - abs(edge) >= 0.08
      - confidence in ["medium", "high"]

    edge = analyzed.model_prob - market.yes_price
    """
    model_prob = analyzed.get("model_prob", 0)
    market_prob = market.get("yes_price", 0)
    confidence = analyzed.get("confidence", "low")

    edge = model_prob - market_prob

    if abs(edge) < 0.08:
        return None
    if confidence not in ("medium", "high"):
        return None

    direction = "BUY_YES" if edge > 0 else "BUY_NO"
    side = "yes" if edge > 0 else "no"

    kf = kelly_fraction(edge, model_prob, market_prob)
    raw_amount = bankroll * kf
    amount = min(raw_amount, 50.0)

    entry_prob = market_prob if direction == "BUY_YES" else (1 - market_prob)
    limit_price = max(1, min(99, int(round(entry_prob * 100))))

    return {
        "market_id": market.get("market_id") or analyzed.get("market_id"),
        "ticker": market.get("market_id") or analyzed.get("market_id"),
        "question": market.get("question") or analyzed.get("question"),
        "direction": direction,
        "side": side,
        "limit_price": limit_price,
        "count": max(1, int(amount)),
        "model_prob": model_prob,
        "market_prob": market_prob,
        "edge": edge,
        "kelly_fraction": kf,
        "amount": round(amount, 2),
        "confidence": confidence,
        "rationale": analyzed.get("rationale", ""),
    }
