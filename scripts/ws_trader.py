#!/usr/bin/env python3
"""
Kalshi WebSocket trading daemon.

Connects to Kalshi's real-time WebSocket feed, evaluates significant price moves
with Claude, and places momentum paper trades automatically.

Usage:
    python scripts/ws_trader.py            # run daemon
    python scripts/ws_trader.py --dry-run  # connect, log first event, exit
"""

import argparse
import asyncio
import logging
import logging.handlers
import os
import signal
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from src.ws_client import KalshiWebSocketClient
from src.ws_handler import WSHandler
from src.momentum_portfolio import load_portfolio, portfolio_summary

TELEGRAM_TARGET = "8740704554"
LOGS_DIR = PROJECT_ROOT / "logs"
DATA_DIR = PROJECT_ROOT / "data"
PID_FILE = DATA_DIR / "ws_trader.pid"
ET = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    rot = logging.handlers.RotatingFileHandler(
        LOGS_DIR / "ws_trader.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
    )
    rot.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(rot)
    root.addHandler(stream)


def _ping_telegram(message: str) -> None:
    openclaw_bin = "/opt/homebrew/bin/openclaw"
    if not os.path.exists(openclaw_bin):
        openclaw_bin = shutil.which("openclaw") or ""
    if not openclaw_bin:
        logger.warning("openclaw binary not found, skipping Telegram ping")
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
            timeout=15,
        )
    except Exception as exc:
        logger.warning("Telegram ping failed: %s", exc)


def _write_pid() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def _remove_pid() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


async def _midnight_summary_loop() -> None:
    """Send a daily P&L summary at midnight ET, then repeat."""
    while True:
        now_et = datetime.now(ET)
        next_midnight = (now_et + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        sleep_s = (next_midnight - now_et).total_seconds()
        await asyncio.sleep(sleep_s)

        try:
            portfolio = load_portfolio()
            summary = portfolio_summary(portfolio)
            date_str = datetime.now(ET).strftime("%Y-%m-%d")
            _ping_telegram(f"📊 Daily WS Trader Summary — {date_str}\n{summary}")
        except Exception as exc:
            logger.error("Daily summary error: %s", exc)


async def main(dry_run: bool = False) -> int:
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    first_event = asyncio.Event()

    def _handle_signal():
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    client = KalshiWebSocketClient()
    handler = WSHandler()

    def on_price_update(event: dict) -> None:
        result = handler.handle(event)
        action = result.get("action", "")
        ticker = result.get("ticker", "")
        first_event.set()

        if action == "trade":
            pos = result.get("position", {})
            direction = result.get("direction", "")
            edge = result.get("edge", 0.0)
            amount = pos.get("amount", 0.0)
            question = pos.get("question", "")[:80]
            logger.info("TRADE: %s %s edge=%.1f%% $%.2f", direction, ticker, edge * 100, amount)
            _ping_telegram(
                f"📈 WS Trade: {direction} {ticker}\n"
                f"Edge: {edge:.1%} | Amount: ${amount:.2f}\n"
                f"Q: {question}"
            )
        elif action == "error":
            reason = result.get("reason", "")
            logger.warning("Handler error for %s: %s", ticker, reason)
            _ping_telegram(f"⚠️ WS handler error [{ticker}]: {reason[:150]}")

    client.on_price_update(on_price_update)
    # Any incoming frame (including subscribe ack) satisfies dry-run
    client.on_any_message(lambda _frame: first_event.set())

    _write_pid()
    mode_label = "dry-run" if dry_run else "live"
    logger.info("WS Trader starting. PID=%d mode=%s", os.getpid(), mode_label)
    _ping_telegram(f"🚀 WS Trader started (PID {os.getpid()}) — {mode_label}")

    tasks: list[asyncio.Task] = []
    exit_code = 0

    try:
        tasks.append(asyncio.create_task(client.run(stop_event=stop_event), name="ws_client"))

        if not dry_run:
            tasks.append(asyncio.create_task(_midnight_summary_loop(), name="midnight_summary"))

        if dry_run:
            logger.info("Dry-run: waiting for first price event (timeout 60s)...")
            try:
                await asyncio.wait_for(first_event.wait(), timeout=60)
                logger.info("Dry-run: first event received — shutting down")
            except asyncio.TimeoutError:
                logger.warning("Dry-run: timed out waiting for first event")
                exit_code = 1
            stop_event.set()

        await asyncio.gather(*tasks, return_exceptions=True)

    except Exception as exc:
        logger.error("Fatal daemon error: %s", exc)
        _ping_telegram(f"🚨 WS Trader fatal error: {exc}")
        exit_code = 1

    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        _remove_pid()
        _ping_telegram("🛑 WS Trader stopped cleanly")
        logger.info("WS Trader stopped. Exit code: %d", exit_code)

    return exit_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kalshi WebSocket trading daemon")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Connect, wait for first price event, then exit",
    )
    args = parser.parse_args()

    _setup_logging()
    sys.exit(asyncio.run(main(dry_run=args.dry_run)))
