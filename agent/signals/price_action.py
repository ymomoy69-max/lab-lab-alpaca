"""Price action — market structure, rejection, close location, failed breaks."""
from __future__ import annotations

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


def _f(b: dict, *keys: str, default: float | None = None) -> float | None:
    for k in keys:
        v = b.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return default


def _candles(bars_raw: Any) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for b in _bars_list(bars_raw):
        c = _f(b, "c", "close")
        if c is None:
            continue
        o = _f(b, "o", "open", default=c) or c
        h = _f(b, "h", "high", default=max(o, c)) or max(o, c)
        l = _f(b, "l", "low", default=min(o, c)) or min(o, c)
        h, l = max(h, o, c), min(l, o, c)
        body = abs(c - o)
        span = max(h - l, 1e-9)
        out.append(
            {
                "o": o,
                "h": h,
                "l": l,
                "c": c,
                "body": body,
                "span": span,
                "upper": h - max(o, c),
                "lower": min(o, c) - l,
                "bull": 1.0 if c > o else 0.0,
            }
        )
    return out


def _extrema(xs: list[float], order: int = 3) -> tuple[list[int], list[int]]:
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


def _close_loc(c: dict) -> float:
    return (c["c"] - c["l"]) / c["span"]


def _structure(highs: list[float], lows: list[float], closes: list[float]) -> dict[str, Any]:
    peaks, troughs = _extrema(highs, 3)
    hh = hl = lh = ll = False
    if len(peaks) >= 2:
        hh = highs[peaks[-1]] > highs[peaks[-2]]
        lh = highs[peaks[-1]] < highs[peaks[-2]]
    if len(troughs) >= 2:
        hl = lows[troughs[-1]] > lows[troughs[-2]]
        ll = lows[troughs[-1]] < lows[troughs[-2]]
    if hh and hl:
        structure = "uptrend"
        side = "up"
    elif lh and ll:
        structure = "downtrend"
        side = "down"
    elif hh and ll:
        structure = "expanding"
        side = None
    elif lh and hl:
        structure = "contracting"
        side = None
    else:
        structure = "balanced"
        side = None

    last_sh = highs[peaks[-1]] if peaks else max(highs[-10:])
    last_sl = lows[troughs[-1]] if troughs else min(lows[-10:])
    px = closes[-1]
    bos = None
    choch = None
    if structure == "uptrend" and px > last_sh:
        bos = "up"
    elif structure == "downtrend" and px < last_sl:
        bos = "down"
    if structure == "uptrend" and px < last_sl:
        choch = "down"
    elif structure == "downtrend" and px > last_sh:
        choch = "up"
    return {
        "structure": structure,
        "side": side,
        "hh": hh,
        "hl": hl,
        "lh": lh,
        "ll": ll,
        "last_swing_high": last_sh,
        "last_swing_low": last_sl,
        "bos": bos,
        "choch": choch,
    }


def _tape_events(cs: list[dict], struct: dict) -> dict[str, Any]:
    last, prev = cs[-1], cs[-2]
    avg_span = sum(x["span"] for x in cs[-10:]) / min(10, len(cs))
    loc = _close_loc(last)
    loc3 = sum(_close_loc(x) for x in cs[-3:]) / 3
    pin_buy = last["lower"] >= 2.0 * max(last["body"], last["span"] * 0.08) and loc >= 0.62
    pin_sell = last["upper"] >= 2.0 * max(last["body"], last["span"] * 0.08) and loc <= 0.38
    engulf_up = (
        last["bull"] > 0
        and prev["bull"] < 1
        and last["c"] > prev["o"]
        and last["o"] < prev["c"]
        and last["body"] > prev["body"]
    )
    engulf_dn = (
        last["bull"] < 1
        and prev["bull"] > 0
        and last["c"] < prev["o"]
        and last["o"] > prev["c"]
        and last["body"] > prev["body"]
    )
    sh, sl = float(struct["last_swing_high"]), float(struct["last_swing_low"])
    failed_high = last["h"] > sh and last["c"] < sh and last["upper"] > last["body"]
    failed_low = last["l"] < sl and last["c"] > sl and last["lower"] > last["body"]
    impulse = last["span"] >= 1.4 * avg_span and last["body"] >= 0.55 * last["span"]
    inside = last["h"] <= prev["h"] and last["l"] >= prev["l"]
    consec = 0
    for x in reversed(cs):
        if last["bull"] > 0 and x["bull"] > 0:
            consec += 1
        elif last["bull"] < 1 and x["bull"] < 1:
            consec += 1
        else:
            break
    rng20_h = max(x["h"] for x in cs[-20:])
    rng20_l = min(x["l"] for x in cs[-20:])
    pos = (last["c"] - rng20_l) / max(rng20_h - rng20_l, 1e-9)
    return {
        "close_loc": loc,
        "close_loc3": loc3,
        "pin_buy": pin_buy,
        "pin_sell": pin_sell,
        "engulf_up": engulf_up,
        "engulf_dn": engulf_dn,
        "failed_high": failed_high,
        "failed_low": failed_low,
        "impulse": impulse,
        "inside": inside,
        "consec": consec,
        "range_pos": pos,
        "wide_bar": last["span"] >= 1.6 * avg_span,
    }


