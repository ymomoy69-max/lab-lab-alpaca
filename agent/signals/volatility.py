"""Volatility regime — Underwriter IV/RV with Parkinson RV (max of CC and Parkinson)."""
from __future__ import annotations

import math
from typing import Any

from ..options.chain import _snapshot_items


def _realized_vol_cc(closes: list[float], window: int = 20) -> float:
    if len(closes) < window + 1:
        return 0.15
    rets = []
    for i in range(-window, -1):
        if closes[i - 1] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))
    if not rets:
        return 0.15
    var = sum(r * r for r in rets) / len(rets)
    return max(0.05, math.sqrt(var * 252))


def _realized_vol_parkinson(bars: list[dict], window: int = 20) -> float:
    if len(bars) < window:
        return 0.15
    slice_ = bars[-window:]
    terms = []
    for b in slice_:
        h = float(b.get("high") or b.get("h") or 0)
        l = float(b.get("low") or b.get("l") or 0)
        if h > 0 and l > 0 and h >= l:
            terms.append(math.log(h / l) ** 2)
    if not terms:
        return 0.15
    var = sum(terms) / (4 * len(terms) * math.log(2))
    return max(0.05, math.sqrt(var * 252))


def _realized_vol(closes: list[float], window: int = 20) -> float:
    return _realized_vol_cc(closes, window)


def _chain_iv(chain: Any) -> float | None:
    """Average ATM implied vol from MCP option chain snapshots."""
    items = _snapshot_items(chain)
    if not items:
        return None
    ivs: list[float] = []
    for item in items[:80]:
        g = item.get("greeks") or {}
        iv = (
            g.get("implied_volatility")
            or g.get("iv")
            or item.get("impliedVolatility")
            or item.get("implied_volatility")
        )
        if iv is None:
            continue
        try:
            ivs.append(float(iv))
        except (TypeError, ValueError):
            pass
    return sum(ivs) / len(ivs) if ivs else None


def analyze(underlying: str, closes: list[float], chain: Any, *, bars: list[dict] | None = None, settings=None) -> dict[str, Any]:
    rv_cc = _realized_vol_cc(closes)
    rv_park = _realized_vol_parkinson(bars or [], window=20) if bars else rv_cc
    rv = max(rv_cc, rv_park)
    iv = _chain_iv(chain) or rv * 1.1
    ratio = iv / rv if rv > 0 else 1.0
    sell_at = float(getattr(settings, "apex_iv_rv_sell", 1.15) if settings else 1.15)
    buy_at = float(getattr(settings, "apex_iv_rv_buy", 0.95) if settings else 0.95)
    if ratio > sell_at:
        regime = "sell_premium"
    elif ratio < buy_at:
        regime = "cheap"
    elif ratio > 1.35:
        regime = "rich"
    elif ratio < 0.95:
        regime = "cheap"
    else:
        regime = "fair"
    return {
        "underlying": underlying,
        "realized_vol": round(rv, 4),
        "realized_vol_cc": round(rv_cc, 4),
        "realized_vol_parkinson": round(rv_park, 4),
        "implied_vol": round(iv, 4),
        "iv_rv_ratio": round(ratio, 3),
        "regime": regime,
    }
