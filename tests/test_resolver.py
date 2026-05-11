from unittest.mock import patch

import src.resolver as resolver_mod
from src.resolver import check_and_resolve_all


def _pos(
    pos_id="pos-001",
    ticker="MKTX-01",
    direction="BUY_YES",
    status="open",
    entry_price=0.60,
):
    return {
        "id": pos_id,
        "ticker": ticker,
        "market_id": ticker,
        "direction": direction,
        "amount": 20.0,
        "entry_price": entry_price,
        "status": status,
        "exit_price": None,
        "pnl": None,
        "closed_at": None,
    }


def _portfolio(positions):
    return {"bankroll": 980.0, "positions": positions, "closed_pnl": 0.0}


def _mom_portfolio(positions):
    return {
        "strategy": "momentum",
        "bankroll": 980.0,
        "positions": positions,
        "closed_pnl": 0.0,
    }


def _finalized(result="yes"):
    return {"market": {"status": "finalized", "result": result}}


def _closed(position, pnl, exit_price):
    return {**position, "status": "closed", "exit_price": exit_price, "pnl": pnl}


# ── Test 1: BUY_YES + YES → exit_price=1.0, outcome=YES ──────────────────────


def test_resolves_yes_position():
    p = _pos()
    portfolio = _portfolio([p])
    closed_p = _closed(p, pnl=13.33, exit_price=1.0)

    with (
        patch("src.resolver.portfolio_mod.load_portfolio", return_value=portfolio),
        patch("src.resolver.momentum_mod.load_portfolio", return_value=_mom_portfolio([])),
        patch("src.resolver.kalshi_get", return_value=_finalized("yes")),
        patch("src.resolver.portfolio_mod.close_position", return_value=closed_p) as mock_close,
        patch("time.sleep"),
    ):
        result = check_and_resolve_all()

    assert len(result) == 1
    r = result[0]
    assert r["ticker"] == "MKTX-01"
    assert r["outcome"] == "YES"
    assert r["portfolio"] == "regular"
    assert r["pnl"] == 13.33
    mock_close.assert_called_once_with(portfolio, "pos-001", "YES", 1.0)


# ── Test 2: BUY_NO + NO → exit_price=1.0 ─────────────────────────────────────


def test_resolves_no_position():
    p = _pos(direction="BUY_NO", entry_price=0.40)
    portfolio = _portfolio([p])
    closed_p = _closed(p, pnl=13.33, exit_price=1.0)

    with (
        patch("src.resolver.portfolio_mod.load_portfolio", return_value=portfolio),
        patch("src.resolver.momentum_mod.load_portfolio", return_value=_mom_portfolio([])),
        patch("src.resolver.kalshi_get", return_value=_finalized("no")),
        patch("src.resolver.portfolio_mod.close_position", return_value=closed_p) as mock_close,
        patch("time.sleep"),
    ):
        result = check_and_resolve_all()

    assert result[0]["outcome"] == "NO"
    mock_close.assert_called_once_with(portfolio, p["id"], "NO", 1.0)


# ── Test 3: BUY_YES + NO → exit_price=0.0 (losing side) ─────────────────────


def test_losing_position_exit_price_zero():
    p = _pos(direction="BUY_YES")
    portfolio = _portfolio([p])
    closed_p = _closed(p, pnl=-20.0, exit_price=0.0)

    with (
        patch("src.resolver.portfolio_mod.load_portfolio", return_value=portfolio),
        patch("src.resolver.momentum_mod.load_portfolio", return_value=_mom_portfolio([])),
        patch("src.resolver.kalshi_get", return_value=_finalized("no")),
        patch("src.resolver.portfolio_mod.close_position", return_value=closed_p) as mock_close,
        patch("time.sleep"),
    ):
        check_and_resolve_all()

    mock_close.assert_called_once_with(portfolio, p["id"], "NO", 0.0)


# ── Test 4: positions in both portfolios are checked ─────────────────────────


