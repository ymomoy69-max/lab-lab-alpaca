"""Regime-based options strategy with live quotes and liquidity gates."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .options.chain import OptionContract, parse_chain
from .options.strikes import (
    pick_bear_put_spread,
    pick_bull_call_spread,
    pick_by_delta,
    pick_otm_strike,
    pick_strangle_legs,
)


@dataclass
class LegQuote:
    symbol: str
    side: str
    mid: float
    bid: float
    ask: float
    delta: float | None
    spread_frac: float
    strike: float = 0.0
    opt_type: str = ""


@dataclass
class TradePlan:
    symbol: str
    strategy: str
    structure: str
    confidence: float
    rationale: str
    legs: list[dict] | None = None
    single_legs: list[dict] | None = None
    leg_quotes: list[LegQuote] = field(default_factory=list)
    qty: int = 1
    estimated_debit: float = 0.0
    net_credit: float = 0.0
    max_risk: float = 0.0
    limit_price: float = 0.0
    max_debit: float = 0.0
    expiry: str = ""
    confirm: dict | None = None
    backtest: dict | None = None
    var: dict | None = None
    spread_width: float = 0.0
    sleeve: str = ""
    gate_notes: str = ""


def pick_expiry(
    min_dte: int,
    max_dte: int,
    *,
    contest_close: date | None = None,
) -> str:
    target = date.today() + timedelta(days=min_dte)
    if contest_close and contest_close >= date.today():
        target = max(target, contest_close + timedelta(days=2))
    while target.weekday() >= 5:
        target += timedelta(days=1)
    if (target - date.today()).days > max_dte:
        target = date.today() + timedelta(days=max_dte)
    return target.isoformat()


def pick_apex_expiry(
    min_dte: int,
    max_dte: int,
    *,
    contest_close: date | None = None,
) -> str:
    """Pick expiry — 0DTE (today) for intraday, else weekly."""
    today = date.today()
    if max_dte <= 0:
        return today.isoformat()

    floor = max(min_dte, 1) if min_dte > 0 else 0
    target = today + timedelta(days=floor)
    if contest_close and contest_close >= today:
        target = max(target, contest_close + timedelta(days=2))
    while target.weekday() != 4:
        target += timedelta(days=1)
    if (target - today).days > max_dte:
        target = today + timedelta(days=min(max_dte, max(floor, 7)))
        while target.weekday() >= 5:
            target += timedelta(days=1)
    return target.isoformat()


def _wing_width(quotes: list[LegQuote]) -> float:
    """Max spread width in dollars from strike distances (iron condor / credit spreads)."""
    calls = sorted([q for q in quotes if q.opt_type == "call"], key=lambda q: q.strike)
    puts = sorted([q for q in quotes if q.opt_type == "put"], key=lambda q: q.strike)
    widths = []
    if len(calls) >= 2:
        widths.append(abs(calls[-1].strike - calls[0].strike) * 100)
    if len(puts) >= 2:
        widths.append(abs(puts[-1].strike - puts[0].strike) * 100)
    return max(widths) if widths else 0.0


def _enrich_plan(
    plan: TradePlan,
    contracts: list[OptionContract],
    leg_specs: list[tuple[str, str, str]],
    *,
    slippage: float,
    conservative: bool = False,
) -> TradePlan | None:
    """Attach live quotes and compute debit/limit from mids or bid/ask."""
    quotes: list[LegQuote] = []
    debit = 0.0
    sym_map = {c.symbol: c for c in contracts}

    for occ, side, intent in leg_specs:
        c = sym_map.get(occ)
        if not c or c.mid < 0.01 or c.ask <= 0:
            return None
        if conservative:
            px = c.ask if side == "buy" else c.bid
            if px <= 0 and side == "sell" and c.ask > 0:
                px = max(c.mid * 0.95, c.ask * 0.90, 0.01)
            if px <= 0:
                px = c.mid
        else:
            px = c.mid
        mult = 1 if side == "buy" else -1
        debit += mult * px * 100
        quotes.append(
            LegQuote(
                symbol=occ,
                side=side,
                mid=c.mid,
                bid=c.bid,
                ask=c.ask,
                delta=c.delta,
                spread_frac=c.spread_frac,
                strike=c.strike,
                opt_type=c.opt_type,
            )
        )

    # Indicative feed often inverts bid/ask on penny options — fall back to mids for credits.
    if conservative and debit > 0 and any(s == "sell" for _, s, _ in leg_specs):
        debit = 0.0
        for occ, side, intent in leg_specs:
            c = sym_map[occ]
            mult = 1 if side == "buy" else -1
            debit += mult * c.mid * 100

    plan.leg_quotes = quotes

    if debit <= 0:
        credit = abs(debit)
        if credit <= 0:
            return None
        strikes = [q.strike for q in quotes if q.strike > 0]
        width = (max(strikes) - min(strikes)) * 100 if len(strikes) >= 2 else plan.spread_width
        wing = width if width > 0 else _wing_width(quotes)
        if wing <= 0:
            wing = credit * 4
        plan.spread_width = wing
        plan.net_credit = round(credit, 2)
        plan.max_risk = round(max(wing - credit, credit * 0.5), 2)
        plan.estimated_debit = plan.max_risk
        plan.limit_price = round(credit * (1 - slippage) / 100, 2)
        return plan

    plan.estimated_debit = round(debit, 2)
    plan.max_risk = round(debit, 2)
    plan.max_debit = round(debit * 1.05, 2)
    if plan.legs:
        plan.limit_price = round(debit * (1 + slippage) / 100, 2)
    else:
        plan.limit_price = round(sum(q.ask * (1 + slippage) for q in quotes if q.side == "buy") / max(1, len(quotes)), 2)
    return plan


def marketable_limit(plan: TradePlan, *, cushion: float = 0.20) -> float:
    """Net package price that crosses the live bid/ask so the order can fill.

    Alpaca often rejects options *market* mleg with 'no available quote — reenter with a limit'.
    """
    quotes = plan.leg_quotes or []
    if quotes:
        net = 0.0
        for q in quotes:
            buy_px = q.ask if q.ask > 0 else (q.mid or 0.0)
            sell_px = q.bid if q.bid > 0 else (q.mid or 0.0)
            net += buy_px if q.side == "buy" else -sell_px
        if net >= 0:
            return max(round(net * (1 + cushion) + 0.10, 2), 0.05)
        return max(round(abs(net) * max(0.01, 1 - cushion), 2), 0.01)
    if plan.net_credit > 0:
        return max(round(plan.limit_price * max(0.01, 1 - cushion), 2), 0.01)
    return max(round((plan.limit_price or 0) * (1 + cushion) + 0.10, 2), 0.05)


def build_plan(
    underlying: str,
    debate: dict,
    vol: dict,
    tech: dict,
    chain: Any,
    settings,
    *,
    backtest: dict | None = None,
    var: dict | None = None,
) -> TradePlan | None:
    action = debate.get("action", "HOLD")
    conf = float(debate.get("confidence", 0))
    min_conf = settings.min_confidence
    if action in ("VOL", "SELL_VOL"):
        min_conf = min(min_conf, 58.0)
    if conf < min_conf or action == "HOLD":
        return None

    spot = float(tech.get("current_price") or 0)
    if spot <= 0:
        return None

    expiry = pick_apex_expiry(
        settings.min_dte,
        settings.max_dte,
        contest_close=settings.contest_close if settings.contest_expiry_exit else None,
    )
    contracts = parse_chain(
        chain,
        underlying,
        spot,
        max_spread_frac=settings.max_spread_frac,
        target_expiry=None,
    )
    if not contracts:
        return None

    today = date.today()
    by_exp: dict[str, list[OptionContract]] = {}
    for c in contracts:
        if not c.expiration:
            continue
        try:
            dte = (date.fromisoformat(c.expiration[:10]) - today).days
        except ValueError:
            continue
        if settings.min_dte <= dte <= settings.max_dte:
            by_exp.setdefault(c.expiration[:10], []).append(c)

    if not by_exp:
        # Scalp hold is 5–10 minutes — use the nearest listed expiry if the
        # tight DTE window is empty (sector ETFs often only have Friday weeklies).
        for c in contracts:
            if c.expiration:
                by_exp.setdefault(c.expiration[:10], []).append(c)
        if not by_exp:
            return None

    target = expiry[:10]
    exp_key = min(by_exp.keys(), key=lambda k: abs((date.fromisoformat(k) - date.fromisoformat(target)).days))
    exp_contracts = by_exp[exp_key]
    expiry = exp_key

    ratio = float(vol.get("iv_rv_ratio") or 1.0)
    target_rr = float(getattr(settings, "reward_risk_ratio", 3.0) or 3.0)

    # Long strangle — Vega convexity (explicit VOL signal only)
    if action == "VOL":
        legs = pick_strangle_legs(
            exp_contracts,
            spot,
            max_delta_divergence=settings.max_delta_divergence,
            max_spread_frac=settings.max_spread_frac,
        )
        if not legs:
            return None
        call, put = legs
        plan = TradePlan(
            symbol=underlying,
            strategy="long_strangle",
            structure="long_vol",
            confidence=conf,
            rationale=f"IV/RV={ratio:.2f} — long gamma breakout",
            single_legs=[
                {"symbol": call.symbol, "side": "buy", "position_intent": "buy_to_open"},
                {"symbol": put.symbol, "side": "buy", "position_intent": "buy_to_open"},
            ],
            expiry=expiry,
            backtest=backtest,
            var=var,
        )
        return _enrich_plan(
            plan,
            exp_contracts,
            [(call.symbol, "buy", "buy_to_open"), (put.symbol, "buy", "buy_to_open")],
            slippage=settings.limit_slippage,
        )

    if action == "BUY":
        spread = pick_bull_call_spread(
            exp_contracts,
            spot,
            max_delta_divergence=settings.max_delta_divergence,
            max_spread_frac=settings.max_spread_frac,
            target_rr=target_rr,
        )
        if not spread:
            return None
        long_c, short_c = spread
        plan = TradePlan(
            symbol=underlying,
            strategy="bull_call_spread",
            structure="debit_spread",
            confidence=conf,
            rationale=f"Bull {conf:.0f}% + {tech.get('trend')} trend · {target_rr:.0f}:1 R/R",
            legs=[
                {"symbol": long_c.symbol, "side": "buy", "ratio_qty": "1", "position_intent": "buy_to_open"},
                {"symbol": short_c.symbol, "side": "sell", "ratio_qty": "1", "position_intent": "sell_to_open"},
            ],
            expiry=expiry,
            backtest=backtest,
            var=var,
        )
        return _enrich_plan(
            plan,
            exp_contracts,
            [(long_c.symbol, "buy", ""), (short_c.symbol, "sell", "")],
            slippage=settings.limit_slippage,
        )

    # Iron condor — rich IV premium harvest
    if action == "SELL_VOL" or (vol.get("regime") == "rich" and action == "HOLD" and conf >= 58):
        sc = pick_by_delta(
            exp_contracts,
            "call",
            0.16,
            max_delta_divergence=settings.max_delta_divergence,
            spot=spot,
        )
        lc = pick_otm_strike(exp_contracts, "call", spot, 0.04) or pick_by_delta(
            exp_contracts,
            "call",
            0.08,
            max_delta_divergence=settings.max_delta_divergence + 0.05,
            spot=spot,
        )
        sp = pick_by_delta(
            exp_contracts,
            "put",
            0.16,
            max_delta_divergence=settings.max_delta_divergence,
            spot=spot,
        )
        lp = pick_otm_strike(exp_contracts, "put", spot, 0.04) or pick_by_delta(
            exp_contracts,
            "put",
            0.08,
            max_delta_divergence=settings.max_delta_divergence + 0.05,
            spot=spot,
        )
        if not all([sc, lc, sp, lp]):
            return None
        plan = TradePlan(
            symbol=underlying,
            strategy="iron_condor",
            structure="credit_spread",
            confidence=max(conf, 62.0),
            rationale=f"IV/RV={ratio:.2f} rich — sell premium iron condor",
            legs=[
                {"symbol": sc.symbol, "side": "sell", "ratio_qty": "1", "position_intent": "sell_to_open"},
                {"symbol": lc.symbol, "side": "buy", "ratio_qty": "1", "position_intent": "buy_to_open"},
                {"symbol": sp.symbol, "side": "sell", "ratio_qty": "1", "position_intent": "sell_to_open"},
                {"symbol": lp.symbol, "side": "buy", "ratio_qty": "1", "position_intent": "buy_to_open"},
            ],
            expiry=expiry,
            backtest=backtest,
            var=var,
        )
        return _enrich_plan(
            plan,
            exp_contracts,
            [
                (sc.symbol, "sell", ""),
                (lc.symbol, "buy", ""),
                (sp.symbol, "sell", ""),
                (lp.symbol, "buy", ""),
            ],
            slippage=settings.limit_slippage,
        )

    if action == "SELL":
        spread = pick_bear_put_spread(
            exp_contracts,
            spot,
            max_delta_divergence=settings.max_delta_divergence,
            max_spread_frac=settings.max_spread_frac,
            target_rr=target_rr,
        )
        if not spread:
            return None
        long_p, short_p = spread
        plan = TradePlan(
            symbol=underlying,
            strategy="bear_put_spread",
            structure="debit_spread",
            confidence=conf,
            rationale=f"Bear {conf:.0f}% consensus · {target_rr:.0f}:1 R/R",
            legs=[
                {"symbol": long_p.symbol, "side": "buy", "ratio_qty": "1", "position_intent": "buy_to_open"},
                {"symbol": short_p.symbol, "side": "sell", "ratio_qty": "1", "position_intent": "sell_to_open"},
            ],
            expiry=expiry,
            backtest=backtest,
            var=var,
        )
        return _enrich_plan(
            plan,
            exp_contracts,
            [(long_p.symbol, "buy", ""), (short_p.symbol, "sell", "")],
            slippage=settings.limit_slippage,
        )

    return None


def build_apex_plan(
    underlying: str,
    surface,
    tech: dict,
    chain: Any,
    settings,
    *,
    structure_choice: str,
    sleeve_reason: str,
) -> TradePlan | None:
    """Build trade from apex sleeve router (Underwriter + Contour + Gatekeeper + Hedgify)."""
    from .options.strikes import pick_credit_spread, pick_protective_put

    if structure_choice == "NO_TRADE":
        return None

    spot = float(tech.get("current_price") or surface.spot or 0)
    if spot <= 0:
        return None

    expiry = pick_apex_expiry(
        settings.min_dte,
        settings.max_dte,
        contest_close=settings.contest_close if settings.contest_expiry_exit else None,
    )
    contracts = parse_chain(
        chain,
        underlying,
        spot,
        max_spread_frac=settings.max_spread_frac,
        target_expiry=None,
        min_mid=0.01 if getattr(settings, "apex_mode", False) else 0.05,
    )
    if not contracts:
        return None

    today = date.today()
    by_exp: dict[str, list[OptionContract]] = {}
    for c in contracts:
        if not c.expiration:
            continue
        try:
            dte = (date.fromisoformat(c.expiration[:10]) - today).days
        except ValueError:
            continue
        if settings.min_dte <= dte <= settings.max_dte:
            by_exp.setdefault(c.expiration[:10], []).append(c)

    if not by_exp:
        for c in contracts:
            if c.expiration:
                by_exp.setdefault(c.expiration[:10], []).append(c)
        if not by_exp:
            return None

    exp_key = min(by_exp.keys(), key=lambda k: abs((date.fromisoformat(k) - date.fromisoformat(expiry[:10])).days))
    exp_contracts = by_exp[exp_key]
    expiry = exp_key
    conservative = bool(getattr(settings, "apex_conservative_fills", True))
    width = float(getattr(settings, "apex_credit_width", 5.0))
    otm = float(getattr(settings, "apex_credit_otm_pct", 0.03))
    short_delta = float(getattr(settings, "apex_short_delta_target", 0.21))
    ratio = surface.iv_rv_ratio
    choice = structure_choice

    if choice == "PROTECTIVE_PUT":
        put = pick_protective_put(exp_contracts, spot, otm_pct=0.05, max_spread_frac=settings.max_spread_frac)
        if not put:
            return None
        plan = TradePlan(
            symbol=underlying,
            strategy="protective_put",
            structure="long_vol",
            confidence=70.0,
            rationale=f"Hedgify hedge: {sleeve_reason}",
            single_legs=[{"symbol": put.symbol, "side": "buy", "position_intent": "buy_to_open"}],
            expiry=expiry,
            sleeve="hedge",
            gate_notes=sleeve_reason,
        )
        return _enrich_plan(
            plan, exp_contracts, [(put.symbol, "buy", "buy_to_open")],
            slippage=settings.limit_slippage, conservative=conservative,
        )

    if choice == "LONG_STRANGLE":
        legs = pick_strangle_legs(
            exp_contracts, spot,
            max_delta_divergence=settings.max_delta_divergence,
            max_spread_frac=settings.max_spread_frac,
        )
        if not legs:
            return None
        call, put = legs
        plan = TradePlan(
            symbol=underlying,
            strategy="long_strangle",
            structure="long_vol",
            confidence=65.0,
            rationale=f"Vega sleeve IV/RV={ratio:.2f} — {sleeve_reason}",
            single_legs=[
                {"symbol": call.symbol, "side": "buy", "position_intent": "buy_to_open"},
                {"symbol": put.symbol, "side": "buy", "position_intent": "buy_to_open"},
            ],
            expiry=expiry,
            sleeve="long_vol",
            gate_notes=sleeve_reason,
        )
        return _enrich_plan(
            plan, exp_contracts,
            [(call.symbol, "buy", "buy_to_open"), (put.symbol, "buy", "buy_to_open")],
            slippage=settings.limit_slippage, conservative=conservative,
        )

    if choice in ("PUT_CREDIT_SPREAD", "CALL_CREDIT_SPREAD", "IRON_CONDOR"):
        leg_specs: list[tuple[str, str, str]] = []
        legs_json: list[dict] = []
        strategy_name = "put_credit_spread"
        rationale = f"Income IV/RV={ratio:.2f} skew_z={surface.skew_z:+.2f} — {sleeve_reason}"
        wing = width * 100

        if choice == "PUT_CREDIT_SPREAD":
            spread = pick_credit_spread(
                exp_contracts, spot, "put", width=width, otm_pct=otm,
                target_delta=short_delta, max_spread_frac=settings.max_spread_frac + 0.15,
                min_credit=1.0,
            )
            if not spread:
                return None
            short, long = spread
            wing = abs(short.strike - long.strike) * 100
            leg_specs = [(short.symbol, "sell", ""), (long.symbol, "buy", "")]
            legs_json = [
                {"symbol": short.symbol, "side": "sell", "ratio_qty": "1", "position_intent": "sell_to_open"},
                {"symbol": long.symbol, "side": "buy", "ratio_qty": "1", "position_intent": "buy_to_open"},
            ]
        elif choice == "CALL_CREDIT_SPREAD":
            strategy_name = "call_credit_spread"
            spread = pick_credit_spread(
                exp_contracts, spot, "call", width=width, otm_pct=otm,
                target_delta=short_delta, max_spread_frac=settings.max_spread_frac + 0.15,
                min_credit=1.0,
            )
            if not spread:
                return None
            short, long = spread
            wing = abs(short.strike - long.strike) * 100
            leg_specs = [(short.symbol, "sell", ""), (long.symbol, "buy", "")]
            legs_json = [
                {"symbol": short.symbol, "side": "sell", "ratio_qty": "1", "position_intent": "sell_to_open"},
                {"symbol": long.symbol, "side": "buy", "ratio_qty": "1", "position_intent": "buy_to_open"},
            ]
        else:
            strategy_name = "iron_condor"
            put_sp = pick_credit_spread(exp_contracts, spot, "put", width=width, otm_pct=otm, target_delta=short_delta, max_spread_frac=settings.max_spread_frac)
            call_sp = pick_credit_spread(exp_contracts, spot, "call", width=width, otm_pct=otm, target_delta=short_delta, max_spread_frac=settings.max_spread_frac)
            if not put_sp or not call_sp:
                return None
            sp, lp = put_sp
            sc, lc = call_sp
            wing = max(abs(sp.strike - lp.strike), abs(sc.strike - lc.strike)) * 100
            leg_specs = [(sp.symbol, "sell", ""), (lp.symbol, "buy", ""), (sc.symbol, "sell", ""), (lc.symbol, "buy", "")]
            legs_json = [
                {"symbol": sp.symbol, "side": "sell", "ratio_qty": "1", "position_intent": "sell_to_open"},
                {"symbol": lp.symbol, "side": "buy", "ratio_qty": "1", "position_intent": "buy_to_open"},
                {"symbol": sc.symbol, "side": "sell", "ratio_qty": "1", "position_intent": "sell_to_open"},
                {"symbol": lc.symbol, "side": "buy", "ratio_qty": "1", "position_intent": "buy_to_open"},
            ]

        plan = TradePlan(
            symbol=underlying,
            strategy=strategy_name,
            structure="credit_spread",
            confidence=72.0,
            rationale=rationale,
            legs=legs_json,
            expiry=expiry,
            spread_width=wing,
            sleeve="income",
            gate_notes=sleeve_reason,
        )
        return _enrich_plan(plan, exp_contracts, leg_specs, slippage=settings.limit_slippage, conservative=conservative)

    if choice == "BULL_CALL_SPREAD":
        spread = pick_bull_call_spread(
            exp_contracts, spot,
            max_delta_divergence=settings.max_delta_divergence,
            max_spread_frac=settings.max_spread_frac,
            target_rr=float(getattr(settings, "reward_risk_ratio", 3.0)),
        )
        if not spread:
            return None
        long_c, short_c = spread
        plan = TradePlan(
            symbol=underlying,
            strategy="bull_call_spread",
            structure="debit_spread",
            confidence=60.0,
            rationale=f"Satellite: {sleeve_reason}",
            legs=[
                {"symbol": long_c.symbol, "side": "buy", "ratio_qty": "1", "position_intent": "buy_to_open"},
                {"symbol": short_c.symbol, "side": "sell", "ratio_qty": "1", "position_intent": "sell_to_open"},
            ],
            expiry=expiry,
            sleeve="satellite",
            gate_notes=sleeve_reason,
        )
        return _enrich_plan(
            plan, exp_contracts, [(long_c.symbol, "buy", ""), (short_c.symbol, "sell", "")],
            slippage=settings.limit_slippage, conservative=conservative,
        )

    if choice == "BEAR_PUT_SPREAD":
        spread = pick_bear_put_spread(
            exp_contracts, spot,
            max_delta_divergence=settings.max_delta_divergence,
            max_spread_frac=settings.max_spread_frac,
            target_rr=float(getattr(settings, "reward_risk_ratio", 3.0)),
        )
        if not spread:
            return None
        long_p, short_p = spread
        plan = TradePlan(
            symbol=underlying,
            strategy="bear_put_spread",
            structure="debit_spread",
            confidence=60.0,
            rationale=f"Satellite: {sleeve_reason}",
            legs=[
                {"symbol": long_p.symbol, "side": "buy", "ratio_qty": "1", "position_intent": "buy_to_open"},
                {"symbol": short_p.symbol, "side": "sell", "ratio_qty": "1", "position_intent": "sell_to_open"},
            ],
            expiry=expiry,
            sleeve="satellite",
            gate_notes=sleeve_reason,
        )
        return _enrich_plan(
            plan, exp_contracts, [(long_p.symbol, "buy", ""), (short_p.symbol, "sell", "")],
            slippage=settings.limit_slippage, conservative=conservative,
        )

    return None
