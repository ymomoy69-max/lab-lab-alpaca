"""Order fill verification — poll until terminal state."""
from __future__ import annotations

import time
from typing import Any

from .mcp_client import _dig


TERMINAL = frozenset({"filled", "canceled", "cancelled", "expired", "rejected", "done_for_day"})


def _order_list(raw: Any) -> list[dict]:
    from .mcp_client import parse_orders

    return parse_orders(raw)


def _order_id(order: Any) -> str | None:
    if isinstance(order, dict):
        return str(_dig(order, "id") or _dig(order, "order_id") or "")
    return None


def _order_status(order: Any) -> str:
    if isinstance(order, dict):
        return str(_dig(order, "status") or "").lower()
    return ""


def extract_order_ids(results: list[Any]) -> list[str]:
    ids = []
    for r in results:
        oid = _order_id(r)
        if oid:
            ids.append(oid)
    return ids


def wait_for_orders(
    mcp,
    order_ids: list[str],
    *,
    timeout_sec: int = 90,
    poll_sec: float = 2.0,
) -> list[dict]:
    """Poll MCP orders until all reach terminal state or timeout."""
    pending = set(order_ids)
    final: dict[str, dict] = {}
    deadline = time.time() + timeout_sec

    while pending and time.time() < deadline:
        raw = mcp.orders(status="all", limit=100)
        for o in _order_list(raw):
            oid = _order_id(o)
            if oid in pending:
                st = _order_status(o)
                if st in TERMINAL or st in ("partially_filled",):
                    if st in TERMINAL:
                        final[oid] = o
                        pending.discard(oid)
                    elif st == "partially_filled":
                        final[oid] = o
        if pending:
            time.sleep(poll_sec)

    for oid in pending:
        final[oid] = {"id": oid, "status": "timeout", "filled_qty": 0}
    return list(final.values())


def summarize_fills(verified: list[dict]) -> dict[str, Any]:
    filled = [o for o in verified if _order_status(o) == "filled"]
    failed = [o for o in verified if _order_status(o) not in ("filled", "partially_filled")]
    return {
        "filled_count": len(filled),
        "failed_count": len(failed),
        "all_filled": len(failed) == 0 and len(filled) > 0,
        "orders": verified,
    }
