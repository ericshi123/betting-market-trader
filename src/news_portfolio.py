"""
News paper trading ledger.
Completely separate from the regular and momentum portfolios.
Positions stored in data/news_portfolio.json.

Starting bankroll: $1,000 (isolated paper account for news strategy).
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_PORTFOLIO_PATH = Path(__file__).parent.parent / "data" / "news_portfolio.json"

STARTING_BANKROLL = 1000.0
MAX_POSITION_SIZE = 40.0
MAX_OPEN_POSITIONS = 10
KELLY_FRACTION = 0.10

_DEFAULT_PORTFOLIO = {
    "strategy": "news",
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


def _size_bet(edge: float, bankroll: float) -> float:
    """Size bet proportional to edge magnitude, capped at MAX_POSITION_SIZE."""
    fraction = min(edge * KELLY_FRACTION, KELLY_FRACTION)
    raw = bankroll * fraction
    return round(min(raw, MAX_POSITION_SIZE), 2)


def open_position(portfolio: dict, signal: dict) -> dict | None:
    """
    Open a news paper position. Returns the position dict or None if
    the portfolio is full or bankroll is insufficient.
    """
    open_count = sum(1 for p in portfolio["positions"] if p["status"] == "open")
    if open_count >= MAX_OPEN_POSITIONS:
        return None

    edge = signal.get("edge", 0.08)
    amount = _size_bet(edge, portfolio["bankroll"])
    if amount < 1.0 or portfolio["bankroll"] < amount:
        return None

    position = {
        "id": str(uuid4()),
        "ticker": signal.get("ticker", ""),
        "market_id": signal.get("market_id", ""),
        "question": signal.get("question", ""),
        "direction": signal["direction"],
        "amount": amount,
        "entry_price": signal["entry_price"],
        "yes_price_at_entry": signal.get("yes_price", signal["entry_price"]),
        "edge": edge,
        "model_prob": signal.get("model_prob"),
        "confidence": signal.get("confidence", ""),
        "headline": signal.get("headline", ""),
        "headline_url": signal.get("headline_url", ""),
        "strategy": "news",
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "status": "open",
        "exit_price": None,
        "pnl": None,
        "closed_at": None,
    }

    portfolio["bankroll"] = round(portfolio["bankroll"] - amount, 2)
    portfolio["positions"].append(position)
    portfolio["total_trades"] = portfolio.get("total_trades", 0) + 1
    save_portfolio(portfolio)
    return position


def close_position(
    portfolio: dict, position_id: str, outcome: str, exit_price: float
) -> dict:
    """Close a news position. outcome = 'YES' or 'NO'."""
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
        pnl = round(amount * (1 / entry_price - 1), 2) if outcome == "YES" else -amount
    else:
        pnl = round(amount * (1 / entry_price - 1), 2) if outcome == "NO" else -amount

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
    return match


def portfolio_summary(portfolio: dict) -> str:
    bankroll = portfolio["bankroll"]
    closed_pnl = portfolio.get("closed_pnl", 0)
    total = portfolio.get("total_trades", 0)
    wins = portfolio.get("wins", 0)
    losses = portfolio.get("losses", 0)
    open_pos = [p for p in portfolio["positions"] if p["status"] == "open"]
    win_rate = f"{100*wins//(wins+losses)}%" if (wins + losses) > 0 else "N/A"

    lines = [
        f"📰 News Paper Portfolio",
        f"  Bankroll:     ${bankroll:.2f}  (started ${STARTING_BANKROLL:.2f})",
        f"  Realized P&L: ${closed_pnl:+.2f}",
        f"  Total trades: {total}  |  {wins}W / {losses}L  ({win_rate})",
        f"  Open positions: {len(open_pos)}",
    ]
    if open_pos:
        lines.append("")
        for p in open_pos:
            lines.append(
                f"  [{p['id'][:8]}] {p['direction']} {p['ticker']} "
                f"${p['amount']:.2f} @ {p['entry_price']:.2%}"
            )
    return "\n".join(lines)
