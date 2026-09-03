"""Black-Scholes pricing, IV inversion, delta — Vega ivsolve pattern."""
from __future__ import annotations

import math


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S: float, K: float, T: float, sig: float, kind: str, r: float = 0.04) -> float:
    if T <= 0 or sig <= 0:
        return max(0.0, (S - K) if kind == "call" else (K - S))
    d1 = (math.log(S / K) + (r + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
    d2 = d1 - sig * math.sqrt(T)
    if kind == "call":
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


def bs_delta(S: float, K: float, T: float, sig: float, kind: str, r: float = 0.04) -> float:
    if T <= 0 or sig <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
    if kind == "call":
        return norm_cdf(d1)
    return norm_cdf(d1) - 1.0


def implied_vol(
    price: float,
    S: float,
    K: float,
    T: float,
    kind: str,
    r: float = 0.04,
    lo: float = 0.01,
    hi: float = 5.0,
) -> float | None:
    if price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    intrinsic = max(0.0, (S - K) if kind == "call" else (K - S))
    if price < intrinsic - 1e-6:
        return None
    f_lo = bs_price(S, K, T, lo, kind, r) - price
    f_hi = bs_price(S, K, T, hi, kind, r) - price
    if f_lo * f_hi > 0:
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        f = bs_price(S, K, T, mid, kind, r) - price
        if abs(f) < 1e-6:
            return mid
        if f_lo * f < 0:
            hi = mid
        else:
            lo, f_lo = mid, f
    return 0.5 * (lo + hi)
