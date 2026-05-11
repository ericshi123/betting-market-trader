from typing import Optional
"""
Auto-Resolve Checker — Phase 8
Polls Kalshi for finalized markets and closes matching paper positions.
"""

import time

import src.momentum_portfolio as momentum_mod
import src.portfolio as portfolio_mod
from src.client import kalshi_get


def _get_ticker(position: dict) -> Optional[str]:
    return position.get("ticker") or position.get("market_id") or None


def _fetch_market(ticker: str) -> Optional[dict]:
    try:
        data = kalshi_get(f"/markets/{ticker}")
        return data.get("market") or data
    except Exception:
        return None


def _resolve_outcome(result: Optional[str]) -> Optional[str]:
    if result is None:
        return None
    r = result.lower()
    if r == "yes":
        return "YES"
    if r == "no":
        return "NO"
    return None


def _exit_price(direction: str, outcome: str) -> float:
    if direction == "BUY_YES":
        return 1.0 if outcome == "YES" else 0.0
    return 1.0 if outcome == "NO" else 0.0


def check_and_resolve_all() -> list[dict]:
    """
    Check all open positions in both paper portfolios for finalized markets.
    Returns list of dicts: {ticker, portfolio, direction, outcome, pnl}
    """
    resolved = []

    portfolios = [
        ("regular", portfolio_mod.load_portfolio(), portfolio_mod.close_position),
        ("momentum", momentum_mod.load_portfolio(), momentum_mod.close_position),
    ]

    for portfolio_name, portfolio, close_fn in portfolios:
        open_positions = [p for p in portfolio["positions"] if p["status"] == "open"]

        for position in open_positions:
            ticker = _get_ticker(position)
            if not ticker:
                continue

            market = _fetch_market(ticker)
            time.sleep(0.5)

            if market is None:
                continue

            status = market.get("status", "")
            result = market.get("result")

            if status != "finalized" and not result:
                continue

            outcome = _resolve_outcome(result)
            if outcome is None:
                continue

            exit_p = _exit_price(position["direction"], outcome)

            try:
                closed = close_fn(portfolio, position["id"], outcome, exit_p)
                resolved.append(
                    {
                        "ticker": ticker,
                        "portfolio": portfolio_name,
                        "direction": position["direction"],
                        "outcome": outcome,
                        "pnl": closed["pnl"],
                    }
                )
            except Exception:
                continue

    return resolved
