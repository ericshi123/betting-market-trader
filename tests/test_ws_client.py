"""
Tests for src/ws_client.py — WebSocket client.

Tests cover:
  - Auth param generation (keys present)
  - Ticker price parsing (cents → float, delta tracking)
  - Callback dispatch
  - Subscribe message format
  - Message processing (ticker events, ping/pong)
  - Run loop: connect + subscribe + process
  - Reconnect on disconnect
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from src.ws_client import KalshiWebSocketClient, build_auth_params


# ── Helpers ────────────────────────────────────────────────────────────────────

class _AsyncIter:
    """Async iterator that yields from a list then stops."""
    def __init__(self, items):
        self._it = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _make_mock_ws(messages=()) -> MagicMock:
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.__aiter__ = lambda self: _AsyncIter(messages)
    return ws


def _make_mock_cm(ws):
    """Context manager that yields ws."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=ws)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


_FAKE_AUTH = {
    "kalshi_access_key": "test-key",
    "kalshi_access_signature": "ZmFrZXNpZw==",
    "kalshi_access_timestamp": "1700000000000",
}


# ── Auth ───────────────────────────────────────────────────────────────────────

def test_build_auth_params_required_keys():
    """build_auth_params() returns the three keys Kalshi WS expects."""
    mock_pk = MagicMock()
    mock_pk.sign.return_value = b"\x00" * 64

    with patch("src.ws_client.os.getenv", return_value="test-key-id"), \
         patch("src.ws_client._get_private_key", return_value=mock_pk):
        params = build_auth_params()

    assert set(params.keys()) == {
        "kalshi_access_key",
        "kalshi_access_signature",
        "kalshi_access_timestamp",
    }
    assert params["kalshi_access_key"] == "test-key-id"
    assert isinstance(params["kalshi_access_timestamp"], str)
    assert params["kalshi_access_timestamp"].isdigit()


def test_build_url_contains_auth_params():
    """_build_url() returns a URL with all three auth query params."""
    client = KalshiWebSocketClient()
    with patch("src.ws_client.build_auth_params", return_value=_FAKE_AUTH):
        url = client._build_url()

    assert "wss://api.elections.kalshi.com/trade-api/ws/v2" in url
    assert "kalshi_access_key=test-key" in url
    assert "kalshi_access_signature=" in url
    assert "kalshi_access_timestamp=" in url


# ── Ticker parsing ─────────────────────────────────────────────────────────────

def test_handle_ticker_first_time_returns_none():
    """No previous price stored → no event (we need a baseline first)."""
    client = KalshiWebSocketClient()
    result = client._handle_ticker({"market_ticker": "MKTX", "yes_ask": 50})
    assert result is None
    assert client._prices["MKTX"] == pytest.approx(0.50)


def test_handle_ticker_converts_cents_to_float():
    """Cent values 1-99 are divided by 100 before storing."""
    client = KalshiWebSocketClient()
    client._handle_ticker({"market_ticker": "X", "yes_ask": 73})
    assert client._prices["X"] == pytest.approx(0.73)


def test_handle_ticker_emits_delta_event():
    """Second update with different price emits a delta event."""
    client = KalshiWebSocketClient()
    client._prices["MKTX"] = 0.40
    result = client._handle_ticker({"market_ticker": "MKTX", "yes_ask": 55})

    assert result is not None
    assert result["ticker"] == "MKTX"
    assert result["yes_price"] == pytest.approx(0.55)
    assert result["prev_yes_price"] == pytest.approx(0.40)
    assert result["delta"] == pytest.approx(0.15)
    assert "timestamp" in result


def test_handle_ticker_no_change_returns_none():
    """Same price → no event (delta == 0)."""
    client = KalshiWebSocketClient()
    client._prices["MKTX"] = 0.50
    result = client._handle_ticker({"market_ticker": "MKTX", "yes_ask": 50})
    assert result is None


def test_handle_ticker_negative_delta():
    """Price drop produces a negative delta."""
    client = KalshiWebSocketClient()
    client._prices["MKTX"] = 0.70
    result = client._handle_ticker({"market_ticker": "MKTX", "yes_ask": 60})
    assert result is not None
    assert result["delta"] == pytest.approx(-0.10)


def test_handle_ticker_missing_fields_returns_none():
    """Messages missing ticker or price fields are silently ignored."""
    client = KalshiWebSocketClient()
    assert client._handle_ticker({}) is None
    assert client._handle_ticker({"market_ticker": "X"}) is None
    assert client._handle_ticker({"yes_ask": 50}) is None


def test_handle_ticker_uses_yes_price_fallback():
    """Falls back to 'yes_price' key if 'yes_ask' is absent."""
    client = KalshiWebSocketClient()
    client._prices["MKTX"] = 0.40
    result = client._handle_ticker({"market_ticker": "MKTX", "yes_price": 48})
    assert result is not None
    assert result["yes_price"] == pytest.approx(0.48)