def analyze(symbol: str, bars_raw: Any) -> dict[str, Any]:
    cs = _candles(bars_raw)
    empty = {"symbol": symbol, "ok": False, "side": None, "summary": "need 15+ bars"}
    if len(cs) < 15:
        return empty
    highs = [x["h"] for x in cs]
    lows = [x["l"] for x in cs]
    closes = [x["c"] for x in cs]
    struct = _structure(highs, lows, closes)
    tape = _tape_events(cs, struct)

    score = 50.0
    tags: list[str] = [struct["structure"]]
    if struct["side"] == "up":
        score += 12
    elif struct["side"] == "down":
        score -= 12
    if struct.get("bos") == "up":
        score += 8
        tags.append("BOS-up")
    elif struct.get("bos") == "down":
        score -= 8
        tags.append("BOS-down")
    if struct.get("choch") == "up":
        score += 10
        tags.append("CHOCH-up")
    elif struct.get("choch") == "down":
        score -= 10
        tags.append("CHOCH-down")
    if tape["pin_buy"] or tape["engulf_up"] or tape["failed_low"]:
        score += 10
        tags.append("buy-rejection" if tape["pin_buy"] or tape["failed_low"] else "bull-engulf")
    if tape["pin_sell"] or tape["engulf_dn"] or tape["failed_high"]:
        score -= 10
        tags.append("sell-rejection" if tape["pin_sell"] or tape["failed_high"] else "bear-engulf")
    if tape["close_loc"] >= 0.7:
        score += 6
        tags.append("close-high")
    elif tape["close_loc"] <= 0.3:
        score -= 6
        tags.append("close-low")
    if tape["impulse"] and cs[-1]["bull"] > 0:
        score += 5
        tags.append("impulse-up")
    elif tape["impulse"] and cs[-1]["bull"] < 1:
        score -= 5
        tags.append("impulse-down")
    if tape["range_pos"] >= 0.85 and tape["close_loc"] <= 0.4:
        score -= 6
        tags.append("reject-range-high")
    if tape["range_pos"] <= 0.15 and tape["close_loc"] >= 0.6:
        score += 6
        tags.append("reject-range-low")

    score = max(0.0, min(100.0, score))
    if score >= 62:
        side = "up"
    elif score <= 38:
        side = "down"
    else:
        side = None

    hostile_buy = bool(
        struct.get("choch") == "down"
        or tape["failed_high"]
        or tape["pin_sell"]
        or tape["engulf_dn"]
        or (tape["close_loc"] <= 0.25 and tape["close_loc3"] <= 0.38 and not tape["pin_buy"])
        or (tape["range_pos"] >= 0.88 and tape["close_loc"] <= 0.4)
    )
    hostile_sell = bool(
        struct.get("choch") == "up"
        or tape["failed_low"]
        or tape["pin_buy"]
        or tape["engulf_up"]
        or (tape["close_loc"] >= 0.75 and tape["close_loc3"] >= 0.62 and not tape["pin_sell"])
        or (tape["range_pos"] <= 0.12 and tape["close_loc"] >= 0.6)
    )

    return {
        "symbol": symbol,
        "ok": True,
        "side": side,
        "score": round(score, 1),
        "structure": struct["structure"],
        "bos": struct.get("bos"),
        "choch": struct.get("choch"),
        "close_loc": round(tape["close_loc"], 3),
        "close_loc3": round(tape["close_loc3"], 3),
        "range_pos": round(tape["range_pos"], 3),
        "failed_high": tape["failed_high"],
        "failed_low": tape["failed_low"],
        "pin_buy": tape["pin_buy"],
        "pin_sell": tape["pin_sell"],
        "engulf_up": tape["engulf_up"],
        "engulf_dn": tape["engulf_dn"],
        "hostile_buy": hostile_buy,
        "hostile_sell": hostile_sell,
        "tags": tags,
        "summary": f"PA {struct['structure']} · close {tape['close_loc']:.0%} · " + ", ".join(tags[:5]),
    }


def attach_to_tech(tech: dict, pa: dict) -> dict:
    out = dict(tech)
    if not pa.get("ok"):
        return out
    out["price_action"] = pa
    out["pa_side"] = pa.get("side")
    out["pa_structure"] = pa.get("structure")
    out["pa_summary"] = pa.get("summary")
    out["pa_hostile_buy"] = bool(pa.get("hostile_buy"))
    out["pa_hostile_sell"] = bool(pa.get("hostile_sell"))
    # Price action outweighs smoother indicators when they disagree.
    out["quant_score"] = max(
        0,
        min(100, 0.42 * float(out.get("quant_score", 50)) + 0.58 * float(pa.get("score", 50))),
    )
    return out
