import time
import requests
from datetime import datetime, timezone
from typing import Optional

GAMMA_HOST = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"

_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "application/json"})


def _get(url: str, params: dict | None = None, retries: int = 3) -> dict | list:
    delay = 1.0
    last_err = None
    for attempt in range(retries):
        try:
            resp = _SESSION.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last_err}")


def _parse_market(raw: dict) -> dict:
    """Normalize a Gamma API market record into a flat dict."""
    outcomes = []
    prices = []

    # Gamma returns outcomes as a JSON-encoded string or list
    raw_outcomes = raw.get("outcomes", [])
    if isinstance(raw_outcomes, str):
        import json
        try:
            raw_outcomes = json.loads(raw_outcomes)
        except Exception:
            raw_outcomes = []

    raw_prices = raw.get("outcomePrices", [])
    if isinstance(raw_prices, str):
        import json
        try:
            raw_prices = json.loads(raw_prices)
        except Exception:
            raw_prices = []

    for i, o in enumerate(raw_outcomes):
        outcomes.append(o)

    for p in raw_prices:
        try:
            prices.append(float(p))
        except (ValueError, TypeError):
            prices.append(None)

    yes_price = prices[0] if prices else None
    no_price = prices[1] if len(prices) > 1 else None

    # end_date: endDate may be full ISO "2026-07-20T00:00:00Z" — take first 10 chars
    end_date_raw = raw.get("endDate") or raw.get("endDateIso") or ""
    end_date = end_date_raw[:10] if end_date_raw else "unknown"

    volume = _safe_float(raw.get("volume") or raw.get("volume24hr") or raw.get("volumeNum"))
    liquidity = _safe_float(raw.get("liquidity") or raw.get("liquidityNum"))

    return {
        "market_id": raw.get("conditionId") or raw.get("id", ""),
        "question": raw.get("question", "").strip(),
        "description": (raw.get("description") or "").strip()[:600],
        "end_date": end_date,
        "outcomes": outcomes,
        "yes_price": yes_price,
        "no_price": no_price,
        "best_bid": _safe_float(raw.get("bestBid")),
        "best_ask": _safe_float(raw.get("bestAsk")),
        "volume": volume,
        "liquidity": liquidity,
        "active": raw.get("active", True),
        "closed": raw.get("closed", False),
        "_raw": raw,
    }


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def fetch_active_markets(limit: int = 50) -> list[dict]:
    """
    Pull active markets via the Gamma events endpoint (which carries proper volume data).
    Events are sorted by aggregate volume; markets within each event are flattened and
    returned sorted by their individual volume descending.
    """
    url = f"{GAMMA_HOST}/events"
    raw_markets: list[dict] = []
    offset = 0
    page_size = 50

    while len(raw_markets) < limit:
        params = {
            "active": "true",
            "closed": "false",
            "limit": page_size,
            "offset": offset,
            "order": "volume",
            "ascending": "false",
        }
        data = _get(url, params=params)
        events = data if isinstance(data, list) else data.get("events", [])
        if not events:
            break

        for event in events:
            for m in event.get("markets", []):
                if m.get("active") and not m.get("closed"):
                    raw_markets.append(m)

        if len(events) < page_size:
            break
        offset += page_size

    markets = [_parse_market(m) for m in raw_markets]
    # Sort by volume descending (individual market volume)
    markets.sort(key=lambda m: m.get("volume") or 0, reverse=True)
    return markets[:limit]


def fetch_market_orderbook(market_id: str) -> dict:
    """
    Fetch the order book for a specific market from the CLOB API.
    Returns dict with 'bids' and 'asks' lists of {price, size}.
    """
    # CLOB requires the token_id (outcome token), not conditionId.
    # First resolve via CLOB markets endpoint.
    clob_market = _get(f"{CLOB_HOST}/markets/{market_id}")

    token_id = None
    tokens = clob_market.get("tokens", [])
    for t in tokens:
        if t.get("outcome", "").lower() in ("yes", "1"):
            token_id = t.get("token_id")
            break
    if not token_id and tokens:
        token_id = tokens[0].get("token_id")

    if not token_id:
        return {"bids": [], "asks": [], "error": "no token_id found"}

    book = _get(f"{CLOB_HOST}/book", params={"token_id": token_id})
    return {
        "token_id": token_id,
        "bids": [{"price": float(b["price"]), "size": float(b["size"])} for b in book.get("bids", [])],
        "asks": [{"price": float(a["price"]), "size": float(a["size"])} for a in book.get("asks", [])],
    }


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
