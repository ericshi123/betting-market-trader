"""
Tests for src/dashboard.py using Flask test client.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

import src.dashboard as dashboard_module
from src.dashboard import app, configure, record_signal, record_heartbeat


@pytest.fixture(autouse=True)
def reset_state():
    """Reset dashboard shared state before each test."""
    with dashboard_module._state_lock:
        dashboard_module._state.update({
            "trading_paused": False,
            "last_heartbeat": None,
            "daemon_start": None,
            "signal_feed": [],
            "set_paused_fn": None,
            "get_paused_fn": None,
        })
    yield


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


_MOCK_PORTFOLIO = {
    "bankroll": 950.0,
    "peak_bankroll": 1000.0,
    "positions": [
        {
            "id": "pos-abc12345",
            "ticker": "MKTX",
            "market_id": "MKTX",
            "question": "Test market?",
            "direction": "BUY_YES",
            "amount": 10.0,
            "entry_price": 0.50,
            "yes_price_at_entry": 0.50,
            "baseline_price": 0.45,
            "delta": 0.05,
            "strategy": "momentum",
            "opened_at": "2026-01-01T00:00:00+00:00",
            "status": "open",
            "exit_price": None,
            "pnl": None,
            "closed_at": None,
        }
    ],
    "closed_pnl": -50.0,
    "total_trades": 5,
    "wins": 2,
    "losses": 3,
}


# ── GET / ─────────────────────────────────────────────────────────────────────

def test_index_returns_200(client):
    with patch("src.dashboard.load_portfolio", return_value=_MOCK_PORTFOLIO):
        resp = client.get("/")
    assert resp.status_code == 200
    assert b"WS Trader" in resp.data


def test_index_contains_positions_section(client):
    with patch("src.dashboard.load_portfolio", return_value=_MOCK_PORTFOLIO):
        resp = client.get("/")
    assert b"Open Positions" in resp.data


def test_index_renders_signal_feed(client):
    record_signal({"action": "trade", "ticker": "MKTX", "reason": "edge", "timestamp": 0})
    with patch("src.dashboard.load_portfolio", return_value=_MOCK_PORTFOLIO):
        resp = client.get("/")
    assert resp.status_code == 200
    assert b"MKTX" in resp.data


# ── GET /api/status ───────────────────────────────────────────────────────────

def test_api_status_returns_200(client):
    with patch("src.dashboard.load_portfolio", return_value=_MOCK_PORTFOLIO):
        resp = client.get("/api/status")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "trading_paused" in data
    assert "bankroll" in data


def test_api_status_paused_false_by_default(client):
    with patch("src.dashboard.load_portfolio", return_value=_MOCK_PORTFOLIO):
        resp = client.get("/api/status")
    data = json.loads(resp.data)
    assert data["trading_paused"] is False


def test_api_status_reflects_pause_state(client):
    paused_state = [False]
    configure(
        set_paused=lambda v: paused_state.__setitem__(0, v),
        get_paused=lambda: paused_state[0],
    )
    paused_state[0] = True
    with patch("src.dashboard.load_portfolio", return_value=_MOCK_PORTFOLIO):
        resp = client.get("/api/status")
    data = json.loads(resp.data)
    assert data["trading_paused"] is True


def test_api_status_includes_pnl_by_strategy(client):
    portfolio_with_closed = dict(_MOCK_PORTFOLIO)
    portfolio_with_closed["positions"] = [
        {**_MOCK_PORTFOLIO["positions"][0], "status": "closed", "pnl": 15.0, "strategy": "momentum"},
    ]
    with patch("src.dashboard.load_portfolio", return_value=portfolio_with_closed):
        resp = client.get("/api/status")
    data = json.loads(resp.data)
    assert data["pnl_by_strategy"].get("momentum") == 15.0


def test_api_status_handles_portfolio_error(client):
    with patch("src.dashboard.load_portfolio", side_effect=Exception("disk error")):
        resp = client.get("/api/status")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "portfolio_summary" in data


# ── GET /api/positions ────────────────────────────────────────────────────────

def test_api_positions_returns_200(client):
    with patch("src.dashboard.load_portfolio", return_value=_MOCK_PORTFOLIO):
        resp = client.get("/api/positions")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "positions" in data


def test_api_positions_returns_open_only(client):
    portfolio_mixed = dict(_MOCK_PORTFOLIO)
    portfolio_mixed["positions"] = [
        {**_MOCK_PORTFOLIO["positions"][0], "status": "open"},
        {**_MOCK_PORTFOLIO["positions"][0], "id": "closed-1", "status": "closed"},
    ]
    with patch("src.dashboard.load_portfolio", return_value=portfolio_mixed):
        resp = client.get("/api/positions")
    data = json.loads(resp.data)
    assert len(data["positions"]) == 1
    assert data["positions"][0]["status"] == "open"


# ── POST /pause ───────────────────────────────────────────────────────────────

def test_pause_returns_200(client):
    resp = client.post("/pause")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "paused"


def test_pause_sets_state(client):
    paused_state = [False]
    configure(
        set_paused=lambda v: paused_state.__setitem__(0, v),
        get_paused=lambda: paused_state[0],
    )
    client.post("/pause")
    assert paused_state[0] is True


# ── POST /resume ──────────────────────────────────────────────────────────────

def test_resume_returns_200(client):
    resp = client.post("/resume")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "active"


def test_resume_clears_state(client):
    paused_state = [True]
    configure(
        set_paused=lambda v: paused_state.__setitem__(0, v),
        get_paused=lambda: paused_state[0],
    )
    client.post("/resume")
    assert paused_state[0] is False


# ── record_signal / record_heartbeat ─────────────────────────────────────────

def test_record_signal_appended(client):
    record_signal({"action": "trade", "ticker": "A"})
    record_signal({"action": "skip", "ticker": "B"})
    with dashboard_module._state_lock:
        feed = dashboard_module._state["signal_feed"]
    assert len(feed) == 2
    assert feed[0]["ticker"] == "A"


def test_record_signal_capped_at_20(client):
    for i in range(25):
        record_signal({"action": "skip", "ticker": str(i)})
    with dashboard_module._state_lock:
        feed = dashboard_module._state["signal_feed"]
    assert len(feed) == 20
    # Should keep the most recent 20
    assert feed[-1]["ticker"] == "24"


def test_record_heartbeat(client):
    record_heartbeat()
    with dashboard_module._state_lock:
        hb = dashboard_module._state["last_heartbeat"]
    assert hb is not None
