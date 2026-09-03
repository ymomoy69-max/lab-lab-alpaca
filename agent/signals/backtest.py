"""Pre-trade validation — momentum + options-aware proxy backtests."""
from __future__ import annotations

import math
from typing import Any


def momentum_backtest(closes: list[float], direction: str, *, lookback: int = 20) -> dict[str, Any]:
    """Simple walk-forward: would recent momentum have been profitable?"""
    if len(closes) < lookback + 5:
        return {"passed": True, "win_rate": 0.5, "reason": "insufficient history — pass"}

    wins = 0
    trials = 0
    for i in range(lookback, len(closes) - 1):
        ret = (closes[i + 1] - closes[i]) / closes[i]
        mom = (closes[i] - closes[i - lookback]) / closes[i - lookback]
        if direction == "BUY" and mom > 0:
            trials += 1
            if ret > 0:
                wins += 1
        elif direction == "SELL" and mom < 0:
            trials += 1
            if ret < 0:
                wins += 1
        elif direction in ("VOL", "HOLD", "SELL_VOL"):
            trials += 1
            if abs(ret) > 0.005:
                wins += 1

    if trials == 0:
        return {"passed": True, "win_rate": 0.5, "reason": "no trials — pass"}

    win_rate = wins / trials
    passed = win_rate >= 0.48 if direction in ("VOL", "SELL_VOL") else win_rate >= 0.52
    return {
        "passed": passed,
        "win_rate": round(win_rate, 3),
        "trials": trials,
        "reason": f"momentum win_rate={win_rate:.1%}",
    }


def _realized_vol(closes: list[float], window: int = 20) -> float:
    if len(closes) < window + 1:
        return 0.15
    rets = []
    for i in range(-window, -1):
        if closes[i - 1] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))
    if not rets:
        return 0.15
    var = sum(r * r for r in rets) / len(rets)
    return max(0.05, math.sqrt(var * 252))


def options_backtest(
    closes: list[float],
    direction: str,
    *,
    lookback: int = 20,
    spread_width_pct: float = 0.03,
    strangle_move_pct: float = 0.025,
) -> dict[str, Any]:
    """Proxy P&L for options structures using spot moves only."""
    if len(closes) < lookback + 10:
        return {"passed": True, "win_rate": 0.5, "reason": "insufficient history — pass"}

    wins = 0
    trials = 0
    for i in range(lookback, len(closes) - 5):
        window = closes[i - lookback : i + 1]
        fwd = closes[i + 5]
        spot = closes[i]
        if spot <= 0:
            continue
        move = (fwd - spot) / spot
        rv = _realized_vol(window, min(20, len(window) - 1))

        if direction == "BUY":
            trials += 1
            if move > spread_width_pct * 0.5:
                wins += 1
        elif direction == "SELL":
            trials += 1
            if move < -spread_width_pct * 0.5:
                wins += 1
        elif direction in ("VOL",):
            trials += 1
            if abs(move) >= max(strangle_move_pct, rv / math.sqrt(252) * 2):
                wins += 1
        elif direction == "SELL_VOL":
            trials += 1
            if abs(move) < spread_width_pct:
                wins += 1

    if trials == 0:
        return {"passed": True, "win_rate": 0.5, "reason": "no options trials — pass"}

    win_rate = wins / trials
    passed = win_rate >= 0.48
    return {
        "passed": passed,
        "win_rate": round(win_rate, 3),
        "trials": trials,
        "reason": f"options proxy win_rate={win_rate:.1%}",
    }


def combined_backtest(
    closes: list[float],
    direction: str,
    *,
    min_win_rate: float = 0.38,
) -> dict[str, Any]:
    mom = momentum_backtest(closes, direction)
    opt = options_backtest(closes, direction)
    win_rate = (float(mom.get("win_rate", 0.5)) + float(opt.get("win_rate", 0.5))) / 2
    passed = win_rate >= min_win_rate
    return {
        "passed": passed,
        "win_rate": round(win_rate, 3),
        "momentum": mom,
        "options": opt,
        "reason": f"combined win_rate={win_rate:.1%}",
    }


def scalp_backtest(
    closes: list[float],
    direction: str,
    *,
    hold_bars: int = 8,
    lookback: int = 3,
    min_win_rate: float = 0.32,
) -> dict[str, Any]:
    """Walk-forward: 3-bar 1-min impulse, hold `hold_bars` minutes, did spot move with us?"""
    need = lookback + hold_bars + 8
    if len(closes) < need:
        return {"passed": True, "win_rate": 0.5, "reason": "insufficient 1-min history — pass"}

    wins = 0
    trials = 0
    end = len(closes) - hold_bars
    for i in range(lookback, end):
        base = closes[i - lookback]
        if base <= 0 or closes[i] <= 0:
            continue
        impulse = (closes[i] - base) / base
        fwd = (closes[i + hold_bars] - closes[i]) / closes[i]
        if direction == "BUY" and impulse > 0:
            trials += 1
            if fwd > 0:
                wins += 1
        elif direction == "SELL" and impulse < 0:
            trials += 1
            if fwd < 0:
                wins += 1
        elif direction in ("VOL", "HOLD", "SELL_VOL"):
            trials += 1
            if abs(fwd) > 0.0008:
                wins += 1

    if trials == 0:
        return {"passed": True, "win_rate": 0.5, "reason": "no scalp trials — pass"}

    win_rate = wins / trials
    passed = win_rate >= min_win_rate
    return {
        "passed": passed,
        "win_rate": round(win_rate, 3),
        "trials": trials,
        "hold_bars": hold_bars,
        "reason": f"scalp {hold_bars}m win_rate={win_rate:.1%}",
    }
