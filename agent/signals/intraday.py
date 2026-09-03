"""Intraday session signals — hourly overlay + 1-minute scalp tape."""
from __future__ import annotations

from typing import Any

from .technical import bars_list, closes_from_bars


def _bar_vol(bar: dict) -> float:
    for k in ("v", "volume", "V"):
        if bar.get(k) is not None:
            try:
                return float(bar[k])
            except (TypeError, ValueError):
                continue
    return 0.0


def analyze(symbol: str, bars_raw: Any) -> dict[str, Any]:
    bars = bars_list(bars_raw)
    closes = closes_from_bars(bars)
    if len(closes) < 25:
        return {
            "symbol": symbol,
            "ok": False,
            "trend": "unknown",
            "score_adjustment": 0.0,
            "summary": "insufficient hourly bars",
        }

    price = closes[-1]
    sma5 = sum(closes[-5:]) / 5
    sma20 = sum(closes[-20:]) / 20
    mom3 = (closes[-1] - closes[-4]) / closes[-4] if closes[-4] > 0 else 0.0

    if price > sma5 > sma20 and mom3 > 0.002:
        trend = "bullish_intraday"
        adj = 4.0
    elif price < sma5 < sma20 and mom3 < -0.002:
        trend = "bearish_intraday"
        adj = -4.0
    elif abs(mom3) < 0.001:
        trend = "flat_intraday"
        adj = -2.0
    else:
        trend = "mixed_intraday"
        adj = 0.0

    return {
        "symbol": symbol,
        "ok": True,
        "trend": trend,
        "score_adjustment": adj,
        "momentum_3h_pct": round(mom3 * 100, 3),
        "summary": f"{trend} · 3h {mom3:+.2%}",
    }


def analyze_scalp(symbol: str, bars_raw: Any, *, hold_bars: int = 8) -> dict[str, Any]:
    """1-minute tape for 5–10 minute scalps. hold_bars ≈ minutes in the chair."""
    bars = bars_list(bars_raw)
    closes = closes_from_bars(bars)
    if len(closes) < 15:
        return {
            "symbol": symbol,
            "ok": False,
            "side": "",
            "trend": "unknown",
            "score_adjustment": 0.0,
            "hostile_buy": False,
            "hostile_sell": False,
            "summary": "insufficient 1-min bars",
        }

    price = closes[-1]
    sma5 = sum(closes[-5:]) / 5
    sma10 = sum(closes[-10:]) / 10
    mom3 = (closes[-1] - closes[-4]) / closes[-4] if closes[-4] > 0 else 0.0
    look = min(hold_bars, len(closes) - 1)
    mom_hold = (closes[-1] - closes[-1 - look]) / closes[-1 - look] if closes[-1 - look] > 0 else 0.0

    vols = [_bar_vol(b) for b in bars[-15:]]
    avg_vol = (sum(vols[:-1]) / max(1, len(vols) - 1)) if vols else 0.0
    last_vol = vols[-1] if vols else 0.0
    vol_spike = bool(avg_vol > 0 and last_vol >= avg_vol * 1.4)

    if price > sma5 > sma10 and mom3 > 0:
        side, trend, adj = "up", "scalp_long", 10.0 if vol_spike else 7.0
    elif price < sma5 < sma10 and mom3 < 0:
        side, trend, adj = "down", "scalp_short", -10.0 if vol_spike else -7.0
    elif mom3 > 0.0008 and price > sma5:
        side, trend, adj = "up", "scalp_long_weak", 4.0
    elif mom3 < -0.0008 and price < sma5:
        side, trend, adj = "down", "scalp_short_weak", -4.0
    elif mom3 > 0 or (mom3 == 0.0 and closes[-1] >= closes[-2]):
        side, trend, adj = "up", "scalp_long_micro", 2.0
    else:
        side, trend, adj = "down", "scalp_short_micro", -2.0

    return {
        "symbol": symbol,
        "ok": True,
        "side": side,
        "trend": trend,
        "score_adjustment": adj,
        "momentum_3m_pct": round(mom3 * 100, 3),
        "momentum_hold_pct": round(mom_hold * 100, 3),
        "vol_spike": vol_spike,
        "hostile_buy": side == "down",
        "hostile_sell": side == "up",
        "summary": f"{trend} · 3m {mom3:+.2%} · {look}m {mom_hold:+.2%}"
        + (" · vol spike" if vol_spike else ""),
    }
