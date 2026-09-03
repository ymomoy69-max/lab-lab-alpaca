"""Load offline calibration artifacts produced by tools/calibrate.py."""
from __future__ import annotations

import json
import os
from typing import Any


def load_calibration(path: str) -> dict[str, Any]:
    if not path or not os.path.exists(path):
        return {"ok": False, "symbols": {}, "global": {}}
    try:
        with open(path) as f:
            data = json.load(f)
        data["ok"] = True
        return data
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "symbols": {}, "global": {}}


def symbol_profile(cal: dict[str, Any], symbol: str) -> dict[str, Any]:
    return (cal.get("symbols") or {}).get(symbol.upper(), {})


def effective_min_confidence(cal: dict[str, Any], symbol: str, base: float, action: str = "BUY") -> float:
    prof = symbol_profile(cal, symbol)
    sym_min = prof.get("min_confidence")
    out = float(sym_min) if sym_min is not None else base
    if action in ("VOL", "SELL_VOL"):
        vol_floor = float((cal.get("global") or {}).get("vol_min_confidence", 58))
        out = min(out, vol_floor)
    return out


def calibration_score_boost(cal: dict[str, Any], symbol: str, strategy: str) -> float:
    prof = symbol_profile(cal, symbol)
    boost = 0.0
    if prof.get("best_strategy") == strategy:
        boost += float(prof.get("score_boost") or 0)
    wr = float(prof.get("backtest_win_rate") or 0)
    if wr >= 0.55:
        boost += 2.0
    elif wr > 0 and wr < 0.48:
        boost -= 3.0
    return boost
