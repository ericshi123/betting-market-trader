"""
Tests for src/telegram_commands.py.

Mocks the Telegram API so no real HTTP calls are made.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from src.telegram_commands import TelegramCommandHandler, build_default_commands


CHAT_ID = "8740704554"


def _make_handler(token="test-token"):
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": token}):
        h = TelegramCommandHandler(allowed_chat_id=CHAT_ID)
    return h


def _make_update(text: str, chat_id: str = CHAT_ID, update_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "chat": {"id": int(chat_id), "type": "private"},
            "from": {"id": int(chat_id)},
            "text": text,
        },
    }


# ── Token / disabled path ─────────────────────────────────────────────────────

def test_no_token_does_not_start():
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        h = TelegramCommandHandler(allowed_chat_id=CHAT_ID)
    h.start()
    assert h._thread is None


def test_send_message_calls_api():
    h = _make_handler()
    with patch.object(h, "_api") as mock_api:
        h.send_message(CHAT_ID, "hello")
        mock_api.assert_called_once_with("sendMessage", chat_id=CHAT_ID, text="hello")


# ── Command dispatch ──────────────────────────────────────────────────────────

def test_command_dispatched_to_handler():
    h = _make_handler()
    received = {}

    def my_handler(args, reply):
        received["args"] = args
        received["replied"] = []
        reply("pong")

    h.on_command("/ping", my_handler)

    with patch.object(h, "send_message") as mock_send:
        h._process_update(_make_update("/ping"))
        assert received["args"] == []
        mock_send.assert_called_once_with(CHAT_ID, "pong")


def test_command_with_args():
    h = _make_handler()
    received_args = []

    def my_handler(args, reply):
        received_args.extend(args)
        reply("ok")

    h.on_command("/close", my_handler)
    h._process_update(_make_update("/close abc123"))
    assert received_args == ["abc123"]


def test_unknown_command_replies_with_error():
    h = _make_handler()
    with patch.object(h, "send_message") as mock_send:
        h._process_update(_make_update("/unknown"))
        text = mock_send.call_args[0][1]
        assert "Unknown command" in text
        assert "/help" in text


def test_ignores_non_command_messages():
    h = _make_handler()
    called = []
    h.on_command("/status", lambda a, r: called.append(1))
    h._process_update(_make_update("just a plain message"))
    assert called == []


def test_ignores_wrong_chat_id():
    h = _make_handler()
    called = []
    h.on_command("/status", lambda a, r: called.append(1))
    h._process_update(_make_update("/status", chat_id="9999999"))
    assert called == []


def test_botname_suffix_stripped():
    """'/status@MyBot' should route to /status handler."""
    h = _make_handler()
    called = []
    h.on_command("/status", lambda a, r: called.append(1))
    h._process_update(_make_update("/status@MyBot"))
    assert called == [1]


# ── build_default_commands ────────────────────────────────────────────────────

def _default_handler():
    h = _make_handler()
    paused_state = [False]

    build_default_commands(
        handler=h,
        get_portfolio_summary=lambda: "Portfolio summary here",
        close_position_fn=lambda pid: f"Closed {pid}",
        get_paused=lambda: paused_state[0],
        set_paused=lambda v: paused_state.__setitem__(0, v),
        trigger_scan=lambda: None,
    )
    return h, paused_state


def test_status_command():
    h, _ = _default_handler()
    with patch.object(h, "send_message") as mock_send:
        h._process_update(_make_update("/status"))
        text = mock_send.call_args[0][1]
        assert "Portfolio summary here" in text
        assert "ACTIVE" in text


def test_status_shows_paused():
    h, paused_state = _default_handler()
    paused_state[0] = True
    with patch.object(h, "send_message") as mock_send:
        h._process_update(_make_update("/status"))
        text = mock_send.call_args[0][1]
        assert "PAUSED" in text


def test_close_command():
    h, _ = _default_handler()
    with patch.object(h, "send_message") as mock_send:
        h._process_update(_make_update("/close pos-abc"))
        text = mock_send.call_args[0][1]
        assert "Closed pos-abc" in text


def test_close_missing_id():
    h, _ = _default_handler()
    with patch.object(h, "send_message") as mock_send:
        h._process_update(_make_update("/close"))
        text = mock_send.call_args[0][1]
        assert "Usage" in text


def test_pause_command():
    h, paused_state = _default_handler()
    with patch.object(h, "send_message") as mock_send:
        h._process_update(_make_update("/pause"))
        assert paused_state[0] is True
        text = mock_send.call_args[0][1]
        assert "PAUSED" in text


def test_resume_command():
    h, paused_state = _default_handler()
    paused_state[0] = True
    with patch.object(h, "send_message") as mock_send:
        h._process_update(_make_update("/resume"))
        assert paused_state[0] is False
        text = mock_send.call_args[0][1]
        assert "RESUMED" in text


def test_scan_command():
    h = _make_handler()
    scan_called = []
    build_default_commands(
        handler=h,
        get_portfolio_summary=lambda: "",
        close_position_fn=lambda pid: "",
        get_paused=lambda: False,
        set_paused=lambda v: None,
        trigger_scan=lambda: scan_called.append(1),
    )
    with patch.object(h, "send_message") as mock_send:
        h._process_update(_make_update("/scan"))
        assert scan_called == [1]
        text = mock_send.call_args[0][1]
        assert "triggered" in text.lower()


def test_help_command():
    h, _ = _default_handler()
    with patch.object(h, "send_message") as mock_send:
        h._process_update(_make_update("/help"))
        text = mock_send.call_args[0][1]
        for cmd in ["/status", "/close", "/pause", "/resume", "/scan", "/help"]:
            assert cmd in text
