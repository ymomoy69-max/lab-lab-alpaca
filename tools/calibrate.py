#!/usr/bin/env python3
"""Offline calibration — walk-forward backtests per symbol, write data/calibration.json."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from agent.config import SETTINGS
from agent.mcp_client import AlpacaMCP
from agent.signals.backtest import combined_backtest
from agent.signals.history import analyze_history
from agent.signals.technical import analyze as analyze_technical, bars_list, closes_from_bars

STRATEGY_FOR_ACTION = {
    "BUY": "bull_call_spread",
    "SELL": "bear_put_spread",
    "VOL": "long_strangle",
    "SELL_VOL": "iron_condor",
}


def _pick_action(history: dict, bt_by_action: dict[str, dict]) -> tuple[str, dict]:
    bias = history.get("regime_bias") or "HOLD"
    candidates = [bias] if bias in bt_by_action else []
    for action in ("BUY", "VOL", "SELL", "SELL_VOL"):
        if action not in candidates:
            candidates.append(action)

    best_action = "HOLD"
    best_bt = {"passed": False, "win_rate": 0.5}
    best_wr = -1.0
    for action in candidates:
        bt = bt_by_action.get(action) or {}
        wr = float(bt.get("win_rate") or 0)
        if wr > best_wr:
            best_wr = wr
            best_action = action
            best_bt = bt
    return best_action, best_bt


def main() -> int:
    symbols = list(dict.fromkeys(["SPY", *SETTINGS.watchlist]))
    out_symbols: dict = {}
    global_min = SETTINGS.min_confidence

    print(f"Calibrating {len(symbols)} symbols (daily {SETTINGS.history_bars} bars)...")
    print()

    with AlpacaMCP() as mcp:
        for symbol in symbols:
            bars = mcp.stock_bars(symbol, limit=SETTINGS.history_bars)
            closes = closes_from_bars(bars_list(bars))
            tech = analyze_technical(symbol, bars)
            history = analyze_history(symbol, closes)
            if not history.get("ok"):
                print(f"  {symbol}: skip — {history.get('reason')}")
                continue

            bt_by_action = {
                action: combined_backtest(closes, action)
                for action in ("BUY", "SELL", "VOL", "SELL_VOL")
            }
            action, best_bt = _pick_action(history, bt_by_action)
            wr = float(best_bt.get("win_rate") or 0.5)
            passed = bool(best_bt.get("passed", False))

            if wr >= 0.56 and passed:
                sym_min = max(58, SETTINGS.min_confidence - 3)
                boost = 5.0
            elif wr >= 0.52 and passed:
                sym_min = SETTINGS.min_confidence
                boost = 3.0
            elif wr >= 0.48:
                sym_min = SETTINGS.min_confidence + 2
                boost = 0.0
            else:
                sym_min = SETTINGS.min_confidence + 5
                boost = -4.0

            strategy = STRATEGY_FOR_ACTION.get(action, "bull_call_spread")
            if history.get("favored_strategy"):
                strategy = history["favored_strategy"]

            out_symbols[symbol] = {
                "regime": history.get("regime"),
                "regime_bias": history.get("regime_bias"),
                "best_action": action,
                "best_strategy": strategy,
                "backtest_win_rate": round(wr, 3),
                "backtest_passed": passed,
                "min_confidence": sym_min,
                "score_boost": boost,
                "momentum_20d_pct": history.get("momentum_20d_pct"),
                "summary": history.get("summary"),
            }
            global_min = min(global_min, sym_min)

            print(
                f"  {symbol}: {strategy} · action={action} · win={wr:.1%} · "
                f"min_conf={sym_min:.0f} · boost={boost:+.0f}"
            )

    payload = {
        "schema": "nexus-calibration-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "history_bars": SETTINGS.history_bars,
        "global": {
            "min_confidence": round(global_min, 1),
            "vol_min_confidence": 58,
            "default_min_confidence": SETTINGS.min_confidence,
        },
        "symbols": out_symbols,
    }

    out_path = os.path.join(ROOT, SETTINGS.calibration_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)

    print()
    print(f"Calibration written → {out_path}")
    print(f"Suggested NEXUS_MIN_CONFIDENCE={payload['global']['min_confidence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
