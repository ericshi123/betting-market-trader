#!/usr/bin/env python3
"""
Resolve Checker — Phase 8
Checks all open paper positions for finalized Kalshi markets and closes them.
Run every 15 minutes via cron.
"""

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from src.resolver import check_and_resolve_all

TELEGRAM_TARGET = "8740704554"


def _ping_telegram(message: str) -> None:
    try:
        subprocess.run(
            [
                "openclaw",
                "message",
                "send",
                "--channel",
                "telegram",
                "--target",
                TELEGRAM_TARGET,
                "--message",
                message,
            ],
            check=True,
            capture_output=True,
        )
    except Exception as e:
        print(f"[warn] Telegram ping failed: {e}")


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"=== Resolve Checker — {now} ===")

    resolved = check_and_resolve_all()

    if not resolved:
        print("Nothing to resolve.")
        return

    print(f"Resolved {len(resolved)} position(s):")
    lines = [f"🏁 Auto-resolved {len(resolved)} position(s):"]

    for r in resolved:
        sign = "+" if r["pnl"] >= 0 else ""
        outcome_emoji = "✅" if r["pnl"] >= 0 else "❌"
        print(f"  {outcome_emoji} {r['ticker']} — {r['outcome']} {sign}${r['pnl']:.2f} [{r['portfolio']}]")
        lines.append(f"  {outcome_emoji} {r['ticker']} — {r['outcome']} {sign}${r['pnl']:.2f}")

    _ping_telegram("\n".join(lines))
    print("Done.")


if __name__ == "__main__":
    main()
