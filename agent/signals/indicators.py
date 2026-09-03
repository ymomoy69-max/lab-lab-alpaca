"""Multi-indicator stack: EMA 5/20/63, RSI, MACD, stochastic, Bollinger, Elliott-style swings."""
from __future__ import annotations

import math
from typing import Any


def _bars_list(raw: Any) -> list[dict]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for root in (raw, raw.get("data") if isinstance(raw.get("data"), dict) else {}):
            if not isinstance(root, dict):
                continue
            bars = root.get("bars")
            if isinstance(bars, dict):
                for sym_bars in bars.values():
                    if isinstance(sym_bars, list):
                        return sym_bars
            if isinstance(bars, list):
                return bars
            for k in ("items", "data"):
                v = root.get(k)
                if isinstance(v, list):
                    return v
            if "bar" in root:
                return [root["bar"]]
    return []


def _ohlcv(bars_raw: Any) -> tuple[list[float], list[float], list[float]]:
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for b in _bars_list(bars_raw):
        c = b.get("c") if b.get("c") is not None else b.get("close")
        if c is None:
            continue
        close = float(c)
        h = b.get("h") if b.get("h") is not None else b.get("high")
        l = b.get("l") if b.get("l") is not None else b.get("low")
        highs.append(float(h) if h is not None else close)
        lows.append(float(l) if l is not None else close)
        closes.append(close)
    return highs, lows, closes


def _ema(closes: list[float], period: int) -> list[float]:
    if not closes or period < 1:
        return []
    k = 2.0 / (period + 1)
    out: list[float] = []
    seed = sum(closes[:period]) / min(period, len(closes))
    ema = seed
    for i, px in enumerate(closes):
        if i < period - 1:
            out.append(sum(closes[: i + 1]) / (i + 1))
            continue
        if i == period - 1:
            ema = seed
        else:
            ema = px * k + ema * (1 - k)
        out.append(ema)
    return out


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0.0)) / period
    if avg_loss <= 1e-12:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _macd(closes: list[float]) -> dict[str, float]:
    if len(closes) < 35:
        return {"line": 0.0, "signal": 0.0, "hist": 0.0, "ok": False}
    fast = _ema(closes, 12)
    slow = _ema(closes, 26)
    macd_line = [a - b for a, b in zip(fast, slow)]
    signal = _ema(macd_line[25:], 9)
    line = macd_line[-1]
    sig = signal[-1] if signal else 0.0
    prev_hist = (macd_line[-2] - (signal[-2] if len(signal) > 1 else 0.0)) if len(macd_line) > 1 else 0.0
    hist = line - sig
    return {"line": line, "signal": sig, "hist": hist, "prev_hist": prev_hist, "ok": True}


def _stoch(highs: list[float], lows: list[float], closes: list[float], k_len: int = 14, d_len: int = 3) -> dict[str, float]:
    if len(closes) < k_len + d_len:
        return {"k": 50.0, "d": 50.0, "ok": False}
    ks: list[float] = []
    for i in range(k_len - 1, len(closes)):
        window_h = max(highs[i - k_len + 1 : i + 1])
        window_l = min(lows[i - k_len + 1 : i + 1])
        span = window_h - window_l
        ks.append(50.0 if span <= 1e-12 else (closes[i] - window_l) / span * 100.0)
    d = sum(ks[-d_len:]) / d_len
    return {"k": ks[-1], "d": d, "ok": True}


def _bollinger(closes: list[float], period: int = 20, nstd: float = 2.0) -> dict[str, float]:
    if len(closes) < period:
        return {"mid": closes[-1] if closes else 0.0, "upper": 0.0, "lower": 0.0, "pct_b": 0.5, "width": 0.0, "ok": False}
    window = closes[-period:]
    mid = sum(window) / period
    var = sum((x - mid) ** 2 for x in window) / period
    sd = math.sqrt(var)
    upper = mid + nstd * sd
    lower = mid - nstd * sd
    width = (upper - lower) / mid if mid else 0.0
    pct_b = (closes[-1] - lower) / (upper - lower) if upper != lower else 0.5
    return {"mid": mid, "upper": upper, "lower": lower, "pct_b": pct_b, "width": width, "ok": True}


def _extrema(xs: list[float], order: int = 4) -> tuple[list[int], list[int]]:
    peaks: list[int] = []
    troughs: list[int] = []
    n = len(xs)
    for i in range(order, n - order):
        w = xs[i - order : i + order + 1]
        if xs[i] >= max(w) - 1e-12 and xs[i] > xs[i - 1] and xs[i] > xs[i + 1]:
            peaks.append(i)
        if xs[i] <= min(w) + 1e-12 and xs[i] < xs[i - 1] and xs[i] < xs[i + 1]:
            troughs.append(i)
    return peaks, troughs


