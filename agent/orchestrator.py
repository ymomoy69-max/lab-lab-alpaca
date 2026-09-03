"""Main agent loop — full fusion pipeline with history + feedback + lifecycle."""
from __future__ import annotations

import threading
import time
from datetime import date
from typing import Callable

from .account import account_metrics
from .audit import AuditStore
from .config import SETTINGS, Settings, PRIORITY_STOCKS
from .calibration import effective_min_confidence, load_calibration
from .debate import run_debate
from .executor import Executor
from .feedback import build_feedback, score_candidate
from .mcp_client import AlpacaMCP, market_open, parse_account_number
from .positions import PositionManager
from .rest_client import RestExecutor
from .risk import evaluate, evaluate_apex
from .risk.correlation import THEME_MAP
from .risk.surface import choose_apex_sleeve, measure_surface
from .risk.var import monte_carlo_var
from .signals.backtest import combined_backtest, scalp_backtest
from .signals.breakout import analyze as analyze_breakout, attach_to_tech as attach_breakout
from .signals.history import analyze_history
from .signals.indicators import confirm_trade
from .signals.intraday import analyze as analyze_intraday, analyze_scalp
from .signals.news import analyze_symbol as analyze_news
from .signals.patterns import analyze as analyze_patterns, attach_to_tech as attach_patterns
from .signals.pcr import analyze as analyze_pcr
from .signals.technical import analyze as analyze_technical, bars_list, closes_from_bars
from .signals.universe import discover_dynamic, merge_watchlist
from .options.chain import parse_chain
from .signals.volatility import analyze as analyze_vol
from .strategy import build_apex_plan, build_plan, pick_expiry


