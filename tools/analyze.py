#!/usr/bin/env python3
"""Run historical market analysis + feedback loop (no orders)."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from agent.calibration import effective_min_confidence, load_calibration
from agent.config import SETTINGS
from agent.audit import AuditStore
from agent.feedback import build_feedback, score_candidate
from agent.mcp_client import AlpacaMCP
from agent.account import account_metrics
from agent.debate import run_debate
from agent.risk.var import monte_carlo_var
from agent.signals.backtest import combined_backtest
from agent.signals.breakout import analyze as analyze_breakout, attach_to_tech as attach_breakout
from agent.signals.history import analyze_history
from agent.signals.indicators import confirm_trade
from agent.signals.intraday import analyze as analyze_intraday
from agent.signals.news import analyze_symbol as analyze_news
from agent.signals.patterns import analyze as analyze_patterns, attach_to_tech as attach_patterns
from agent.signals.pcr import analyze as analyze_pcr
from agent.signals.technical import analyze as analyze_technical, bars_list, closes_from_bars
from agent.signals.universe import discover_dynamic, merge_watchlist
from agent.signals.volatility import analyze as analyze_vol
from agent.strategy import build_plan


def main() -> int:
    audit = AuditStore(SETTINGS.db_path, SETTINGS.audit_dir)
    calibration = load_calibration(SETTINGS.calibration_path)
    candidates: list[tuple[float, str, object, dict]] = []

    with AlpacaMCP() as mcp:
        acct = mcp.account()
        metrics = account_metrics(acct)
        positions = mcp.positions()
        port_hist = mcp.portfolio_history(period="3M", timeframe="1D")
        feedback = build_feedback(audit, positions, port_hist, day_pnl_pct=metrics["daily_pnl_pct"])

        print(f"Account feedback: {feedback['summary']}")
        cal_note = "loaded" if calibration.get("ok") else "missing — run: python tools/calibrate.py"
        print(f"Calibration: {cal_note}")
        print()

        sticky: dict[str, int] = {}
        discovered = discover_dynamic(mcp, SETTINGS, SETTINGS.watchlist)
        scan, added = merge_watchlist(
            SETTINGS.watchlist,
            discovered,
            sticky,
            ttl_ticks=SETTINGS.dynamic_ttl_ticks,
            max_dynamic=SETTINGS.max_dynamic_symbols
            + SETTINGS.max_breakout_symbols
            + SETTINGS.max_pattern_symbols,
        )
        if added:
            print(f"Dynamic watchlist adds: {', '.join(added)}")
        print(f"Scanning {len(scan)} names: {', '.join(scan)}")
        print()

        for symbol in scan:
            bars = mcp.stock_bars(symbol, limit=SETTINGS.history_bars)
            closes = closes_from_bars(bars_list(bars))
            history = analyze_history(symbol, closes)
            tech = analyze_technical(symbol, bars)
            breakout = analyze_breakout(
                symbol,
                bars,
                cons_bars=SETTINGS.breakout_cons_bars,
                max_range_pct=SETTINGS.breakout_max_range_pct,
                max_drift_pct=SETTINGS.breakout_max_drift_pct,
                vol_mult=SETTINGS.breakout_vol_mult,
                max_extension_pct=SETTINGS.breakout_max_extension_pct,
                min_price=SETTINGS.min_dynamic_price,
            )
            tech = attach_breakout(tech, breakout)
            patterns = analyze_patterns(symbol, bars, vol_mult=SETTINGS.pattern_vol_mult)
            try:
                hourly = mcp.stock_bars(symbol, timeframe="1Hour", limit=SETTINGS.hourly_bars)
                intraday = analyze_intraday(symbol, hourly)
            except Exception:
                intraday = {"ok": False, "summary": "n/a", "score_adjustment": 0.0}
            chain = mcp.resolve_option_chain(symbol, SETTINGS)
            vol = analyze_vol(symbol, closes[-60:] if len(closes) > 60 else closes, chain)
            pcr = analyze_pcr(
                symbol,
                chain,
                resistance=SETTINGS.pcr_resistance,
                support=SETTINGS.pcr_support,
            )
            tech = attach_patterns(tech, patterns, pcr)
            tech_debate = dict(tech)
            tech_debate["quant_score"] = max(
                0,
                min(100, float(tech.get("quant_score", 50)) + float(intraday.get("score_adjustment", 0))),
            )
            news = analyze_news(symbol, mcp.news(symbol, limit=10), SETTINGS)
            debate = run_debate(tech_debate, news, vol, SETTINGS, history=history)
            confirm = confirm_trade(str(debate.get("action", "HOLD")), tech_debate, SETTINGS)
            debate["confirm"] = confirm
            base_min = SETTINGS.min_confidence + feedback.get("min_confidence_boost", 0)
            eff_min = effective_min_confidence(
                calibration,
                symbol,
                base_min,
                str(debate.get("action", "HOLD")),
            )

            print(f"{symbol}")
            print(f"  history : {history.get('summary', history.get('reason'))}")
            if tech.get("breakout"):
                print(f"  breakout: {tech.get('breakout_summary')}")
            if tech.get("pattern"):
                print(f"  pattern : {tech.get('pattern_summary')}")
            if tech.get("pcr_summary"):
                print(f"  pcr     : {tech.get('pcr_summary')}")
            if tech.get("indicator_summary"):
                print(f"  indic   : {tech.get('indicator_summary')}")
            if tech.get("pa_summary"):
                print(f"  tape    : {tech.get('pa_summary')}")
            print(f"  confirm : {confirm.get('summary')}")
            if intraday.get("ok"):
                print(f"  intraday: {intraday.get('summary')}")
            print(f"  debate  : {debate['action']} @ {debate['confidence']:.0f}%")
            print(f"  favored : {history.get('favored_strategy', 'n/a')}")

            if debate.get("action") in ("BUY", "SELL") and not confirm.get("ok"):
                print(f"  plan    : skip ({confirm.get('reason')})")
                print()
                continue

            if float(debate.get("confidence", 0)) < eff_min:
                print(f"  plan    : skip (below {eff_min:.0f}% threshold)")
                print()
                continue

            bt = combined_backtest(closes, debate.get("action", "HOLD"))
            var = monte_carlo_var(
                symbol,
                tech["current_price"],
                tech.get("atr", tech["current_price"] * 0.02),
                max_var_pct=SETTINGS.max_var_pct,
            )
            plan = build_plan(symbol, debate, vol, tech, chain, SETTINGS, backtest=bt, var=var)
            if not plan:
                print("  plan    : no qualifying chain/strikes")
                print()
                continue
            plan.confirm = confirm

            score = score_candidate(
                plan.confidence
                + (5 if bt.get("passed") else 0)
                + (8 if tech.get("breakout") else 0)
                + (6 if tech.get("pattern_volume_ok") else 0)
                + (5 if tech.get("pcr_bias") == debate.get("action") else 0),
                plan,
                history,
                feedback,
                calibration=calibration,
            )
            candidates.append((score, symbol, plan, debate))
            print(
                f"  plan    : {plan.strategy} · risk ${plan.estimated_debit:,.0f} · score {score:.0f} · exp {plan.expiry}"
            )
            print()

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        print("=== RANKED (next session priority) ===")
        for score, symbol, plan, debate in candidates:
            print(
                f"  {score:.0f}  {symbol}  {plan.strategy}  ${plan.estimated_debit:,.0f}  "
                f"({debate['action']} @ {debate['confidence']:.0f}%)"
            )
    else:
        print("=== RANKED ===")
        print("  (none above confidence / no buildable plans)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
