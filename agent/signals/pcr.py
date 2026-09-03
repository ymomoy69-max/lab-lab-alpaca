"""Put/call ratio from the live option chain — 0.5 resistance, 1.5 support."""
from __future__ import annotations

from typing import Any

from ..options.chain import _parse_occ, _snapshot_items


def _num(item: dict, *keys: str) -> float:
    for k in keys:
        v = item.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def _nested_vol(item: dict) -> float:
    daily = item.get("dailyBar") or item.get("daily_bar") or item.get("bar") or {}
    v = _num(daily, "v", "volume")
    if v > 0:
        return v
    return _num(item, "volume", "v", "trade_count")


def _nested_oi(item: dict) -> float:
    return _num(item, "open_interest", "openInterest", "oi", "openinterest")


def _opt_type(item: dict, underlying: str) -> str | None:
    raw = str(item.get("type") or item.get("option_type") or "").lower()
    if raw in ("call", "put"):
        return raw
    sym = str(item.get("symbol") or item.get("contract_symbol") or "")
    parsed = _parse_occ(sym, underlying) if sym else None
    if parsed:
        return parsed[0]
    if "C" in sym[-9:] and "P" not in sym[-9:]:
        return "call"
    if "P" in sym[-9:]:
        return "put"
    return None


def analyze(
    symbol: str,
    chain: Any,
    *,
    resistance: float = 0.5,
    support: float = 1.5,
) -> dict[str, Any]:
    """PCR = put/call. Low (~0.5) is call-heavy resistance; high (~1.5) is put-heavy support."""
    items = _snapshot_items(chain)
    put_vol = call_vol = 0.0
    put_oi = call_oi = 0.0
    n_puts = n_calls = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = _opt_type(item, symbol)
        if kind not in ("call", "put"):
            continue
        vol = _nested_vol(item)
        oi = _nested_oi(item)
        if kind == "put":
            put_vol += vol
            put_oi += oi
            n_puts += 1
        else:
            call_vol += vol
            call_oi += oi
            n_calls += 1

    pcr_vol = (put_vol / call_vol) if call_vol > 0 else None
    pcr_oi = (put_oi / call_oi) if call_oi > 0 else None
    pcr = pcr_vol if pcr_vol is not None else pcr_oi
    if pcr is None:
        return {
            "symbol": symbol,
            "ok": False,
            "pcr": None,
            "zone": "unknown",
            "bias": None,
            "summary": "no put/call volume or OI",
        }

    if pcr <= resistance:
        zone, bias = "resistance", "SELL"
        note = f"PCR {pcr:.2f} ≤ {resistance:.2f} — call-heavy resistance"
    elif pcr >= support:
        zone, bias = "support", "BUY"
        note = f"PCR {pcr:.2f} ≥ {support:.2f} — put-heavy support"
    else:
        zone, bias = "neutral", None
        note = f"PCR {pcr:.2f} between {resistance:.2f}–{support:.2f}"

    return {
        "symbol": symbol,
        "ok": True,
        "pcr": round(pcr, 3),
        "pcr_volume": round(pcr_vol, 3) if pcr_vol is not None else None,
        "pcr_oi": round(pcr_oi, 3) if pcr_oi is not None else None,
        "put_volume": put_vol,
        "call_volume": call_vol,
        "put_oi": put_oi,
        "call_oi": call_oi,
        "contracts": n_puts + n_calls,
        "zone": zone,
        "bias": bias,
        "resistance": resistance,
        "support": support,
        "summary": note,
    }
