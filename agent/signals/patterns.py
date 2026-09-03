"""Chart patterns + candlesticks, with volume confirmation."""
from __future__ import annotations

from typing import Any

from .technical import bars_list


def _f(b: dict, *keys: str, default: float | None = None) -> float | None:
    for k in keys:
        v = b.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return default


def candles_from_bars(bars_raw: Any) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for b in bars_list(bars_raw):
        c = _f(b, "c", "close")
        if c is None:
            continue
        o = _f(b, "o", "open", default=c) or c
        h = _f(b, "h", "high", default=max(o, c)) or max(o, c)
        l = _f(b, "l", "low", default=min(o, c)) or min(o, c)
        v = _f(b, "v", "volume", default=0.0) or 0.0
        h = max(h, o, c)
        l = min(l, o, c)
        body = abs(c - o)
        span = max(h - l, 1e-9)
        out.append(
            {
                "o": o,
                "h": h,
                "l": l,
                "c": c,
                "v": v,
                "body": body,
                "span": span,
                "upper": h - max(o, c),
                "lower": min(o, c) - l,
                "bull": 1.0 if c > o else 0.0,
            }
        )
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _vol_ratio(candle: dict, avg: float) -> float:
    if avg <= 0:
        return 1.0
    return candle["v"] / avg


def _peaks(xs: list[float], order: int = 3) -> list[int]:
    out: list[int] = []
    n = len(xs)
    for i in range(order, n - order):
        window = xs[i - order : i + order + 1]
        if xs[i] >= max(window) - 1e-12 and xs[i] > xs[i - 1] and xs[i] > xs[i + 1]:
            out.append(i)
    return out


def _troughs(xs: list[float], order: int = 3) -> list[int]:
    out: list[int] = []
    n = len(xs)
    for i in range(order, n - order):
        window = xs[i - order : i + order + 1]
        if xs[i] <= min(window) + 1e-12 and xs[i] < xs[i - 1] and xs[i] < xs[i + 1]:
            out.append(i)
    return out


def _hit(
    name: str,
    side: str,
    *,
    volume_confirmed: bool,
    detail: str,
    strength: int,
) -> dict[str, Any]:
    return {
        "pattern": name,
        "side": side,
        "volume_confirmed": volume_confirmed,
        "detail": detail,
        "strength": strength,
    }


def _doji(cs: list[dict], avg_vol: float) -> dict[str, Any] | None:
    if len(cs) < 8:
        return None
    last = cs[-1]
    if last["span"] <= 0 or last["body"] / last["span"] > 0.12:
        return None
    prior = cs[-8:-1]
    drift = last["c"] - prior[0]["c"]
    vr = _vol_ratio(last, avg_vol)
    dragonfly = last["lower"] >= 2.0 * max(last["body"], last["span"] * 0.08) and last["upper"] <= last["span"] * 0.25
    gravestone = last["upper"] >= 2.0 * max(last["body"], last["span"] * 0.08) and last["lower"] <= last["span"] * 0.25
    at_highs = drift > 0
    at_lows = drift < 0
    high_vol = vr >= 1.3
    low_vol = vr > 0 and vr < 0.8

    if low_vol:
        return _hit(
            "doji",
            "none",
            volume_confirmed=False,
            detail=f"low-volume pause (vol x{vr:.1f}) — ignore",
            strength=1,
        )
    if dragonfly and (at_lows or high_vol):
        return _hit(
            "dragonfly_doji",
            "up",
            volume_confirmed=high_vol,
            detail=f"dragonfly doji at lows · vol x{vr:.1f}",
            strength=3 if high_vol else 2,
        )
    if gravestone and (at_highs or high_vol):
        return _hit(
            "gravestone_doji",
            "down",
            volume_confirmed=high_vol,
            detail=f"gravestone doji at highs · vol x{vr:.1f}",
            strength=3 if high_vol else 2,
        )
    if high_vol and at_lows:
        return _hit("doji", "up", volume_confirmed=True, detail=f"high-volume doji after selloff · vol x{vr:.1f}", strength=2)
    if high_vol and at_highs:
        return _hit("doji", "down", volume_confirmed=True, detail=f"high-volume doji after rally · vol x{vr:.1f}", strength=2)
    return _hit("doji", "none", volume_confirmed=False, detail=f"indecision doji · vol x{vr:.1f}", strength=1)


