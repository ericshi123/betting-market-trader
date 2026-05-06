#!/usr/bin/env python3
"""
Daily scan script for Polymarket Intelligence.
Run with venv active: source .venv/bin/activate && python scripts/daily_scan.py
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from src.markets import fetch_active_markets, filter_markets
from src.analyzer import estimate_probability
from src.edge import rank_markets
from src.betting import recommend_bet

SCAN_LIMIT = 15
MIN_VOLUME = 10_000
MIN_EDGE = 0.05
BANKROLL = 1000.0


def run_daily_scan():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"=== Polymarket Daily Scan — {today} ===")

    # Fetch + filter
    print("Fetching markets...")
    markets = fetch_active_markets(limit=200)
    markets = filter_markets(markets, min_volume=MIN_VOLUME, min_yes_price=0.05, max_yes_price=0.95)
    targets = markets[:SCAN_LIMIT]
    print(f"Analyzing {len(targets)} markets...")

    # Analyze
    analyzed = []
    for i, market in enumerate(targets):
        result = estimate_probability(market)
        analyzed.append({**market, **result})
        if i < len(targets) - 1:
            time.sleep(1)

    # Rank
    ranked = rank_markets(analyzed, min_confidence="medium", min_edge=MIN_EDGE)

    # Recommend
    recommendations = []
    for m in ranked:
        rec = recommend_bet(m, m, BANKROLL)
        if rec:
            recommendations.append(rec)
    recommendations.sort(key=lambda r: abs(r["edge"]), reverse=True)

    # Save report
    report_dir = PROJECT_ROOT / "data" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{today}.json"
    report = {
        "date": today,
        "scanned": len(targets),
        "edges_found": len(ranked),
        "recommendations": recommendations,
        "ranked_markets": ranked[:10],
    }
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # Print summary
    print(f"Scanned: {len(targets)} markets")
    print(f"Edges found: {len(ranked)}")
    if recommendations:
        print("Top recommendations:")
        for i, r in enumerate(recommendations[:5], 1):
            q = r["question"] or ""
            if len(q) > 60:
                q = q[:57] + "..."
            print(f"  {i}. [{r['direction']}] \"{q}\" — edge: {r['edge']*100:+.1f}pp, amount: ${r['amount']:.0f}, conf: {r['confidence']}")
    else:
        print("No recommendations met criteria.")
    print(f"Report saved to {report_path}")
    return report


if __name__ == "__main__":
    try:
        run_daily_scan()
        sys.exit(0)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