def _elliott(highs: list[float], lows: list[float], closes: list[float]) -> dict[str, Any]:
    """Heuristic impulse vs correction from swing structure (Elliott-style, not a full count)."""
    empty = {"phase": "unknown", "direction": None, "wave": None, "exhaustion": False, "ok": False}
    if len(closes) < 30:
        return empty
    peaks, troughs = _extrema(highs, 4)
    if len(peaks) < 2 or len(troughs) < 2:
        peaks, troughs = _extrema(highs, 3)
    swings: list[tuple[int, str, float]] = []
    for i in peaks:
        swings.append((i, "H", highs[i]))
    for i in troughs:
        swings.append((i, "L", lows[i]))
    swings.sort(key=lambda x: x[0])
    if len(swings) < 5:
        return empty
    last = swings[-5:]
    labels = "".join(s[1] for s in last)
    prices = [s[2] for s in last]
    impulse_up = labels in ("LHLHL", "HLHLH") and prices[-1] > prices[0] and max(prices) == max(prices[-3:])
    impulse_dn = labels in ("HLHLH", "LHLHL") and prices[-1] < prices[0] and min(prices) == min(prices[-3:])
    hh_hl = len(peaks) >= 2 and len(troughs) >= 2 and highs[peaks[-1]] > highs[peaks[-2]] and lows[troughs[-1]] > lows[troughs[-2]]
    lh_ll = len(peaks) >= 2 and len(troughs) >= 2 and highs[peaks[-1]] < highs[peaks[-2]] and lows[troughs[-1]] < lows[troughs[-2]]
    if impulse_up or hh_hl:
        phase, direction, wave = "impulse", "up", 5 if labels.endswith("H") else 4
    elif impulse_dn or lh_ll:
        phase, direction, wave = "impulse", "down", 5 if labels.endswith("L") else 4
    else:
        phase, direction, wave = "corrective", None, 3
    exhaustion = phase == "impulse" and wave == 5
    return {
        "phase": phase,
        "direction": direction,
        "wave": wave,
        "exhaustion": exhaustion,
        "ok": True,
        "summary": f"{phase} {direction or ''} w{wave}".strip(),
    }


def _vote(condition_up: bool, condition_down: bool) -> str:
    if condition_up and not condition_down:
        return "up"
    if condition_down and not condition_up:
        return "down"
    return "flat"


