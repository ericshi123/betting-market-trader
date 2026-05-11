#!/usr/bin/env python3
"""
Momentum paper trader — runs automatically on a cron schedule.

Each run:
  1. Fetches live Kalshi markets
  2. Saves a timestamped momentum snapshot
  3. Compares to a 1–6h-old snapshot to detect price moves
  4. Auto-places paper trades for markets with >= 5pp momentum
  5. Sends a Telegram summary

Run from project root:
  source .venv/bin/activate && python scripts/momentum_trader.py
"""

import os
import shutil
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from src.momentum import run_momentum_scan
from src.momentum_portfolio import (
    load_portfolio,
    open_position,
    portfolio_summary,
)

MAX_NEW_TRADES_PER_RUN = 3   # cap how many positions we open in one scan
TELEGRAM_TARGET = "8740704554"


def _ping_telegram(message: str) -> None:
    openclaw_bin = "/opt/homebrew/bin/openclaw"
    if not os.path.exists(openclaw_bin):
        openclaw_bin = shutil.which("openclaw") or ""
    if not openclaw_bin:
        print("[warn] openclaw binary not found, skipping Telegram ping")
        return
    try:
        subprocess.run(
            [
                openclaw_bin, "message", "send",
                "--channel", "telegram",
                "--target", TELEGRAM_TARGET,
                "--message", message,
            ],
            check=True,
            capture_output=True,
        )
    except Exception as e:
        print(f"[warn] Telegram ping failed: {e}")


def _already_open(portfolio: dict, ticker: str) -> bool:
    """Don't double-up on the same market."""
    for pos in portfolio["positions"]:
        if pos.get("ticker") == ticker and pos["status"] == "open":
            return True
    return False


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"=== Momentum Trader — {now} ===")

    # ── 1. Scan ───────────────────────────────────────────────────────────────
    try:
        markets, signals, snap_path = run_momentum_scan()
    except Exception as e:
        msg = f"🚨 Momentum trader scan failed: {e}"
        print(msg)
        _ping_telegram(msg)
        sys.exit(1)

    print(f"Fetched {len(markets)} markets, saved snapshot → {snap_path.name}")
    print(f"Signals detected: {len(signals)}")

    # ── 2. No baseline yet (first run) ───────────────────────────────────────
    if not signals and len(markets) > 0:
        print("No baseline snapshot available yet (need ≥1h between runs). "
              "Snapshot saved — signals will appear next run.")
        return  # Silent — not worth pinging Telegram

    # ── 3. Place paper trades ─────────────────────────────────────────────────
    portfolio = load_portfolio()
    placed = []
    skipped_dup = 0
    skipped_full = 0

    for signal in signals[:MAX_NEW_TRADES_PER_RUN]:
        ticker = signal["ticker"]

        if _already_open(portfolio, ticker):
            skipped_dup += 1
            continue

        pos = open_position(portfolio, signal)
        if pos is None:
            skipped_full += 1
            continue

        placed.append((signal, pos))
        print(
            f"  ✅ {signal['direction']} {ticker}  Δ{signal['delta']:+.1%}  "
            f"${pos['amount']:.2f} @ {pos['entry_price']:.2%}"
        )

    # ── 4. Build Telegram message ─────────────────────────────────────────────
    lines = [f"📈 *Momentum Paper Trader* — {now}"]

    if placed:
        lines.append(f"\n*{len(placed)} new position(s) opened:*")
        for sig, pos in placed:
            direction_emoji = "🟢" if sig["direction"] == "BUY_YES" else "🔴"
            lines.append(
                f"{direction_emoji} {sig['direction']} `{sig['ticker']}`\n"
                f"  {sig['question'][:70]}{'...' if len(sig['question'])>70 else ''}\n"
                f"  Move: {sig['baseline_price']:.2%} → {sig['yes_price']:.2%}  "
                f"(Δ{sig['delta']:+.1%})\n"
                f"  Stake: ${pos['amount']:.2f} @ {pos['entry_price']:.2%}"
            )
    else:
        lines.append("\nNo new positions — no qualifying signals or portfolio full.")

    if skipped_dup:
        lines.append(f"_(skipped {skipped_dup} already-open market(s))_")

    # Portfolio summary
    lines.append(f"\n{portfolio_summary(portfolio)}")

    if signals:
        lines.append(f"\n_Signals found: {len(signals)} | Shown top {MAX_NEW_TRADES_PER_RUN}_")
        # List remaining signals even if not traded
        for sig in signals[MAX_NEW_TRADES_PER_RUN:MAX_NEW_TRADES_PER_RUN+5]:
            lines.append(
                f"  ⬜ {sig['direction']} `{sig['ticker']}`  "
                f"Δ{sig['delta']:+.1%}  {sig['question'][:50]}"
            )

    message = "\n".join(lines)
    print("\n--- Telegram ---")
    print(message)
    _ping_telegram(message)
    print("Done.")


if __name__ == "__main__":
    main()
