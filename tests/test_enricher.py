import time
import pytest
from unittest.mock import patch, MagicMock

import src.enricher as enricher_mod
from src.enricher import enrich_market


@pytest.fixture(autouse=True)
def clear_cache():
    enricher_mod._cache.clear()
    yield
    enricher_mod._cache.clear()


_FAKE_HTML = """
<html><body>
<div class="result">
  <a class="result__snippet">First snippet about rates.</a>
</div>
<div class="result">
  <a class="result__snippet">Second snippet about the Fed.</a>
</div>
<div class="result">
  <a class="result__snippet">Third snippet about markets.</a>
</div>
<div class="result">
  <a class="result__snippet">Fourth snippet — should be ignored.</a>
</div>
</body></html>
"""


def _mock_response(html=_FAKE_HTML, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.text = html
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return resp


# ── Test 1: top 3 snippets extracted ──────────────────────────────────────────

def test_extracts_top_3_snippets():
    market = {"market_id": "MKT1", "question": "Will the Fed cut rates in June 2026?"}
    with patch("src.enricher.requests.get", return_value=_mock_response()):
        result = enrich_market(market)

    assert result["has_fresh_context"] is True
    ctx = result["search_context"]
    assert "First snippet" in ctx
    assert "Second snippet" in ctx
    assert "Third snippet" in ctx
    assert "Fourth snippet" not in ctx


# ── Test 2: cache TTL — no second HTTP call within 1h ─────────────────────────

def test_cache_hit_within_ttl():
    market = {"market_id": "MKT2", "question": "Will it rain?"}
    with patch("src.enricher.requests.get", return_value=_mock_response()) as mock_get:
        enrich_market(market)
        assert mock_get.call_count == 1

        market2 = {"market_id": "MKT2", "question": "Will it rain?"}
        result = enrich_market(market2)
        assert mock_get.call_count == 1  # no second HTTP call

    assert result["has_fresh_context"] is True
    assert "First snippet" in result["search_context"]


def test_cache_expired_makes_new_request():
    market = {"market_id": "MKT3", "question": "What happens?"}
    with patch("src.enricher.requests.get", return_value=_mock_response()) as mock_get:
        enrich_market(market)
        assert mock_get.call_count == 1

        # Expire the cache entry
        enricher_mod._cache["MKT3"]["ts"] = time.time() - 7200

        enrich_market({"market_id": "MKT3", "question": "What happens?"})
        assert mock_get.call_count == 2


# ── Test 3: graceful fallback on HTTP error ───────────────────────────────────

def test_http_error_returns_market_unchanged():
    market = {"market_id": "MKT4", "question": "Will X happen?", "extra": "data"}
    with patch("src.enricher.requests.get", return_value=_mock_response(status=500)):
        result = enrich_market(market)

    assert result["extra"] == "data"
    assert result["has_fresh_context"] is False
    assert "search_context" not in result


def test_network_exception_returns_market_unchanged():
    market = {"market_id": "MKT5", "question": "Will Y happen?"}
    with patch("src.enricher.requests.get", side_effect=ConnectionError("timeout")):
        result = enrich_market(market)

    assert result["has_fresh_context"] is False
    assert "search_context" not in result


# ── Test 4: analyzer prompt includes search_context ───────────────────────────

def test_analyzer_includes_search_context():
    import src.analyzer as analyzer_mod

    market = {
        "market_id": "ANLZ1",
        "question": "Will rates drop?",
        "yes_price": 0.55,
        "end_date": "2026-06-30",
        "volume": 50000,
    }

    fake_response = MagicMock()
    fake_response.content = [MagicMock(text=(
        "REASONING: Some reasoning here.\n"
        "PROBABILITY: 0.60\n"
        "CONFIDENCE: medium\n"
        "RATIONALE: Strong signal.\n"
        "SOURCE: finance"
    ))]

    with patch("src.enricher.requests.get", return_value=_mock_response()):
        with patch.object(analyzer_mod, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.return_value = fake_response

            analyzer_mod.estimate_probability(market)

            call_kwargs = mock_client.messages.create.call_args
            user_content = call_kwargs[1]["messages"][0]["content"]

    assert "Recent context:" in user_content
    assert "First snippet" in user_content


def test_analyzer_no_search_context_prompt_unchanged():
    import src.analyzer as analyzer_mod

    market = {
        "market_id": "ANLZ2",
        "question": "Will rates drop?",
        "yes_price": 0.55,
        "end_date": "2026-06-30",
        "volume": 50000,
    }

    fake_response = MagicMock()
    fake_response.content = [MagicMock(text=(
        "REASONING: Some reasoning.\n"
        "PROBABILITY: 0.55\n"
        "CONFIDENCE: low\n"
        "RATIONALE: Uncertain.\n"
        "SOURCE: finance"
    ))]

    with patch("src.enricher.requests.get", side_effect=ConnectionError("no network")):
        with patch.object(analyzer_mod, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.return_value = fake_response

            analyzer_mod.estimate_probability(market)

            call_kwargs = mock_client.messages.create.call_args
            user_content = call_kwargs[1]["messages"][0]["content"]

    assert "Recent context:" not in user_content
