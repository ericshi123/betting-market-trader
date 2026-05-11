"""Tests for src/news_monitor.py"""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

import src.news_monitor as news_monitor_mod
from src.news_monitor import fetch_headlines, match_markets


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_entry(title, url, published_offset_seconds=0):
    """Build a fake feedparser entry dict-like object."""
    entry = MagicMock()
    entry.title = title
    entry.link = url
    entry.summary = f"Summary for {title}"
    now = datetime.now(timezone.utc)
    pub_dt = now - timedelta(seconds=published_offset_seconds)
    # feedparser RFC 2822 format
    entry.published = pub_dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
    entry.published_parsed = pub_dt.timetuple()
    entry.updated = None
    entry.updated_parsed = None
    return entry


def _make_feed(entries):
    feed = MagicMock()
    feed.entries = entries
    return feed


# ── Test 1: Headlines parsed correctly from feed ──────────────────────────────

def test_headlines_parsed_correctly(tmp_path):
    entries = [
        _make_entry("Trump Signs Executive Order on Tariffs", "https://reuters.com/1"),
        _make_entry("Bitcoin Hits Record High Amid ETF Inflows", "https://coindesk.com/2"),
    ]
    fake_feed = _make_feed(entries)

    with (
        patch("src.news_monitor._SEEN_PATH", tmp_path / "news_seen.json"),
        patch("feedparser.parse", return_value=fake_feed),
    ):
        results = fetch_headlines()

    assert len(results) == len(entries) * len(news_monitor_mod.RSS_FEEDS)
    # Dedupe within the same run means unique URLs across all feeds
    urls = {r["url"] for r in results}
    # 2 unique URLs * 5 feeds = but dedupe means 2 unique total
    assert len(urls) == 2

    titles = {r["title"] for r in results}
    assert "Trump Signs Executive Order on Tariffs" in titles
    assert "Bitcoin Hits Record High Amid ETF Inflows" in titles

    for r in results:
        assert "title" in r
        assert "summary" in r
        assert "url" in r
        assert "feed" in r
        assert "published_at" in r


# ── Test 2: Dedupe — second run with same URLs returns empty list ─────────────

def test_dedupe_skips_already_seen_urls(tmp_path):
    seen_path = tmp_path / "news_seen.json"

    entries = [_make_entry("Breaking News About Economy", "https://reuters.com/econ")]
    fake_feed = _make_feed(entries)

    with (
        patch("src.news_monitor._SEEN_PATH", seen_path),
        patch("feedparser.parse", return_value=fake_feed),
    ):
        first = fetch_headlines()
        second = fetch_headlines()

    assert len(first) > 0
    assert len(second) == 0


# ── Test 3: Only headlines within 2h are returned ─────────────────────────────

def test_only_recent_headlines_returned(tmp_path):
    entries = [
        _make_entry("Recent Article", "https://reuters.com/recent", published_offset_seconds=30 * 60),    # 30 min ago — OK
        _make_entry("Old Article",    "https://reuters.com/old",    published_offset_seconds=3 * 3600),  # 3h ago — excluded
    ]
    fake_feed = _make_feed(entries)

    with (
        patch("src.news_monitor._SEEN_PATH", tmp_path / "news_seen.json"),
        patch("feedparser.parse", return_value=fake_feed),
    ):
        results = fetch_headlines()

    urls = {r["url"] for r in results}
    assert "https://reuters.com/recent" in urls
    assert "https://reuters.com/old" not in urls


# ── Test 4: Keyword matching finds correct markets ────────────────────────────

def test_match_markets_finds_correct_markets():
    headline = {"title": "Federal Reserve Raises Interest Rates Again"}

    markets = [
        {"market_id": "FED-RATE-MAY", "question": "Will the Federal Reserve raise rates in May?"},
        {"market_id": "BITCOIN-2024", "question": "Will Bitcoin exceed $100k in 2024?"},
        {"market_id": "FED-CUT-JUN",  "question": "Will the Fed cut interest rates in June?"},
        {"market_id": "NBA-CHAMP",    "question": "Who wins the NBA Championship?"},
    ]

    matched = match_markets(headline, markets)
    matched_ids = {m["market_id"] for m in matched}

    assert "FED-RATE-MAY" in matched_ids
    assert "BITCOIN-2024" not in matched_ids
    # "interest" and "rates" appear in FED-CUT-JUN too
    assert "FED-CUT-JUN" in matched_ids
    assert "NBA-CHAMP" not in matched_ids


# ── Test 5: match_markets caps results at 5 ───────────────────────────────────

def test_match_markets_max_five():
    headline = {"title": "President Biden Signs Major Climate Bill"}

    markets = [
        {"market_id": f"CLIMATE-{i}", "question": f"Climate bill vote {i} — will President sign?"}
        for i in range(10)
    ]

    matched = match_markets(headline, markets)
    assert len(matched) <= 5


# ── Test 6: No keywords → no matches ─────────────────────────────────────────

def test_match_markets_no_keywords_returns_empty():
    headline = {"title": "The and or is"}  # all stopwords/short words
    markets = [{"market_id": "ANY", "question": "Will something happen?"}]
    matched = match_markets(headline, markets)
    assert matched == []


# ── Test 7: Feed parse error → skipped gracefully ────────────────────────────

def test_feed_parse_error_skipped(tmp_path):
    with (
        patch("src.news_monitor._SEEN_PATH", tmp_path / "news_seen.json"),
        patch("feedparser.parse", side_effect=Exception("network error")),
    ):
        results = fetch_headlines()

    assert results == []


# ── Test 8: seen.json persisted correctly ─────────────────────────────────────

def test_seen_json_persisted(tmp_path):
    seen_path = tmp_path / "news_seen.json"
    entries = [_make_entry("Persistent Headline", "https://reuters.com/persist")]
    fake_feed = _make_feed(entries)

    with (
        patch("src.news_monitor._SEEN_PATH", seen_path),
        patch("feedparser.parse", return_value=fake_feed),
    ):
        fetch_headlines()

    assert seen_path.exists()
    saved = json.loads(seen_path.read_text())
    assert "https://reuters.com/persist" in saved
