"""Account metric parsing."""
from __future__ import annotations

from typing import Any

from .mcp_client import _dig


def account_metrics(acct: Any) -> dict[str, float]:
    equity = float(_dig(acct, "equity") or _dig(acct, "portfolio_value") or 0)
    last = float(_dig(acct, "last_equity") or equity)
    cash = float(_dig(acct, "cash") or 0)
    bp = float(_dig(acct, "buying_power") or 0)
    daily_pnl = equity - last
    daily_pnl_pct = (daily_pnl / last * 100) if last > 0 else 0.0
    return {
        "equity": equity,
        "last_equity": last,
        "cash": cash,
        "buying_power": bp,
        "daily_pnl": daily_pnl,
        "daily_pnl_pct": daily_pnl_pct,
    }
