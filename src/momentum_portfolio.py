from typing import Optional
"""
Momentum paper trading ledger.
Completely separate from the regular paper portfolio (data/portfolio.json).
Positions stored in data/momentum_portfolio.json.

Starting bankroll: $1,000 (isolated paper account for momentum strategy).
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import src.calibration as calibration

_PORTFOLIO_PATH = Path(__file__).parent.parent / "data" / "momentum_portfolio.json"

STARTING_BANKROLL = 1000.0
MAX_POSITION_SIZE = 50.0    # cap per trade
MAX_OPEN_POSITIONS = 10     # don't stack too many at once
KELLY_FRACTION = 0.10       # flat 10% of bankroll × signal strength proxy

_DEFAULT_PORTFOLIO = {
    "strategy": "momentum",
    "bankroll": STARTING_BANKROLL,
    "peak_bankroll": STARTING_BANKROLL,
    "positions": [],
    "closed_pnl": 0.0,
    "total_trades": 0,
    "wins": 0,
    "losses": 0,
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


def _size_bet(signal: dict, bankroll: float) -> float:
    """
    Size the bet proportional to the magnitude of the price move.
    Larger momentum = bigger stake, capped at MAX_POSITION_SIZE.
    """
    delta = signal.get("abs_delta", 0.05)
    # Scale: 5pp move → 5% of KELLY_FRACTION; 20pp move → 20% ...
    fraction = min(delta * 2, KELLY_FRACTION)   # 5pp → 10%, 10pp → 20%...capped
    raw = bankroll * fraction
    return round(min(raw, MAX_POSITION_SIZE), 2)


def open_position(portfolio: dict, signal: dict) -> Optional[dict]:
    """
    Open a momentum paper position. Returns the position dict or None if
    the portfolio is full or bankroll is insufficient.
    """
    open_count = sum(1 for p in portfolio["positions"] if p["status"] == "open")
    if open_count >= MAX_OPEN_POSITIONS:
        return None

    amount = _size_bet(signal, portfolio["bankroll"])
    if amount < 1.0 or portfolio["bankroll"] < amount:
        return None

    position = {
        "id":             str(uuid4()),
        "ticker":         signal.get("ticker", ""),
        "market_id":      signal.get("market_id", ""),
        "question":       signal.get("question", ""),
        "direction":      signal["direction"],
        "amount":         amount,
        "entry_price":    signal["entry_price"],
        "yes_price_at_entry": signal["yes_price"],
        "baseline_price": signal["baseline_price"],
        "delta":          signal["delta"],
        "strategy":       "momentum",
        "opened_at":      datetime.now(timezone.utc).isoformat(),
        "status":         "open",
        "exit_price":     None,
        "pnl":            None,
        "closed_at":      None,
    }

    portfolio["bankroll"] = round(portfolio["bankroll"] - amount, 2)
    portfolio["positions"].append(position)
    portfolio["total_trades"] = portfolio.get("total_trades", 0) + 1
    save_portfolio(portfolio)
    return position


def close_position(
    portfolio: dict, position_id: str, outcome: str, exit_price: float
) -> dict:
    """
    Close a momentum position. outcome = 'YES' or 'NO'.
    Returns the closed position dict.
    """
    match = None
    for pos in portfolio["positions"]:
        if pos["id"] == position_id or pos["id"].startswith(position_id):
            match = pos
            break
    if match is None:
        raise ValueError(f"Position not found: {position_id}")
    if match["status"] == "closed":
        raise ValueError(f"Already closed: {position_id}")

    amount = match["amount"]
    entry_price = match["entry_price"]
    direction = match["direction"]

    if direction == "BUY_YES":
        if outcome == "YES":
            pnl = round(amount * (1 / entry_price - 1), 2)
        else:
            pnl = -amount
    else:  # BUY_NO
        if outcome == "NO":
            pnl = round(amount * (1 / entry_price - 1), 2)
        else:
            pnl = -amount

    match["status"] = "closed"
    match["exit_price"] = exit_price
    match["pnl"] = pnl
    match["closed_at"] = datetime.now(timezone.utc).isoformat()

    portfolio["bankroll"] = round(portfolio["bankroll"] + amount + pnl, 2)
    portfolio["closed_pnl"] = round(portfolio.get("closed_pnl", 0) + pnl, 2)
    if pnl >= 0:
        portfolio["wins"] = portfolio.get("wins", 0) + 1
    else:
        portfolio["losses"] = portfolio.get("losses", 0) + 1
    portfolio["peak_bankroll"] = max(
        portfolio.get("peak_bankroll", STARTING_BANKROLL),
        portfolio["bankroll"],
    )
    save_portfolio(portfolio)
    try:
        calibration.record_resolution(match, "momentum")
    except Exception:
        pass
    return match


def portfolio_summary(portfolio: dict) -> str:
    """Return a human-readable summary string."""
    bankroll = portfolio["bankroll"]
    start = STARTING_BANKROLL
    pnl = round(bankroll - start + portfolio.get("closed_pnl", 0) -
                 sum(p["amount"] for p in portfolio["positions"] if p["status"] == "open"), 2)
    closed_pnl = portfolio.get("closed_pnl", 0)
    total = portfolio.get("total_trades", 0)
    wins = portfolio.get("wins", 0)
    losses = portfolio.get("losses", 0)
    open_pos = [p for p in portfolio["positions"] if p["status"] == "open"]

    win_rate = f"{100*wins//(wins+losses)}%" if (wins + losses) > 0 else "N/A"

    lines = [
        f"📊 Momentum Paper Portfolio",
        f"  Bankroll:    ${bankroll:.2f}  (started ${start:.2f})",
        f"  Realized P&L: ${closed_pnl:+.2f}",
        f"  Total trades: {total}  |  {wins}W / {losses}L  ({win_rate})",
        f"  Open positions: {len(open_pos)}",
    ]
    if open_pos:
        lines.append("")
        for p in open_pos:
            lines.append(
                f"  [{p['id'][:8]}] {p['direction']} {p['ticker']} "
                f"${p['amount']:.2f} @ {p['entry_price']:.2%}  Δ{p['delta']:+.1%}"
            )
    return "\n".join(lines)
