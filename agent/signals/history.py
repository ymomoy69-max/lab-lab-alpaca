"""Extended historical market analysis — regime detection + strategy fit."""
from __future__ import annotations

import math
from typing import Any


def _returns(closes: list[float]) -> list[float]:
    out = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            out.append((closes[i] - closes[i - 1]) / closes[i - 1])
    return out


def _realized_vol(rets: list[float], window: int = 20) -> float:
    if len(rets) < window:
        return 0.15
    chunk = rets[-window:]
    var = sum(r * r for r in chunk) / len(chunk)
    return max(0.05, math.sqrt(var * 252))


def _max_drawdown(closes: list[float]) -> float:
    if not closes:
        return 0.0
    peak = closes[0]
    worst = 0.0
    for c in closes:
        peak = max(peak, c)
        if peak > 0:
            worst = min(worst, (c - peak) / peak)
    return abs(worst)


def _momentum(closes: list[float], window: int) -> float:
    if len(closes) <= window or closes[-window - 1] <= 0:
        return 0.0
    return (closes[-1] - closes[-window - 1]) / closes[-window - 1]


def _directional_hit_rate(closes: list[float], *, lookback: int = 20) -> dict[str, float]:
    """Walk historical bars: does momentum predict next-day direction?"""
    wins = {"BUY": 0, "SELL": 0, "VOL": 0}
    trials = {"BUY": 0, "SELL": 0, "VOL": 0}
    for i in range(lookback, len(closes) - 1):
        mom = (closes[i] - closes[i - lookback]) / closes[i - lookback]
        nxt = (closes[i + 1] - closes[i]) / closes[i]
        if mom > 0.01:
            trials["BUY"] += 1
            if nxt > 0:
                wins["BUY"] += 1
        elif mom < -0.01:
            trials["SELL"] += 1
            if nxt < 0:
                wins["SELL"] += 1
        trials["VOL"] += 1
        if abs(nxt) > 0.005:
            wins["VOL"] += 1
    rates = {}
    for k in ("BUY", "SELL", "VOL"):
        rates[k] = wins[k] / trials[k] if trials[k] else 0.5
    return rates


def analyze_history(symbol: str, closes: list[float]) -> dict[str, Any]:
    if len(closes) < 60:
        return {
            "symbol": symbol,
            "ok": False,
            "reason": "need 60+ bars for history analysis",
        }

    rets = _returns(closes)
    mom20 = _momentum(closes, 20)
    mom60 = _momentum(closes, 60)
    rv20 = _realized_vol(rets, 20)
    rv60 = _realized_vol(rets, 60)
    dd90 = _max_drawdown(closes[-90:])
    hit = _directional_hit_rate(closes)

    trend_strength = abs(mom20) / rv20 if rv20 > 0 else 0.0
    if trend_strength > 0.35 and mom20 > 0:
        regime = "trending_up"
    elif trend_strength > 0.35 and mom20 < 0:
        regime = "trending_down"
    elif rv20 > rv60 * 1.15:
        regime = "high_vol"
    else:
        regime = "range_bound"

    if regime == "trending_up":
        favored = "bull_call_spread"
        bias = "BUY"
    elif regime == "trending_down":
        favored = "bear_put_spread"
        bias = "SELL"
    elif regime == "high_vol":
        favored = "long_strangle"
        bias = "VOL"
    else:
        favored = "iron_condor"
        bias = "SELL_VOL"

    best_action = max(hit, key=hit.get)
    confidence_boost = 0.0
    if hit.get(bias, 0.5) >= 0.55:
        confidence_boost = min(8, (hit[bias] - 0.5) * 40)
    elif hit.get(bias, 0.5) < 0.45:
        confidence_boost = max(-8, (hit[bias] - 0.5) * 40)

    return {
        "symbol": symbol,
        "ok": True,
        "regime": regime,
        "momentum_20d_pct": round(mom20 * 100, 2),
        "momentum_60d_pct": round(mom60 * 100, 2),
        "realized_vol_20d": round(rv20, 4),
        "realized_vol_60d": round(rv60, 4),
        "max_drawdown_90d_pct": round(dd90 * 100, 2),
        "directional_hit_rate": {k: round(v, 3) for k, v in hit.items()},
        "best_historical_action": best_action,
        "regime_bias": bias,
        "favored_strategy": favored,
        "confidence_adjustment": round(confidence_boost, 1),
        "summary": (
            f"{regime} · 20d {mom20:+.1%} · hit-rate {bias}={hit.get(bias, 0.5):.0%} · favors {favored}"
        ),
    }
