#!/usr/bin/env python3
"""
Snapshot-based momentum strategy backtester.

Replays consecutive daily snapshots from data/snapshots/ and simulates the
momentum strategy: enter on 5pp+ price moves, exit at the next snapshot price.

Note: simplified backtest using daily snapshot prices — does not model order
flow, slippage, or intraday resolution. Treat results as directional only.

Usage: python scripts/backtest.py
"""
import json
import math
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

SNAPSHOTS_DIR = PROJECT_ROOT / "data" / "snapshots"
MIN_DELTA_PP = 0.05  # 5 percentage points


def load_snapshots() -> list[dict]:
    """Load all snapshot files sorted by filename (YYYY-MM-DD.json)."""
    if not SNAPSHOTS_DIR.exists():
        return []
    files = sorted(SNAPSHOTS_DIR.glob("*.json"))
    snapshots = []
    for f in files:
        with open(f) as fh:
            data = json.load(fh)
        markets = {m["market_id"]: m for m in data.get("markets", [])}
        snapshots.append({"date": data.get("date", f.stem), "markets": markets})
    return snapshots


def simulate_momentum(snapshots: list[dict]) -> list[dict]:
    """
    For each consecutive snapshot pair, simulate momentum trades.
    Enter when |yes_price delta| >= MIN_DELTA_PP; exit at next snapshot price.
    """
    trades = []
    for i in range(len(snapshots) - 1):
        snap_a = snapshots[i]
        snap_b = snapshots[i + 1]
        shared = set(snap_a["markets"]) & set(snap_b["markets"])

        for market_id in shared:
            m_a = snap_a["markets"][market_id]
            m_b = snap_b["markets"][market_id]
            price_a = m_a.get("yes_price", 0.0)
            price_b = m_b.get("yes_price", 0.0)
            delta = price_b - price_a

            if abs(delta) < MIN_DELTA_PP:
                continue

            direction = "BUY_YES" if delta > 0 else "BUY_NO"
            entry = price_a if direction == "BUY_YES" else (1.0 - price_a)
            exit_p = price_b if direction == "BUY_YES" else (1.0 - price_b)

            pnl_pct = (exit_p - entry) / entry if entry > 0 else 0.0

            trades.append({
                "date_from": snap_a["date"],
                "date_to": snap_b["date"],
                "market_id": market_id,
                "question": m_a.get("question", "")[:60],
                "direction": direction,
                "delta_pp": round(delta * 100, 2),
                "entry": round(entry, 4),
                "exit": round(exit_p, 4),
                "pnl_pct": round(pnl_pct, 4),
                "win": pnl_pct > 0,
            })
    return trades


def _sharpe(returns: list[float]) -> float:
    """Annualized Sharpe from daily per-trade returns."""
    if len(returns) < 2:
        return 0.0
    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(variance) if variance > 0 else 0.0
    return round((mean / std) * math.sqrt(252), 2) if std > 0 else 0.0


def print_summary(trades: list[dict]) -> None:
    n = len(trades)
    if n == 0:
        print("No qualifying trades found (no 5pp+ price moves across snapshot pairs).")
        return
    wins = sum(1 for t in trades if t["win"])
    win_rate = wins / n
    total_pnl = sum(t["pnl_pct"] for t in trades)
    s = _sharpe([t["pnl_pct"] for t in trades])

    print(f"\n{'Strategy':<15} {'Trades':>7} {'Win Rate':>9} {'Total PnL':>10} {'Sharpe':>8}")
    print("-" * 55)
    print(
        f"{'Momentum':15} {n:7d} {win_rate*100:8.1f}% "
        f"{total_pnl*100:+9.1f}pp {s:8.2f}"
    )
    print()
    print("Note: simplified backtest using daily snapshot prices.")
    print("Does not model slippage, order flow, or intraday resolution.")


def main():
    print("=== Snapshot Momentum Backtester ===")
    print(f"Snapshot dir: {SNAPSHOTS_DIR}")

    snapshots = load_snapshots()
    print(f"Snapshots loaded: {len(snapshots)}")

    if len(snapshots) < 2:
        print(
            "Insufficient snapshots (need 2+). "
            "Accumulate daily snapshots to run a meaningful backtest."
        )
        return

    trades = simulate_momentum(snapshots)
    print(f"Trades simulated: {len(trades)}")
    print_summary(trades)


if __name__ == "__main__":
    main()
