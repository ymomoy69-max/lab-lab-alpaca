"""Risk gates — liquidity, VaR, backtest, circuit breaker, premium caps."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..options.chain import occ_root
from ..strategy import TradePlan
from .correlation import correlation_gate, spy_regime_gate
from .greeks import greeks_preflight
from .session import session_gate
from .gates import GateContext, estimate_open_risk, estimate_portfolio_delta, gates_passed, run_apex_gates


def indicator_gate(plan, settings) -> str | None:
    if not getattr(settings, "indicator_confirm", True):
        return None
    confirm = getattr(plan, "confirm", None) or {}
    if confirm and confirm.get("ok") is False:
        return f"indicator confirm: {confirm.get('reason', 'failed')}"
    return None


@dataclass
class RiskVerdict:
    approved: bool
    reason: str
    qty: int = 0
    limit_price: float | None = None


from ..mcp_client import parse_positions


def _option_positions(positions: Any) -> list[dict]:
    raw = parse_positions(positions)
    out = []
    for p in raw:
        sym = str(p.get("symbol") or "")
        asset = str(p.get("asset_class") or "")
        if asset == "us_option" or len(sym) > 12:
            out.append(p)
    return out


def estimate_premium_at_risk(positions: Any) -> float:
    total = 0.0
    for p in _option_positions(positions):
        mv = abs(float(p.get("market_value") or 0))
        if mv > 0:
            total += mv
        else:
            total += abs(float(p.get("cost_basis") or 0))
    return total


def macro_veto(news: dict, threshold: float) -> str | None:
    score = float(news.get("score") or 0)
    if score <= threshold:
        return f"macro VETO: news score {score:.2f} <= {threshold}"
    return None


def _capital_at_risk(plan: TradePlan) -> float:
    if plan.max_risk > 0:
        return plan.max_risk
    return plan.estimated_debit


def evaluate(
    plan: TradePlan,
    equity: float,
    positions: Any,
    settings,
    *,
    news: dict | None = None,
    day_pnl_pct: float = 0.0,
    clock: Any | None = None,
    cash: float | None = None,
    remaining_slots: int | None = None,
    spy_history: dict | None = None,
    new_entries: bool = True,
) -> RiskVerdict:
    if not settings.paper:
        return RiskVerdict(False, "paper-only mode required")

    if new_entries and clock:
        sess = session_gate(
            clock,
            open_buffer_min=settings.session_open_buffer_min,
            close_buffer_min=settings.session_close_buffer_min,
        )
        if sess and sess != "market closed":
            return RiskVerdict(False, sess)

    if day_pnl_pct <= settings.circuit_breaker_pct:
        return RiskVerdict(False, f"circuit breaker: day P&L {day_pnl_pct:.1f}%")

    size_from_risk = bool(
        getattr(settings, "size_from_risk", False) or getattr(settings, "scalp_mode", False)
    )

    if news:
        veto = macro_veto(news, settings.macro_veto_threshold)
        if veto and plan.structure not in ("long_vol", "credit_spread"):
            return RiskVerdict(False, veto)

    if plan.backtest and not plan.backtest.get("passed", True) and not size_from_risk:
        return RiskVerdict(False, f"backtest failed: {plan.backtest.get('reason')}")

    if plan.var and not plan.var.get("passed", True) and not size_from_risk:
        return RiskVerdict(False, f"VaR failed: {plan.var.get('reason')}")

    greek_block = None
    if not getattr(settings, "scalp_mode", False):
        greek_block = greeks_preflight(plan, scalp=False)
    if greek_block:
        return RiskVerdict(False, greek_block)

    corr_block = correlation_gate(plan, positions, settings=settings)
    if corr_block:
        return RiskVerdict(False, corr_block)

    if not getattr(settings, "scalp_mode", False):
        spy_block = spy_regime_gate(plan, spy_history)
        if spy_block:
            return RiskVerdict(False, spy_block)

    ind_block = indicator_gate(plan, settings)
    if ind_block:
        return RiskVerdict(False, ind_block)

    at_risk = _capital_at_risk(plan)
    if at_risk <= 0:
        return RiskVerdict(False, "no priced risk from live quotes")

    for q in plan.leg_quotes:
        if q.spread_frac > settings.max_spread_frac:
            return RiskVerdict(False, f"illiquid {q.symbol} spread {q.spread_frac:.0%}")

    opts = _option_positions(positions)
    structures = len({occ_root(str(p.get("symbol") or "")) for p in opts}) or len(opts) // 2
    if structures >= settings.max_structures:
        return RiskVerdict(False, f"max structures ({settings.max_structures})")

    premium_used = estimate_premium_at_risk(positions)
    cash_now = float(cash) if cash is not None and cash > 0 else equity
    deploy_pct = float(getattr(settings, "deploy_cash_pct", 0.98) or 0.98)
    deploy_pct = min(max(deploy_pct, 0.5), 1.0)
    cash_pool = max(0.0, cash_now * deploy_pct)
    max_total = equity * settings.max_total_premium_pct / 100
    remaining_prem = max(0.0, max_total - premium_used)
    pool = min(cash_pool, remaining_prem) if remaining_prem > 0 else cash_pool
    if pool < at_risk:
        return RiskVerdict(False, f"not enough cash for 1 contract (${pool:,.0f} < ${at_risk:,.0f})")

    slots = remaining_slots
    if slots is None:
        slots = max(1, int(settings.max_structures) - structures)
    slots = max(1, int(slots))
    slice_ = pool / slots
    qty_cap = int(getattr(settings, "max_contracts", 200) or 200)
    qty = max(1, min(int(slice_ / at_risk), qty_cap))
    if qty < 1:
        return RiskVerdict(False, "computed qty < 1")
    if not getattr(settings, "scalp_mode", False):
        if size_from_risk and plan.var:
            var_pct = float(plan.var.get("var_95_pct") or 0)
            cap = float(settings.max_var_pct or 0)
            if var_pct > cap > 0:
                qty = max(1, int(qty * cap / var_pct))
        if size_from_risk and plan.backtest and not plan.backtest.get("passed", True):
            qty = max(1, int(qty * 0.5))
        size_mult = float((getattr(plan, "confirm", None) or {}).get("size_mult") or 1.0)
        if 0 < size_mult < 0.99:
            qty = max(1, int(qty * size_mult))

    limit = plan.limit_price
    if plan.legs and plan.structure == "debit_spread":
        limit = round(plan.estimated_debit * (1 + settings.limit_slippage) / 100, 2)

    label = "credit" if plan.net_credit > 0 else "debit"
    return RiskVerdict(
        True,
        f"approved qty={qty} {label}≈${at_risk:,.0f} limit={limit}",
        qty=qty,
        limit_price=limit,
    )


def _apex_calculate_qty(plan: TradePlan, ctx: GateContext, settings, at_risk_per: float) -> int:
    """Size to per-trade cap, aggregate budget, and buying power — quality-scaled for thin credits."""
    if at_risk_per <= 0:
        return 0

    equity = ctx.equity
    deploy_pct = float(getattr(settings, "deploy_cash_pct", 0.98))
    per_trade_pct = float(getattr(settings, "apex_max_loss_per_trade_pct", 2.5))
    agg_pct = float(getattr(settings, "apex_max_aggregate_risk_pct", 28.0))

    per_trade_cap = equity * per_trade_pct / 100
    agg_cap = equity * agg_pct / 100
    remaining = max(0.0, agg_cap - ctx.open_risk)
    bp_budget = (ctx.buying_power or equity) * deploy_pct

    qty = int(per_trade_cap / at_risk_per)
    qty = min(qty, int(remaining / at_risk_per))
    qty = min(qty, int(bp_budget / at_risk_per))

    if plan.sleeve == "income" and plan.structure == "credit_spread":
        credit = float(plan.net_credit or 0)
        if credit >= 40:
            quality_cap = 25
        elif credit >= 25:
            quality_cap = 15
        elif credit >= 15:
            quality_cap = 10
        else:
            quality_cap = 6
        qty = min(qty, quality_cap)
    elif plan.sleeve == "long_vol":
        qty = min(qty, 3)

    qty = min(qty, int(getattr(settings, "max_contracts", 200) or 200))
    return max(1, qty) if qty >= 1 else 0


def evaluate_apex(
    plan: TradePlan,
    equity: float,
    positions: Any,
    settings,
    *,
    day_pnl_pct: float = 0.0,
    clock: Any | None = None,
    cash: float | None = None,
    starting_equity: float | None = None,
    peak_equity: float | None = None,
    event_veto_symbols: set[str] | None = None,
) -> RiskVerdict:
    """Apex gate kernel + sizing — LLM cannot override."""
    if not settings.paper:
        return RiskVerdict(False, "paper-only mode required")

    if clock:
        sess = session_gate(
            clock,
            open_buffer_min=settings.session_open_buffer_min,
            close_buffer_min=settings.session_close_buffer_min,
        )
        if sess and sess != "market closed":
            return RiskVerdict(False, sess)

    opts = _option_positions(positions)
    und_count: dict[str, int] = {}
    for p in opts:
        root = occ_root(str(p.get("symbol") or ""))
        if not root:
            continue
        qty = float(p.get("qty") or p.get("quantity") or 0)
        if qty < 0:
            und_count[root] = und_count.get(root, 0) + 1

    ctx = GateContext(
        equity=equity,
        starting_equity=starting_equity or equity,
        peak_equity=peak_equity or equity,
        day_pnl_pct=day_pnl_pct,
        open_risk=estimate_open_risk(positions),
        portfolio_delta=estimate_portfolio_delta(positions),
        position_count=sum(und_count.values()),
        underlying_count=und_count,
        buying_power=float(cash) if cash else equity,
        event_veto_symbols=event_veto_symbols or set(),
    )

    at_risk = _capital_at_risk(plan)
    qty = _apex_calculate_qty(plan, ctx, settings, at_risk)
    if qty < 1:
        return RiskVerdict(False, "aggregate/buying-power budget full — no room for new risk")

    ctx.planned_qty = qty
    ctx.at_risk_per_contract = at_risk

    gate_results = run_apex_gates(plan, ctx, settings)
    ok, reason = gates_passed(gate_results)
    if not ok:
        # Retry with reduced size if aggregate or BP gate failed
        for reduced in (max(1, qty // 2), 1):
            if reduced >= qty:
                continue
            ctx.planned_qty = reduced
            gate_results = run_apex_gates(plan, ctx, settings)
            ok, reason = gates_passed(gate_results)
            if ok:
                qty = reduced
                break
        if not ok:
            return RiskVerdict(False, reason)

    greek_block = greeks_preflight(plan, max_short_delta=0.35, scalp=False)
    if greek_block:
        return RiskVerdict(False, greek_block)

    total_risk = at_risk * qty
    agg_cap = equity * float(getattr(settings, "apex_max_aggregate_risk_pct", 28.0)) / 100
    util_pct = (ctx.open_risk + total_risk) / agg_cap * 100 if agg_cap > 0 else 0

    limit = plan.limit_price
    if plan.structure == "credit_spread":
        limit = plan.limit_price
    elif plan.legs and plan.structure == "debit_spread":
        limit = round(plan.estimated_debit * (1 + settings.limit_slippage) / 100, 2)

    return RiskVerdict(
        True,
        f"apex qty={qty} risk=${total_risk:,.0f} ({util_pct:.0f}% agg cap) [{plan.sleeve}]",
        qty=qty,
        limit_price=limit,
    )