# ── Callbacks ──────────────────────────────────────────────────────────────────

def test_dispatch_calls_all_callbacks():
    client = KalshiWebSocketClient()
    received_a, received_b = [], []
    client.on_price_update(received_a.append)
    client.on_price_update(received_b.append)

    event = {"ticker": "X", "yes_price": 0.5, "delta": 0.05}
    client._dispatch(event)

    assert received_a == [event]
    assert received_b == [event]


def test_dispatch_swallows_callback_exceptions():
    """A crashing callback does not prevent others from running."""
    client = KalshiWebSocketClient()

    def bad_cb(e):
        raise RuntimeError("boom")

    received = []
    client.on_price_update(bad_cb)
    client.on_price_update(received.append)

    client._dispatch({"ticker": "X"})
    assert len(received) == 1


# ── Async: subscribe & message processing ────────────────────────────────────

async def test_subscribe_sends_correct_message():
    client = KalshiWebSocketClient()
    mock_ws = AsyncMock()
    await client._subscribe(mock_ws)

    mock_ws.send.assert_called_once()
    sent = json.loads(mock_ws.send.call_args[0][0])
    assert sent["cmd"] == "subscribe"
    assert "ticker" in sent["params"]["channels"]
    assert sent["id"] == 1


async def test_process_messages_ticker_event():
    """A ticker frame with a price move fires the callback."""
    client = KalshiWebSocketClient()
    client._prices["MKTX"] = 0.40

    events = []
    client.on_price_update(events.append)

    msg = json.dumps({"type": "ticker", "msg": {"market_ticker": "MKTX", "yes_ask": 55}})
    mock_ws = _make_mock_ws([msg])
    await client._process_messages(mock_ws)

    assert len(events) == 1
    assert events[0]["ticker"] == "MKTX"
    assert events[0]["yes_price"] == pytest.approx(0.55)


async def test_process_messages_ping_replies_pong():
    """A ping frame results in a pong reply."""
    client = KalshiWebSocketClient()
    mock_ws = _make_mock_ws([json.dumps({"type": "ping"})])
    mock_ws.send = AsyncMock()
    await client._process_messages(mock_ws)

    mock_ws.send.assert_called_once()
    sent = json.loads(mock_ws.send.call_args[0][0])
    assert sent["type"] == "pong"


async def test_process_messages_invalid_json_skipped():
    """Malformed JSON frames are silently skipped."""
    client = KalshiWebSocketClient()
    events = []
    client.on_price_update(events.append)
    mock_ws = _make_mock_ws(["not-json", "{bad"])
    await client._process_messages(mock_ws)
    assert events == []


# ── Async: run() integration ───────────────────────────────────────────────────

async def test_run_connects_subscribes_and_processes():
    """
    run() should: connect with auth URL, send subscribe, process messages,
    and stop when the message iterator is exhausted (connection closes).
    """
    client = KalshiWebSocketClient()
    client._initial_backoff = 0.01

    events = []
    client.on_price_update(events.append)

    # Two messages: first seeds the price, second produces a delta
    msg1 = json.dumps({"type": "ticker", "msg": {"market_ticker": "TST", "yes_ask": 40}})
    msg2 = json.dumps({"type": "ticker", "msg": {"market_ticker": "TST", "yes_ask": 50}})

    mock_ws = _make_mock_ws([msg1, msg2])
    cm = _make_mock_cm(mock_ws)

    stop_event = asyncio.Event()
    call_count = 0

    def fake_connect(url, **kwargs):
        nonlocal call_count
        call_count += 1
        assert "kalshi_access_key" in url
        assert "kalshi_access_signature" in url
        stop_event.set()  # stop after first successful connection
        return cm

    with patch("src.ws_client.websockets.connect", side_effect=fake_connect), \
         patch("src.ws_client.build_auth_params", return_value=_FAKE_AUTH):
        await client.run(stop_event=stop_event)

    assert call_count == 1
    mock_ws.send.assert_called()  # subscribe message
    assert len(events) == 1  # one delta event from msg2
    assert events[0]["ticker"] == "TST"


async def test_run_reconnects_after_failure():
    """
    When a connection fails, run() should sleep then retry.
    Here we allow two connection attempts: first raises, second sets stop_event.
    """
    client = KalshiWebSocketClient()
    client._initial_backoff = 0.01

    call_count = 0
    stop_event = asyncio.Event()
    mock_ws = _make_mock_ws([])
    cm = _make_mock_cm(mock_ws)

    def fake_connect(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("simulated disconnect")
        # Second attempt: succeed but immediately signal stop
        stop_event.set()
        return cm

    with patch("src.ws_client.websockets.connect", side_effect=fake_connect), \
         patch("src.ws_client.build_auth_params", return_value=_FAKE_AUTH):
        await client.run(stop_event=stop_event)

    assert call_count == 2, f"Expected 2 connect attempts, got {call_count}"
