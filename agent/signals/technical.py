"""Technical signals — AlphaSwarm-inspired quant score."""
from __future__ import annotations

from typing import Any

from .indicators import analyze as analyze_indicators, attach_to_tech as attach_indicators
from .price_action import analyze as analyze_price_action, attach_to_tech as attach_price_action


def bars_list(raw: Any) -> list[dict]:
    return _bars_list(raw)


def closes_from_bars(bars: list[dict]) -> list[float]:
    return _closes(bars)


def _bars_list(raw: Any) -> list[dict]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        # Alpaca v2: {"bars": {"SPY": [...]}} or MCP wrapper with data
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


def _closes(bars: list[dict]) -> list[float]:
    out = []
    for b in bars:
        c = b.get("c") or b.get("close")
        if c is not None:
            out.append(float(c))
    return out


def analyze(symbol: str, bars_raw: Any) -> dict[str, Any]:
    bars = _bars_list(bars_raw)
    closes = _closes(bars)
    if len(closes) < 20:
        return {
            "symbol": symbol,
            "ok": False,
            "quant_score": 50,
            "trend": "unknown",
            "rsi": 50.0,
            "current_price": closes[-1] if closes else 0.0,
        }

    price = closes[-1]
    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sma20

    gains, losses = [], []
    for i in range(1, min(15, len(closes))):
        d = closes[-i] - closes[-i - 1]
        (gains if d > 0 else losses).append(abs(d))
    avg_gain = sum(gains) / len(gains) if gains else 0.001
    avg_loss = sum(losses) / len(losses) if losses else 0.001
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    trend = "bullish" if price > sma20 > sma50 else "bearish" if price < sma20 < sma50 else "neutral"
    score = 50.0
    if trend == "bullish":
        score += min(25, (price - sma20) / sma20 * 500)
    elif trend == "bearish":
        score -= min(25, (sma20 - price) / sma20 * 500)
    if rsi > 70:
        score -= 10
    elif rsi < 30:
        score += 10
    score = max(0, min(100, score))

    # ATR(14) for VaR engine
    trs = []
    for i in range(-14, 0):
        if abs(i) >= len(closes):
            continue
        hi = closes[i]
        lo = closes[i]
        prev = closes[i - 1]
        trs.append(max(hi - lo, abs(hi - prev), abs(lo - prev)))
    atr = sum(trs) / len(trs) if trs else price * 0.02
    atr_pct = (atr / price * 100) if price > 0 else 2.0

    out = {
        "symbol": symbol,
        "ok": True,
        "current_price": round(price, 2),
        "sma_20": round(sma20, 2),
        "sma_50": round(sma50, 2),
        "rsi": round(rsi, 1),
        "atr": round(atr, 2),
        "atr_pct": round(atr_pct, 2),
        "trend": trend,
        "quant_score": round(score, 1),
    }
    out = attach_indicators(out, analyze_indicators(symbol, bars_raw))
    return attach_price_action(out, analyze_price_action(symbol, bars_raw))
