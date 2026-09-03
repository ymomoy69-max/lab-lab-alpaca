"""Delta-target strike selection with moneyness guards and spread ordering."""
from __future__ import annotations

from .chain import OptionContract


def _typed_pool(
    contracts: list[OptionContract],
    opt_type: str,
    *,
    spot: float | None = None,
    liquid_only: bool = True,
    max_spread_frac: float = 0.25,
) -> list[OptionContract]:
    pool: list[OptionContract] = []
    for c in contracts:
        if c.opt_type != opt_type or c.mid < 0.01:
            continue
        if liquid_only:
            if not c.liquid:
                continue
        elif c.spread_frac > max_spread_frac:
            continue
        if spot and spot > 0:
            if opt_type == "call" and not (0.82 * spot <= c.strike <= 1.28 * spot):
                continue
            if opt_type == "put" and not (0.72 * spot <= c.strike <= 1.05 * spot):
                continue
        pool.append(c)
    return pool


def _is_otm(c: OptionContract, spot: float, opt_type: str) -> bool:
    if spot <= 0:
        return True
    if opt_type == "call":
        return c.strike > spot * 1.005
    return c.strike < spot * 0.995


def pick_by_delta(
    contracts: list[OptionContract],
    opt_type: str,
    target_delta: float,
    *,
    max_delta_divergence: float = 0.05,
    spot: float | None = None,
    liquid_only: bool = True,
    max_spread_frac: float = 0.25,
) -> OptionContract | None:
    pool = _typed_pool(
        contracts,
        opt_type,
        spot=spot,
        liquid_only=liquid_only,
        max_spread_frac=max_spread_frac,
    )
    if not pool and liquid_only:
        pool = _typed_pool(
            contracts,
            opt_type,
            spot=spot,
            liquid_only=False,
            max_spread_frac=max_spread_frac + 0.15,
        )
    if not pool:
        return None

    # Without greeks, only consider OTM strikes (avoid deep ITM on 0DTE).
    if spot and spot > 0 and all(c.delta is None for c in pool[:5]):
        pool = [c for c in pool if _is_otm(c, spot, opt_type)]

    if not pool:
        return None

    def _score(c: OptionContract) -> float:
        if c.delta is not None:
            return abs(abs(c.delta) - target_delta)
        if spot and spot > 0:
            if opt_type == "call":
                return abs(c.strike - spot * (1 + target_delta * 0.12)) / spot
            return abs(c.strike - spot * (1 - target_delta * 0.12)) / spot
        return abs(c.strike)

    best = min(pool, key=_score)
    if best.delta is not None and abs(abs(best.delta) - target_delta) > max_delta_divergence + 0.08:
        return None
    return best


def pick_otm_strike(
    contracts: list[OptionContract],
    opt_type: str,
    spot: float,
    otm_pct: float,
    *,
    liquid_only: bool = True,
    max_spread_frac: float = 0.25,
) -> OptionContract | None:
    pool = _typed_pool(
        contracts,
        opt_type,
        spot=spot,
        liquid_only=liquid_only,
        max_spread_frac=max_spread_frac,
    )
    if not pool and liquid_only:
        pool = _typed_pool(
            contracts,
            opt_type,
            spot=spot,
            liquid_only=False,
            max_spread_frac=max_spread_frac + 0.15,
        )
    if opt_type == "call":
        target = spot * (1 + otm_pct)
        candidates = [c for c in pool if c.strike >= target]
        return min(candidates, key=lambda c: c.strike) if candidates else None
    target = spot * (1 - otm_pct)
    candidates = [c for c in pool if c.strike <= target]
    return max(candidates, key=lambda c: c.strike) if candidates else None


def _debit_reward_risk(long_c: OptionContract, short_c: OptionContract) -> tuple[float, float]:
    debit = (long_c.mid - short_c.mid) * 100
    width = abs(short_c.strike - long_c.strike) * 100
    if debit <= 0 or width <= 0:
        return debit, 0.0
    return debit, (width - debit) / debit


def _best_debit_pair(
    longs: list[OptionContract],
    shorts_for,
    *,
    spot: float,
    target_rr: float,
) -> tuple[OptionContract, OptionContract] | None:
    qualified: list[tuple[float, float, OptionContract, OptionContract]] = []
    fallback: list[tuple[float, float, OptionContract, OptionContract]] = []
    for long_c in longs:
        short_c = shorts_for(long_c)
        if short_c is None:
            continue
        debit, rr = _debit_reward_risk(long_c, short_c)
        if debit <= 0 or debit > spot * 8:
            continue
        row = (rr, -debit, long_c, short_c)
        if rr + 1e-9 >= target_rr:
            qualified.append(row)
        else:
            fallback.append(row)
    pool = qualified or fallback
    if not pool:
        return None
    pool.sort(key=lambda r: (r[0], r[1]), reverse=True)
    _, _, long_c, short_c = pool[0]
    return long_c, short_c


