from typing import Optional
"""
News feed monitor — polls RSS feeds and matches headlines to Kalshi markets.
Tracks seen URLs in data/news_seen.json to avoid reprocessing.
Only returns headlines published within the last 2 hours.
"""

import json
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser

_SEEN_PATH = Path(__file__).parent.parent / "data" / "news_seen.json"

RSS_FEEDS = [
    ("politics", "https://feeds.reuters.com/Reuters/PoliticsNews"),
    ("politics", "https://rss.politico.com/politics-news.xml"),
    ("economics", "https://feeds.reuters.com/reuters/businessNews"),
    ("sports", "https://www.espn.com/espn/rss/news"),
    ("crypto", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
]

_STOPWORDS = {
    "the", "and", "that", "this", "with", "from", "have", "will", "been",
    "they", "their", "there", "about", "which", "would", "could", "should",
    "when", "what", "where", "into", "than", "then", "also", "more", "over",
    "after", "before", "under", "such", "says", "said", "says", "were",
    "your", "year", "years", "week", "weeks", "month", "months", "days",
    "being", "some", "each", "its", "our", "are", "was", "has",
}

TWO_HOURS = 7200  # seconds


def _load_seen() -> set[str]:
    if _SEEN_PATH.exists():
        try:
            with open(_SEEN_PATH) as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def _save_seen(seen: set[str]) -> None:
    _SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_SEEN_PATH, "w") as f:
        json.dump(list(seen), f)


def _parse_published(entry) -> Optional[datetime]:
    """Parse the published time from a feedparser entry. Returns UTC datetime or None."""
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
        # feedparser also provides parsed tuples
        parsed_attr = attr + "_parsed"
        tup = getattr(entry, parsed_attr, None)
        if tup:
            try:
                ts = time.mktime(tup)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except Exception:
                pass
    return None


def fetch_headlines() -> list[dict]:
    """
    Poll all RSS feeds, dedupe against seen URLs, and return headlines
    published in the last 2 hours.

    Each item: {title, summary, url, feed, published_at}
    """
    seen = _load_seen()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=TWO_HOURS)

    results = []
    new_urls = set()

    for feed_tag, feed_url in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception:
            continue

        for entry in parsed.entries:
            url = getattr(entry, "link", "") or ""
            if not url or url in seen:
                continue

            published_at = _parse_published(entry)

            # Skip if older than 2 hours (but allow if we can't parse the date)
            if published_at is not None and published_at < cutoff:
                continue

            title = getattr(entry, "title", "") or ""
            summary = getattr(entry, "summary", "") or ""

            results.append({
                "title": title.strip(),
                "summary": summary.strip()[:300],
                "url": url,
                "feed": feed_tag,
                "published_at": published_at.isoformat() if published_at else None,
            })
            new_urls.add(url)

    if new_urls:
        seen.update(new_urls)
        _save_seen(seen)

    return results


def match_markets(headline: dict, markets: list[dict]) -> list[dict]:
    """
    Match a headline to relevant markets by keyword overlap.

    Keywords: nouns/entities from the title — words >4 chars, not stopwords.
    Returns up to 5 markets whose question contains any keyword (case-insensitive).
    """
    title = headline.get("title", "")
    words = title.split()
    keywords = {
        w.strip(".,!?\"'();:").lower()
        for w in words
        if len(w.strip(".,!?\"'();:")) > 4 and w.lower() not in _STOPWORDS
    }

    if not keywords:
        return []

    matched = []
    for market in markets:
        question = (market.get("question") or "").lower()
        if any(kw in question for kw in keywords):
            matched.append(market)
        if len(matched) >= 5:
            break

    return matched
