"""
Kalshi WebSocket client — persistent connection with RSA-PSS auth and reconnect.

Connects to wss://api.elections.kalshi.com/trade-api/ws/v2, subscribes to the
ticker channel, converts cent prices to floats, and dispatches delta events to
registered callbacks.
"""

import asyncio
import base64
import json
import logging
import os
import time
from typing import Callable, Optional
from urllib.parse import urlencode

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
_WS_PATH = "/trade-api/ws/v2"

_private_key = None


def _get_private_key():
    global _private_key
    if _private_key is None:
        key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH")
        if not key_path:
            raise EnvironmentError("KALSHI_PRIVATE_KEY_PATH not set")
        with open(key_path, "rb") as f:
            _private_key = serialization.load_pem_private_key(f.read(), password=None)
    return _private_key


def build_auth_params() -> dict:
    """
    Build RSA-PSS signed auth params for WebSocket connection.
    Returns both lowercase query-param keys and uppercase header keys
    so callers can choose the delivery method.
    """
    key_id = os.getenv("KALSHI_API_KEY_ID")
    if not key_id:
        raise EnvironmentError("KALSHI_API_KEY_ID not set")

    ts_ms = str(int(time.time() * 1000))
    msg = (ts_ms + "GET" + _WS_PATH).encode("utf-8")

    sig = _get_private_key().sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
    sig_b64 = base64.b64encode(sig).decode("utf-8")

    return {
        "kalshi_access_key": key_id,
        "kalshi_access_signature": sig_b64,
        "kalshi_access_timestamp": ts_ms,
    }


def build_auth_headers() -> dict:
    """Build RSA-PSS signed HTTP headers for WebSocket handshake (same format as REST)."""
    key_id = os.getenv("KALSHI_API_KEY_ID")
    if not key_id:
        raise EnvironmentError("KALSHI_API_KEY_ID not set")

    ts_ms = str(int(time.time() * 1000))
    msg = (ts_ms + "GET" + _WS_PATH).encode("utf-8")

    sig = _get_private_key().sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )

    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode("utf-8"),
        "KALSHI-ACCESS-TIMESTAMP": ts_ms,
    }


class KalshiWebSocketClient:
    """
    Persistent WebSocket client for Kalshi real-time price events.

    Usage:
        client = KalshiWebSocketClient()
        client.on_price_update(my_callback)
        await client.run(stop_event=shutdown_event)

    Callbacks receive: {ticker, yes_price, prev_yes_price, delta, timestamp}
    """

    # Exposed as class attrs so tests can override without subclassing
    _initial_backoff: float = 2.0
    _max_backoff: float = 60.0

    def __init__(self):
        self._callbacks: list[Callable] = []
        self._any_callbacks: list[Callable] = []
        self._prices: dict[str, float] = {}
        self._ws = None

    def on_price_update(self, callback: Callable) -> None:
        """Register a callback invoked for every non-zero price delta."""
        self._callbacks.append(callback)

    def on_any_message(self, callback: Callable) -> None:
        """Register a callback invoked for every raw WS frame received (useful for dry-run)."""
        self._any_callbacks.append(callback)

    def _dispatch(self, event: dict) -> None:
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.error("Price update callback error: %s", exc)

    def _dispatch_any(self, frame: dict) -> None:
        for cb in self._any_callbacks:
            try:
                cb(frame)
            except Exception as exc:
                logger.error("any_message callback error: %s", exc)

    def _build_url(self) -> str:
        params = build_auth_params()
        return f"{WS_URL}?{urlencode(params)}"

    def _build_headers(self) -> dict:
        return build_auth_headers()

    def _handle_ticker(self, msg: dict) -> Optional[dict]:
        """
        Parse a ticker message. Returns event dict or None.
        Prices arrive in cents (1-99); we convert to float (0.01-0.99).
        """
        ticker = msg.get("market_ticker") or msg.get("ticker")
        if not ticker:
            return None

        raw = msg.get("yes_ask") if msg.get("yes_ask") is not None else msg.get("yes_price")
        if raw is None:
            return None

        yes_price = raw / 100.0
        prev = self._prices.get(ticker)
        self._prices[ticker] = yes_price

        if prev is None:
            return None  # no baseline yet — don't emit

        delta = round(yes_price - prev, 4)
        if delta == 0:
            return None

        return {
            "ticker": ticker,
            "yes_price": yes_price,
            "prev_yes_price": prev,
            "delta": delta,
            "timestamp": time.time(),
        }

    async def _subscribe(self, ws) -> None:
        await ws.send(json.dumps({
            "id": 1,
            "cmd": "subscribe",
            "params": {"channels": ["ticker"]},
        }))
        logger.info("Subscribed to ticker channel")

    async def _process_messages(self, ws) -> None:
        async for raw in ws:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue

            self._dispatch_any(frame)

            msg_type = frame.get("type")
            if msg_type == "ticker":
                event = self._handle_ticker(frame.get("msg", {}))
                if event:
                    self._dispatch(event)
            elif msg_type == "ping":
                await ws.send(json.dumps({"type": "pong"}))

    async def run(self, stop_event: Optional[asyncio.Event] = None) -> None:
        """
        Main loop: connect → subscribe → process messages → reconnect on failure.

        Exits when stop_event is set. Uses exponential backoff on failure,
        starting at _initial_backoff seconds up to _max_backoff.
        """
        if stop_event is None:
            stop_event = asyncio.Event()

        backoff = self._initial_backoff

        while not stop_event.is_set():
            url = self._build_url()
            headers = self._build_headers()
            try:
                logger.info("Connecting to Kalshi WebSocket...")
                async with websockets.connect(
                    url,
                    additional_headers=headers,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    self._ws = ws
                    backoff = self._initial_backoff
                    logger.info("Connected. Subscribing...")
                    await self._subscribe(ws)

                    process_task = asyncio.create_task(self._process_messages(ws))
                    stop_task = asyncio.create_task(stop_event.wait())

                    done, pending = await asyncio.wait(
                        {process_task, stop_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                        try:
                            await t
                        except asyncio.CancelledError:
                            pass

            except Exception as exc:
                if stop_event.is_set():
                    break
                logger.warning("WebSocket error: %s — reconnecting in %.0fs", exc, backoff)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                    break  # stop requested during backoff sleep
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, self._max_backoff)

        self._ws = None
        logger.info("WebSocket client stopped")