def test_checks_both_portfolios():
    reg_p = _pos(pos_id="reg-001", ticker="REG-01")
    mom_p = _pos(pos_id="mom-001", ticker="MOM-01")

    reg_portfolio = _portfolio([reg_p])
    mom_portfolio = _mom_portfolio([mom_p])

    closed_reg = _closed(reg_p, pnl=5.0, exit_price=1.0)
    closed_mom = _closed(mom_p, pnl=5.0, exit_price=1.0)

    with (
        patch("src.resolver.portfolio_mod.load_portfolio", return_value=reg_portfolio),
        patch("src.resolver.momentum_mod.load_portfolio", return_value=mom_portfolio),
        patch("src.resolver.kalshi_get", return_value=_finalized("yes")),
        patch("src.resolver.portfolio_mod.close_position", return_value=closed_reg),
        patch("src.resolver.momentum_mod.close_position", return_value=closed_mom),
        patch("time.sleep"),
    ):
        result = check_and_resolve_all()

    portfolios_seen = {r["portfolio"] for r in result}
    assert portfolios_seen == {"regular", "momentum"}


# ── Test 5: API error → position skipped gracefully ──────────────────────────


def test_api_error_skips_position():
    p = _pos()
    portfolio = _portfolio([p])

    with (
        patch("src.resolver.portfolio_mod.load_portfolio", return_value=portfolio),
        patch("src.resolver.momentum_mod.load_portfolio", return_value=_mom_portfolio([])),
        patch("src.resolver.kalshi_get", side_effect=Exception("404 Not Found")),
        patch("time.sleep"),
    ):
        result = check_and_resolve_all()

    assert result == []


# ── Test 6: already-closed positions are not re-checked ──────────────────────


def test_skips_closed_positions():
    p = _pos(status="closed")
    portfolio = _portfolio([p])

    with (
        patch("src.resolver.portfolio_mod.load_portfolio", return_value=portfolio),
        patch("src.resolver.momentum_mod.load_portfolio", return_value=_mom_portfolio([])),
        patch("src.resolver.kalshi_get") as mock_get,
        patch("time.sleep"),
    ):
        result = check_and_resolve_all()

    mock_get.assert_not_called()
    assert result == []


# ── Test 7: open (non-finalized) market → not resolved ───────────────────────


def test_active_market_not_resolved():
    p = _pos()
    portfolio = _portfolio([p])
    active_market = {"market": {"status": "open", "result": None}}

    with (
        patch("src.resolver.portfolio_mod.load_portfolio", return_value=portfolio),
        patch("src.resolver.momentum_mod.load_portfolio", return_value=_mom_portfolio([])),
        patch("src.resolver.kalshi_get", return_value=active_market),
        patch("time.sleep"),
    ):
        result = check_and_resolve_all()

    assert result == []


# ── Test 8: position missing both ticker and market_id → skipped ─────────────


def test_missing_ticker_skipped():
    p = _pos()
    p.pop("ticker")
    p.pop("market_id")
    portfolio = _portfolio([p])

    with (
        patch("src.resolver.portfolio_mod.load_portfolio", return_value=portfolio),
        patch("src.resolver.momentum_mod.load_portfolio", return_value=_mom_portfolio([])),
        patch("src.resolver.kalshi_get") as mock_get,
        patch("time.sleep"),
    ):
        result = check_and_resolve_all()

    mock_get.assert_not_called()
    assert result == []


# ── Test 9: rate limit sleep called between API calls ────────────────────────


def test_rate_limit_sleep_called():
    p1 = _pos(pos_id="p1", ticker="MKT-01")
    p2 = _pos(pos_id="p2", ticker="MKT-02")
    portfolio = _portfolio([p1, p2])
    active = {"market": {"status": "open", "result": None}}

    with (
        patch("src.resolver.portfolio_mod.load_portfolio", return_value=portfolio),
        patch("src.resolver.momentum_mod.load_portfolio", return_value=_mom_portfolio([])),
        patch("src.resolver.kalshi_get", return_value=active),
        patch("time.sleep") as mock_sleep,
    ):
        check_and_resolve_all()

    assert mock_sleep.call_count == 2
    mock_sleep.assert_called_with(0.5)
