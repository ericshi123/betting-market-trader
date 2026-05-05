"""
Live order execution via the Kalshi API.

All trading functions check the kill switch before acting.
cancel_all_orders() intentionally bypasses the kill switch — it IS the kill switch action.
"""

import logging

from src.client import kalshi_get, kalshi_post, kalshi_delete
from src.safety import check_kill_switch

logger = logging.getLogger(__name__)


def place_order(ticker: str, side: str, count: int, limit_price: int) -> dict:
    """
    Place a limit order on Kalshi. Returns the API response dict.

    ticker:      Kalshi market ticker (e.g. KXBTCD-25JAN2028-B50000)
    side:        "yes" or "no"
    count:       number of contracts (each pays $1 at resolution)
    limit_price: price in cents (1–99)
    """
    check_kill_switch()

    price_key = "yes_price" if side == "yes" else "no_price"
    body = {
        "ticker": ticker,
        "action": "buy",
        "type": "limit",
        "side": side,
        "count": count,
        price_key: limit_price,
    }

    result = kalshi_post("/portfolio/orders", body)
    order = result.get("order", result)
    order_id = order.get("order_id") or order.get("id") or "unknown"

    logger.info(
        "Order placed: ticker=%s side=%s count=%d price=%d order_id=%s",
        ticker, side, count, limit_price, order_id,
    )
    return result


def get_order_status(order_id: str) -> dict:
    """Fetch the current status of an order."""
    check_kill_switch()
    return kalshi_get(f"/portfolio/orders/{order_id}")


def cancel_order(order_id: str) -> bool:
    """Cancel a single open order. Returns True on success."""
    check_kill_switch()
    kalshi_delete(f"/portfolio/orders/{order_id}")
    logger.info("Order cancelled: %s", order_id)
    return True


def cancel_all_orders() -> list:
    """
    Cancel all resting orders.
    Intentionally bypasses the kill switch — this is called by the kill switch itself.
    Returns list of cancelled order IDs.
    """
    data = kalshi_get("/portfolio/orders", params={"status": "resting"})
    orders = data.get("orders", [])
    cancelled = []
    for order in orders:
        oid = order.get("order_id") or order.get("id")
        if oid:
            try:
                kalshi_delete(f"/portfolio/orders/{oid}")
                cancelled.append(oid)
            except Exception as e:
                logger.warning("Failed to cancel order %s: %s", oid, e)
    logger.info("All orders cancelled: %s", cancelled)
    return cancelled


def get_balance() -> float:
    """
    Fetch account balance from Kalshi.
    Returns balance in dollars (API returns cents).
    """
    check_kill_switch()
    data = kalshi_get("/portfolio/balance")
    cents = data.get("balance", 0)
    return round(cents / 100.0, 2)
