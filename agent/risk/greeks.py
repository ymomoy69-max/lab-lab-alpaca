"""Greeks preflight — Vega-style delta check before order submission."""
from __future__ import annotations

from typing import Any

from ..strategy import TradePlan


def net_delta(plan: TradePlan) -> float | None:
    total = 0.0
    found = False
    for q in plan.leg_quotes:
        if q.delta is None:
            continue
        found = True
        sign = 1 if q.side == "buy" else -1
        total += sign * q.delta
    return round(total, 4) if found else None


def greeks_preflight(plan: TradePlan, *, max_short_delta: float = 0.45, scalp: bool = False) -> str | None:
    """Fail-closed if net delta conflicts with strategy intent."""
    nd = net_delta(plan)
    if nd is None:
        return None

    floor = 0.01 if scalp else 0.05
    if plan.strategy == "bull_call_spread" and nd < floor:
        return f"bull spread net delta {nd:.2f} too low"
    if plan.strategy == "bear_put_spread" and nd > -floor:
        return f"bear spread net delta {nd:.2f} too high"

    for q in plan.leg_quotes:
        if q.side == "sell" and q.delta is not None and abs(q.delta) > max_short_delta:
            return f"short leg {q.symbol} delta {q.delta:.2f} > {max_short_delta}"

    return None
