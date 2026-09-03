"""Apex gate kernel — deterministic risk checks (Underwriter + aftershock inspired)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..options.chain import occ_root
from ..strategy import TradePlan


@dataclass
class GateResult:
    passed: bool
    gate: str
    reason: str


@dataclass
class GateContext:
    equity: float
    starting_equity: float
    peak_equity: float
    day_pnl_pct: float
    open_risk: float
    portfolio_delta: float
    position_count: int
    underlying_count: dict[str, int]
    session_phase: str = "open"
    event_veto_symbols: set[str] = field(default_factory=set)
    buying_power: float = 0.0
    planned_qty: int = 1
    at_risk_per_contract: float = 0.0


def _conservative_credit(plan: TradePlan) -> float:
    """Sell at bid, buy at ask — worst-case fill (Underwriter G9)."""
    net = 0.0
    for q in plan.leg_quotes:
        if q.side == "sell":
            net += (q.bid if q.bid > 0 else q.mid) * 100
        else:
            net -= (q.ask if q.ask > 0 else q.mid) * 100
    return net


def _conservative_debit(plan: TradePlan) -> float:
    net = 0.0
    for q in plan.leg_quotes:
        if q.side == "buy":
            net += (q.ask if q.ask > 0 else q.mid) * 100
        else:
            net -= (q.bid if q.bid > 0 else q.mid) * 100
    return max(net, 0.0)


def estimate_open_risk(positions: Any) -> float:
    """Defined-risk estimate — group legs by underlying, avoid double-counting spreads."""
    from ..mcp_client import parse_positions

    by_root: dict[str, list[dict]] = defaultdict(list)
    for p in parse_positions(positions):
        sym = str(p.get("symbol") or "")
        asset = str(p.get("asset_class") or "")
        if asset != "us_option" and len(sym) <= 12:
            continue
        by_root[occ_root(sym) or sym[:6]].append(p)

    total = 0.0
    for legs in by_root.values():
        shorts = [p for p in legs if float(p.get("qty") or p.get("quantity") or 0) < 0]
        longs = [p for p in legs if float(p.get("qty") or p.get("quantity") or 0) > 0]
        if shorts and longs:
            qty = min(abs(float(s.get("qty") or s.get("quantity") or 0)) for s in shorts)
            long_debit = sum(abs(float(l.get("cost_basis") or l.get("market_value") or 0)) for l in longs)
            short_credit = sum(abs(float(s.get("cost_basis") or s.get("market_value") or 0)) for s in shorts)
            spread_risk = max(long_debit - short_credit, 0.0)
            if spread_risk <= 0:
                spread_risk = max(
                    abs(float(s.get("market_value") or 0)) + abs(float(l.get("market_value") or 0))
                    for s in shorts for l in longs
                ) / max(qty, 1) * qty
            total += spread_risk
        else:
            for p in legs:
                mv = abs(float(p.get("market_value") or 0))
                cost = abs(float(p.get("cost_basis") or 0))
                total += mv if mv > 0 else cost
    return total


def estimate_portfolio_delta(positions: Any) -> float:
    from ..mcp_client import parse_positions

    total = 0.0
    for p in parse_positions(positions):
        sym = str(p.get("symbol") or "")
        asset = str(p.get("asset_class") or "")
        if asset != "us_option" and len(sym) <= 12:
            continue
        delta = float(p.get("delta") or 0)
        qty = abs(float(p.get("qty") or p.get("quantity") or 0))
        total += delta * qty * 100
    return total


def run_apex_gates(plan: TradePlan, ctx: GateContext, settings) -> list[GateResult]:
    """Ordered gate stack — first failure does not short-circuit logging; all run for audit."""
    results: list[GateResult] = []

    def gate(name: str, ok: bool, reason: str) -> GateResult:
        r = GateResult(ok, name, reason)
        results.append(r)
        return r

    # G1: session phase
    if ctx.session_phase in ("closed", "late"):
        gate("G1_session", False, f"session {ctx.session_phase} — no new entries")
    else:
        gate("G1_session", True, "session open")

    # G2: daily loss halt
    daily_halt = float(getattr(settings, "apex_daily_loss_halt_pct", -2.0))
    if ctx.day_pnl_pct <= daily_halt:
        gate("G2_daily_halt", False, f"daily P&L {ctx.day_pnl_pct:.2f}% ≤ {daily_halt:.2f}%")
    else:
        gate("G2_daily_halt", True, f"daily P&L {ctx.day_pnl_pct:.2f}%")

    # G3: event drawdown halt
    dd_halt = float(getattr(settings, "apex_drawdown_halt_pct", -5.0))
    if ctx.starting_equity > 0:
        dd_pct = (ctx.equity - ctx.starting_equity) / ctx.starting_equity * 100
        if dd_pct <= dd_halt:
            gate("G3_drawdown_halt", False, f"event drawdown {dd_pct:.2f}% ≤ {dd_halt:.2f}%")
        else:
            gate("G3_drawdown_halt", True, f"event drawdown {dd_pct:.2f}%")
    else:
        gate("G3_drawdown_halt", True, "no starting equity baseline")

    # G4: peak drawdown hedge trigger (informational — sleeve handles hedge)
    peak_dd = 0.0
    if ctx.peak_equity > 0:
        peak_dd = (ctx.peak_equity - ctx.equity) / ctx.peak_equity * 100
    gate("G4_peak_drawdown", True, f"peak drawdown {peak_dd:.2f}%")

    # G5: event calendar veto (LLM/news can only remove)
    if plan.symbol in ctx.event_veto_symbols:
        gate("G5_event_veto", False, f"{plan.symbol} vetoed by event calendar")
    else:
        gate("G5_event_veto", True, "no event veto")

    # G6: liquidity — spread checks (relax penny options on indicative paper feed)
    liq_ok = True
    liq_reason = "liquidity ok"
    max_spread = float(getattr(settings, "max_spread_frac", 0.25))
    penny_cap = max_spread + 0.35 if max_spread < 0.5 else max_spread + 0.15
    for q in plan.leg_quotes:
        cap = penny_cap if q.mid < 0.15 else max_spread
        if q.spread_frac > cap:
            liq_ok = False
            liq_reason = f"{q.symbol} spread {q.spread_frac:.0%} > {cap:.0%}"
            break
        if q.mid <= 0:
            liq_ok = False
            liq_reason = f"{q.symbol} no mid"
            break
    gate("G6_liquidity", liq_ok, liq_reason)

    # G7: premium floor for credit structures
    if plan.structure == "credit_spread" and plan.net_credit > 0:
        width = float(getattr(plan, "spread_width", 0) or 500)
        min_ctw = float(getattr(settings, "apex_min_credit_to_width", 0.10))
        min_credit = float(getattr(settings, "apex_min_credit_dollars", 10.0))
        credit = max(_conservative_credit(plan), plan.net_credit * 0.5, 0.0)
        if credit <= 0:
            credit = plan.net_credit
        if credit < min_credit:
            gate("G7_credit_floor", False, f"credit ${credit:.0f} < min ${min_credit:.0f}")
        elif width > 0 and credit / width + 1e-9 < min_ctw:
            gate("G7_credit_floor", False, f"credit/width {credit/width:.2f} < {min_ctw:.2f}")
        else:
            gate("G7_credit_floor", True, f"credit ${credit:.0f} / width ${width:.0f}")
    else:
        gate("G7_credit_floor", True, "not credit structure")

    # G8: per-trade risk cap (allow sizing down — fail only if 1-lot exceeds cap)
    per_trade_pct = float(getattr(settings, "apex_max_loss_per_trade_pct", 2.0))
    at_risk = plan.max_risk if plan.max_risk > 0 else plan.estimated_debit
    cap = ctx.equity * per_trade_pct / 100
    max_qty = int(cap / at_risk) if at_risk > 0 else 0
    if at_risk > cap and max_qty < 1:
        gate("G8_position_size", False, f"risk ${at_risk:.0f} > {per_trade_pct}% cap ${cap:.0f}")
    else:
        gate("G8_position_size", True, f"risk ${at_risk:.0f} ≤ cap ${cap:.0f} (max qty {max(max_qty,1)})")

    # G9: aggregate risk cap (qty-scaled)
    agg_pct = float(getattr(settings, "apex_max_aggregate_risk_pct", 6.0))
    agg_cap = ctx.equity * agg_pct / 100
    qty = max(1, int(ctx.planned_qty or 1))
    per_contract = ctx.at_risk_per_contract if ctx.at_risk_per_contract > 0 else (
        plan.max_risk if plan.max_risk > 0 else plan.estimated_debit
    )
    projected = ctx.open_risk + per_contract * qty
    if projected > agg_cap:
        gate("G9_aggregate", False, f"open+new ${projected:.0f} > {agg_pct}% cap ${agg_cap:.0f} (qty {qty})")
    else:
        util = (projected / agg_cap * 100) if agg_cap > 0 else 0
        gate("G9_aggregate", True, f"projected ${projected:.0f} ({util:.0f}% of cap)")

    # G10: concentration
    max_pos = int(getattr(settings, "apex_max_positions", 6))
    max_per_und = int(getattr(settings, "apex_max_per_underlying", 2))
    und_count = ctx.underlying_count.get(plan.symbol, 0)
    if ctx.position_count >= max_pos:
        gate("G10_concentration", False, f"{ctx.position_count} positions ≥ max {max_pos}")
    elif und_count >= max_per_und:
        gate("G10_concentration", False, f"{plan.symbol} already has {und_count} position(s)")
    else:
        gate("G10_concentration", True, f"{ctx.position_count}/{max_pos} positions")

    # G11: portfolio delta
    max_delta = float(getattr(settings, "apex_max_portfolio_delta", 40))
    plan_delta = sum(
        (1 if q.side == "buy" else -1) * (q.delta or 0) * 100 for q in plan.leg_quotes
    )
    after = ctx.portfolio_delta + plan_delta
    if abs(after) > max_delta:
        gate("G11_portfolio_delta", False, f"delta {after:.0f} > ±{max_delta:.0f}")
    else:
        gate("G11_portfolio_delta", True, f"delta after {after:.0f}")

    # G12: buying power (qty-scaled margin ≈ defined max loss)
    bp = ctx.buying_power or ctx.equity
    deploy_pct = float(getattr(settings, "deploy_cash_pct", 0.98))
    need = per_contract * qty
    bp_budget = bp * deploy_pct
    if need > bp_budget:
        gate("G12_buying_power", False, f"need ${need:.0f} > deploy budget ${bp_budget:.0f} ({deploy_pct:.0%} BP)")
    else:
        gate("G12_buying_power", True, f"deploy ${need:.0f} / ${bp_budget:.0f} budget")

    return results


def gates_passed(results: list[GateResult]) -> tuple[bool, str]:
    failures = [r for r in results if not r.passed]
    if failures:
        return False, "; ".join(f"{r.gate}: {r.reason}" for r in failures)
    return True, "all apex gates passed"
