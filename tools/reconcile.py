#!/usr/bin/env python3
"""Credential-free sanity checks — Vega reconcile pattern."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent.options.pricing import bs_price, implied_vol, bs_delta
from agent.signals.history import analyze_history
from agent.feedback import build_feedback, apply_history_to_debate, score_candidate
from agent.signals.backtest import momentum_backtest, combined_backtest, options_backtest
from agent.signals.technical import analyze, closes_from_bars
from agent.risk.var import monte_carlo_var
from agent.risk.greeks import greeks_preflight, net_delta
from agent.risk.correlation import correlation_gate, spy_regime_gate
from agent.risk.session import session_gate
from agent.options.chain import OptionContract, occ_root
from agent.options.strikes import pick_bull_call_spread, pick_strangle_legs
from agent.strategy import TradePlan, LegQuote


def test_pricing():
    S, K, T, sig = 100.0, 100.0, 30 / 365, 0.2
    c = bs_price(S, K, T, sig, "call")
    assert c > 0
    iv = implied_vol(c, S, K, T, "call")
    assert iv and abs(iv - sig) < 0.01
    d = bs_delta(S, K, T, sig, "call")
    assert 0.4 < d < 0.6


def test_backtest():
    from agent.signals.backtest import combined_backtest

    closes = [100 + i * 0.5 for i in range(40)]
    r = momentum_backtest(closes, "BUY")
    assert "win_rate" in r
    mixed = [100 + ((-1) ** i) * 0.4 + i * 0.02 for i in range(80)]
    c = combined_backtest(mixed, "BUY", min_win_rate=0.38)
    assert "win_rate" in c
    assert c["passed"] == (c["win_rate"] >= 0.38)
    from agent.strategy import TradePlan, LegQuote, marketable_limit
    plan = TradePlan(
        "XLI", "bull_call_spread", "debit_spread", 90, "t",
        leg_quotes=[
            LegQuote("L", "buy", 1.50, 1.40, 1.60, 0.5, 0.1, 176, "call"),
            LegQuote("S", "sell", 0.40, 0.30, 0.50, 0.2, 0.1, 196, "call"),
        ],
        limit_price=1.10,
    )
    ml = marketable_limit(plan)
    assert ml >= 1.30  # must pay through the live ask-bid net


def test_var():
    v = monte_carlo_var("SPY", 450, 5.0)
    assert "var_95_pct" in v


def test_technical():
    bars = [{"c": 100 + i} for i in range(30)]
    t = analyze("SPY", bars)
    assert t["ok"] and t["atr"] > 0


def test_history():
    closes = [100 + i * 0.3 + (i % 7) * 0.5 for i in range(120)]
    h = analyze_history("SPY", closes)
    assert h["ok"] and h["regime"] in ("trending_up", "trending_down", "range_bound", "high_vol")


def test_feedback_scoring():
    history = {"ok": True, "favored_strategy": "bull_call_spread", "regime_bias": "BUY"}
    feedback = {"strategy_adjustments": {}, "symbol_adjustments": {}, "scale_down": False}

    class Plan:
        strategy = "bull_call_spread"
        symbol = "SPY"

    s = score_candidate(70, Plan(), history, feedback)
    assert s > 70


def test_apply_history():
    debate = {"action": "BUY", "confidence": 65}
    history = {
        "ok": True,
        "regime_bias": "BUY",
        "confidence_adjustment": 5,
        "summary": "test",
    }
    out = apply_history_to_debate(debate, history)
    assert out["confidence"] >= 65


def test_options_backtest():
    closes = [100 + i * 0.2 for i in range(60)]
    r = options_backtest(closes, "BUY")
    assert "win_rate" in r
    c = combined_backtest(closes, "VOL")
    assert "momentum" in c and "options" in c


def test_spread_ordering():
    spot = 250.0
    contracts = [
        OptionContract("A1", "AAPL", "call", 250, "2026-09-09", 4, 4.2, 4.1, 0.05, 0.52, 0.3, True),
        OptionContract("A2", "AAPL", "call", 260, "2026-09-09", 2, 2.2, 2.1, 0.05, 0.35, 0.3, True),
        OptionContract("A3", "AAPL", "call", 270, "2026-09-09", 1, 1.2, 1.1, 0.1, 0.22, 0.3, True),
    ]
    spread = pick_bull_call_spread(contracts, spot)
    assert spread is not None
    long_c, short_c = spread
    assert long_c.strike < short_c.strike
    assert long_c.mid > short_c.mid


def test_occ_root():
    assert occ_root("MSFT260909C00500000") == "MSFT"
    assert occ_root("AAPL260909C00250000") == "AAPL"


def test_strangle_sanity():
    spot = 400.0
    contracts = [
        OptionContract("C1", "MSFT", "call", 440, "2026-09-09", 2, 2.2, 2.1, 0.05, 0.15, 0.3, True),
        OptionContract("P1", "MSFT", "put", 360, "2026-09-09", 2, 2.2, 2.1, 0.05, -0.15, 0.3, True),
        OptionContract("BAD", "MSFT", "call", 415, "2026-09-09", 97, 102, 99.5, 0.05, 0.5, 0.3, True),
    ]
    legs = pick_strangle_legs(contracts, spot)
    assert legs is not None
    call, put = legs
    assert call.symbol == "C1"
    assert put.symbol == "P1"


def test_greeks():
    plan = TradePlan(
        symbol="SPY",
        strategy="bull_call_spread",
        structure="debit_spread",
        confidence=70,
        rationale="test",
        leg_quotes=[
            LegQuote("x", "buy", 2, 1.9, 2.1, 0.4, 0.1, 450, "call"),
            LegQuote("y", "sell", 1, 0.9, 1.1, 0.2, 0.1, 460, "call"),
        ],
    )
    assert net_delta(plan) is not None
    assert greeks_preflight(plan) is None


def test_correlation():
    plan = TradePlan("NVDA", "bull_call_spread", "debit_spread", 70, "test")
    assert correlation_gate(plan, []) is None
    assert spy_regime_gate(plan, {"ok": True, "regime": "trending_down"}) is not None
    energy = TradePlan("XOM", "bull_call_spread", "debit_spread", 70, "test")
    assert correlation_gate(energy, []) is None


def test_reward_risk_exits():
    from agent.positions import exit_thresholds, exits_needed

    class S:
        contest_expiry_exit = False
        contest_close = None
        exit_dte = 2
        take_profit_pct = 0.66
        stop_loss_pct = -0.22
        reward_risk_ratio = 3.0

    tp, sl = exit_thresholds(S())
    assert abs(sl + 0.22) < 1e-9
    assert abs(tp - 0.66) < 1e-9
    assert abs(tp / abs(sl) - 3.0) < 1e-9

    pos = [
        {
            "symbol": "AAPL260918C00200000",
            "asset_class": "us_option",
            "qty": 1,
            "cost_basis": 100,
            "unrealized_pl": 66,
            "expiration_date": "2029-12-19",
        }
    ]
    hits = exits_needed(pos, S())
    assert hits and "take profit" in hits[0]["reason"]

    pos[0]["unrealized_pl"] = -22
    hits = exits_needed(pos, S())
    assert hits and "stop loss" in hits[0]["reason"]


def test_spread_reward_risk():
    spot = 250.0
    contracts = [
        OptionContract("A1", "AAPL", "call", 250, "2026-09-09", 4, 4.2, 4.1, 0.05, 0.52, 0.3, True),
        OptionContract("A2", "AAPL", "call", 260, "2026-09-09", 2, 2.2, 2.1, 0.05, 0.35, 0.3, True),
        OptionContract("A3", "AAPL", "call", 270, "2026-09-09", 1, 1.2, 1.1, 0.1, 0.22, 0.3, True),
    ]
    spread = pick_bull_call_spread(contracts, spot, target_rr=3.0)
    assert spread is not None
    long_c, short_c = spread
    debit = (long_c.mid - short_c.mid) * 100
    width = (short_c.strike - long_c.strike) * 100
    rr = (width - debit) / debit
    assert rr >= 3.0 - 1e-6


def test_session():
    assert session_gate({"is_open": True, "timestamp": "2026-08-29T14:35:00-04:00", "next_close": "2026-08-29T16:00:00-04:00"}) is None
    assert session_gate({"is_open": True, "timestamp": "2026-08-29T15:45:00-04:00", "next_close": "2026-08-29T16:00:00-04:00"}) is not None


def test_dynamic_universe():
    from agent.signals.universe import (
        extract_news_mentions,
        merge_watchlist,
        parse_most_actives,
        parse_snapshots,
        rank_dynamic,
        valid_ticker,
    )

    assert valid_ticker("ORCL")
    assert not valid_ticker("BTCUSD")
    assert not valid_ticker("BRK.B")

    mentions = extract_news_mentions(
        {
            "news": [
                {"headline": "Oracle beats estimates", "symbols": ["ORCL", "MSFT"]},
                {"headline": "$PLTR volume surge", "symbols": []},
                {"headline": "microcap chatter", "symbols": ["XYZ"]},
            ]
        }
    )
    assert mentions["ORCL"]["count"] >= 1
    assert "PLTR" in mentions

    actives = parse_most_actives({"most_actives": [{"symbol": "ORCL", "volume": 20_000_000}]})
    snaps = parse_snapshots(
        {
            "ORCL": {
                "dailyBar": {"v": 20_000_000, "c": 140},
                "prevDailyBar": {"v": 10_000_000},
                "latestTrade": {"p": 140},
            },
            "PLTR": {
                "dailyBar": {"v": 8_000_000, "c": 30},
                "prevDailyBar": {"v": 5_000_000},
                "latestTrade": {"p": 30},
            },
            "XYZ": {
                "dailyBar": {"v": 50_000, "c": 1.2},
                "prevDailyBar": {"v": 40_000},
                "latestTrade": {"p": 1.2},
            },
        }
    )
    picked = rank_dynamic(
        mentions,
        actives,
        snaps,
        {"MSFT"},
        min_share_volume=1_000_000,
        min_relative_volume=1.0,
        min_price=8.0,
        max_symbols=8,
    )
    symbols = [p["symbol"] for p in picked]
    assert "ORCL" in symbols
    assert "PLTR" in symbols
    assert "XYZ" not in symbols
    assert "MSFT" not in symbols

    sticky: dict[str, int] = {}
    scan, added = merge_watchlist(("SPY",), picked, sticky, ttl_ticks=2, max_dynamic=8)
    assert "ORCL" in scan and "ORCL" in added
    scan2, added2 = merge_watchlist(("SPY",), [], sticky, ttl_ticks=2, max_dynamic=8)
    assert "ORCL" in scan2 and added2 == []


def _coil_bars(*, last_close: float, last_vol: float = 2_500_000) -> list[dict]:
    bars = []
    for i in range(70):
        wave = (i % 8) / 8
        c = 100 + (wave - 0.5) * 4
        bars.append({"o": c, "h": min(c + 0.7, 103.5), "l": max(c - 0.7, 96.5), "c": c, "v": 1_000_000})
    bars.append({"o": 103.0, "h": last_close + 0.8, "l": 102.0, "c": last_close, "v": last_vol})
    return bars


def test_breakout_coil():
    from agent.signals.breakout import analyze as analyze_breakout
    from agent.debate import run_debate

    up = analyze_breakout("TEST", _coil_bars(last_close=108.0))
    assert up["breakout"] and up["side"] == "up"

    none = analyze_breakout("TEST", [{"c": 80 + i} for i in range(90)])
    assert not none.get("breakout")

    class _S:
        gemini_key = ""
        openai_key = ""

    tech = {
        "symbol": "TEST",
        "quant_score": 52,
        "trend": "neutral",
        "breakout": True,
        "breakout_side": "up",
        "breakout_summary": "BREAKOUT up",
    }
    news = {"score": 0.0, "label": "neutral"}
    vol = {"regime": "fair", "iv_rv_ratio": 1.0}
    d = run_debate(tech, news, vol, _S())
    assert d["action"] == "BUY" and d["confidence"] >= 70


def _bar(o, h, l, c, v=1_000_000):
    return {"o": o, "h": h, "l": l, "c": c, "v": v}


def test_patterns_and_pcr():
    from agent.signals.patterns import analyze as analyze_patterns
    from agent.signals.pcr import analyze as analyze_pcr
    from agent.debate import run_debate

    pad = [_bar(110 - i * 0.4, 110 - i * 0.3, 109.5 - i * 0.4, 109.6 - i * 0.4) for i in range(8)]
    morning = pad + [
        _bar(108, 108.2, 100, 101, 1_000_000),
        _bar(100.5, 101.2, 98.5, 99.5, 800_000),
        _bar(100, 109, 99.5, 108, 1_800_000),
    ]
    m = analyze_patterns("TEST", morning)
    assert m["pattern"] == "morning_star" and m["side"] == "up" and m["volume_confirmed"]

    evening = pad + [
        _bar(100, 108, 99.5, 107, 1_000_000),
        _bar(107.5, 109, 106.8, 108, 800_000),
        _bar(107, 107.4, 99.5, 101, 1_800_000),
    ]
    e = analyze_patterns("TEST", evening)
    assert e["pattern"] == "evening_star" and e["side"] == "down"

    doji_bars = [_bar(110 - i, 110.4 - i, 109.5 - i, 109.7 - i) for i in range(10)]
    doji_bars.append(_bar(99.2, 99.4, 94.0, 99.1, 2_200_000))
    dji = analyze_patterns("TEST", doji_bars)
    assert dji["pattern"] == "dragonfly_doji" and dji["side"] == "up" and dji["volume_confirmed"]

    db = []
    for i in range(40):
        if i < 12:
            c = 100 - i * 1.0
        elif i < 20:
            c = 88 + (i - 12) * 1.8
        elif i < 28:
            c = 102.4 - (i - 20) * 1.8
        else:
            c = 88 + (i - 28) * 1.8
        db.append(_bar(c, c + 0.4, c - 0.4, c, 1_200_000))
    db[-1] = _bar(102, 108, 101.5, 107, 2_000_000)
    bottom = analyze_patterns("TEST", db)
    assert bottom["pattern"] == "double_bottom" and bottom["side"] == "up"

    chain_res = {
        "snapshots": {
            "AAPL260909C00150000": {"dailyBar": {"v": 2000}},
            "AAPL260909P00150000": {"dailyBar": {"v": 1000}},
        }
    }
    r = analyze_pcr("AAPL", chain_res, resistance=0.5, support=1.5)
    assert r["ok"] and r["zone"] == "resistance" and r["bias"] == "SELL"

    chain_sup = {
        "snapshots": {
            "AAPL260909C00150000": {"dailyBar": {"v": 1000}},
            "AAPL260909P00150000": {"dailyBar": {"v": 1500}},
        }
    }
    s = analyze_pcr("AAPL", chain_sup, resistance=0.5, support=1.5)
    assert s["ok"] and s["zone"] == "support" and s["bias"] == "BUY"

    class _S:
        gemini_key = ""
        openai_key = ""

    tech = {
        "symbol": "TEST",
        "quant_score": 50,
        "trend": "neutral",
        "pattern": "morning_star",
        "pattern_side": "up",
        "pattern_volume_ok": True,
        "pattern_summary": "morning star",
        "pcr": 1.6,
        "pcr_zone": "support",
        "pcr_bias": "BUY",
        "pcr_summary": "PCR 1.60 support",
    }
    d = run_debate(tech, {"score": 0.0, "label": "neutral"}, {"regime": "fair", "iv_rv_ratio": 1.0}, _S())
    assert d["action"] == "BUY" and d["confidence"] >= 74


def test_indicators_confirm():
    from agent.signals.indicators import analyze as analyze_ind, confirm_trade
    from agent.signals.technical import analyze as analyze_tech

    up = [{"c": 100 + i} for i in range(30)]
    t = analyze_tech("SPY", up)
    assert t["ok"] and t["atr"] > 0
    assert t.get("ema5") and t.get("ema20")

    bars = []
    px = 100.0
    for i in range(80):
        px += 0.35
        bars.append({"o": px - 0.1, "h": px + 0.3, "l": px - 0.3, "c": px, "v": 1_000_000})
    # gentle pullback so oscillators are not pinned at extremes
    for dlt in (-1.2, -0.6, -0.2, 0.3, 0.5):
        px += dlt
        bars.append({"o": px - 0.1, "h": px + 0.25, "l": px - 0.25, "c": px, "v": 1_100_000})
    ind = analyze_ind("SPY", bars)
    assert ind["ok"]
    assert ind["ema5"] > 0 and ind["ema20"] > 0 and ind["ema63"] > 0
    assert "macd_hist" in ind and "stoch_k" in ind and "bb_pct_b" in ind
    assert ind["ema_stack"] in ("bull", "bear", "mixed")

    stacked = {
        "ok": True,
        "ema63": 100.0,
        "rsi14": 55.0,
        "bb_pct_b": 0.62,
        "stoch_k": 58.0,
        "bb_width": 0.1,
        "macd_hist": 0.4,
        "ema_stack": "bull",
        "votes": {
            "ema": "up",
            "macd": "up",
            "rsi": "up",
            "stoch": "up",
            "bollinger": "up",
            "elliott": "up",
            "divergence": "flat",
        },
        "elliott": {"exhaustion": False, "direction": "up", "phase": "impulse", "wave": 3},
        "bull_votes": 6,
        "bear_votes": 0,
        "bias": "up",
    }
    good = confirm_trade("BUY", {"current_price": 110.0, "indicators": stacked})
    assert good["ok"] and good["size_mult"] > 0

    chase = dict(stacked)
    chase["rsi14"] = 82.0
    chase["bb_pct_b"] = 1.08
    chase["stoch_k"] = 91.0
    blocked = confirm_trade("BUY", {"current_price": 140.0, "indicators": chase})
    assert not blocked["ok"]


def test_price_action():
    from agent.signals.price_action import analyze as analyze_pa
    from agent.debate import run_debate
    from agent.signals.indicators import confirm_trade

    # Rising HH/HL closes near the high — bullish tape
    bull = []
    px = 100.0
    for i in range(30):
        px += 0.8
        bull.append({"o": px - 0.5, "h": px + 0.2, "l": px - 0.6, "c": px + 0.15, "v": 1_000_000})
    up = analyze_pa("TEST", bull)
    assert up["ok"]
    assert up["structure"] in ("uptrend", "expanding", "balanced")
    assert up["close_loc"] >= 0.5

    # Wick through a high then close back inside — bull trap / failed break
    trap = []
    px = 100.0
    for i in range(20):
        px += 0.3
        trap.append({"o": px - 0.2, "h": px + 0.3, "l": px - 0.3, "c": px, "v": 1_000_000})
    swing_high = max(b["h"] for b in trap)
    trap.append({"o": px, "h": swing_high + 2.0, "l": px - 0.4, "c": swing_high - 0.5, "v": 1_800_000})
    failed = analyze_pa("TEST", trap)
    assert failed["failed_high"] or failed["hostile_buy"]

    class _S:
        gemini_key = ""
        openai_key = ""

    tech = {
        "symbol": "TEST",
        "quant_score": 80,
        "trend": "bullish",
        "pa_hostile_buy": True,
        "pa_side": "down",
        "pa_summary": "failed high / sell rejection",
        "price_action": failed,
    }
    d = run_debate(tech, {"score": 0.0, "label": "neutral"}, {"regime": "fair", "iv_rv_ratio": 1.0}, _S())
    assert d["action"] == "HOLD"

    stacked = {
        "ok": True,
        "ema63": 90.0,
        "rsi14": 55.0,
        "bb_pct_b": 0.6,
        "stoch_k": 55.0,
        "votes": {k: "up" for k in ("ema", "macd", "rsi", "stoch", "bollinger", "elliott", "divergence")},
        "elliott": {"exhaustion": False, "direction": "up"},
    }
    blocked = confirm_trade(
        "BUY",
        {
            "current_price": 110.0,
            "indicators": stacked,
            "price_action": {"ok": True, "hostile_buy": True, "side": "down", "summary": "failed high"},
        },
    )
    assert not blocked["ok"]


def test_scalp_exits():
    import datetime as dt

    from agent.positions import exit_thresholds, exits_needed

    class S:
        contest_expiry_exit = False
        contest_close = None
        exit_dte = 0
        take_profit_pct = 0.15
        stop_loss_pct = -0.05
        reward_risk_ratio = 3.0
        scalp_mode = True
        scalp_hold_sec = 600
        scalp_giveup_sec = 300

    tp, sl = exit_thresholds(S())
    assert abs(sl + 0.05) < 1e-9
    assert abs(tp - 0.15) < 1e-9

    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=12)).isoformat()
    pos = [
        {
            "symbol": "SPY260831C00600000",
            "asset_class": "us_option",
            "qty": 1,
            "cost_basis": 100,
            "unrealized_pl": 2,
            "expiration_date": "2029-12-19",
            "created_at": old,
        }
    ]
    hits = exits_needed(pos, S())
    assert hits and "time-stop" in hits[0]["reason"]

    pos[0]["created_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=6)).isoformat()
    pos[0]["unrealized_pl"] = -1
    hits = exits_needed(pos, S())
    assert hits and "give-up" in hits[0]["reason"]


def test_scalp_tape():
    from agent.signals.backtest import scalp_backtest
    from agent.signals.intraday import analyze_scalp

    bars = []
    px = 100.0
    for _ in range(30):
        px += 0.05
        bars.append({"c": px, "v": 10_000})
    s = analyze_scalp("SPY", bars)
    assert s["ok"] and s["side"] == "up"
    closes = [b["c"] for b in bars]
    bt = scalp_backtest(closes, "BUY", hold_bars=5)
    assert "win_rate" in bt


def test_var_horizon():
    v = monte_carlo_var("SPY", 450, 5.0, horizon_minutes=10, max_var_pct=40)
    assert "var_95_pct" in v
    assert v["var_95_pct"] >= 0


def main():
    test_pricing()
    test_backtest()
    test_var()
    test_technical()
    test_history()
    test_feedback_scoring()
    test_apply_history()
    test_options_backtest()
    test_occ_root()
    test_strangle_sanity()
    test_spread_ordering()
    test_greeks()
    test_correlation()
    test_reward_risk_exits()
    test_spread_reward_risk()
    test_session()
    test_dynamic_universe()
    test_breakout_coil()
    test_patterns_and_pcr()
    test_indicators_confirm()
    test_price_action()
    test_scalp_exits()
    test_scalp_tape()
    test_var_horizon()
    print("reconcile: 24/24 checks PASS")


if __name__ == "__main__":
    main()
