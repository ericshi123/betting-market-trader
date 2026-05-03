#!/usr/bin/env python3
"""
Backtest Claude's prediction accuracy on resolved Polymarket markets.
Usage: python scripts/backtest.py --limit 30 --days-back 90 --min-volume 10000
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

import requests

from src.analyzer import estimate_probability


def fetch_resolved_markets(days_back=90, min_volume=10000, limit=50):
    """Fetch recently resolved binary markets."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    r = requests.get(
        "https://gamma-api.polymarket.com/events",
        params={"closed": "true", "limit": 100, "order": "volume", "ascending": "false"},
    )
    r.raise_for_status()
    events = r.json()

    markets = []
    for event in events:
        for m in event.get("markets", []):
            op = m.get("outcomePrices", [])
            try:
                prices = [float(p) for p in (op if isinstance(op, list) else json.loads(op))]
            except Exception:
                continue
            if len(prices) != 2:
                continue

            if prices[0] >= 0.99:
                actual_outcome = 1
            elif prices[0] <= 0.01:
                actual_outcome = 0
            else:
                continue

            try:
                vol = float(m.get("volume") or 0)
            except Exception:
                vol = 0
            if vol < min_volume:
                continue

            end_date_str = m.get("endDate") or m.get("endDateIso") or ""
            try:
                end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                if end_dt < cutoff:
                    continue
            except Exception:
                continue

            markets.append({
                "market_id": m.get("conditionId", ""),
                "question": m.get("question", ""),
                "description": m.get("description", ""),
                "end_date": end_date_str[:10],
                "volume": vol,
                "yes_price": None,  # blind test — do not leak resolved price
                "actual_outcome": actual_outcome,
            })

            if len(markets) >= limit:
                return markets
    return markets


def compute_simulated_pnl(results):
    """
    Simulate betting $25 at a hypothetical 50% market price using half-Kelly.
    Kelly fraction = (p - 0.5) / 0.5  (b=1 for even-money bet).
    Bet = $25 * Kelly (floored at 0).
    """
    total_pnl = 0.0
    for r in results:
        p = r["model_prob"]
        if p is None:
            continue
        kelly = (p - 0.5) / 0.5
        bet = 25.0 * max(kelly, 0.0)
        outcome = r["actual_outcome"]
        if bet > 0:
            total_pnl += bet if outcome == 1 else -bet
    return total_pnl


def run_backtest(limit=30, days_back=90, min_volume=10000):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"=== Polymarket Backtest — {today} ===")
    print(f"Fetching up to {limit} resolved markets (last {days_back} days, vol >= ${min_volume:,})...")

    markets = fetch_resolved_markets(days_back=days_back, min_volume=min_volume, limit=limit)
    print(f"Found {len(markets)} qualifying markets. Analyzing...\n")

    results = []
    for i, market in enumerate(markets):
        result = estimate_probability(market)
        model_prob = result.get("model_prob")
        actual_outcome = market["actual_outcome"]

        correct = None
        if model_prob is not None:
            correct = (model_prob > 0.5) == (actual_outcome == 1)

        results.append({
            "question": market["question"],
            "end_date": market["end_date"],
            "volume": market["volume"],
            "model_prob": model_prob,
            "confidence": result.get("confidence"),
            "actual_outcome": actual_outcome,
            "correct": correct,
            "rationale": result.get("rationale", ""),
        })

        if i < len(markets) - 1:
            time.sleep(1)

    # Stats
    scored = [r for r in results if r["correct"] is not None]
    n = len(scored)
    accuracy = sum(r["correct"] for r in scored) / n if n else 0.0
    brier = (
        sum((r["model_prob"] - r["actual_outcome"]) ** 2 for r in scored) / n
        if n else 0.25
    )
    simulated_pnl = compute_simulated_pnl(scored)

    # Print results table
    print("Results:")
    for r in results:
        mark = "✓" if r["correct"] else ("✗" if r["correct"] is not None else "?")
        outcome_label = "YES" if r["actual_outcome"] == 1 else "NO"
        prob_pct = f"{r['model_prob'] * 100:.0f}%" if r["model_prob"] is not None else "N/A"
        question_trunc = r["question"][:70] + ("..." if len(r["question"]) > 70 else "")
        print(f"  {mark} [{outcome_label}]  \"{question_trunc}\" — model: {prob_pct}, outcome: {outcome_label}")

    print()
    print(f"Markets tested: {n}")
    print(f"Accuracy: {accuracy * 100:.1f}% (baseline: 50.0%)")
    print(f"Brier score: {brier:.3f} (baseline: 0.250, lower is better)")
    print(f"Simulated P&L (vs 50% market): ${simulated_pnl:.2f}")
    print()
    print("⚠️  Contamination warning: Claude's training data may include outcomes for")
    print("    markets resolved before its training cutoff. Results may overstate real edge.")
    print("    Weight recent markets (last 30 days) more heavily.")

    # Save report
    out_dir = Path("data/backtest")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{today}.json"
    report = {
        "date": today,
        "markets_tested": n,
        "accuracy": accuracy,
        "brier_score": brier,
        "simulated_pnl": simulated_pnl,
        "results": results,
    }
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nReport saved to {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Backtest Claude predictions on resolved Polymarket markets.")
    parser.add_argument("--limit", type=int, default=30, help="Max markets to test (default: 30)")
    parser.add_argument("--days-back", type=int, default=90, help="Look back window in days (default: 90)")
    parser.add_argument("--min-volume", type=int, default=10000, help="Min USD volume filter (default: 10000)")
    args = parser.parse_args()

    run_backtest(limit=args.limit, days_back=args.days_back, min_volume=args.min_volume)


if __name__ == "__main__":
    main()
