"""Session time filters — avoid open/close volatility windows."""
from __future__ import annotations

import datetime as dt
from typing import Any

from ..mcp_client import _dig


def _parse_ts(val: Any) -> dt.datetime | None:
    if not val:
        return None
    s = str(val).replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def session_gate(clock: Any, *, open_buffer_min: int = 15, close_buffer_min: int = 30) -> str | None:
    """Return block reason if inside restricted session window."""
    if not isinstance(clock, dict):
        return None
    if not (_dig(clock, "is_open") or _dig(clock, "is_open_now")):
        return "market closed"

    now = _parse_ts(_dig(clock, "timestamp") or _dig(clock, "time"))
    next_close = _parse_ts(_dig(clock, "next_close"))
    next_open = _parse_ts(_dig(clock, "next_open"))

    if now and next_close:
        mins_to_close = (next_close - now).total_seconds() / 60
        if 0 < mins_to_close <= close_buffer_min:
            return f"within {close_buffer_min}m of close — no new entries"

    if now and next_open:
        mins_since_open = (now - next_open).total_seconds() / 60
        if 0 <= mins_since_open <= open_buffer_min:
            return f"within {open_buffer_min}m of open — no new entries"

    return None
