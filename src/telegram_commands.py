"""
Telegram command handler for the ws_trader daemon.

Polls getUpdates in a background thread and dispatches:
  /status  — portfolio snapshot
  /close   — close a position by ID
  /pause   — halt new trades
  /resume  — resume trading
  /scan    — trigger an immediate market scan
  /help    — list commands

Requires TELEGRAM_BOT_TOKEN env var. Uses the same TELEGRAM_TARGET chat ID
as the rest of the daemon for filtering incoming messages.
"""

import logging
import os
import threading
import time
from typing import Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 5  # seconds between getUpdates calls
_TIMEOUT = 30       # long-poll timeout


class TelegramCommandHandler:
    """
    Background thread that polls Telegram for commands and dispatches them.

    Usage:
        handler = TelegramCommandHandler(allowed_chat_id="8740704554")
        handler.on_command("/status", lambda args, reply: reply(status_text()))
        handler.start()
        ...
        handler.stop()
    """

    def __init__(self, allowed_chat_id: str):
        self._token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._allowed_chat_id = str(allowed_chat_id)
        self._handlers: Dict[str, Callable] = {}
        self._offset: int = 0
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        if not self._token:
            logger.warning("TELEGRAM_BOT_TOKEN not set — command handler disabled")

    # ── Registration ──────────────────────────────────────────────────────────

    def on_command(self, command: str, handler: Callable) -> None:
        """Register handler(args: List[str], reply: Callable[[str], None]) for a command."""
        self._handlers[command.lower()] = handler

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self._token:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="tg_commands")
        self._thread.start()
        logger.info("Telegram command handler started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Telegram command handler stopped")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _api(self, method: str, **kwargs) -> Optional[dict]:
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        try:
            resp = requests.post(url, json=kwargs, timeout=_TIMEOUT + 5)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Telegram API %s failed: %s", method, exc)
            return None

    def send_message(self, chat_id: str, text: str) -> None:
        self._api("sendMessage", chat_id=chat_id, text=text)

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            data = self._api(
                "getUpdates",
                offset=self._offset,
                timeout=_TIMEOUT,
                allowed_updates=["message"],
            )
            if data and data.get("ok"):
                for update in data.get("result", []):
                    self._offset = update["update_id"] + 1
                    self._process_update(update)
            else:
                time.sleep(_POLL_INTERVAL)

    def _process_update(self, update: dict) -> None:
        msg = update.get("message", {})
        if not msg:
            return

        chat_id = str(msg.get("chat", {}).get("id", ""))
        if chat_id != self._allowed_chat_id:
            logger.debug("Ignoring message from non-allowed chat %s", chat_id)
            return

        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return

        parts = text.split()
        command = parts[0].lower().split("@")[0]  # strip @botname suffix
        args = parts[1:]

        def reply(response_text: str) -> None:
            self.send_message(chat_id, response_text)

        handler = self._handlers.get(command)
        if handler:
            try:
                handler(args, reply)
            except Exception as exc:
                logger.error("Command handler %s error: %s", command, exc)
                reply(f"Error handling {command}: {exc}")
        else:
            reply(f"Unknown command: {command}\nType /help for a list of commands.")


def build_default_commands(
    handler: TelegramCommandHandler,
    get_portfolio_summary: Callable,
    close_position_fn: Callable,
    get_paused: Callable,
    set_paused: Callable,
    trigger_scan: Callable,
) -> None:
    """
    Register the standard set of trading commands on a TelegramCommandHandler.

    Args:
        handler: the TelegramCommandHandler to register on
        get_portfolio_summary: () -> str — returns human-readable portfolio summary
        close_position_fn: (position_id: str) -> str — closes position, returns status msg
        get_paused: () -> bool — returns current TRADING_PAUSED state
        set_paused: (bool) -> None — sets TRADING_PAUSED state
        trigger_scan: () -> None — triggers an immediate market scan
    """

    def _status(args: List[str], reply: Callable) -> None:
        paused = get_paused()
        status_line = "PAUSED" if paused else "ACTIVE"
        summary = get_portfolio_summary()
        reply(f"Trading: {status_line}\n\n{summary}")

    def _close(args: List[str], reply: Callable) -> None:
        if not args:
            reply("Usage: /close <position_id>")
            return
        pos_id = args[0]
        try:
            msg = close_position_fn(pos_id)
            reply(msg)
        except Exception as exc:
            reply(f"Failed to close {pos_id}: {exc}")

    def _pause(args: List[str], reply: Callable) -> None:
        set_paused(True)
        reply("Trading PAUSED. No new positions will be opened.")

    def _resume(args: List[str], reply: Callable) -> None:
        set_paused(False)
        reply("Trading RESUMED. New positions will be opened normally.")

    def _scan(args: List[str], reply: Callable) -> None:
        try:
            trigger_scan()
            reply("Market scan triggered.")
        except Exception as exc:
            reply(f"Scan failed: {exc}")

    def _help(args: List[str], reply: Callable) -> None:
        reply(
            "Available commands:\n"
            "/status — portfolio snapshot (open positions, P&L)\n"
            "/close <id> — manually close a position by ID\n"
            "/pause — halt all new trades\n"
            "/resume — resume trading\n"
            "/scan — trigger immediate market scan\n"
            "/help — show this message"
        )

    handler.on_command("/status", _status)
    handler.on_command("/close", _close)
    handler.on_command("/pause", _pause)
    handler.on_command("/resume", _resume)
    handler.on_command("/scan", _scan)
    handler.on_command("/help", _help)