def _stars(cs: list[dict], avg_vol: float) -> dict[str, Any] | None:
    if len(cs) < 3:
        return None
    a, b, c = cs[-3], cs[-2], cs[-1]
    vr = _vol_ratio(c, avg_vol)
    vol_ok = avg_vol <= 0 or vr >= 1.15
    a_mid = (a["o"] + a["c"]) / 2
    small_mid = b["body"] <= max(a["body"] * 0.45, a["span"] * 0.25)

    morning = (
        a["bull"] < 1
        and a["body"] >= a["span"] * 0.45
        and small_mid
        and b["c"] < a["c"]
        and c["bull"] > 0
        and c["c"] > a_mid
        and c["body"] >= a["body"] * 0.35
    )
    evening = (
        a["bull"] > 0
        and a["body"] >= a["span"] * 0.45
        and small_mid
        and b["c"] > a["c"]
        and c["bull"] < 1
        and c["c"] < a_mid
        and c["body"] >= a["body"] * 0.35
    )
    if morning:
        return _hit(
            "morning_star",
            "up",
            volume_confirmed=vol_ok,
            detail=f"morning star · vol x{vr:.1f}",
            strength=4 if vol_ok else 3,
        )
    if evening:
        return _hit(
            "evening_star",
            "down",
            volume_confirmed=vol_ok,
            detail=f"evening star · vol x{vr:.1f}",
            strength=4 if vol_ok else 3,
        )
    return None


def _double(highs: list[float], lows: list[float], closes: list[float], vols: list[float], avg_vol: float) -> dict[str, Any] | None:
    n = len(closes)
    if n < 20:
        return None
    last = n - 1
    vr = (vols[-1] / avg_vol) if avg_vol > 0 else 1.0
    vol_ok = avg_vol <= 0 or vr >= 1.15
    pk = _peaks(highs, order=3)
    tr = _troughs(lows, order=3)
    if len(pk) >= 2:
        i, j = pk[-2], pk[-1]
        if 5 <= j - i <= 40:
            left, right = highs[i], highs[j]
            if left > 0 and abs(right - left) / left <= 0.025:
                neck = min(lows[i : j + 1])
                if closes[last] < neck and right >= left * 0.99:
                    return _hit(
                        "double_top",
                        "down",
                        volume_confirmed=vol_ok,
                        detail=f"double top {left:.1f}/{right:.1f} broke {neck:.1f} · vol x{vr:.1f}",
                        strength=5 if vol_ok else 4,
                    )
    if len(tr) >= 2:
        i, j = tr[-2], tr[-1]
        if 5 <= j - i <= 40:
            left, right = lows[i], lows[j]
            if left > 0 and abs(right - left) / left <= 0.025:
                neck = max(highs[i : j + 1])
                if closes[last] > neck and right <= left * 1.01:
                    return _hit(
                        "double_bottom",
                        "up",
                        volume_confirmed=vol_ok,
                        detail=f"double bottom {left:.1f}/{right:.1f} broke {neck:.1f} · vol x{vr:.1f}",
                        strength=5 if vol_ok else 4,
                    )
    return None


def _head_shoulders(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    vols: list[float],
    avg_vol: float,
) -> dict[str, Any] | None:
    n = len(closes)
    if n < 25:
        return None
    vr = (vols[-1] / avg_vol) if avg_vol > 0 else 1.0
    vol_ok = avg_vol <= 0 or vr >= 1.15
    pk = _peaks(highs, order=3)
    tr = _troughs(lows, order=3)
    if len(pk) >= 3:
        a, b, c = pk[-3], pk[-2], pk[-1]
        ls, head, rs = highs[a], highs[b], highs[c]
        if head > ls * 1.02 and head > rs * 1.02 and ls > 0 and abs(rs - ls) / ls <= 0.04:
            neck = min(lows[a : c + 1])
            if closes[-1] < neck:
                return _hit(
                    "head_and_shoulders",
                    "down",
                    volume_confirmed=vol_ok,
                    detail=f"H&S neck {neck:.1f} · vol x{vr:.1f}",
                    strength=6 if vol_ok else 5,
                )
    if len(tr) >= 3:
        a, b, c = tr[-3], tr[-2], tr[-1]
        ls, head, rs = lows[a], lows[b], lows[c]
        if head < ls * 0.98 and head < rs * 0.98 and ls > 0 and abs(rs - ls) / ls <= 0.04:
            neck = max(highs[a : c + 1])
            if closes[-1] > neck:
                return _hit(
                    "inverse_head_and_shoulders",
                    "up",
                    volume_confirmed=vol_ok,
                    detail=f"inv H&S neck {neck:.1f} · vol x{vr:.1f}",
                    strength=6 if vol_ok else 5,
                )
    return None