def pick_bull_call_spread(
    contracts: list[OptionContract],
    spot: float,
    *,
    max_delta_divergence: float = 0.05,
    max_spread_frac: float = 0.25,
    target_rr: float = 3.0,
) -> tuple[OptionContract, OptionContract] | None:
    pool = _typed_pool(contracts, "call", spot=spot, max_spread_frac=max_spread_frac)
    if not pool:
        pool = _typed_pool(
            contracts,
            "call",
            spot=spot,
            liquid_only=False,
            max_spread_frac=max_spread_frac + 0.15,
        )
    if len(pool) < 2:
        return None

    long_candidates = sorted(
        [c for c in pool if c.strike <= spot * 1.03],
        key=lambda c: abs(c.strike - spot),
    )[:8]
    if not long_candidates:
        long_candidates = sorted(pool, key=lambda c: abs(c.strike - spot))[:8]

    def _short_for(long_c: OptionContract) -> OptionContract | None:
        # Prefer a wider wing so max profit / debit can hit ~3:1.
        min_short = long_c.strike + max(5.0, long_c.strike * 0.03)
        higher = [c for c in pool if c.strike >= min_short]
        if not higher:
            min_short = long_c.strike + max(2.5, long_c.strike * 0.015)
            higher = [c for c in pool if c.strike >= min_short]
        if not higher:
            return None
        # Among shorts that clear the wing, pick the one closest to 3:1 then nearest.
        ranked = []
        for short_c in higher:
            if short_c.strike <= long_c.strike:
                continue
            debit, rr = _debit_reward_risk(long_c, short_c)
            if debit <= 0:
                continue
            ranked.append((abs(rr - target_rr), -rr, short_c.strike, short_c))
        if not ranked:
            return None
        ranked.sort()
        return ranked[0][-1]

    return _best_debit_pair(long_candidates, _short_for, spot=spot, target_rr=target_rr)


def pick_bear_put_spread(
    contracts: list[OptionContract],
    spot: float,
    *,
    max_delta_divergence: float = 0.05,
    max_spread_frac: float = 0.25,
    target_rr: float = 3.0,
) -> tuple[OptionContract, OptionContract] | None:
    pool = _typed_pool(contracts, "put", spot=spot, max_spread_frac=max_spread_frac)
    if not pool:
        pool = _typed_pool(
            contracts,
            "put",
            spot=spot,
            liquid_only=False,
            max_spread_frac=max_spread_frac + 0.15,
        )
    if len(pool) < 2:
        return None

    long_candidates = sorted(
        [c for c in pool if c.strike >= spot * 0.97],
        key=lambda c: abs(c.strike - spot),
    )[:8]
    if not long_candidates:
        long_candidates = sorted(pool, key=lambda c: abs(c.strike - spot))[:8]

    def _short_for(long_p: OptionContract) -> OptionContract | None:
        max_short = long_p.strike - max(5.0, long_p.strike * 0.03)
        lower = [c for c in pool if c.strike <= max_short]
        if not lower:
            max_short = long_p.strike - max(2.5, long_p.strike * 0.015)
            lower = [c for c in pool if c.strike <= max_short]
        if not lower:
            return None
        ranked = []
        for short_p in lower:
            if short_p.strike >= long_p.strike:
                continue
            debit, rr = _debit_reward_risk(long_p, short_p)
            if debit <= 0:
                continue
            ranked.append((abs(rr - target_rr), -rr, -short_p.strike, short_p))
        if not ranked:
            return None
        ranked.sort()
        return ranked[0][-1]

    return _best_debit_pair(long_candidates, _short_for, spot=spot, target_rr=target_rr)


def _leg_quote_sane(c: OptionContract, spot: float) -> bool:
    """Reject stale/indicative quotes that would imply absurd premium."""
    if c.mid <= 0 or spot <= 0:
        return False
    return c.mid <= spot * 0.12


def _credit_spread_pair(
    contracts: list[OptionContract],
    short: OptionContract,
    opt_type: str,
    width: float,
) -> tuple[OptionContract, OptionContract, float] | None:
    if opt_type == "put":
        target = short.strike - width
        longs = [
            c for c in contracts
            if c.opt_type == "put" and c.strike <= target + 0.01 and c.strike < short.strike
            and c.mid >= 0.01 and c.ask > 0
        ]
        long_leg = max(longs, key=lambda c: c.strike) if longs else None
    else:
        target = short.strike + width
        longs = [
            c for c in contracts
            if c.opt_type == "call" and c.strike >= target - 0.01 and c.strike > short.strike
            and c.mid >= 0.01 and c.ask > 0
        ]
        long_leg = min(longs, key=lambda c: c.strike) if longs else None
    if not long_leg or long_leg.symbol == short.symbol:
        return None
    mid_credit = (short.mid - long_leg.mid) * 100
    cons_credit = (max(short.bid, short.mid * 0.95, 0.01) - long_leg.ask) * 100
    credit = mid_credit if mid_credit > 0 else cons_credit
    if credit <= 0:
        return None
    return short, long_leg, credit


