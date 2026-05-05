"""
Live order execution via the Polymarket CLOB API.

All trading functions check the kill switch before acting.
cancel_all_orders() intentionally bypasses the kill switch — it IS the kill switch action.
"""

import logging
import requests

from src.client import get_clob_client, CLOB_HOST
from src.safety import check_kill_switch

logger = logging.getLogger(__name__)


def resolve_token_id(market_id: str, direction: str) -> str:
    """
    Resolve the YES or NO outcome token_id for a market from the CLOB.

    direction: "BUY_YES" returns the YES token, "BUY_NO" returns the NO token.
    """
    resp = requests.get(f"{CLOB_HOST}/markets/{market_id}", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    tokens = data.get("tokens", [])
    target_outcome = "yes" if direction == "BUY_YES" else "no"
    fallback = "1" if direction == "BUY_YES" else "0"
    for t in tokens:
        if t.get("outcome", "").lower() in (target_outcome, fallback):
            return t["token_id"]
    if tokens:
        # Last resort: first token for YES, second for NO
        idx = 0 if direction == "BUY_YES" else min(1, len(tokens) - 1)
        return tokens[idx]["token_id"]
    raise ValueError(f"Cannot resolve token_id for market {market_id!r} direction {direction!r}")


def place_order(
    market_id: str,
    token_id: str,
    direction: str,
    amount: float,
    price: float,
) -> dict:
    """
    Place a GTC limit order on the CLOB. Returns the order result dict (contains order_id).

    direction: "BUY_YES" or "BUY_NO" — we always BUY the outcome token.
    amount:    USDC to spend.
    price:     limit price per share (0–1).
    """
    check_kill_switch()

    from py_clob_client.clob_types import OrderArgs, OrderType

    client = get_clob_client(authenticated=True)
    size = round(amount / price, 4)  # shares = USDC / price_per_share

    order_args = OrderArgs(
        token_id=token_id,
        price=price,
        size=size,
        side="BUY",
    )

    signed_order = client.create_order(order_args)
    result = client.post_order(signed_order, orderType=OrderType.GTC)

    logger.info(
        "Order placed: market=%s direction=%s amount=%.2f price=%.4f size=%.4f result=%s",
        market_id,
        direction,
        amount,
        price,
        size,
        result,
    )
    return result if isinstance(result, dict) else {"raw": result}


def get_order_status(order_id: str) -> dict:
    """Fetch the current status of an order by its CLOB order ID."""
    check_kill_switch()

    client = get_clob_client(authenticated=True)
    return client.get_order(order_id)


def cancel_order(order_id: str) -> bool:
    """Cancel a single open order. Returns True on success."""
    check_kill_switch()

    client = get_clob_client(authenticated=True)
    result = client.cancel(order_id)
    logger.info("Order cancelled: %s result=%s", order_id, result)
    return True


def cancel_all_orders() -> list:
    """
    Cancel all open orders for this account.
    Intentionally bypasses the kill switch — this is called by the kill switch itself.
    Returns list of cancelled order IDs (or raw API response if not a list).
    """
    client = get_clob_client(authenticated=True)
    result = client.cancel_all()
    logger.info("All orders cancelled: %s", result)
    if isinstance(result, list):
        return result
    return [result] if result is not None else []


def get_usdc_balance() -> float:
    """
    Fetch the wallet's USDC collateral balance from the CLOB API.
    Returns balance as a float (human-readable USDC, not raw 6-decimal units).
    """
    check_kill_switch()

    from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

    client = get_clob_client(authenticated=True)
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    resp = client.get_balance_allowance(params)
    try:
        return float(resp.get("balance", 0))
    except (TypeError, ValueError, AttributeError):
        return 0.0
