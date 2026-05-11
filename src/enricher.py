import time
import urllib.parse

import requests
from bs4 import BeautifulSoup

_cache: dict[str, dict] = {}
_TTL = 3600  # 1 hour


def enrich_market(market: dict) -> dict:
    """Add search_context and has_fresh_context keys to market dict. Never raises."""
    market_id = market.get("market_id", "")
    now = time.time()

    cached = _cache.get(market_id)
    if cached and now - cached["ts"] < _TTL:
        market["search_context"] = cached["snippets"]
        market["has_fresh_context"] = bool(cached["snippets"])
        return market

    question = market.get("question", "")
    query = question[:80]

    try:
        snippets = _search(query)
        search_context = "\n".join(snippets)
        _cache[market_id] = {"snippets": search_context, "ts": now}
        market["search_context"] = search_context
        market["has_fresh_context"] = bool(snippets)
    except Exception:
        market["has_fresh_context"] = False

    return market


def _search(query: str) -> list[str]:
    url = "https://html.duckduckgo.com/html/"
    params = {"q": query}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, params=params, headers=headers, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    snippets = []
    for tag in soup.select(".result__snippet")[:3]:
        text = tag.get_text(strip=True)
        if text:
            snippets.append(text)
    return snippets
