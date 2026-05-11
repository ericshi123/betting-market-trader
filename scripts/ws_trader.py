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
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from src.ws_client import KalshiWebSocketClient
from src.ws_handler import WSHandler
from src.momentum_portfolio import load_portfolio, portfolio_summary, close_position
from src.telegram_commands import TelegramCommandHandler, build_default_commands
from src.dashboard import app as dashboard_app, configure as configure_dashboard
from src.dashboard import record_heartbeat, record_signal, start_dashboard

TELEGRAM_TARGET = "8740704554"
LOGS_DIR = PROJECT_ROOT / "logs"
DATA_DIR = PROJECT_ROOT / "data"
PID_FILE = DATA_DIR / "ws_trader.pid"
ET = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)

# ── Global trading state ──────────────────────────────────────────────────────
_TRADING_PAUSED = False
_TRADING_PAUSED_LOCK = threading.Lock()
_SCAN_REQUESTED = threading.Event()

# Per-summary-period counters
_SUMMARY_LOCK = threading.Lock()
_SUMMARY_TRADES_SINCE_LAST: List[dict] = []
_SUMMARY_SIGNALS_SINCE_LAST = 0


def _is_paused() -> bool:
    with _TRADING_PAUSED_LOCK:
        return _TRADING_PAUSED


def _set_paused(value: bool) -> None:
    global _TRADING_PAUSED
    with _TRADING_PAUSED_LOCK:
        _TRADING_PAUSED = value
    logger.info("Trading %s via command", "PAUSED" if value else "RESUMED")


def _trigger_scan() -> None:
    _SCAN_REQUESTED.set()


def _close_position_cmd(position_id: str) -> str:
    portfolio = load_portfolio()
    open_positions = [p for p in portfolio["positions"] if p["status"] == "open"]
    match = next((p for p in open_positions if p["id"].startswith(position_id)), None)
    if match is None:
        raise ValueError(f"No open position matching ID: {position_id}")
    closed = close_position(portfolio, match["id"], "NO", 0.0)
    pnl = closed.get("pnl", 0.0)
    return f"Closed {match['ticker']} ({match['direction']}) — P&L: ${pnl:+.2f}"


# ── Logging setup ─────────────────────────────────────────────────────────────

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


# ── Telegram ──────────────────────────────────────────────────────────────────

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


# ── PID management ────────────────────────────────────────────────────────────

def _write_pid() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def _remove_pid() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ── Summary helpers ───────────────────────────────────────────────────────────

def _build_summary_text() -> str:
    with _SUMMARY_LOCK:
        trades = list(_SUMMARY_TRADES_SINCE_LAST)
        signals_count = _SUMMARY_SIGNALS_SINCE_LAST
        _SUMMARY_TRADES_SINCE_LAST.clear()

    portfolio = load_portfolio()
    summary = portfolio_summary(portfolio)

    date_str = datetime.now(ET).strftime("%Y-%m-%d %I:%M %p ET")
    trade_count = len(trades)
    win_count = sum(1 for t in trades if t.get("pnl", 0) and t["pnl"] > 0)

    lines = [
        f"📊 WS Trader Summary — {date_str}",
        f"Signals evaluated: {signals_count}",
        f"Trades opened: {trade_count}",
    ]
    if trade_count > 0:
        lines.append(f"Win rate (resolved): {win_count}/{trade_count}")
    lines.append("")
    lines.append(summary)
    return "\n".join(lines)


async def _twice_daily_summary_loop() -> None:
    """Send a P&L summary at 9 AM and 9 PM ET, then repeat."""
    target_hours = (9, 21)  # 9 AM and 9 PM
    while True:
        now_et = datetime.now(ET)
        # Find next firing time
        next_fire = None
        for h in sorted(target_hours):
            candidate = now_et.replace(hour=h, minute=0, second=0, microsecond=0)
            if candidate > now_et:
                next_fire = candidate
                break
        if next_fire is None:
            # Both times have passed today — fire at 9 AM tomorrow
            next_fire = (now_et + timedelta(days=1)).replace(
                hour=target_hours[0], minute=0, second=0, microsecond=0
            )

        sleep_s = (next_fire - now_et).total_seconds()
        await asyncio.sleep(sleep_s)

        try:
            text = _build_summary_text()
            _ping_telegram(text)
        except Exception as exc:
            logger.error("Summary error: %s", exc)


async def _heartbeat_loop() -> None:
    """Record a dashboard heartbeat every 30 seconds."""
    while True:
        record_heartbeat()
        await asyncio.sleep(30)


# ── Main ──────────────────────────────────────────────────────────────────────

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

    # ── Wire up dashboard ──────────────────────────────────────────────────
    configure_dashboard(set_paused=_set_paused, get_paused=_is_paused)

    # ── Wire up Telegram command handler ──────────────────────────────────
    tg_handler = TelegramCommandHandler(allowed_chat_id=TELEGRAM_TARGET)
    build_default_commands(
        handler=tg_handler,
        get_portfolio_summary=lambda: portfolio_summary(load_portfolio()),
        close_position_fn=_close_position_cmd,
        get_paused=_is_paused,
        set_paused=_set_paused,
        trigger_scan=_trigger_scan,
    )

    def on_price_update(event: dict) -> None:
        global _SUMMARY_SIGNALS_SINCE_LAST
        if _is_paused():
            logger.debug("SKIP (trading paused): %s", event.get("ticker"))
            return

        result = handler.handle(event)
        action = result.get("action", "")
        ticker = result.get("ticker", "")
        first_event.set()

        # Track for twice-daily summary
        with _SUMMARY_LOCK:
            _SUMMARY_SIGNALS_SINCE_LAST += 1
            if action == "trade":
                _SUMMARY_TRADES_SINCE_LAST.append(result.get("position", {}))

        # Record to dashboard signal feed
        record_signal({
            "action": action,
            "ticker": ticker,
            "reason": result.get("reason") or result.get("direction", ""),
            "timestamp": event.get("timestamp"),
        })

        if action == "trade":
            pos = result.get("position", {})
            direction = result.get("direction", "")
            edge = result.get("edge", 0.0)
            amount = pos.get("amount", 0.0)
            logger.info("TRADE: %s %s edge=%.1f%% $%.2f", direction, ticker, edge * 100, amount)
            # No per-trade Telegram ping — summaries are sent twice daily

        elif action == "error":
            reason = result.get("reason", "")
            logger.warning("Handler error for %s: %s", ticker, reason)
            _ping_telegram(f"⚠️ WS handler error [{ticker}]: {reason[:150]}")

    client.on_price_update(on_price_update)
    client.on_any_message(lambda _frame: first_event.set())

    _write_pid()
    mode_label = "dry-run" if dry_run else "live"
    logger.info("WS Trader starting. PID=%d mode=%s", os.getpid(), mode_label)
    _ping_telegram(f"🚀 WS Trader started (PID {os.getpid()}) — {mode_label}")

    # ── Start background threads ───────────────────────────────────────────
    if not dry_run:
        tg_handler.start()
        start_dashboard()

    tasks: List[asyncio.Task] = []
    exit_code = 0

    try:
        tasks.append(asyncio.create_task(client.run(stop_event=stop_event), name="ws_client"))

        if not dry_run:
            tasks.append(asyncio.create_task(_twice_daily_summary_loop(), name="twice_daily_summary"))
            tasks.append(asyncio.create_task(_heartbeat_loop(), name="heartbeat"))

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
        tg_handler.stop()
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
