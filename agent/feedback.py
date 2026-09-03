"""Performance feedback loop — learn from portfolio curve and past decisions."""
from __future__ import annotations

from typing import Any

from .calibration import calibration_score_boost, effective_min_confidence, load_calibration
from .mcp_client import _dig, parse_positions


def _equity_series(raw: Any) -> list[float]:
    if isinstance(raw, dict):
        for key in ("equity",):
            vals = _dig(raw, key)
            if isinstance(vals, list) and len(vals) >= 2:
                out = [float(v) for v in vals if v is not None and float(v) > 1000]
                if len(out) >= 2:
                    return out
        data = raw.get("data") or raw
        if isinstance(data, dict):
            vals = data.get("equity")
            if isinstance(vals, list) and len(vals) >= 2:
                out = [float(v) for v in vals if v is not None and float(v) > 1000]
                if len(out) >= 2:
                    return out
    return []


def _slope(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return (values[-1] - values[0]) / max(abs(values[0]), 1.0)


def build_feedback(
    audit,
    positions: Any,
    portfolio_history: Any,
    *,
    day_pnl_pct: float = 0.0,
) -> dict[str, Any]:
    """Aggregate account + decision history into scoring adjustments."""
    stats = audit.strategy_stats()
    equity_snaps = audit.equity_series(limit=20)
    hist_equity = _equity_series(portfolio_history)

    series = hist_equity or equity_snaps
    equity_slope = _slope(series[-10:]) if series else 0.0
    losing_streak = sum(1 for s in equity_snaps[-5:] if s < (equity_snaps[-6] if len(equity_snaps) > 5 else s)) >= 3

    pos_list = parse_positions(positions)
    open_pnl = 0.0
    for p in pos_list:
        open_pnl += float(p.get("unrealized_pl") or p.get("unrealized_plpc") or 0)

    strategy_adj: dict[str, float] = {}
    symbol_adj: dict[str, float] = {}
    for row in stats:
        strat = row.get("strategy") or "unknown"
        submitted = int(row.get("submitted") or 0)
        rejected = int(row.get("rejected") or 0)
        if submitted == 0 and rejected >= 3:
            strategy_adj[strat] = -3.0
        elif submitted >= 2:
            strategy_adj[strat] = 2.0

    for row in audit.outcome_stats():
        strat = row.get("strategy") or "unknown"
        avg_pnl = float(row.get("avg_pnl_ratio") or 0)
        wins = int(row.get("wins") or 0)
        losses = int(row.get("losses") or 0)
        if losses > wins:
            strategy_adj[strat] = strategy_adj.get(strat, 0) - 5
        elif wins > losses and avg_pnl > 0:
            strategy_adj[strat] = strategy_adj.get(strat, 0) + 4

    scale_down = day_pnl_pct <= -5 or equity_slope < -0.02 or losing_streak
    min_conf_boost = 5.0 if scale_down else 0.0

    return {
        "equity_slope_10d": round(equity_slope, 4),
        "open_unrealized_pnl": round(open_pnl, 2),
        "losing_streak": losing_streak,
        "scale_down": scale_down,
        "min_confidence_boost": min_conf_boost,
        "strategy_adjustments": strategy_adj,
        "symbol_adjustments": symbol_adj,
        "decision_stats": stats,
        "summary": (
            f"slope={equity_slope:+.2%} · day={day_pnl_pct:+.1f}% · "
            f"{'caution' if scale_down else 'normal'} mode"
        ),
    }


def apply_history_to_debate(debate: dict, history: dict) -> dict:
    """Nudge debate confidence when historical regime aligns or conflicts."""
    if not history.get("ok"):
        return debate
    out = dict(debate)
    action = out.get("action", "HOLD")
    conf = float(out.get("confidence", 50))
    bias = history.get("regime_bias")
    adj = float(history.get("confidence_adjustment") or 0)

    if action == bias or (action == "HOLD" and bias in ("VOL", "SELL_VOL")):
        conf += adj
    elif action in ("BUY", "SELL") and bias in ("BUY", "SELL") and action != bias:
        conf -= abs(adj) * 0.5

    if history.get("regime") == "high_vol" and action in ("BUY", "SELL"):
        conf -= 4

    out["confidence"] = max(35, min(95, conf))
    out["history_note"] = history.get("summary")
    return out


def score_candidate(
    base_score: float,
    plan,
    history: dict,
    feedback: dict,
    *,
    calibration: dict | None = None,
) -> float:
    score = base_score
    if history.get("ok") and plan.strategy == history.get("favored_strategy"):
        score += 6
    elif history.get("ok") and plan.strategy in ("bull_call_spread", "bear_put_spread"):
        bias = history.get("regime_bias")
        if (bias == "BUY" and plan.strategy == "bull_call_spread") or (
            bias == "SELL" and plan.strategy == "bear_put_spread"
        ):
            score += 4

    score += feedback.get("strategy_adjustments", {}).get(plan.strategy, 0)
    score += feedback.get("symbol_adjustments", {}).get(plan.symbol, 0)
    if calibration and calibration.get("ok"):
        score += calibration_score_boost(calibration, plan.symbol, plan.strategy)
    if feedback.get("scale_down"):
        score -= 3
    return score
