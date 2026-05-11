from typing import Optional
"""
Calibration tracker: records resolved positions and computes prediction accuracy metrics.
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

_LOG_PATH = Path(__file__).parent.parent / "data" / "calibration_log.json"


def _load_log() -> list:
    if _LOG_PATH.exists():
        with open(_LOG_PATH) as f:
            return json.load(f)
    return []


def _save_log(log: list) -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)


def _derive_outcome(pos: dict) -> str:
    """Derive YES/NO outcome from direction + pnl sign."""
    direction = pos.get("direction", "BUY_YES")
    pnl = pos.get("pnl") or 0
    if direction == "BUY_YES":
        return "YES" if pnl >= 0 else "NO"
    return "NO" if pnl >= 0 else "YES"


def _brier_for_bucket(records: list) -> Optional[float]:
    """Brier score for records that have model_prob. Returns None if no calibratable records."""
    calibratable = [r for r in records if r.get("model_prob") is not None]
    if not calibratable:
        return None
    total = 0.0
    for r in calibratable:
        outcome_binary = 1.0 if r["outcome"] == "YES" else 0.0
        total += (r["model_prob"] - outcome_binary) ** 2
    return round(total / len(calibratable), 4)


def record_resolution(position: dict, portfolio_name: str) -> None:
    """Append a resolved position to the calibration log."""
    outcome = _derive_outcome(position)
    record = {
        "ticker": position.get("ticker") or position.get("market_id", ""),
        "question": position.get("question", ""),
        "portfolio": portfolio_name,
        "strategy": position.get("strategy", portfolio_name),
        "direction": position.get("direction", ""),
        "model_prob": position.get("model_prob"),
        "market_prob": position.get("entry_price", 0),
        "edge": position.get("edge"),
        "confidence": position.get("confidence") or "",
        "outcome": outcome,
        "pnl": position.get("pnl", 0),
        "opened_at": position.get("opened_at", ""),
        "closed_at": position.get("closed_at") or datetime.now(timezone.utc).isoformat(),
    }
    log = _load_log()
    log.append(record)
    _save_log(log)


def _bucket_stats(records: list) -> dict:
    n = len(records)
    if n == 0:
        return {"n": 0, "win_rate": 0.0, "brier": None}
    wins = sum(1 for r in records if (r.get("pnl") or 0) >= 0)
    return {
        "n": n,
        "win_rate": round(wins / n, 4),
        "brier": _brier_for_bucket(records),
    }


def calibration_report() -> dict:
    """Load calibration log and return computed metrics."""
    log = _load_log()

    if not log:
        return {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "avg_edge": 0.0,
            "brier_score": None,
            "by_confidence": {
                "low": {"n": 0, "win_rate": 0.0, "brier": None},
                "medium": {"n": 0, "win_rate": 0.0, "brier": None},
                "high": {"n": 0, "win_rate": 0.0, "brier": None},
            },
            "by_strategy": {},
            "expected_value": 0.0,
        }

    total = len(log)
    wins = sum(1 for r in log if (r.get("pnl") or 0) >= 0)
    losses = total - wins
    win_rate = round(wins / total, 4)

    edges = [r["edge"] for r in log if r.get("edge") is not None]
    avg_edge = round(sum(abs(e) for e in edges) / len(edges), 4) if edges else 0.0

    brier_score = _brier_for_bucket(log)

    by_confidence = {
        level: _bucket_stats([r for r in log if r.get("confidence") == level])
        for level in ("low", "medium", "high")
    }

    strategies = sorted({r.get("strategy", "") for r in log if r.get("strategy")})
    by_strategy = {
        strat: _bucket_stats([r for r in log if r.get("strategy") == strat])
        for strat in strategies
    }

    total_pnl = sum(r.get("pnl") or 0 for r in log)
    expected_value = round(total_pnl / total, 4)

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "avg_edge": avg_edge,
        "brier_score": brier_score,
        "by_confidence": by_confidence,
        "by_strategy": by_strategy,
        "expected_value": expected_value,
    }


def print_calibration_report() -> None:
    """Pretty-print the calibration report."""
    r = calibration_report()
    print("=" * 55)
    print("  Calibration Report")
    print("=" * 55)
    print(f"  Total resolved:  {r['total']}")
    print(f"  Wins / Losses:   {r['wins']} / {r['losses']}")
    win_pct = f"{r['win_rate']*100:.1f}%" if r["total"] > 0 else "N/A"
    print(f"  Win rate:        {win_pct}")
    print(f"  Avg edge:        {r['avg_edge']*100:.1f}pp")
    brier_str = f"{r['brier_score']:.4f}" if r["brier_score"] is not None else "N/A"
    print(f"  Brier score:     {brier_str}  (lower is better, <0.20 target)")
    print(f"  Expected value:  ${r['expected_value']:.4f} per trade")
    print()
    print("  By confidence:")
    for level in ("low", "medium", "high"):
        b = r["by_confidence"][level]
        brier_s = f"{b['brier']:.4f}" if b["brier"] is not None else "N/A"
        print(f"    {level:8s}: n={b['n']:4d}  win={b['win_rate']*100:.1f}%  brier={brier_s}")
    if r["by_strategy"]:
        print()
        print("  By strategy:")
        for strat, b in r["by_strategy"].items():
            brier_s = f"{b['brier']:.4f}" if b["brier"] is not None else "N/A"
            print(f"    {strat:12s}: n={b['n']:4d}  win={b['win_rate']*100:.1f}%  brier={brier_s}")
    print("=" * 55)