def pick_credit_spread(
    contracts: list[OptionContract],
    spot: float,
    opt_type: str,
    *,
    width: float = 5.0,
    otm_pct: float = 0.03,
    target_delta: float = 0.21,
    max_spread_frac: float = 0.25,
    min_credit: float = 5.0,
) -> tuple[OptionContract, OptionContract] | None:
    """Defined-risk credit spread — scan OTM shorts and wing widths for best fill."""
    best: tuple[OptionContract, OptionContract] | None = None
    best_credit = 0.0
    widths = sorted(set([max(1.0, width * w) for w in (0.2, 0.4, 0.6, 0.8, 1.0, 1.5, 2.0)]))

    for otm in (otm_pct, 0.02, 0.04, 0.05, 0.07, 0.10):
        short = pick_otm_strike(contracts, opt_type, spot, otm, max_spread_frac=max_spread_frac)
        if not short:
            short = pick_otm_strike(
                contracts, opt_type, spot, otm,
                liquid_only=False, max_spread_frac=max_spread_frac + 0.25,
            )
        if not short:
            short = pick_by_delta(
                contracts, opt_type, target_delta,
                max_delta_divergence=0.15, spot=spot, max_spread_frac=max_spread_frac + 0.15,
            )
        if not short or (spot > 0 and not _is_otm(short, spot, opt_type)):
            continue

        for wing in widths:
            row = _credit_spread_pair(contracts, short, opt_type, wing)
            if not row:
                continue
            _, long_leg, credit = row
            if credit >= min_credit and credit > best_credit:
                best = (short, long_leg)
                best_credit = credit

    if best:
        return best

    # Paper/indicative feed: accept small positive credit if structure is valid.
    for otm in (otm_pct, 0.02, 0.03, 0.05):
        short = pick_otm_strike(
            contracts, opt_type, spot, otm,
            liquid_only=False, max_spread_frac=max_spread_frac + 0.30,
        )
        if not short:
            continue
        for wing in widths:
            row = _credit_spread_pair(contracts, short, opt_type, wing)
            if row and row[2] > best_credit:
                best = (row[0], row[1])
                best_credit = row[2]
    return best


def pick_protective_put(
    contracts: list[OptionContract],
    spot: float,
    *,
    otm_pct: float = 0.05,
    max_spread_frac: float = 0.25,
) -> OptionContract | None:
    """Tail hedge put (Hedgify)."""
    return pick_otm_strike(contracts, "put", spot, otm_pct, max_spread_frac=max_spread_frac)


def pick_strangle_legs(
    contracts: list[OptionContract],
    spot: float,
    *,
    max_delta_divergence: float = 0.05,
    max_spread_frac: float = 0.25,
) -> tuple[OptionContract, OptionContract] | None:
    call = pick_by_delta(
        contracts,
        "call",
        0.12,
        max_delta_divergence=max_delta_divergence,
        spot=spot,
        max_spread_frac=max_spread_frac,
    )
    put = pick_by_delta(
        contracts,
        "put",
        0.12,
        max_delta_divergence=max_delta_divergence,
        spot=spot,
        max_spread_frac=max_spread_frac,
    )
    for otm in (0.03, 0.05, 0.07, 0.10):
        if not call:
            call = pick_otm_strike(
                contracts,
                "call",
                spot,
                otm,
                liquid_only=True,
                max_spread_frac=max_spread_frac,
            )
        if not put:
            put = pick_otm_strike(
                contracts,
                "put",
                spot,
                otm,
                liquid_only=True,
                max_spread_frac=max_spread_frac,
            )
        if call and put:
            break
    if not call or not put:
        for otm in (0.03, 0.05, 0.07, 0.10, 0.12):
            if not call:
                call = pick_otm_strike(
                    contracts,
                    "call",
                    spot,
                    otm,
                    liquid_only=False,
                    max_spread_frac=max_spread_frac + 0.20,
                )
            if not put:
                put = pick_otm_strike(
                    contracts,
                    "put",
                    spot,
                    otm,
                    liquid_only=False,
                    max_spread_frac=max_spread_frac + 0.20,
                )
            if call and put:
                break
    if not call or not put:
        return None
    if not (_leg_quote_sane(call, spot) and _leg_quote_sane(put, spot)):
        return None
    cost = (call.mid + put.mid) * 100
    if cost <= 0 or cost > spot * 12:
        return None
    return call, put
