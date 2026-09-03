"""Monte Carlo VaR — AlphaSwarm RiskSentinel pattern."""
from __future__ import annotations

import math
from typing import Any

import numpy as np


def monte_carlo_var(
    symbol: str,
    current_price: float,
    atr: float,
    *,
    days: int = 21,
    simulations: int = 1000,
    max_var_pct: float = 12.0,
    horizon_minutes: int | None = None,
) -> dict[str, Any]:
    price = float(current_price)
    daily_vol = (atr / price) if atr > 0 and price > 0 else 0.02
    drift = 0.0003

    rng = np.random.default_rng(42)
    if horizon_minutes and horizon_minutes > 0:
        steps = max(1, int(horizon_minutes))
        step_vol = daily_vol / math.sqrt(390.0)
        shocks = rng.normal(0.0, step_vol, (steps, simulations))
        n = steps
    else:
        shocks = rng.normal(drift, daily_vol, (days, simulations))
        n = days
    paths = np.zeros((n + 1, simulations))
    paths[0] = price
    for t in range(1, n + 1):
        paths[t] = paths[t - 1] * (1 + shocks[t - 1])

    terminal = paths[-1]
    rets = (terminal - price) / price
    var_95 = float(np.percentile(rets, 5))
    var_pct = abs(var_95) * 100
    win_prob = float(np.mean(terminal > price)) * 100

    peak = np.maximum.accumulate(paths, axis=0)
    dd = (paths - peak) / peak
    max_dd = abs(float(np.percentile(dd.min(axis=0), 5))) * 100

    passed = var_pct <= max_var_pct
    return {
        "symbol": symbol,
        "var_95_pct": round(var_pct, 2),
        "max_drawdown_95_pct": round(max_dd, 2),
        "win_probability_pct": round(win_prob, 1),
        "passed": passed,
        "reason": "VaR within limit" if passed else f"VaR {var_pct:.1f}% > {max_var_pct}%",
    }
