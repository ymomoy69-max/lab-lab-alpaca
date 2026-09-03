"""Runtime configuration loaded from environment."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

# Single-name leaders first so scalp scans are not stuck on sector ETFs.
APEX_UNIVERSE = ("SPY", "QQQ", "IWM")

DEFAULT_WATCHLIST = (
    "SPY",
    "QQQ",
    "IWM",
    "AAPL",
    "NVDA",
    "MSFT",
    "AMD",
    "JPM",
    "XOM",
    "UNH",
    "WMT",
    "CAT",
    "BA",
    "DIS",
    "KO",
    "GE",
    "DIA",
    "XLK",
    "XLF",
    "XLE",
    "XLV",
    "XLY",
    "XLP",
    "XLI",
    "XLB",
    "XLU",
    "XLC",
    "XLRE",
    "SMH",
    "XBI",
)
PRIORITY_STOCKS = (
    "AAPL",
    "NVDA",
    "MSFT",
    "AMD",
    "JPM",
    "XOM",
    "UNH",
    "WMT",
    "CAT",
    "BA",
    "DIS",
    "KO",
    "GE",
)
DEFAULT_WATCHLIST_CSV = ",".join(DEFAULT_WATCHLIST)


def _parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw or raw.lower() in ("none", "off", "disable", "disabled"):
        return None
    return date.fromisoformat(raw[:10])


@dataclass
class Settings:
    paper: bool = True
    expected_account: str = ""
    armed: bool = False
    watchlist: tuple[str, ...] = DEFAULT_WATCHLIST
    tick_seconds: int = 5
    min_confidence: float = 48.0
    max_premium_pct: float = 100.0
    max_total_premium_pct: float = 100.0
    max_structures: int = 15
    max_contracts: int = 200
    max_entries_per_tick: int = 12
    contest_close: date | None = date(2026, 9, 4)
    contest_enforce: bool = False
    contest_expiry_exit: bool = False
    min_dte: int = 0
    max_dte: int = 10
    max_spread_frac: float = 0.35
    max_delta_divergence: float = 0.05
    max_var_pct: float = 40.0
    circuit_breaker_pct: float = -45.0
    macro_veto_threshold: float = -0.55
    min_backtest_win_rate: float = 0.32
    limit_slippage: float = 0.02
    history_bars: int = 200
    hourly_bars: int = 500
    analyze_when_closed: bool = True
    # Scalp 3:1: cut at 5% of premium, take 15%. Time-stop 5–10 min.
    take_profit_pct: float = 0.15
    stop_loss_pct: float = -0.05
    reward_risk_ratio: float = 3.0
    exit_dte: int = 0
    session_open_buffer_min: int = 2
    session_close_buffer_min: int = 12
    fill_timeout_sec: int = 4
    scalp_mode: bool = True
    scalp_hold_sec: int = 600
    scalp_giveup_sec: int = 300
    scalp_bar_timeframe: str = "1Min"
    scalp_bars: int = 90
    scalp_scan_limit: int = 18
    var_horizon_min: int = 10
    size_from_risk: bool = True
    deploy_cash_pct: float = 0.98
    order_type: str = "market"
    required_equity: float = 100_000.0
    gemini_key: str = ""
    openai_key: str = ""
    audit_dir: str = "data/audit"
    db_path: str = "data/nexus.db"
    proof_path: str = "data/live-trading-proof.json"
    calibration_path: str = "data/calibration.json"
    dynamic_watchlist: bool = True
    max_dynamic_symbols: int = 12
    min_share_volume: float = 1_000_000.0
    min_relative_volume: float = 1.0
    min_dynamic_price: float = 8.0
    news_scan_limit: int = 40
    most_active_top: int = 35
    dynamic_ttl_ticks: int = 6
    breakout_scan: bool = True
    breakout_cons_bars: int = 60
    breakout_max_range_pct: float = 0.15
    breakout_max_drift_pct: float = 0.06
    breakout_vol_mult: float = 1.25
    breakout_max_extension_pct: float = 0.08
    breakout_scan_limit: int = 12
    max_breakout_symbols: int = 6
    pattern_scan: bool = True
    pattern_vol_mult: float = 1.15
    max_pattern_symbols: int = 6
    pcr_resistance: float = 0.5
    pcr_support: float = 1.5
    min_indicator_votes: int = 2
    indicator_confirm: bool = True
    # Apex Underwriter — composite of top hackathon agents (default ON)
    apex_mode: bool = True
    apex_universe: tuple[str, ...] = APEX_UNIVERSE
    apex_iv_rv_sell: float = 1.15
    apex_iv_rv_buy: float = 0.95
    apex_skew_z_trigger: float = 0.8
    apex_max_loss_per_trade_pct: float = 2.5
    apex_max_aggregate_risk_pct: float = 28.0
    apex_target_deploy_pct: float = 25.0
    apex_daily_loss_halt_pct: float = -2.0
    apex_drawdown_halt_pct: float = -5.0
    apex_max_portfolio_delta: float = 50.0
    apex_max_positions: int = 6
    apex_max_per_underlying: int = 2
    apex_credit_otm_pct: float = 0.03
    apex_credit_width: float = 5.0
    apex_short_delta_target: float = 0.21
    apex_min_credit_to_width: float = 0.15
    apex_min_credit_dollars: float = 10.0
    apex_credit_tp_pct: float = 0.50
    apex_credit_stop_mult: float = 2.0
    apex_hedge_drawdown_pct: float = 2.0
    apex_satellite_budget_pct: float = 3.4
    apex_conservative_fills: bool = True
    apex_llm_veto_only: bool = True
    apex_hold_sec: int = 10800
    apex_giveup_sec: int = 3600
    apex_profit_floor_pct: float = 8.0
    apex_profit_floor_enabled: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        wl = os.getenv("NEXUS_WATCHLIST", DEFAULT_WATCHLIST_CSV)
        contest_raw = os.getenv("NEXUS_CONTEST_CLOSE", "2026-09-04")
        contest_close = _parse_date(contest_raw)
        apex_on = os.getenv("NEXUS_APEX_MODE", "yes").lower() in ("1", "true", "yes")
        apex_uni = os.getenv("NEXUS_APEX_UNIVERSE", "SPY,QQQ,IWM,DIA")
        return cls(
            paper=os.getenv("ALPACA_PAPER", "true").lower() in ("1", "true", "yes"),
            expected_account=os.getenv("NEXUS_EXPECTED_ACCOUNT", "").strip(),
            armed=os.getenv("NEXUS_ARMED", "no").lower() in ("1", "true", "yes"),
            watchlist=tuple(s.strip().upper() for s in wl.split(",") if s.strip()),
            tick_seconds=int(os.getenv("NEXUS_TICK_SECONDS", "30" if apex_on else "5")),
            min_confidence=float(os.getenv("NEXUS_MIN_CONFIDENCE", "48")),
            max_premium_pct=float(os.getenv("NEXUS_MAX_PREMIUM_PCT", "100")),
            max_total_premium_pct=float(os.getenv("NEXUS_MAX_TOTAL_PREMIUM_PCT", "100")),
            max_structures=int(os.getenv("NEXUS_MAX_STRUCTURES", "6" if apex_on else "15")),
            max_contracts=int(os.getenv("NEXUS_MAX_CONTRACTS", "200")),
            max_entries_per_tick=int(os.getenv("NEXUS_MAX_ENTRIES_PER_TICK", "3" if apex_on else "12")),
            contest_close=contest_close,
            contest_enforce=os.getenv("NEXUS_CONTEST_ENFORCE", "no").lower() in ("1", "true", "yes"),
            contest_expiry_exit=os.getenv("NEXUS_CONTEST_EXPIRY_EXIT", "no").lower() in ("1", "true", "yes"),
            max_var_pct=float(os.getenv("NEXUS_MAX_VAR_PCT", "40")),
            circuit_breaker_pct=float(os.getenv("NEXUS_CIRCUIT_BREAKER_PCT", "-2" if apex_on else "-45")),
            min_backtest_win_rate=float(os.getenv("NEXUS_MIN_BACKTEST_WIN_RATE", "0.32")),
            history_bars=int(os.getenv("NEXUS_HISTORY_BARS", "200")),
            hourly_bars=int(os.getenv("NEXUS_HOURLY_BARS", "500")),
            analyze_when_closed=os.getenv("NEXUS_ANALYZE_WHEN_CLOSED", "true").lower()
            in ("1", "true", "yes"),
            take_profit_pct=float(os.getenv("NEXUS_TAKE_PROFIT_PCT", "0.50" if apex_on else "0.15")),
            stop_loss_pct=float(os.getenv("NEXUS_STOP_LOSS_PCT", "-1.0" if apex_on else "-0.05")),
            reward_risk_ratio=float(os.getenv("NEXUS_REWARD_RISK_RATIO", "3")),
            exit_dte=int(os.getenv("NEXUS_EXIT_DTE", "0" if apex_on else "0")),
            fill_timeout_sec=int(os.getenv("NEXUS_FILL_TIMEOUT_SEC", "15" if apex_on else "4")),
            order_type=os.getenv("NEXUS_ORDER_TYPE", "limit" if apex_on else "market").strip().lower() or "limit",
            max_dte=int(os.getenv("NEXUS_MAX_DTE", "0" if apex_on else "10")),
            min_dte=int(os.getenv("NEXUS_MIN_DTE", "0" if apex_on else "0")),
            session_open_buffer_min=int(os.getenv("NEXUS_SESSION_OPEN_BUFFER_MIN", "2")),
            session_close_buffer_min=int(os.getenv("NEXUS_SESSION_CLOSE_BUFFER_MIN", "45" if apex_on else "12")),
            scalp_mode=(not apex_on)
            and os.getenv("NEXUS_SCALP_MODE", "yes").lower() in ("1", "true", "yes"),
            scalp_hold_sec=int(os.getenv("NEXUS_SCALP_HOLD_SEC", "600")),
            scalp_giveup_sec=int(os.getenv("NEXUS_SCALP_GIVEUP_SEC", "300")),
            scalp_bar_timeframe=os.getenv("NEXUS_SCALP_BAR_TIMEFRAME", "1Min").strip() or "1Min",
            scalp_bars=int(os.getenv("NEXUS_SCALP_BARS", "90")),
            scalp_scan_limit=int(os.getenv("NEXUS_SCALP_SCAN_LIMIT", "18")),
            var_horizon_min=int(os.getenv("NEXUS_VAR_HORIZON_MIN", "10")),
            size_from_risk=os.getenv("NEXUS_SIZE_FROM_RISK", "yes").lower() in ("1", "true", "yes"),
            deploy_cash_pct=float(os.getenv("NEXUS_DEPLOY_CASH_PCT", "0.98")),
            max_spread_frac=float(os.getenv("NEXUS_MAX_SPREAD_FRAC", "0.25" if apex_on else "0.35")),
            gemini_key=os.getenv("GEMINI_API_KEY", "").strip(),
            openai_key=os.getenv("OPENAI_API_KEY", "").strip(),
            calibration_path=os.getenv("NEXUS_CALIBRATION_PATH", "data/calibration.json"),
            dynamic_watchlist=os.getenv("NEXUS_DYNAMIC_WATCHLIST", "true").lower()
            in ("1", "true", "yes"),
            max_dynamic_symbols=int(os.getenv("NEXUS_MAX_DYNAMIC_SYMBOLS", "12")),
            min_share_volume=float(os.getenv("NEXUS_MIN_SHARE_VOLUME", "1000000")),
            min_relative_volume=float(os.getenv("NEXUS_MIN_RELATIVE_VOLUME", "1.0")),
            min_dynamic_price=float(os.getenv("NEXUS_MIN_DYNAMIC_PRICE", "8")),
            news_scan_limit=int(os.getenv("NEXUS_NEWS_SCAN_LIMIT", "40")),
            most_active_top=int(os.getenv("NEXUS_MOST_ACTIVE_TOP", "35")),
            dynamic_ttl_ticks=int(os.getenv("NEXUS_DYNAMIC_TTL_TICKS", "6")),
            breakout_scan=os.getenv("NEXUS_BREAKOUT_SCAN", "true").lower() in ("1", "true", "yes"),
            breakout_cons_bars=int(os.getenv("NEXUS_BREAKOUT_CONS_BARS", "60")),
            breakout_max_range_pct=float(os.getenv("NEXUS_BREAKOUT_MAX_RANGE_PCT", "0.15")),
            breakout_max_drift_pct=float(os.getenv("NEXUS_BREAKOUT_MAX_DRIFT_PCT", "0.06")),
            breakout_vol_mult=float(os.getenv("NEXUS_BREAKOUT_VOL_MULT", "1.25")),
            breakout_max_extension_pct=float(os.getenv("NEXUS_BREAKOUT_MAX_EXTENSION_PCT", "0.08")),
            breakout_scan_limit=int(os.getenv("NEXUS_BREAKOUT_SCAN_LIMIT", "12")),
            max_breakout_symbols=int(os.getenv("NEXUS_MAX_BREAKOUT_SYMBOLS", "6")),
            pattern_scan=os.getenv("NEXUS_PATTERN_SCAN", "true").lower() in ("1", "true", "yes"),
            pattern_vol_mult=float(os.getenv("NEXUS_PATTERN_VOL_MULT", "1.15")),
            max_pattern_symbols=int(os.getenv("NEXUS_MAX_PATTERN_SYMBOLS", "6")),
            pcr_resistance=float(os.getenv("NEXUS_PCR_RESISTANCE", "0.5")),
            pcr_support=float(os.getenv("NEXUS_PCR_SUPPORT", "1.5")),
            min_indicator_votes=int(os.getenv("NEXUS_MIN_INDICATOR_VOTES", "2")),
            indicator_confirm=os.getenv("NEXUS_INDICATOR_CONFIRM", "true").lower()
            in ("1", "true", "yes"),
            apex_mode=apex_on,
            apex_universe=tuple(s.strip().upper() for s in apex_uni.split(",") if s.strip()),
            apex_iv_rv_sell=float(os.getenv("NEXUS_APEX_IV_RV_SELL", "1.15")),
            apex_iv_rv_buy=float(os.getenv("NEXUS_APEX_IV_RV_BUY", "0.95")),
            apex_skew_z_trigger=float(os.getenv("NEXUS_APEX_SKEW_Z", "0.8")),
            apex_max_loss_per_trade_pct=float(os.getenv("NEXUS_APEX_MAX_LOSS_PCT", "2.5")),
            apex_max_aggregate_risk_pct=float(os.getenv("NEXUS_APEX_AGG_RISK_PCT", "28.0")),
            apex_target_deploy_pct=float(os.getenv("NEXUS_APEX_TARGET_DEPLOY_PCT", "25.0")),
            apex_daily_loss_halt_pct=float(os.getenv("NEXUS_APEX_DAILY_HALT_PCT", "-2.0")),
            apex_drawdown_halt_pct=float(os.getenv("NEXUS_APEX_DD_HALT_PCT", "-5.0")),
            apex_max_portfolio_delta=float(os.getenv("NEXUS_APEX_MAX_DELTA", "50")),
            apex_max_positions=int(os.getenv("NEXUS_APEX_MAX_POSITIONS", "6")),
            apex_max_per_underlying=int(os.getenv("NEXUS_APEX_MAX_PER_UNDERLYING", "2")),
            apex_credit_otm_pct=float(os.getenv("NEXUS_APEX_CREDIT_OTM", "0.03")),
            apex_credit_width=float(os.getenv("NEXUS_APEX_CREDIT_WIDTH", "5.0")),
            apex_short_delta_target=float(os.getenv("NEXUS_APEX_SHORT_DELTA", "0.21")),
            apex_min_credit_to_width=float(os.getenv("NEXUS_APEX_MIN_CREDIT_WIDTH", "0.01" if apex_on else "0.15")),
            apex_min_credit_dollars=float(os.getenv("NEXUS_APEX_MIN_CREDIT", "10.0" if apex_on else "25.0")),
            apex_credit_tp_pct=float(os.getenv("NEXUS_APEX_CREDIT_TP", "0.50")),
            apex_credit_stop_mult=float(os.getenv("NEXUS_APEX_CREDIT_STOP", "2.0")),
            apex_hedge_drawdown_pct=float(os.getenv("NEXUS_APEX_HEDGE_DD", "2.0")),
            apex_satellite_budget_pct=float(os.getenv("NEXUS_APEX_SATELLITE_PCT", "3.4")),
            apex_conservative_fills=os.getenv("NEXUS_APEX_CONSERVATIVE_FILLS", "yes").lower()
            in ("1", "true", "yes"),
            apex_llm_veto_only=os.getenv("NEXUS_APEX_LLM_VETO_ONLY", "yes").lower()
            in ("1", "true", "yes"),
            apex_hold_sec=int(os.getenv("NEXUS_APEX_HOLD_SEC", "10800")),
            apex_giveup_sec=int(os.getenv("NEXUS_APEX_GIVEUP_SEC", "3600")),
            apex_profit_floor_pct=float(os.getenv("NEXUS_APEX_PROFIT_FLOOR_PCT", "8.0")),
            apex_profit_floor_enabled=os.getenv("NEXUS_APEX_PROFIT_FLOOR", "yes").lower()
            in ("1", "true", "yes"),
        )


SETTINGS = Settings.from_env()
