from datetime import datetime, timezone
from typing import Optional

from src.client import kalshi_get


def _safe_float(val, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _parse_market(raw: dict) -> dict:
    """Normalize a Kalshi market record into our internal format."""
    ticker = raw.get("ticker", "")
    title = (raw.get("title") or "").strip()

    # Prices come back as floats in 0.0–1.0 range (dollar value of a $1 contract)
    yes_price = _safe_float(raw.get("yes_ask_dollars"))
    no_price = _safe_float(raw.get("no_ask_dollars"))
    yes_bid = _safe_float(raw.get("yes_bid_dollars"))
    no_bid = _safe_float(raw.get("no_bid_dollars"))

    # volume_fp is number of contracts traded (float string)
    volume = _safe_float(raw.get("volume_fp"))

    close_time_raw = raw.get("close_time") or raw.get("expiration_time") or ""
    end_date = "unknown"
    if close_time_raw:
        try:
            end_date = close_time_raw[:10]  # YYYY-MM-DD
        except Exception:
            pass

    description = (
        raw.get("rules_primary") or raw.get("rules_secondary") or ""
    ).strip()[:600]

    return {
        "market_id": ticker,
        "question": title,
        "description": description,
        "end_date": end_date,
        "close_time": close_time_raw,
        "yes_price": yes_price,
        "no_price": no_price,
        "yes_bid": yes_bid,
        "no_bid": no_bid,
        "volume": volume,
        "status": raw.get("status", ""),
        "category": raw.get("category", ""),
    }


def fetch_active_markets(limit: int = 200) -> list[dict]:
    """
    Paginate through Kalshi open markets, normalize each to internal format.
    Returns up to limit markets sorted by volume descending.
    """
    import time as _time
    markets = []
    cursor = None
    page_size = 100
    # Only fetch markets closing at least 2 days from now (skips brand-new 0-volume markets)
    min_close_ts = int(_time.time()) + (86400 * 2)

    while len(markets) < limit:
        params: dict = {
            "limit": page_size,
            "status": "open",
            "min_close_ts": min_close_ts,
        }
        if cursor:
            params["cursor"] = cursor

        data = kalshi_get("/markets", params=params)
        batch = data.get("markets", [])

        for m in batch:
            markets.append(_parse_market(m))

        cursor = data.get("cursor")
        if not cursor or len(batch) < page_size:
            break

    markets.sort(key=lambda m: m.get("volume") or 0, reverse=True)
    return markets[:limit]


def filter_markets(
    markets: list[dict],
    min_volume: Optional[float] = None,
    max_days_to_close: Optional[int] = None,
    min_yes_price: Optional[float] = None,
    max_yes_price: Optional[float] = None,
) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    result = []
    for m in markets:
        if min_volume is not None:
            if (m.get("volume") or 0) < min_volume:
                continue
        if max_days_to_close is not None:
            end = m.get("end_date", "")
            if end and end != "unknown":
                try:
                    end_dt = datetime.strptime(end, "%Y-%m-%d").date()
                    if (end_dt - today).days > max_days_to_close:
                        continue
                except ValueError:
                    pass
        if min_yes_price is not None or max_yes_price is not None:
            yp = m.get("yes_price")
            if yp is None:
                continue
            if min_yes_price is not None and yp < min_yes_price:
                continue
            if max_yes_price is not None and yp > max_yes_price:
                continue
        result.append(m)
    return result