def analyze(symbol: str, bars_raw: Any, *, vol_mult: float = 1.15) -> dict[str, Any]:
    """Return the strongest recent bullish/bearish pattern, tagged with volume."""
    cs = candles_from_bars(bars_raw)
    empty = {
        "symbol": symbol,
        "ok": False,
        "pattern": None,
        "side": None,
        "volume_confirmed": False,
        "summary": "insufficient bars",
    }
    if len(cs) < 8:
        return empty

    vols = [x["v"] for x in cs]
    avg_vol = _mean(vols[-20:-1] or vols[:-1])
    highs = [x["h"] for x in cs]
    lows = [x["l"] for x in cs]
    closes = [x["c"] for x in cs]

    hits: list[dict[str, Any]] = []
    for fn in (
        lambda: _head_shoulders(highs, lows, closes, vols, avg_vol),
        lambda: _double(highs, lows, closes, vols, avg_vol),
        lambda: _stars(cs, avg_vol),
        lambda: _doji(cs, avg_vol),
    ):
        hit = fn()
        if hit and hit.get("side") in ("up", "down"):
            hits.append(hit)
        elif hit and hit.get("pattern") == "doji" and hit.get("side") == "none":
            hits.append(hit)

    tradeable = [h for h in hits if h.get("side") in ("up", "down")]
    tradeable.sort(key=lambda h: (-int(h.get("strength") or 0), 0 if h.get("volume_confirmed") else 1))
    chosen = tradeable[0] if tradeable else (hits[0] if hits else None)
    if not chosen:
        return {
            "symbol": symbol,
            "ok": True,
            "pattern": None,
            "side": None,
            "volume_confirmed": False,
            "summary": "no pattern",
        }

    side = chosen.get("side") if chosen.get("side") in ("up", "down") else None
    return {
        "symbol": symbol,
        "ok": True,
        "pattern": chosen.get("pattern"),
        "side": side,
        "volume_confirmed": bool(chosen.get("volume_confirmed")),
        "strength": chosen.get("strength", 0),
        "summary": chosen.get("detail") or chosen.get("pattern"),
        "vol_mult": vol_mult,
    }


def attach_to_tech(tech: dict, patterns: dict, pcr: dict | None = None) -> dict:
    out = dict(tech)
    out["pattern"] = patterns.get("pattern")
    out["pattern_side"] = patterns.get("side")
    out["pattern_volume_ok"] = bool(patterns.get("volume_confirmed"))
    out["pattern_summary"] = patterns.get("summary", "")
    if patterns.get("side") == "up":
        bump = 8 if patterns.get("volume_confirmed") else 4
        out["quant_score"] = max(0, min(100, float(out.get("quant_score", 50)) + bump))
    elif patterns.get("side") == "down":
        bump = 8 if patterns.get("volume_confirmed") else 4
        out["quant_score"] = max(0, min(100, float(out.get("quant_score", 50)) - bump))

    if pcr:
        out["pcr"] = pcr.get("pcr")
        out["pcr_oi"] = pcr.get("pcr_oi")
        out["pcr_zone"] = pcr.get("zone")
        out["pcr_bias"] = pcr.get("bias")
        out["pcr_summary"] = pcr.get("summary", "")
        if pcr.get("zone") == "support":
            out["quant_score"] = max(0, min(100, float(out.get("quant_score", 50)) + 6))
        elif pcr.get("zone") == "resistance":
            out["quant_score"] = max(0, min(100, float(out.get("quant_score", 50)) - 6))
    return out
