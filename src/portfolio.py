"""
Paper trading portfolio ledger.
Positions stored in data/portfolio.json.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import src.calibration as calibration

_PORTFOLIO_PATH = Path(__file__).parent.parent / "data" / "portfolio.json"

_DEFAULT_PORTFOLIO = {
    "bankroll": 1000.0,
    "positions": [],
    "closed_pnl": 0.0,
}


def load_portfolio() -> dict:
    if _PORTFOLIO_PATH.exists():
        with open(_PORTFOLIO_PATH) as f:
            return json.load(f)
    return dict(_DEFAULT_PORTFOLIO)


def save_portfolio(portfolio: dict) -> None:
    _PORTFOLIO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_PORTFOLIO_PATH, "w") as f:
        json.dump(portfolio, f, indent=2)


def open_position(portfolio: dict, recommendation: dict) -> dict:
    """Deduct amount from bankroll, append position. Returns the new position."""
    amount = recommendation["amount"]
    position = {
        "id": str(uuid4()),
        "market_id": recommendation.get("market_id"),
        "question": recommendation.get("question", ""),
        "direction": recommendation["direction"],
        "amount": amount,
        "entry_price": recommendation["market_prob"],
        "model_prob": recommendation["model_prob"],
        "edge": recommendation["edge"],
        "confidence": recommendation["confidence"],
        "rationale": recommendation.get("rationale", ""),
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "status": "open",
        "exit_price": None,
        "pnl": None,
        "closed_at": None,
    }
    portfolio["bankroll"] = round(portfolio["bankroll"] - amount, 2)
    portfolio["positions"].append(position)
    save_portfolio(portfolio)
    return position


def close_position(
    portfolio: dict, position_id: str, outcome: str, exit_price: float
) -> dict:
    """
    Close a position by full ID or 8-char prefix. Returns the closed position.

    PnL:
      BUY_YES + YES: amount * (1/entry_price - 1)
      BUY_YES + NO:  -amount
      BUY_NO  + NO:  amount * (1/(1-entry_price) - 1)
      BUY_NO  + YES: -amount
    """
    match = None
    for pos in portfolio["positions"]:
        if pos["id"] == position_id or pos["id"].startswith(position_id):
            match = pos
            break
    if match is None:
        raise ValueError(f"Position not found: {position_id}")
    if match["status"] == "closed":
        raise ValueError(f"Position already closed: {position_id}")

    entry = match["entry_price"]
    amount = match["amount"]
    direction = match["direction"]
    outcome = outcome.upper()

    if direction == "BUY_YES":
        pnl = amount * (1 / entry - 1) if outcome == "YES" else -amount
    else:  # BUY_NO
        pnl = amount * (1 / (1 - entry) - 1) if outcome == "NO" else -amount

    pnl = round(pnl, 2)
    match["status"] = "closed"
    match["exit_price"] = exit_price
    match["pnl"] = pnl
    match["closed_at"] = datetime.now(timezone.utc).isoformat()

    portfolio["bankroll"] = round(portfolio["bankroll"] + amount + pnl, 2)
    portfolio["closed_pnl"] = round(portfolio.get("closed_pnl", 0.0) + pnl, 2)
    save_portfolio(portfolio)
    try:
        calibration.record_resolution(match, "paper")
    except Exception:
        pass
    return match


def portfolio_summary(portfolio: dict) -> dict:
    open_positions = [p for p in portfolio["positions"] if p["status"] == "open"]
    closed_positions = [p for p in portfolio["positions"] if p["status"] == "closed"]
    open_exposure = sum(p["amount"] for p in open_positions)
    return {
        "bankroll": portfolio["bankroll"],
        "open_count": len(open_positions),
        "closed_count": len(closed_positions),
        "total_pnl": portfolio.get("closed_pnl", 0.0),
        "open_exposure": round(open_exposure, 2),
    }