class NexusOrchestrator:
    def __init__(self, settings: Settings | None = None, on_event: Callable | None = None):
        self.settings = settings or SETTINGS
        self.audit = AuditStore(self.settings.db_path, self.settings.audit_dir)
        self.on_event = on_event or (lambda *_: None)
        self._running = False
        self._thread: threading.Thread | None = None
        self._mcp: AlpacaMCP | None = None
        self._last_feedback: dict | None = None
        self._spy_history: dict | None = None
        self._sticky_dynamic: dict[str, int] = {}
        self._peak_equity: float | None = None
        self._starting_equity: float | None = None
        self._peak_session_pnl_pct: float | None = None
        self._profit_floor_halted: bool = False

    def _emit(self, type_: str, message: str, symbol: str | None = None, payload: dict | None = None):
        self.audit.event(type_, message, symbol, payload)
        self.on_event(type_, message, symbol, payload or {})

    def _guard(self) -> str | None:
        if not self.settings.armed:
            return "NEXUS_ARMED!=yes — dry run only"
        if not self.settings.paper:
            return "ALPACA_PAPER must be true"
        if self.settings.contest_enforce and self.settings.contest_close and date.today() > self.settings.contest_close:
            return "contest window closed"
        exp = self.settings.expected_account
        if exp and self._mcp:
            acct = self._mcp.account()
            num = parse_account_number(acct)
            if num and exp not in num:
                return f"wrong account {num}, expected {exp}"
        return None

    def _load_spy_history(self, mcp: AlpacaMCP) -> dict | None:
        try:
            bars = mcp.stock_bars("SPY", limit=self.settings.history_bars)
            closes = closes_from_bars(bars_list(bars))
            return analyze_history("SPY", closes)
        except Exception:
            return None

    def _analyze_symbol(
        self,
        mcp: AlpacaMCP,
        symbol: str,
        feedback: dict,
        effective_min_conf: float,
        calibration: dict | None = None,
    ) -> tuple[float, object, dict, dict] | None:
        scalp_mode = bool(self.settings.scalp_mode)
        if scalp_mode:
            bars = mcp.stock_bars(
                symbol,
                timeframe=self.settings.scalp_bar_timeframe,
                limit=self.settings.scalp_bars,
            )
        else:
            bars = mcp.stock_bars(symbol, limit=self.settings.history_bars)
        bar_list = bars_list(bars)
        closes = closes_from_bars(bar_list)
        tech = analyze_technical(symbol, bars)
        if not tech.get("ok"):
            return None

        scalp = {"ok": False, "score_adjustment": 0.0, "side": ""}
        if scalp_mode:
            hold_bars = max(5, min(10, int(self.settings.scalp_hold_sec or 600) // 60))
            scalp = analyze_scalp(symbol, bars, hold_bars=hold_bars)
            tech["scalp"] = scalp
            if not scalp.get("ok"):
                return None
            self._emit("scalp", scalp.get("summary", "scalp"), symbol, scalp)
            tech["quant_score"] = max(
                0,
                min(100, float(tech.get("quant_score", 50)) + float(scalp.get("score_adjustment", 0))),
            )
            breakout = {"breakout": False}
            patterns = {}
            news = {"ok": True, "score": 0, "label": "neutral"}
            history = {"ok": False}
            intraday = scalp
        else:
            breakout = analyze_breakout(
                symbol,
                bars,
                cons_bars=self.settings.breakout_cons_bars,
                max_range_pct=self.settings.breakout_max_range_pct,
                max_drift_pct=self.settings.breakout_max_drift_pct,
                vol_mult=self.settings.breakout_vol_mult,
                max_extension_pct=self.settings.breakout_max_extension_pct,
                min_price=self.settings.min_dynamic_price,
            )
            tech = attach_breakout(tech, breakout)
            if breakout.get("breakout"):
                self._emit("breakout", breakout.get("summary", "breakout"), symbol, breakout)

            patterns = analyze_patterns(symbol, bars, vol_mult=self.settings.pattern_vol_mult)

            try:
                hourly = mcp.stock_bars(symbol, timeframe="1Hour", limit=self.settings.hourly_bars)
                intraday = analyze_intraday(symbol, hourly)
            except Exception:
                intraday = {"ok": False, "score_adjustment": 0.0}

            history = analyze_history(symbol, closes)
            news = analyze_news(symbol, mcp.news(symbol, limit=10), self.settings)
            tech["quant_score"] = max(
                0,
                min(100, float(tech.get("quant_score", 50)) + float(intraday.get("score_adjustment", 0))),
            )
        target_exp = pick_expiry(
            self.settings.min_dte,
            self.settings.max_dte,
            contest_close=self.settings.contest_close if self.settings.contest_expiry_exit else None,
        )
        chain = mcp.resolve_option_chain(symbol, self.settings)
        vol = analyze_vol(symbol, closes[-60:] if len(closes) > 60 else closes, chain)
        pcr = analyze_pcr(
            symbol,
            chain,
            resistance=self.settings.pcr_resistance,
            support=self.settings.pcr_support,
        )
        tech = attach_patterns(tech, patterns, pcr)
        if patterns.get("side"):
            self._emit("pattern", patterns.get("summary", "pattern"), symbol, patterns)
        if pcr.get("ok") and pcr.get("zone") in ("support", "resistance"):
            self._emit("pcr", pcr.get("summary", "pcr"), symbol, pcr)
        pa = tech.get("price_action") or {}
        if pa.get("ok"):
            self._emit("price_action", pa.get("summary", "price action"), symbol, pa)

        tech_debate = dict(tech)
        if not scalp_mode:
            tech_debate["quant_score"] = max(
                0,
                min(100, float(tech.get("quant_score", 50)) + float(intraday.get("score_adjustment", 0))),
            )
        debate = run_debate(tech_debate, news, vol, self.settings, history=history)

        confirm = {"ok": True, "size_mult": 1.0, "summary": "confirm skipped"}
        if self.settings.indicator_confirm:
            confirm = confirm_trade(str(debate.get("action", "HOLD")), tech_debate, self.settings)
            self._emit("confirm", confirm.get("summary", "confirm"), symbol, confirm)
            if debate.get("action") in ("BUY", "SELL") and not confirm.get("ok"):
                self._emit("risk_block", confirm.get("reason", "indicator confirm failed"), symbol, confirm)
                return None
        tech_debate["confirm"] = confirm
        debate["confirm"] = confirm

        self._emit(
            "debate",
            f"{symbol}: {debate['action']} @ {debate['confidence']:.0f}%",
            symbol,
            {
                "debate": debate,
                "vol": vol,
                "news": news,
                "tech": tech,
                "intraday": intraday,
                "history": history,
                "breakout": breakout,
                "patterns": patterns,
                "pcr": pcr,
                "confirm": confirm,
            },
        )

        min_conf = effective_min_confidence(
            calibration or {},
            symbol,
            effective_min_conf,
            str(debate.get("action", "HOLD")),
        )
        if float(debate.get("confidence", 0)) < min_conf:
            return None

        if scalp_mode:
            hold_bars = max(5, min(10, int(self.settings.scalp_hold_sec or 600) // 60))
            bt = scalp_backtest(
                closes,
                debate.get("action", "HOLD"),
                hold_bars=hold_bars,
                min_win_rate=self.settings.min_backtest_win_rate,
            )
            var = monte_carlo_var(
                symbol,
                tech["current_price"],
                tech.get("atr", tech["current_price"] * 0.02),
                max_var_pct=self.settings.max_var_pct,
                horizon_minutes=int(self.settings.var_horizon_min or 10),
            )
        else:
            bt = combined_backtest(
                closes,
                debate.get("action", "HOLD"),
                min_win_rate=self.settings.min_backtest_win_rate,
            )
            var = monte_carlo_var(
                symbol,
                tech["current_price"],
                tech.get("atr", tech["current_price"] * 0.02),
                max_var_pct=self.settings.max_var_pct,
            )

        plan = build_plan(symbol, debate, vol, tech, chain, self.settings, backtest=bt, var=var)
        if not plan:
            self._emit(
                "risk_block",
                "no executable options structure (empty/illiquid chain in DTE window)",
                symbol,
                {"action": debate.get("action"), "expiry_window": f"{self.settings.min_dte}-{self.settings.max_dte}d"},
            )
            return None
        plan.confirm = confirm

        base = plan.confidence + (5 if bt.get("passed") else 0) + (3 if var.get("passed") else 0)
        if tech.get("breakout"):
            base += 8
        if tech.get("pattern_side") and tech.get("pattern_volume_ok"):
            base += 6
        if tech.get("pcr_bias") and debate.get("action") == tech.get("pcr_bias"):
            base += 5
        if confirm.get("ok") and float(confirm.get("aligned") or 0) >= 4:
            base += 4
        if tech.get("pa_side") == ("up" if debate.get("action") == "BUY" else "down" if debate.get("action") == "SELL" else ""):
            base += 10
        score = score_candidate(base, plan, history, feedback, calibration=calibration)
        return score, plan, news, debate

    def _resolve_scan_list(self, mcp: AlpacaMCP) -> list[str]:
        discovered: list[dict] = []
        added: list[str] = []
        if self.settings.dynamic_watchlist and not self.settings.scalp_mode:
            discovered = discover_dynamic(mcp, self.settings, self.settings.watchlist)
            scan, added = merge_watchlist(
                self.settings.watchlist,
                discovered,
                self._sticky_dynamic,
                ttl_ticks=self.settings.dynamic_ttl_ticks,
                max_dynamic=self.settings.max_dynamic_symbols
                + self.settings.max_breakout_symbols
                + self.settings.max_pattern_symbols,
            )
        else:
            scan = [s for s in self.settings.watchlist if s and s != "MARKET"]

        extras = [s for s in scan if s not in self.settings.watchlist]
        self._emit(
            "watchlist",
            f"scan {len(scan)} names"
            + (f" (+{', '.join(added)})" if added else "")
            + (f" · dynamic {', '.join(extras)}" if extras else " · core only"),
            payload={
                "scan": scan,
                "added": added,
                "dynamic": extras,
                "discovered": discovered,
            },
        )
        return scan

    def _limit_scalp_scan(self, scan: list[str], positions) -> list[str]:
        from .mcp_client import parse_positions
        from .options.chain import occ_root

        limit = max(4, int(getattr(self.settings, "scalp_scan_limit", 12) or 12))
        open_und: list[str] = []
        for p in parse_positions(positions):
            sym = str(p.get("symbol") or "")
            if str(p.get("asset_class") or "") == "us_option" or len(sym) > 12:
                u = occ_root(sym)
                if u and u not in open_und:
                    open_und.append(u)
        core = [s for s in scan if s and s != "MARKET"]
        stocks = [s for s in PRIORITY_STOCKS if s in core]
        rest = [s for s in core if s not in stocks]
        ordered: list[str] = []
        for s in open_und + stocks + rest:
            if s not in ordered:
                ordered.append(s)
        out = ordered[: max(limit, len(open_und))]
        self._emit("watchlist", f"scalp tape {len(out)} names · 1m / {self.settings.scalp_hold_sec // 60}m hold", payload={"scan": out})
        return out

    def _symbol_min_conf(self, symbol: str, feedback: dict, calibration: dict) -> float:
        base = self.settings.min_confidence + feedback.get("min_confidence_boost", 0)
        return effective_min_confidence(calibration, symbol, base)

    def _run_analysis_only(
        self,
        mcp: AlpacaMCP,
        feedback: dict,
        calibration: dict,
        symbols: list[str],
        *,
        reason: str,
    ) -> None:
        self._emit("status", reason)
        for symbol in symbols:
            if symbol in ("MARKET", ""):
                continue
            try:
                eff_min = self._symbol_min_conf(symbol, feedback, calibration)
                result = self._analyze_symbol(mcp, symbol, feedback, eff_min, calibration)
                if result:
                    score, plan, _, debate = result
                    self._emit(
                        "analysis",
                        f"{symbol}: would trade {plan.strategy} (score={score:.0f})",
                        symbol,
                        {"plan": plan.strategy, "score": score, "debate": debate["action"]},
                    )
            except Exception as e:
                self._emit("error", str(e), symbol)

    def tick(self) -> None:
        with AlpacaMCP(audit_fn=self.audit.mcp) as mcp:
            self._mcp = mcp
            try:
                self._tick_body(mcp)
            finally:
                self._mcp = None

    def _tick_body(self, mcp: AlpacaMCP) -> None:
        if getattr(self.settings, "apex_mode", False):
            self._tick_apex(mcp)
            return
        guard = self._guard()
        clock = mcp.clock()
        open_now = market_open(clock)

        acct = mcp.account()
        metrics = account_metrics(acct)
        equity = metrics["equity"]
        positions = mcp.positions()
        port_hist = mcp.portfolio_history(period="3M", timeframe="1D")
        self.audit.save_portfolio(metrics, positions)

        self._spy_history = self._load_spy_history(mcp)

        feedback = build_feedback(
            self.audit,
            positions,
            port_hist,
            day_pnl_pct=metrics["daily_pnl_pct"],
        )
        self._last_feedback = feedback
        calibration = load_calibration(self.settings.calibration_path)
        self._emit("feedback", feedback["summary"], payload=feedback)

        self._emit(
            "tick",
            f"equity=${equity:,.0f} day P&L {metrics['daily_pnl_pct']:+.2f}%",
            payload=metrics,
        )

        scan = self._resolve_scan_list(mcp)
        if self.settings.scalp_mode:
            scan = self._limit_scalp_scan(scan, positions)

        if open_now and self.settings.armed:
            if not self.settings.scalp_mode:
                try:
                    cancelled = mcp.cancel_all()
                    self._emit("status", "cancelled open/unfilled orders before new entries", payload={"result": str(cancelled)[:400]})
                except Exception as e:
                    try:
                        RestExecutor().cancel_all()
                        self._emit("status", "cancelled open orders via REST")
                    except Exception as e2:
                        self._emit("warn", f"cancel open orders failed: {e}; REST: {e2}")
            pm = PositionManager(mcp, self.audit, self.settings)
            closed = pm.manage(positions)
            for c in closed:
                if c.get("ok"):
                    self._emit("position_close", c.get("reason", "closed"), c.get("symbol", "")[:6])
            if closed:
                positions = mcp.positions()

        if not open_now:
            if self.settings.analyze_when_closed:
                self._run_analysis_only(
                    mcp,
                    feedback,
                    calibration,
                    scan,
                    reason="market closed — analysis-only mode",
                )
            else:
                self._emit("status", "market closed — skipping tick")
            return

        if guard:
            if guard.startswith("NEXUS_ARMED") and self.settings.analyze_when_closed:
                self._run_analysis_only(
                    mcp,
                    feedback,
                    calibration,
                    scan,
                    reason=f"{guard} — analysis-only mode",
                )
                return
            self._emit("risk_block", guard)
            return

        candidates: list[tuple[float, object, dict, dict]] = []
        open_und: set[str] = set()
        if self.settings.scalp_mode:
            from .mcp_client import parse_positions
            from .options.chain import occ_root

            open_und = {
                occ_root(str(p.get("symbol") or ""))
                for p in parse_positions(positions)
                if str(p.get("asset_class") or "") == "us_option" or len(str(p.get("symbol") or "")) > 12
            }
            open_und.discard("")

        for symbol in scan:
            if symbol in ("MARKET", ""):
                continue
            if symbol in open_und:
                continue
            try:
                eff_min = self._symbol_min_conf(symbol, feedback, calibration)
                result = self._analyze_symbol(mcp, symbol, feedback, eff_min, calibration)
                if result:
                    candidates.append(result)
            except Exception as e:
                self._emit("error", str(e), symbol)

        if not candidates:
            self._emit("decision", "no trade — no qualifying setup")
            return

        stock_rank = {s: i for i, s in enumerate(PRIORITY_STOCKS)}
        candidates.sort(key=lambda x: (stock_rank.get(x[1].symbol, 99), -x[0]))
        max_entries = max(1, int(getattr(self.settings, "max_entries_per_tick", 3) or 3))
        if self.settings.scalp_mode:
            slots = max(0, int(self.settings.max_structures) - len(open_und))
            max_entries = max(0, min(max_entries, slots))
        taken_symbols: set[str] = set()
        taken_themes: set[str] = set()
        filled_any = False
        ex = Executor(mcp, self.audit, self.settings)

        for score, plan, news, debate in candidates:
            if max_entries <= 0:
                self._emit("decision", "book full — waiting for scalp exits")
                break
            if len(taken_symbols) >= max_entries:
                break
            if plan.symbol in taken_symbols:
                continue
            theme = THEME_MAP.get(plan.symbol, plan.symbol)
            if (not self.settings.scalp_mode) and theme in taken_themes:
                self._emit("decision", f"skip {plan.symbol} — already taking {theme} this tick", plan.symbol)
                continue

            slots_left = max(1, max_entries - len(taken_symbols))
            verdict = evaluate(
                plan,
                equity,
                positions,
                self.settings,
                news=news,
                day_pnl_pct=metrics["daily_pnl_pct"],
                clock=clock,
                cash=metrics.get("cash"),
                remaining_slots=slots_left,
                spy_history=self._spy_history,
            )
            if not verdict.approved:
                self.audit.decision(
                    plan.symbol,
                    debate.get("action", "SKIP"),
                    plan.strategy,
                    plan.confidence,
                    "REJECTED",
                    verdict.reason,
                )
                self._emit("risk_block", verdict.reason, plan.symbol, {"plan": plan.strategy})
                continue

            self._emit(
                "order_submitted",
                plan.rationale,
                plan.symbol,
                {
                    "strategy": plan.strategy,
                    "debit": plan.estimated_debit,
                    "qty": verdict.qty,
                    "limit": verdict.limit_price,
                    "score": score,
                },
            )
            try:
                exec_result = ex.execute(plan, verdict.qty, verdict.limit_price)
                verified = exec_result.get("verified", {})
                status = "SUBMITTED" if verified.get("all_filled", True) else "PARTIAL"
                self.audit.decision(
                    plan.symbol,
                    "TRADE",
                    plan.strategy,
                    plan.confidence,
                    status,
                    plan.rationale,
                )
                occ = None
                if plan.leg_quotes:
                    occ = next((q.symbol for q in plan.leg_quotes if q.side == "buy"), plan.leg_quotes[0].symbol)
                self.audit.record_orders(plan.symbol, exec_result)
                self.audit.record_outcome(
                    plan.symbol,
                    plan.strategy,
                    symbol=occ,
                    status="OPEN",
                    reason=plan.rationale,
                    payload={"exec": str(exec_result)[:2000], "occ": occ, "scalp": True},
                )
                if verified.get("all_filled"):
                    filled_any = True
                    positions = mcp.positions()
                try:
                    metrics = account_metrics(mcp.account())
                    equity = metrics["equity"]
                except Exception:
                    pass
                self._emit(
                    "order_filled",
                    f"orders {status.lower()}",
                    plan.symbol,
                    {"verified": verified},
                )
                taken_symbols.add(plan.symbol)
                taken_themes.add(theme)
            except Exception as e:
                self.audit.decision(
                    plan.symbol, "TRADE", plan.strategy, plan.confidence, "ERROR", str(e)
                )
                self._emit("error", str(e), plan.symbol)

        if not taken_symbols and not filled_any:
            self._emit("decision", "no trade — candidates blocked or unfilled")

    def _event_veto_symbols(self, mcp: AlpacaMCP, symbols: list[str]) -> set[str]:
        """LLM veto-only: news score can remove symbols from trading (Underwriter G7)."""
        veto: set[str] = set()
        if not getattr(self.settings, "apex_llm_veto_only", True):
            return veto
        for symbol in symbols:
            try:
                news = analyze_news(symbol, mcp.news(symbol, limit=8), self.settings)
                score = float(news.get("score") or 0)
                if score <= self.settings.macro_veto_threshold:
                    veto.add(symbol)
                    self._emit("apex_veto", f"event calendar veto {symbol} (news {score:.2f})", symbol, news)
            except Exception:
                pass
        return veto

    def _session_pnl_pct(self, equity: float) -> float:
        if not self._starting_equity or self._starting_equity <= 0:
            return 0.0
        return (equity - self._starting_equity) / self._starting_equity * 100

    def _check_profit_floor(self, mcp: AlpacaMCP, equity: float, positions: Any) -> bool:
        """If session profit drops below floor after reaching it, liquidate all and halt."""
        if not getattr(self.settings, "apex_profit_floor_enabled", True):
            return False
        if self._profit_floor_halted:
            return True

        floor = float(getattr(self.settings, "apex_profit_floor_pct", 8.0))
        session_pnl = self._session_pnl_pct(equity)
        if self._peak_session_pnl_pct is None:
            self._peak_session_pnl_pct = session_pnl
        else:
            self._peak_session_pnl_pct = max(self._peak_session_pnl_pct, session_pnl)

        if self._peak_session_pnl_pct < floor or session_pnl >= floor:
            return False

        self._profit_floor_halted = True
        self._emit(
            "profit_floor",
            f"session profit {session_pnl:+.2f}% fell below {floor:.1f}% floor "
            f"(peak was {self._peak_session_pnl_pct:+.2f}%) — liquidating all",
            payload={"session_pnl_pct": session_pnl, "peak_pnl_pct": self._peak_session_pnl_pct, "floor": floor},
        )
        pm = PositionManager(mcp, self.audit, self.settings)
        closed = pm.liquidate_all(positions, reason=f"profit floor {floor:.1f}%")
        for c in closed:
            sym = str(c.get("symbol") or "")[:12]
            if c.get("ok"):
                self._emit("position_close", c.get("reason", "liquidated"), sym)
            else:
                self._emit("error", c.get("error", "liquidate failed"), sym)
        return True

    def _tick_apex(self, mcp: AlpacaMCP) -> None:
        """Apex Underwriter loop — IV/RV regime, skew-adaptive credit, deterministic gates."""
        guard = self._guard()
        clock = mcp.clock()
        open_now = market_open(clock)

        acct = mcp.account()
        metrics = account_metrics(acct)
        equity = metrics["equity"]
        if self._starting_equity is None:
            self._starting_equity = equity
        if self._peak_equity is None or equity > self._peak_equity:
            self._peak_equity = equity
        peak_dd_pct = ((self._peak_equity - equity) / self._peak_equity * 100) if self._peak_equity else 0.0

        positions = mcp.positions()
        if self._check_profit_floor(mcp, equity, positions):
            self._emit("status", "profit floor halt — all trading stopped for this session")
            return

        port_hist = mcp.portfolio_history(period="3M", timeframe="1D")
        self.audit.save_portfolio(metrics, positions)

        feedback = build_feedback(
            self.audit, positions, port_hist, day_pnl_pct=metrics["daily_pnl_pct"],
        )
        self._last_feedback = feedback
        calibration = load_calibration(self.settings.calibration_path)
        self._emit("feedback", feedback["summary"], payload=feedback)
        from .risk.gates import estimate_open_risk

        open_risk = estimate_open_risk(positions)
        agg_cap = equity * float(self.settings.apex_max_aggregate_risk_pct) / 100
        deploy_util = (open_risk / agg_cap * 100) if agg_cap > 0 else 0
        hold_h = int(getattr(self.settings, "apex_hold_sec", 10800) or 10800) // 3600
        self._emit(
            "tick",
            f"[APEX 0DTE] equity=${equity:,.0f} day {metrics['daily_pnl_pct']:+.2f}% · "
            f"risk ${open_risk:,.0f}/{agg_cap:,.0f} ({deploy_util:.0f}%) · max hold {hold_h}h",
            payload={**metrics, "open_risk": open_risk, "agg_cap": agg_cap},
        )

        universe = list(getattr(self.settings, "apex_universe", ("SPY", "QQQ", "IWM")))

        if open_now and self.settings.armed:
            pm = PositionManager(mcp, self.audit, self.settings)
            closed = pm.manage(positions)
            for c in closed:
                if c.get("ok"):
                    self._emit("position_close", c.get("reason", "closed"), c.get("symbol", "")[:6])
            if closed:
                positions = mcp.positions()
                open_risk = estimate_open_risk(positions)
                deploy_util = (open_risk / agg_cap * 100) if agg_cap > 0 else 0

        if not open_now:
            if self.settings.analyze_when_closed:
                self._emit("status", "market closed — apex analysis-only")
            return

        if guard:
            self._emit("risk_block", guard)
            return

        event_veto = self._event_veto_symbols(mcp, universe)
        candidates: list[tuple[float, object, dict]] = []

        for symbol in universe:
            try:
                bars = mcp.stock_bars(symbol, limit=self.settings.history_bars)
                bar_list = bars_list(bars)
                closes = closes_from_bars(bar_list)
                tech = analyze_technical(symbol, bars)
                if not tech.get("ok"):
                    continue

                chain = mcp.resolve_option_chain(symbol, self.settings, spot=float(tech["current_price"]))
                vol = analyze_vol(symbol, closes[-60:] if len(closes) > 60 else closes, chain, bars=bar_list, settings=self.settings)
                contracts = parse_chain(
                    chain, symbol, float(tech["current_price"]),
                    max_spread_frac=self.settings.max_spread_frac,
                )
                surface = measure_surface(
                    symbol,
                    float(tech["current_price"]),
                    closes,
                    chain,
                    contracts,
                    sell_threshold=float(self.settings.apex_iv_rv_sell),
                    buy_threshold=float(self.settings.apex_iv_rv_buy),
                )
                trend = str(tech.get("trend") or "neutral")
                structure, sleeve_reason = choose_apex_sleeve(
                    surface,
                    trend,
                    drawdown_from_peak_pct=peak_dd_pct,
                    hedge_trigger_pct=float(self.settings.apex_hedge_drawdown_pct),
                    sell_threshold=float(self.settings.apex_iv_rv_sell),
                    buy_threshold=float(self.settings.apex_iv_rv_buy),
                    satellite_allowed=(surface.regime == "stand_aside"),
                )

                self._emit(
                    "apex_surface",
                    f"{symbol} IV/RV={surface.iv_rv_ratio:.2f} skew_z={surface.skew_z:+.2f} → {structure}",
                    symbol,
                    {"surface": surface.__dict__, "structure": structure, "reason": sleeve_reason},
                )

                if structure == "NO_TRADE":
                    self._emit("apex_decline", sleeve_reason, symbol)
                    continue

                plan = build_apex_plan(
                    symbol, surface, tech, chain, self.settings,
                    structure_choice=structure, sleeve_reason=sleeve_reason,
                )
                if not plan:
                    self._emit("risk_block", "no liquid structure for sleeve", symbol)
                    continue

                score = float(plan.confidence) + (10 if plan.sleeve == "income" else 5)
                candidates.append((score, plan, vol))
            except Exception as e:
                self._emit("error", str(e), symbol)

        if not candidates:
            self._emit("decision", "apex — no trade (declined or no edge)")
            return

        candidates.sort(key=lambda x: -x[0])
        ex = Executor(mcp, self.audit, self.settings)

        target_pct = float(getattr(self.settings, "apex_target_deploy_pct", 25.0))
        under_deployed = agg_cap > 0 and deploy_util < target_pct
        entry_limit = self.settings.max_entries_per_tick
        if under_deployed:
            entry_limit = min(len(candidates), max(entry_limit, 3))

        for score, plan, vol in candidates[:entry_limit]:
            verdict = evaluate_apex(
                plan,
                equity,
                positions,
                self.settings,
                day_pnl_pct=metrics["daily_pnl_pct"],
                clock=clock,
                cash=metrics.get("cash"),
                starting_equity=self._starting_equity,
                peak_equity=self._peak_equity,
                event_veto_symbols=event_veto,
            )
            if not verdict.approved:
                self.audit.decision(plan.symbol, "SKIP", plan.strategy, plan.confidence, "REJECTED", verdict.reason)
                self._emit("risk_block", verdict.reason, plan.symbol, {"sleeve": plan.sleeve})
                continue

            self._emit(
                "order_submitted",
                plan.rationale,
                plan.symbol,
                {"strategy": plan.strategy, "sleeve": plan.sleeve, "qty": verdict.qty, "score": score},
            )
            try:
                exec_result = ex.execute(plan, verdict.qty, verdict.limit_price)
                verified = exec_result.get("verified", {})
                status = "SUBMITTED" if verified.get("all_filled", True) else "PARTIAL"
                self.audit.decision(plan.symbol, "TRADE", plan.strategy, plan.confidence, status, plan.rationale)
                self.audit.record_orders(plan.symbol, exec_result)
                self.audit.record_outcome(
                    plan.symbol, plan.strategy, status="OPEN", reason=plan.rationale,
                    payload={"sleeve": plan.sleeve, "apex": True},
                )
                self._emit("order_filled", f"apex {plan.sleeve} {status.lower()}", plan.symbol, {"verified": verified})
                positions = mcp.positions()
                try:
                    metrics = account_metrics(mcp.account())
                    equity = metrics["equity"]
                except Exception:
                    pass
                break
            except Exception as e:
                self.audit.decision(plan.symbol, "TRADE", plan.strategy, plan.confidence, "ERROR", str(e))
                self._emit("error", str(e), plan.symbol)

    def start(self, interval: int | None = None):
        if self._running:
            return
        self._running = True
        sec = interval or self.settings.tick_seconds

        def loop():
            while self._running:
                t0 = time.time()
                try:
                    self.tick()
                except Exception as e:
                    self._emit("error", str(e))
                wait = sec - (time.time() - t0)
                if wait > 0:
                    time.sleep(wait)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        self._emit("status", f"agent started (every {sec}s)")

    def stop(self):
        self._running = False
        self._emit("status", "agent stopped")

    def run_once(self):
        self.tick()

    @property
    def last_feedback(self) -> dict | None:
        return self._last_feedback
