#!/usr/bin/env python3
"""
News-triggered paper trader — runs every 10 minutes via cron.

Each run:
  1. Fetch new RSS headlines (published in last 2h, not previously seen)
  2. Fetch active Kalshi markets
  3. For each headline, match up to 3 markets, evaluate with Claude
  4. Place paper trade if edge >= 0.08 and confidence medium/high
  5. Send Telegram ping only if trades were placed

Run from project root:
  source .venv/bin/activate && python scripts/news_trader.py
"""

import os
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from src import news_monitor, analyzer
from src.markets import fetch_active_markets
from src.news_portfolio import load_portfolio, open_position, portfolio_summary

MAX_MARKETS_PER_HEADLINE = 3
MIN_EDGE = 0.08
QUALIFYING_CONFIDENCE = {"medium", "high"}
MAX_TRADES_PER_RUN = 5
TELEGRAM_TARGET = "8740704554"


def _ping_telegram(message: str) -> None:
    try:
        subprocess.run(
            [
                "openclaw", "message", "send",
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
    for pos in portfolio["positions"]:
        if pos.get("ticker") == ticker and pos["status"] == "open":
            return True
    return False


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"=== News Trader — {now} ===")

    # ── 1. Fetch headlines ─────────────────────────────────────────────────────
    try:
        headlines = news_monitor.fetch_headlines()
    except Exception as e:
        print(f"[error] fetch_headlines failed: {e}")
        headlines = []

    print(f"Headlines fetched: {len(headlines)}")
    if not headlines:
        print("No new headlines — exiting silently.")
        return

    # ── 2. Fetch markets ───────────────────────────────────────────────────────
    try:
        markets = fetch_active_markets()
    except Exception as e:
        print(f"[error] fetch_active_markets failed: {e}")
        return

    print(f"Active markets: {len(markets)}")

    # ── 3. Evaluate & trade ────────────────────────────────────────────────────
    portfolio = load_portfolio()
    placed = []
    decisions = []

    for headline in headlines:
        if len(placed) >= MAX_TRADES_PER_RUN:
            break

        matched = news_monitor.match_markets(headline, markets)
        if not matched:
            continue

        print(f"\n[headline] {headline['title'][:80]}")
        print(f"  Matched {len(matched)} market(s)")

        for market in matched[:MAX_MARKETS_PER_HEADLINE]:
            ticker = market.get("market_id", "")
            question = market.get("question", "")
            yes_price = market.get("yes_price") or 0.0

            if not yes_price or yes_price <= 0.01 or yes_price >= 0.99:
                print(f"  SKIP {ticker}: price out of range ({yes_price:.2%})")
                continue

            try:
                result = analyzer.estimate_probability(market)
            except Exception as e:
                print(f"  SKIP {ticker}: analyzer error — {e}")
                continue

            model_prob = result.get("model_prob")
            confidence = result.get("confidence", "low")
            rationale = result.get("rationale", "")

            if model_prob is None:
                print(f"  SKIP {ticker}: no model_prob")
                continue

            # Determine direction and edge
            if model_prob > yes_price:
                direction = "BUY_YES"
                edge = model_prob - yes_price
                entry_price = yes_price
            else:
                direction = "BUY_NO"
                edge = yes_price - model_prob
                entry_price = 1.0 - yes_price

            decisions.append({
                "headline": headline["title"][:80],
                "ticker": ticker,
                "direction": direction,
                "edge": round(edge, 4),
                "confidence": confidence,
                "model_prob": model_prob,
                "yes_price": yes_price,
            })

            print(
                f"  {ticker}: model={model_prob:.2%} market={yes_price:.2%} "
                f"edge={edge:+.2%} conf={confidence}"
            )
            print(f"  Rationale: {rationale[:100]}")

            if edge < MIN_EDGE or confidence not in QUALIFYING_CONFIDENCE:
                print(f"  SKIP: edge {edge:.2%} < {MIN_EDGE:.0%} or conf={confidence}")
                continue

            if _already_open(portfolio, ticker):
                print(f"  SKIP: already have open position in {ticker}")
                continue

            signal = {
                "ticker": ticker,
                "market_id": ticker,
                "question": question,
                "direction": direction,
                "entry_price": entry_price,
                "yes_price": yes_price,
                "edge": edge,
                "model_prob": model_prob,
                "confidence": confidence,
                "headline": headline["title"],
                "headline_url": headline.get("url", ""),
            }

            pos = open_position(portfolio, signal)
            if pos is None:
                print(f"  SKIP: portfolio full or insufficient bankroll")
                continue

            placed.append((headline, signal, pos))
            print(
                f"  TRADE: {direction} {ticker} ${pos['amount']:.2f} @ {entry_price:.2%}"
            )

    # ── 4. Telegram — only if trades placed ───────────────────────────────────
    print(f"\nTrades placed this run: {len(placed)}")

    if placed:
        lines = [f"📰 *News Trader* — {now}", f"\n*{len(placed)} trade(s) placed:*"]
        for headline, sig, pos in placed:
            lines.append(
                f"📰 News Trade: {headline['title'][:60]} -> "
                f"{sig['direction']} {sig['ticker']} ${pos['amount']:.2f}"
            )
        lines.append(f"\n{portfolio_summary(portfolio)}")
        _ping_telegram("\n".join(lines))

    print("Done.")


if __name__ == "__main__":
    main()
