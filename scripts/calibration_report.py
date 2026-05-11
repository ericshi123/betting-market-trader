#!/usr/bin/env python3
"""
Standalone calibration report with live-trading gate check.
Usage: python scripts/calibration_report.py
"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration import calibration_report, print_calibration_report

GATE_MIN_RESOLVED = 30
GATE_MAX_BRIER = 0.20
GATE_MIN_EV = 0.0


def main():
    print_calibration_report()
    print()
    print("Gate check for live trading: need 30+ resolved, Brier < 0.20, positive EV")
    print("-" * 55)

    r = calibration_report()

    gate_resolved = r["total"] >= GATE_MIN_RESOLVED
    gate_brier = r["brier_score"] is not None and r["brier_score"] < GATE_MAX_BRIER
    gate_ev = r["expected_value"] > GATE_MIN_EV

    resolved_status = "PASS" if gate_resolved else "FAIL"
    brier_status = "PASS" if gate_brier else "FAIL"
    ev_status = "PASS" if gate_ev else "FAIL"

    print(f"  Resolved positions: {r['total']:3d}  (need {GATE_MIN_RESOLVED}+)   [{resolved_status}]")

    brier_val = f"{r['brier_score']:.4f}" if r["brier_score"] is not None else "   N/A"
    print(f"  Brier score:      {brier_val:>8s}  (need <{GATE_MAX_BRIER})  [{brier_status}]")

    ev_val = f"{r['expected_value']:+.4f}" if r["total"] > 0 else "   N/A"
    print(f"  Expected value:   {ev_val:>8s}  (need >0)    [{ev_status}]")

    print("-" * 55)
    if gate_resolved and gate_brier and gate_ev:
        print("  STATUS: READY FOR LIVE TRADING")
    else:
        print("  STATUS: NOT READY — accumulate more resolved positions")
    print()


if __name__ == "__main__":
    main()
