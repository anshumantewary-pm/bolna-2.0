"""Phase 0 read-only tool backed by mock data. Swap the dict for the real order API later."""

from __future__ import annotations

from typing import Any

from .base import Tool

_MOCK_ORDERS: dict[str, dict[str, Any]] = {
    "A1001": {
        "status": "shipped",
        "carrier": "UPS",
        "expected_delivery": "2026-11-24",
        "items": ["wireless headphones"],
    },
    "A1002": {
        "status": "processing",
        "expected_ship_date": "2026-11-22",
        "items": ["running shoes", "socks (3 pack)"],
    },
    "A1003": {
        "status": "delivered",
        "delivered_on": "2026-11-15",
        "items": ["coffee grinder"],
    },
}


async def _lookup(args: dict[str, Any]) -> dict[str, Any]:
    # Callers say "a one zero zero one" or "A-1001"; STT may add spaces or hyphens.
    order_id = "".join(ch for ch in args["order_id"] if ch.isalnum()).upper()
    order = _MOCK_ORDERS.get(order_id)
    if order is None:
        return {"found": False, "order_id": order_id}
    return {"found": True, "order_id": order_id, **order}


lookup_order = Tool(
    name="lookup_order",
    description=(
        "Look up the status of a customer's order by its order ID, for example A1001. "
        "Returns status, items, and shipping or delivery dates. Read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "order_id": {
                "type": "string",
                "description": "Order ID as the caller said it, e.g. 'A1001'.",
            }
        },
        "required": ["order_id"],
        "additionalProperties": False,
    },
    handler=_lookup,
)
