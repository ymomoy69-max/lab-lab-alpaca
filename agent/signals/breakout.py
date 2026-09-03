"""Breakout after a long, tight consolidation — coil then range expansion."""
from __future__ import annotations

from typing import Any

from .technical import bars_list


def _ohlcv(bars: list[dict]) -> tuple[list[float], list[float], list[float], list[float]]:
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    vols: list[float] = []
    for b in bars:
        c = b.get("c") if b.get("c") is not None else b.get("close")
        if c is None:
            continue
        close = float(c)
        h = b.get("h") if b.get("h") is not None else b.get("high")
        l = b.get("l") if b.get("l") is not None else b.get("low")
        v = b.get("v") if b.get("v") is not None else b.get("volume")
        highs.append(float(h) if h is not None else close)
        lows.append(float(l) if l is not None else close)
        closes.append(close)
        try:
            vols.append(float(v or 0))
        except (TypeError, ValueError):
            vols.append(0.0)
    return highs, lows, closes, vols


def _atr(highs: list[float], lows: list[float], closes: list[float], end: int, window: int = 14) -> float:
    start = max(1, end - window)
    trs: list[float] = []
    for i in range(start, end):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def analyze(
    symbol: str,
    bars_raw: Any,
    *,
    cons_bars: int = 60,
    max_range_pct: float = 0.15,
    max_drift_pct: float = 0.06,
    vol_mult: float = 1.25,
    max_extension_pct: float = 0.08,
    min_price: float = 8.0,
) -> dict[str, Any]:
    """Detect a close through a long, overlapping range with volume confirmation."""
    highs, lows, closes, vols = _ohlcv(bars_list(bars_raw))
    n = len(closes)
    empty = {
        "symbol": symbol,
        "ok": False,
        "breakout": False,
        "side": None,
        "summary": "insufficient bars",
    }
    if n < cons_bars + 2:
        return empty

    box_end = n - 1  # exclude today
    box_start = box_end - cons_bars
    if box_start < 0:
        return empty

    box_high = max(highs[box_start:box_end])
    box_low = min(lows[box_start:box_end])
    mid = (box_high + box_low) / 2
    if mid <= 0:
        return {**empty, "ok": True, "summary": "invalid range"}

    range_pct = (box_high - box_low) / mid
    half = cons_bars // 2
    first_mid = (max(highs[box_start : box_start + half]) + min(lows[box_start : box_start + half])) / 2
    second_mid = (max(highs[box_start + half : box_end]) + min(lows[box_start + half : box_end])) / 2
    drift_pct = abs(second_mid - first_mid) / mid

    price = closes[-1]
    extension_up = (price - box_high) / box_high if box_high > 0 else 0.0
    extension_dn = (box_low - price) / box_low if box_low > 0 else 0.0

    atr_box = _atr(highs, lows, closes, box_end, 20)
    atr_prior = _atr(highs, lows, closes, box_start, 20) if box_start > 20 else 0.0
    contraction = (atr_box / atr_prior) if atr_prior > 0 else 1.0

    avg_vol = _mean(vols[box_start:box_end])
    today_vol = vols[-1] if vols else 0.0
    vol_ok = avg_vol <= 0 or today_vol >= avg_vol * vol_mult

    coiled = range_pct <= max_range_pct and drift_pct <= max_drift_pct
    price_ok = price >= min_price

    side = None
    extension = 0.0
    if coiled and price_ok and vol_ok:
        if price > box_high and 0 < extension_up <= max_extension_pct:
            side = "up"
            extension = extension_up
        elif price < box_low and 0 < extension_dn <= max_extension_pct:
            side = "down"
            extension = extension_dn

    ok = True
    fired = side is not None
    summary = (
        f"{'BREAKOUT ' + side if fired else 'no breakout'} · "
        f"{cons_bars}d range {range_pct:.1%} · drift {drift_pct:.1%}"
        + (f" · ext {extension:+.1%} · vol x{(today_vol / avg_vol) if avg_vol else 0:.1f}" if fired else "")
    )
    return {
        "symbol": symbol,
        "ok": ok,
        "breakout": fired,
        "side": side,
        "box_high": round(box_high, 2),
        "box_low": round(box_low, 2),
        "range_pct": round(range_pct, 4),
        "drift_pct": round(drift_pct, 4),
        "extension_pct": round(extension * 100, 2),
        "atr_contraction": round(contraction, 3),
        "volume_ratio": round((today_vol / avg_vol) if avg_vol else 0.0, 2),
        "cons_bars": cons_bars,
        "summary": summary,
    }


def attach_to_tech(tech: dict, breakout: dict) -> dict:
    """Copy breakout fields onto the technical payload used by debate."""
    out = dict(tech)
    out["breakout"] = bool(breakout.get("breakout"))
    out["breakout_side"] = breakout.get("side")
    out["breakout_summary"] = breakout.get("summary", "")
    out["breakout_range_pct"] = breakout.get("range_pct")
    if out["breakout"]:
        bump = 10 if breakout.get("side") == "up" else -10
        out["quant_score"] = max(0, min(100, float(out.get("quant_score", 50)) + bump))
    return out
