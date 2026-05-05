"""
Safety rails and kill switch for live trading.
State persisted in data/live_state.json.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_PATH = Path(__file__).parent.parent / "data" / "live_state.json"

_DEFAULT_STATE = {
    "kill_switch": False,
    "kill_switch_reason": None,
    "daily_pnl": 0.0,
    "daily_loss_limit": 200.0,
    "max_position_size": 100.0,
    "last_reset_date": None,
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LiveTradingError(Exception):
    """Base exception for live trading safety violations."""


class KillSwitchError(LiveTradingError):
    """Raised when the kill switch is active and a trading action is attempted."""


class DailyLossLimitError(LiveTradingError):
    """Raised when a proposed trade would breach the daily loss limit."""


class PositionSizeError(LiveTradingError):
    """Raised when a proposed position size exceeds the configured maximum."""


# ---------------------------------------------------------------------------
# State I/O
# ---------------------------------------------------------------------------


def load_state() -> dict:
    """Load live trading state from disk; return defaults if file doesn't exist."""
    if _STATE_PATH.exists():
        with open(_STATE_PATH) as f:
            return json.load(f)
    return dict(_DEFAULT_STATE)


def save_state(state: dict) -> None:
    """Persist live trading state to disk."""
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------


def check_kill_switch() -> None:
    """Raise KillSwitchError if the kill switch is currently active."""
    state = load_state()
    if state.get("kill_switch"):
        reason = state.get("kill_switch_reason") or "no reason given"
        raise KillSwitchError(f"Kill switch is active: {reason}")


def activate_kill_switch(reason: str) -> None:
    """Activate the kill switch and record the reason."""
    state = load_state()
    state["kill_switch"] = True
    state["kill_switch_reason"] = reason
    save_state(state)
    logger.warning("Kill switch activated: %s", reason)


def deactivate_kill_switch() -> None:
    """Deactivate the kill switch and clear the reason."""
    state = load_state()
    state["kill_switch"] = False
    state["kill_switch_reason"] = None
    save_state(state)
    logger.info("Kill switch deactivated")


# ---------------------------------------------------------------------------
# Daily loss limit
# ---------------------------------------------------------------------------


def check_daily_loss_limit(proposed_amount: float) -> None:
    """
    Raise DailyLossLimitError if losing proposed_amount would breach the daily limit.

    Worst-case assumption: the entire proposed_amount is a loss.
    """
    state = load_state()
    _maybe_reset_daily_pnl(state)
    daily_pnl = state.get("daily_pnl", 0.0)
    limit = state.get("daily_loss_limit", 200.0)
    if daily_pnl - proposed_amount < -limit:
        remaining = limit + daily_pnl
        raise DailyLossLimitError(
            f"Trade of ${proposed_amount:.2f} would breach daily loss limit "
            f"(limit ${limit:.2f}, remaining headroom ${remaining:.2f})"
        )


def record_daily_pnl(pnl: float) -> None:
    """
    Add pnl to today's running total.
    Auto-activates kill switch if the cumulative loss exceeds the daily limit.
    Resets daily_pnl to 0 on the first call of a new UTC calendar day.
    """
    state = load_state()
    _maybe_reset_daily_pnl(state)
    state["daily_pnl"] = round(state.get("daily_pnl", 0.0) + pnl, 2)
    limit = state.get("daily_loss_limit", 200.0)
    if state["daily_pnl"] < -limit and not state.get("kill_switch"):
        state["kill_switch"] = True
        state["kill_switch_reason"] = (
            f"Daily loss limit breached: ${abs(state['daily_pnl']):.2f} lost "
            f"(limit ${limit:.2f})"
        )
        logger.warning("Kill switch auto-activated: daily loss limit breached")
    save_state(state)


# ---------------------------------------------------------------------------
# Position size
# ---------------------------------------------------------------------------


def validate_position_size(amount: float) -> None:
    """Raise PositionSizeError if amount exceeds the configured max_position_size."""
    state = load_state()
    max_size = state.get("max_position_size", 100.0)
    if amount > max_size:
        raise PositionSizeError(
            f"Position size ${amount:.2f} exceeds maximum ${max_size:.2f}"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _maybe_reset_daily_pnl(state: dict) -> None:
    """Reset daily_pnl to 0 when the UTC calendar day has rolled over. Mutates state."""
    today = datetime.now(timezone.utc).date().isoformat()
    if state.get("last_reset_date") != today:
        state["daily_pnl"] = 0.0
        state["last_reset_date"] = today
