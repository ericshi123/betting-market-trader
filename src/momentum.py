"""
Momentum strategy for Kalshi paper trading.

Detects markets where yes_price has moved significantly over a lookback window
by comparing current live prices to the most recent saved snapshot.

Signal logic:
  - Price rose   >= THRESHOLD: BUY_YES (momentum up)
  - Price fell   >= THRESHOLD: BUY_NO  (momentum down, NO gaining)

Only trade markets with sufficient volume and a price in the tradeable range.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.markets import fetch_active_markets, filter_markets
from src.storage import SNAPSHOTS_DIR

# ── Tunable parameters ────────────────────────────────────────────────────────
PRICE_MOVE_THRESHOLD = 0.05   # 5pp move required to trigger a signal
MIN_VOLUME           = 10     # minimum Kalshi contracts
MIN_YES_PRICE        = 0.07   # skip near-0 markets
MAX_YES_PRICE        = 0.93   # skip near-1 markets
SCAN_LIMIT           = 100    # how many markets to fetch
# ─────────────────────────────────────────────────────────────────────────────


def _load_snapshot_prices() -> dict[str, float]:
    """
    Load yes_price keyed by ticker from the most recent snapshot file.
    Returns empty dict if no snapshots exist.
    """
    files = sorted(SNAPSHOTS_DIR.glob("*.json"), reverse=True)
    if not files:
        return {}
    with open(files[0]) as f:
        data = json.load(f)
    markets = data.get("markets", [])
    return {
        m["market_id"]: m["yes_price"]
        for m in markets
        if "market_id" in m and "yes_price" in m
    }


def _save_momentum_snapshot(markets: list[dict]) -> Path:
    """
    Save a momentum-specific snapshot with timestamp in filename so
    multiple snapshots per day are preserved for delta calculation.
    """
    snap_dir = SNAPSHOTS_DIR.parent / "momentum_snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M")
    path = snap_dir / f"{stamp}.json"
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "count": len(markets),
        "markets": [
            {"ticker": m.get("market_id"), "yes_price": m.get("yes_price", 0.0)}
            for m in markets
        ],
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    return path


def _load_momentum_snapshot_prices(max_age_hours: int = 6) -> dict[str, float]:
    """
    Load prices from the most recent momentum snapshot that is at least
    1 hour old (so we're comparing across a real interval).
    Returns empty dict if none found within max_age_hours.
    """
    snap_dir = SNAPSHOTS_DIR.parent / "momentum_snapshots"
    if not snap_dir.exists():
        return {}
    files = sorted(snap_dir.glob("*.json"), reverse=True)
    now = datetime.now(timezone.utc)
    for path in files:
        with open(path) as f:
            data = json.load(f)
        ts = data.get("timestamp")
        if not ts:
            continue
        file_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        age_hours = (now - file_time).total_seconds() / 3600
        if 1.0 <= age_hours <= max_age_hours:
            return {m["ticker"]: m["yes_price"] for m in data.get("markets", [])}
    return {}


def detect_momentum_signals(
    current_markets: list[dict],
    baseline_prices: dict[str, float],
) -> list[dict]:
    """
    Compare current market prices to baseline_prices.
    Returns a list of signal dicts for markets that moved >= PRICE_MOVE_THRESHOLD.
    """
    signals = []
    for m in current_markets:
        ticker = m.get("market_id")
        if not ticker or ticker not in baseline_prices:
            continue
        current_price = m.get("yes_price", 0.0)
        baseline_price = baseline_prices[ticker]
        delta = current_price - baseline_price

        if abs(delta) < PRICE_MOVE_THRESHOLD:
            continue

        direction = "BUY_YES" if delta > 0 else "BUY_NO"
        entry_price = current_price if direction == "BUY_YES" else (1 - current_price)

        signals.append({
            "ticker":        ticker,
            "market_id":     ticker,
            "question":      m.get("question", ""),
            "direction":     direction,
            "yes_price":     current_price,
            "baseline_price": baseline_price,
            "delta":         round(delta, 4),
            "abs_delta":     round(abs(delta), 4),
            "entry_price":   round(entry_price, 4),
            "volume":        m.get("volume", 0),
            "end_date":      m.get("end_date", ""),
            "strategy":      "momentum",
        })

    # Strongest moves first
    signals.sort(key=lambda s: s["abs_delta"], reverse=True)
    return signals


def run_momentum_scan() -> tuple[list[dict], list[dict], Path]:
    """
    Full pipeline:
      1. Fetch live markets from Kalshi
      2. Save a new momentum snapshot
      3. Load a recent baseline (1–6h old)
      4. Detect signals

    Returns (current_markets, signals, snapshot_path).
    """
    markets = fetch_active_markets(limit=SCAN_LIMIT)
    markets = filter_markets(
        markets,
        min_volume=MIN_VOLUME,
        min_yes_price=MIN_YES_PRICE,
        max_yes_price=MAX_YES_PRICE,
    )

    snapshot_path = _save_momentum_snapshot(markets)
    baseline = _load_momentum_snapshot_prices(max_age_hours=6)

    if not baseline:
        # No usable baseline yet — return empty signals (first run)
        return markets, [], snapshot_path

    signals = detect_momentum_signals(markets, baseline)
    return markets, signals, snapshot_path
