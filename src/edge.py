from typing import Optional

_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


def calculate_edge(market_prob: float, model_prob: float) -> float:
    """
    Positive → model thinks YES is more likely than market implies (lean BUY_YES).
    Negative → model thinks YES is less likely (lean BUY_NO).
    """
    return model_prob - market_prob


def rank_markets(
    analyzed_markets: list[dict],
    min_confidence: str = "low",
    min_edge: float = 0.0,
) -> list[dict]:
    """
    Score, filter, and sort analyzed markets by absolute edge.
    Each input dict must contain yes_price, model_prob, and confidence.
    Returns new dicts with added keys: edge, abs_edge, direction.
    """
    threshold = _CONFIDENCE_RANK.get(min_confidence, 1)
    result = []

    for m in analyzed_markets:
        model_prob: Optional[float] = m.get("model_prob")
        market_prob: Optional[float] = m.get("yes_price")
        confidence: str = m.get("confidence", "low")

        if model_prob is None or market_prob is None:
            continue
        if _CONFIDENCE_RANK.get(confidence, 0) < threshold:
            continue

        edge = calculate_edge(market_prob, model_prob)
        abs_edge = abs(edge)

        if abs_edge < min_edge:
            continue

        result.append(
            {
                **m,
                "edge": round(edge, 4),
                "abs_edge": round(abs_edge, 4),
                "direction": "BUY_YES" if edge > 0 else "BUY_NO",
            }
        )

    result.sort(key=lambda x: x["abs_edge"], reverse=True)
    return result
