"""Vol surface measurement and skew-adaptive structure selection (Contour-inspired)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from ..options.chain import OptionContract, _snapshot_items

StructureChoice = Literal[
    "NO_TRADE",
    "PUT_CREDIT_SPREAD",
    "CALL_CREDIT_SPREAD",
    "IRON_CONDOR",
    "LONG_STRANGLE",
    "BULL_CALL_SPREAD",
    "BEAR_PUT_SPREAD",
    "PROTECTIVE_PUT",
]

# Skew priors in decimal vol (e.g. 0.025 = 2.5 vol points)
_SKEW_PRIOR: dict[str, tuple[float, float]] = {
    "SPY": (0.025, 0.018),
    "QQQ": (0.020, 0.020),
    "IWM": (0.015, 0.022),
}


@dataclass
class SurfaceMeasurement:
    underlying: str
    spot: float
    atm_iv: float
    rv_cc: float
    rv_parkinson: float
    rv: float
    iv_rv_ratio: float
    skew25: float
    skew_z: float
    regime: str


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
    """Parkinson range-based RV — use higher of CC and Parkinson (Underwriter)."""
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


def _iv_at_strike(contracts: list[OptionContract], spot: float, opt_type: str, target_delta: float = 0.25) -> float | None:
    pool = [c for c in contracts if c.opt_type == opt_type and c.delta is not None and c.mid > 0]
    if not pool:
        return None
    best = min(pool, key=lambda c: abs(abs(c.delta) - target_delta))
    if best.iv is not None:
        return float(best.iv)
    return None


def _atm_iv_from_chain(chain: Any, contracts: list[OptionContract], spot: float) -> float:
    items = _snapshot_items(chain)
    ivs: list[float] = []
    for item in items[:80]:
        g = item.get("greeks") or {}
        iv = g.get("implied_volatility") or g.get("iv") or item.get("implied_volatility")
        if iv is not None:
            try:
                ivs.append(float(iv))
            except (TypeError, ValueError):
                pass
    if ivs:
        return sum(ivs) / len(ivs)
    near = sorted([c for c in contracts if c.mid > 0], key=lambda c: abs(c.strike - spot))[:4]
    chain_ivs = [c.iv for c in near if c.iv is not None]
    if chain_ivs:
        return sum(chain_ivs) / len(chain_ivs)
    return 0.18


def measure_surface(
    underlying: str,
    spot: float,
    closes: list[float],
    chain: Any,
    contracts: list[OptionContract],
    *,
    sell_threshold: float = 1.15,
    buy_threshold: float = 0.95,
) -> SurfaceMeasurement:
    rv_cc = _realized_vol_cc(closes)
    rv_park = rv_cc  # fallback; orchestrator can pass hourly bars for Parkinson
    rv = max(rv_cc, rv_park)
    atm_iv = _atm_iv_from_chain(chain, contracts, spot)
    ratio = atm_iv / rv if rv > 0 else 1.0

    put_iv = _iv_at_strike(contracts, spot, "put", 0.25) or atm_iv
    call_iv = _iv_at_strike(contracts, spot, "call", 0.25) or atm_iv
    skew25 = put_iv - call_iv
    ref, sd = _SKEW_PRIOR.get(underlying, (0.020, 0.020))
    skew_z = (skew25 - ref) / sd if sd > 0 else 0.0

    if ratio > sell_threshold:
        regime = "sell_premium"
    elif ratio < buy_threshold:
        regime = "buy_vol"
    else:
        regime = "stand_aside"

    return SurfaceMeasurement(
        underlying=underlying,
        spot=spot,
        atm_iv=round(atm_iv, 4),
        rv_cc=round(rv_cc, 4),
        rv_parkinson=round(rv_park, 4),
        rv=round(rv, 4),
        iv_rv_ratio=round(ratio, 3),
        skew25=round(skew25, 4),
        skew_z=round(skew_z, 3),
        regime=regime,
    )


def choose_credit_structure(
    m: SurfaceMeasurement,
    *,
    vrp_floor: float = 1.15,
    skew_z_trigger: float = 0.8,
) -> tuple[StructureChoice, str]:
    """Skew-adaptive premium selling — sell only the rich wing."""
    if m.iv_rv_ratio < vrp_floor:
        return "NO_TRADE", f"IV/RV {m.iv_rv_ratio:.2f} < {vrp_floor:.2f} — premium not rich enough"
    # Unreliable skew → default to put credit (Spread Sentinel edge on indices)
    if abs(m.skew_z) > 5.0:
        return "PUT_CREDIT_SPREAD", f"skew indeterminate (z={m.skew_z:+.2f}) — default put credit"
    if m.skew_z >= skew_z_trigger:
        return "PUT_CREDIT_SPREAD", f"put skew rich (z={m.skew_z:+.2f}) — sell puts only"
    if m.skew_z <= -skew_z_trigger:
        return "CALL_CREDIT_SPREAD", f"call skew rich (z={m.skew_z:+.2f}) — sell calls only"
    return "PUT_CREDIT_SPREAD", f"skew neutral (z={m.skew_z:+.2f}) — put credit default"


def choose_apex_sleeve(
    m: SurfaceMeasurement,
    trend: str,
    *,
    drawdown_from_peak_pct: float = 0.0,
    hedge_trigger_pct: float = 2.0,
    sell_threshold: float = 1.15,
    buy_threshold: float = 0.95,
    satellite_allowed: bool = True,
) -> tuple[StructureChoice, str]:
    """Master sleeve router combining Underwriter + Contour + Gatekeeper + Hedgify."""
    if drawdown_from_peak_pct >= hedge_trigger_pct:
        return "PROTECTIVE_PUT", f"drawdown {drawdown_from_peak_pct:.1f}% ≥ {hedge_trigger_pct:.1f}% — hedge"

    if m.regime == "buy_vol" or m.iv_rv_ratio < buy_threshold:
        return "LONG_STRANGLE", f"IV/RV {m.iv_rv_ratio:.2f} — vol cheap, buy convexity"

    if m.regime == "sell_premium" or m.iv_rv_ratio >= sell_threshold:
        return choose_credit_structure(m, vrp_floor=sell_threshold)

    if m.regime == "stand_aside":
        return "NO_TRADE", f"IV/RV {m.iv_rv_ratio:.2f} in stand-aside band — wait for edge"

    return "NO_TRADE", "no sleeve matched"