def analyze(symbol: str, bars_raw: Any) -> dict[str, Any]:
    highs, lows, closes = _ohlcv(bars_raw)
    n = len(closes)
    if n < 20:
        return {"symbol": symbol, "ok": False, "summary": "need 20+ bars"}

    price = closes[-1]
    ema5 = _ema(closes, 5)
    ema20 = _ema(closes, 20)
    ema63 = _ema(closes, 63)
    e5, e20 = ema5[-1], ema20[-1]
    e63 = ema63[-1] if len(closes) >= 63 else e20
    rsi = _rsi(closes, 14)
    macd = _macd(closes)
    stoch = _stoch(highs, lows, closes)
    bb = _bollinger(closes, 20, 2.0)
    wave = _elliott(highs, lows, closes)

    stack_up = price > e5 > e20 > e63
    stack_dn = price < e5 < e20 < e63
    ema_vote = _vote(stack_up or (price > e20 and e5 > e20), stack_dn or (price < e20 and e5 < e20))
    macd_vote = _vote(macd["hist"] > 0 and macd["line"] > macd["signal"], macd["hist"] < 0 and macd["line"] < macd["signal"]) if macd.get("ok") else "flat"
    rsi_vote = _vote(30 < rsi < 70 and rsi >= 52, 30 < rsi < 70 and rsi <= 48)
    if rsi >= 70:
        rsi_vote = "down"
    elif rsi <= 30:
        rsi_vote = "up"
    st_vote = "flat"
    if stoch.get("ok"):
        if stoch["k"] >= 80:
            st_vote = "down"
        elif stoch["k"] <= 20:
            st_vote = "up"
        elif stoch["k"] > stoch["d"] and stoch["k"] > 50:
            st_vote = "up"
        elif stoch["k"] < stoch["d"] and stoch["k"] < 50:
            st_vote = "down"
    bb_vote = "flat"
    if bb.get("ok"):
        if bb["pct_b"] >= 1.0:
            bb_vote = "down"
        elif bb["pct_b"] <= 0.0:
            bb_vote = "up"
        elif 0.55 <= bb["pct_b"] < 0.95:
            bb_vote = "up"
        elif 0.05 < bb["pct_b"] <= 0.45:
            bb_vote = "down"
    ell_vote = "flat"
    if wave.get("ok") and wave.get("direction") in ("up", "down"):
        ell_vote = "down" if wave.get("exhaustion") else str(wave["direction"])

    # Extra: RSI/price divergence on last 20 closes vs RSI path
    div_vote = "flat"
    if n >= 20:
        px_up = closes[-1] > closes[-10]
        rsi_now = rsi
        rsi_prev = _rsi(closes[:-8], 14) if n >= 28 else rsi
        if px_up and rsi_now < rsi_prev - 3:
            div_vote = "down"
        elif (not px_up) and rsi_now > rsi_prev + 3:
            div_vote = "up"

    votes = {
        "ema": ema_vote,
        "macd": macd_vote,
        "rsi": rsi_vote,
        "stoch": st_vote,
        "bollinger": bb_vote,
        "elliott": ell_vote,
        "divergence": div_vote,
    }
    bull = sum(1 for v in votes.values() if v == "up")
    bear = sum(1 for v in votes.values() if v == "down")
    if bull > bear + 1:
        bias = "up"
    elif bear > bull + 1:
        bias = "down"
    else:
        bias = "mixed"

    score = 50.0 + (bull - bear) * 6
    score = max(0, min(100, score))
    if stack_up:
        score = min(100, score + 8)
    if stack_dn:
        score = max(0, score - 8)

    return {
        "symbol": symbol,
        "ok": True,
        "ema5": round(e5, 2),
        "ema20": round(e20, 2),
        "ema63": round(e63, 2),
        "ema_stack": "bull" if stack_up else "bear" if stack_dn else "mixed",
        "rsi14": round(rsi, 1),
        "macd_line": round(macd["line"], 4),
        "macd_signal": round(macd["signal"], 4),
        "macd_hist": round(macd["hist"], 4),
        "stoch_k": round(stoch["k"], 1),
        "stoch_d": round(stoch["d"], 1),
        "bb_upper": round(bb["upper"], 2),
        "bb_mid": round(bb["mid"], 2),
        "bb_lower": round(bb["lower"], 2),
        "bb_pct_b": round(bb["pct_b"], 3),
        "bb_width": round(bb["width"], 4),
        "elliott": wave,
        "votes": votes,
        "bull_votes": bull,
        "bear_votes": bear,
        "bias": bias,
        "score": round(score, 1),
        "summary": (
            f"EMA {('5>20>63' if stack_up else '5<20<63' if stack_dn else 'mixed')} · "
            f"RSI {rsi:.0f} · MACD {macd['hist']:+.3f} · stoch {stoch['k']:.0f} · "
            f"%B {bb['pct_b']:.2f} · {wave.get('summary', 'wave n/a')} · votes {bull}u/{bear}d"
        ),
    }


def attach_to_tech(tech: dict, indicators: dict) -> dict:
    out = dict(tech)
    if not indicators.get("ok"):
        return out
    out["indicators"] = indicators
    out["ema5"] = indicators.get("ema5")
    out["ema20"] = indicators.get("ema20")
    out["ema63"] = indicators.get("ema63")
    out["ema_stack"] = indicators.get("ema_stack")
    out["rsi14"] = indicators.get("rsi14")
    out["rsi"] = indicators.get("rsi14", out.get("rsi"))
    out["macd_hist"] = indicators.get("macd_hist")
    out["stoch_k"] = indicators.get("stoch_k")
    out["bb_pct_b"] = indicators.get("bb_pct_b")
    out["indicator_bias"] = indicators.get("bias")
    out["indicator_summary"] = indicators.get("summary")
    out["quant_score"] = max(
        0,
        min(100, 0.55 * float(out.get("quant_score", 50)) + 0.45 * float(indicators.get("score", 50))),
    )
    return out


