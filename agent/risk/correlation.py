"""Cross-symbol exposure guard + SPY market regime filter."""
from __future__ import annotations

from typing import Any

from ..strategy import TradePlan

INDEX_SYMBOLS = frozenset({"SPY", "QQQ", "IWM", "DIA"})
THEME_MAP = {
    "SPY": "index",
    "QQQ": "index",
    "IWM": "index",
    "DIA": "index",
    "XLK": "tech",
    "SMH": "tech",
    "AAPL": "tech",
    "MSFT": "tech",
    "NVDA": "tech",
    "META": "tech",
    "GOOGL": "tech",
    "AMZN": "tech",
    "TSLA": "tech",
    "AMD": "tech",
    "AVGO": "tech",
    "NFLX": "tech",
    "XLF": "finance",
    "JPM": "finance",
    "BAC": "finance",
    "GS": "finance",
    "V": "finance",
    "XLE": "energy",
    "XOM": "energy",
    "CVX": "energy",
    "OXY": "energy",
    "XLV": "health",
    "XBI": "health",
    "UNH": "health",
    "LLY": "health",
    "JNJ": "health",
    "XLY": "consumer",
    "XLP": "consumer",
    "WMT": "consumer",
    "COST": "consumer",
    "HD": "consumer",
    "DIS": "consumer",
    "KO": "consumer",
    "XLI": "industrial",
    "CAT": "industrial",
    "GE": "industrial",
    "BA": "industrial",
    "XLB": "materials",
    "XLU": "utilities",
    "XLC": "comms",
    "XLRE": "real_estate",
}


from ..options.chain import occ_root
from ..mcp_client import parse_positions


def _option_positions(positions: Any) -> list[dict]:
    raw = parse_positions(positions)
    out = []
    for p in raw:
        sym = str(p.get("symbol") or "")
        if str(p.get("asset_class") or "") == "us_option" or len(sym) > 12:
            out.append(p)
    return out


def _underlying_from_occ(occ: str) -> str:
    return occ_root(occ)


def _direction(plan: TradePlan) -> str:
    if plan.strategy in ("bull_call_spread",):
        return "bull"
    if plan.strategy in ("bear_put_spread",):
        return "bear"
    if plan.strategy in ("long_strangle",):
        return "vol"
    if plan.strategy in ("iron_condor",):
        return "neutral"
    return "unknown"


def exposure_snapshot(positions: Any) -> dict[str, Any]:
    opts = _option_positions(positions)
    by_underlying: dict[str, int] = {}
    themes: dict[str, int] = {}
    for p in opts:
        u = _underlying_from_occ(str(p.get("symbol") or ""))
        by_underlying[u] = by_underlying.get(u, 0) + 1
        theme = THEME_MAP.get(u, "other")
        themes[theme] = themes.get(theme, 0) + 1
    return {"underlyings": by_underlying, "themes": themes, "legs": len(opts)}


def correlation_gate(
    plan: TradePlan,
    positions: Any,
    *,
    max_same_direction: int = 2,
    max_index_directional: int = 1,
    settings=None,
) -> str | None:
    """Limit stacked correlated directional risk."""
    snap = exposure_snapshot(positions)
    direction = _direction(plan)
    if direction == "unknown":
        return None

    if plan.symbol in snap["underlyings"] and snap["underlyings"][plan.symbol] >= 2:
        return f"already have structure on {plan.symbol}"

    if getattr(settings, "scalp_mode", False):
        return None

    theme = THEME_MAP.get(plan.symbol, "other")
    if direction in ("bull", "bear"):
        same_theme = snap["themes"].get(theme, 0)
        if same_theme >= max_same_direction and theme != "other":
            return f"theme {theme} already has {same_theme} option legs"

        if plan.symbol in INDEX_SYMBOLS or theme == "index":
            index_legs = snap["themes"].get("index", 0)
            if index_legs >= max_index_directional * 2:
                return "max index directional exposure reached"

    return None


def spy_regime_gate(plan: TradePlan, spy_history: dict | None) -> str | None:
    """Block single-name directional trades against SPY regime."""
    if not spy_history or not spy_history.get("ok"):
        return None
    if plan.symbol in INDEX_SYMBOLS:
        return None

    regime = spy_history.get("regime")
    direction = _direction(plan)
    if direction == "bull" and regime == "trending_down":
        return f"SPY regime {regime} blocks bullish {plan.symbol}"
    if direction == "bear" and regime == "trending_up":
        return f"SPY regime {regime} blocks bearish {plan.symbol}"
    return None