def confirm_trade(action: str, tech: dict, settings=None) -> dict[str, Any]:
    """Fail-closed pre-trade checklist to avoid chasing and fighting the higher-timeframe trend."""
    ind = tech.get("indicators") or {}
    if not ind.get("ok"):
        return {"ok": True, "size_mult": 1.0, "reason": "indicators unavailable", "summary": "no indicator gate"}

    min_votes = int(getattr(settings, "min_indicator_votes", 3) if settings else 3)
    has_catalyst = bool(tech.get("breakout") or (tech.get("pattern_side") and tech.get("pattern_volume_ok")))
    if has_catalyst:
        min_votes = max(2, min_votes - 1)

    votes: dict[str, str] = ind.get("votes") or {}
    needed = "up" if action == "BUY" else "down" if action == "SELL" else None
    aligned = sum(1 for v in votes.values() if v == needed) if needed else 0
    opposed = sum(1 for v in votes.values() if needed and v == ("down" if needed == "up" else "up"))
    price = float(tech.get("current_price") or 0)
    ema63 = float(ind.get("ema63") or 0)
    rsi = float(ind.get("rsi14") or tech.get("rsi") or 50)
    pct_b = float(ind.get("bb_pct_b") or 0.5)
    stoch_k = float(ind.get("stoch_k") or 50)
    wave = ind.get("elliott") or {}
    blocks: list[str] = []

    scalp_mode = bool(getattr(settings, "scalp_mode", False) if settings else False)
    scalp = tech.get("scalp") or {}

    if action in ("BUY", "SELL"):
        pa = tech.get("price_action") or {}
        if pa.get("ok") and not scalp_mode:
            if action == "BUY" and pa.get("hostile_buy"):
                blocks.append("price action hostile to BUY (" + (pa.get("summary") or "tape") + ")")
            if action == "SELL" and pa.get("hostile_sell"):
                blocks.append("price action hostile to SELL (" + (pa.get("summary") or "tape") + ")")
            if action == "BUY" and pa.get("side") == "down" and not (
                pa.get("pin_buy") or pa.get("engulf_up") or pa.get("choch") == "up" or pa.get("failed_low")
            ):
                blocks.append("market structure/tape is bearish")
            if action == "SELL" and pa.get("side") == "up" and not (
                pa.get("pin_sell") or pa.get("engulf_dn") or pa.get("choch") == "down" or pa.get("failed_high")
            ):
                blocks.append("market structure/tape is bullish")
        if scalp_mode and scalp.get("ok"):
            if action == "BUY" and scalp.get("side") != "up":
                blocks.append("1-min tape not long")
            if action == "SELL" and scalp.get("side") != "down":
                blocks.append("1-min tape not short")
        elif not scalp_mode:
            if aligned < min_votes:
                blocks.append(f"only {aligned}/{min_votes} indicator votes for {action}")
            if opposed > aligned:
                blocks.append(f"indicators oppose {action} ({opposed} vs {aligned})")
            if action == "BUY":
                if ema63 and price < ema63 * 0.995 and not tech.get("breakout"):
                    blocks.append("price below quarterly EMA")
                if rsi >= 78 and pct_b >= 1.0:
                    blocks.append("RSI/Bollinger overbought chase")
                if stoch_k >= 88 and rsi >= 70:
                    blocks.append("stoch+RSI exhaustion")
                if wave.get("exhaustion") and wave.get("direction") == "up" and rsi >= 68:
                    blocks.append("Elliott wave-5 exhaustion")
            if action == "SELL":
                if ema63 and price > ema63 * 1.005 and not tech.get("breakout"):
                    blocks.append("price above quarterly EMA")
                if rsi <= 22 and pct_b <= 0.0:
                    blocks.append("RSI/Bollinger oversold chase")
                if stoch_k <= 12 and rsi <= 30:
                    blocks.append("stoch+RSI capitulation")
                if wave.get("exhaustion") and wave.get("direction") == "down" and rsi <= 32:
                    blocks.append("Elliott wave-5 down exhaustion")
    elif action == "SELL_VOL":
        if wave.get("phase") == "impulse" and ind.get("bb_width", 1) < 0.06:
            blocks.append("short vol into squeeze+impulse")
    elif action == "VOL":
        if ind.get("ema_stack") in ("bull", "bear") and abs(float(ind.get("macd_hist") or 0)) < 1e-6:
            pass

    ok = not blocks
    size = 1.0 if aligned >= min_votes + 2 else 0.5 if ok and aligned >= min_votes else 1.0
    if not ok:
        size = 0.0
    reason = "; ".join(blocks) if blocks else f"{aligned} votes aligned · size {size:.0%}"
    return {
        "ok": ok,
        "size_mult": size,
        "aligned": aligned,
        "opposed": opposed,
        "min_votes": min_votes,
        "reason": reason,
        "summary": ("BLOCK " + reason) if blocks else f"confirm {action} · {reason}",
    }
