"""Dynamic watchlist — add names that have news AND liquid volume."""
from __future__ import annotations

import re
from typing import Any

from .news import _headlines

TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
DOLLAR_TICKER_RE = re.compile(r"\$([A-Z]{1,5})\b")
SKIP = frozenset({"MARKET", "USD", "USDT", "USDC", "NONE", "NULL"})


def valid_ticker(symbol: str) -> bool:
    s = (symbol or "").strip().upper()
    if not s or s in SKIP or not TICKER_RE.fullmatch(s):
        return False
    if s.endswith("USD") and len(s) > 3:
        return False
    return True


def _as_symbol_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                sym = item.get("symbol") or item.get("ticker")
                if isinstance(sym, str):
                    out.append(sym)
        return out
    return []


def extract_news_mentions(raw: Any) -> dict[str, dict[str, Any]]:
    """Count ticker mentions from market-wide news articles."""
    items = _headlines(raw)
    if not items and isinstance(raw, dict) and isinstance(raw.get("data"), dict):
        items = _headlines(raw["data"])
    mentions: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        headline = str(item.get("headline") or item.get("title") or "")
        tickers = _as_symbol_list(item.get("symbols") or item.get("symbol") or item.get("tickers"))
        if not tickers:
            tickers = DOLLAR_TICKER_RE.findall(headline.upper())
        seen: set[str] = set()
        for raw_sym in tickers:
            sym = str(raw_sym).strip().upper()
            if not valid_ticker(sym) or sym in seen:
                continue
            seen.add(sym)
            row = mentions.setdefault(sym, {"symbol": sym, "count": 0, "headline": headline[:160]})
            row["count"] += 1
            if headline and not row.get("headline"):
                row["headline"] = headline[:160]
    return mentions


def parse_most_actives(raw: Any) -> dict[str, dict[str, Any]]:
    """Normalize MCP/Alpaca most-actives payload to {symbol: {volume, rank}}."""
    rows: list[Any] = []
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        if isinstance(data, dict):
            for key in ("most_actives", "mostActives", "actives", "stocks", "items", "result"):
                v = data.get(key)
                if isinstance(v, list):
                    rows = v
                    break
        if not rows and isinstance(raw.get("data"), list):
            rows = raw["data"]

    out: dict[str, dict[str, Any]] = {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        if not valid_ticker(sym):
            continue
        vol = row.get("volume") or row.get("v") or 0
        try:
            volume = float(vol)
        except (TypeError, ValueError):
            volume = 0.0
        out[sym] = {"symbol": sym, "volume": volume, "rank": i + 1}
    return out


def parse_movers(raw: Any) -> list[str]:
    """Return gainer/loser tickers from get_market_movers."""
    rows: list[Any] = []
    blob: Any = raw
    if isinstance(raw, dict):
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        blob = data
    if isinstance(blob, dict):
        for key in ("gainers", "losers", "movers", "stocks", "items"):
            v = blob.get(key)
            if isinstance(v, list):
                rows.extend(v)
    elif isinstance(blob, list):
        rows = blob
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, str):
            sym = row.strip().upper()
        elif isinstance(row, dict):
            sym = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        else:
            continue
        if not valid_ticker(sym) or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def _num(obj: Any, *keys: str) -> float:
    if not isinstance(obj, dict):
        return 0.0
    for k in keys:
        v = obj.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def _bar_volume(bar: Any) -> float:
    return _num(bar, "v", "volume", "V")


def _bar_close(bar: Any) -> float:
    return _num(bar, "c", "close", "C")


def parse_snapshots(raw: Any) -> dict[str, dict[str, Any]]:
    """Extract last price + session/prev volume from stock snapshots."""
    blob: Any = raw
    if isinstance(raw, dict):
        for key in ("snapshots", "snapshot", "data"):
            inner = raw.get(key)
            if isinstance(inner, dict):
                blob = inner
                break
            if isinstance(inner, list):
                blob = inner
                break

    if isinstance(blob, list):
        mapped: dict[str, Any] = {}
        for snap in blob:
            if isinstance(snap, dict):
                sym = snap.get("symbol") or snap.get("ticker")
                if isinstance(sym, str):
                    mapped[sym] = snap
        blob = mapped

    if not isinstance(blob, dict):
        return {}

    out: dict[str, dict[str, Any]] = {}
    for key, snap in blob.items():
        if not isinstance(snap, dict):
            continue
        sym = str(snap.get("symbol") or key).strip().upper()
        if not valid_ticker(sym):
            continue
        daily = snap.get("dailyBar") or snap.get("daily_bar") or snap.get("bar") or {}
        prev = snap.get("prevDailyBar") or snap.get("previousDailyBar") or snap.get("prev_daily_bar") or {}
        trade = snap.get("latestTrade") or snap.get("latest_trade") or {}
        volume = _bar_volume(daily)
        prev_volume = _bar_volume(prev)
        price = _num(trade, "p", "price") or _bar_close(daily) or _bar_close(prev)
        rel = (volume / prev_volume) if prev_volume > 0 else 1.0
        out[sym] = {
            "symbol": sym,
            "volume": volume,
            "prev_volume": prev_volume,
            "rel_volume": round(rel, 3),
            "price": price,
        }
    return out


def volume_ok(
    *,
    volume: float,
    rel_volume: float,
    price: float,
    on_actives: bool,
    min_share_volume: float,
    min_relative_volume: float,
    min_price: float,
) -> bool:
    if price > 0 and price < min_price:
        return False
    if on_actives:
        return True
    if volume < min_share_volume:
        return False
    if rel_volume + 1e-9 < min_relative_volume:
        return False
    return True


def rank_dynamic(
    mentions: dict[str, dict[str, Any]],
    actives: dict[str, dict[str, Any]],
    snapshots: dict[str, dict[str, Any]],
    core: set[str],
    *,
    min_share_volume: float,
    min_relative_volume: float,
    min_price: float,
    max_symbols: int,
) -> list[dict[str, Any]]:
    """News names with good volume, ranked, excluding the static core list."""
    picked: list[dict[str, Any]] = []
    for sym, mention in mentions.items():
        if sym in core:
            continue
        active = actives.get(sym)
        snap = snapshots.get(sym, {})
        volume = float((active or {}).get("volume") or snap.get("volume") or 0)
        rel = float(snap.get("rel_volume") or 1.0)
        price = float(snap.get("price") or 0)
        on_actives = active is not None
        if not volume_ok(
            volume=volume,
            rel_volume=rel,
            price=price,
            on_actives=on_actives,
            min_share_volume=min_share_volume,
            min_relative_volume=min_relative_volume,
            min_price=min_price,
        ):
            continue
        picked.append(
            {
                "symbol": sym,
                "mentions": int(mention.get("count") or 0),
                "headline": mention.get("headline", ""),
                "volume": volume,
                "rel_volume": rel,
                "price": price,
                "on_actives": on_actives,
                "active_rank": int((active or {}).get("rank") or 99),
                "reason": "news+most-active" if on_actives else "news+volume",
            }
        )

    picked.sort(
        key=lambda r: (
            0 if r["on_actives"] else 1,
            r["active_rank"],
            -r["mentions"],
            -r["rel_volume"],
            -r["volume"],
        )
    )
    return picked[: max(0, max_symbols)]


def _breakout_settings(settings) -> dict[str, float | int]:
    return {
        "cons_bars": int(getattr(settings, "breakout_cons_bars", 60)),
        "max_range_pct": float(getattr(settings, "breakout_max_range_pct", 0.15)),
        "max_drift_pct": float(getattr(settings, "breakout_max_drift_pct", 0.06)),
        "vol_mult": float(getattr(settings, "breakout_vol_mult", 1.25)),
        "max_extension_pct": float(getattr(settings, "breakout_max_extension_pct", 0.08)),
        "min_price": float(getattr(settings, "min_dynamic_price", 8.0)),
    }


def scan_breakouts(
    mcp,
    settings,
    core: set[str],
    candidates: list[str],
) -> list[dict[str, Any]]:
    """Fetch daily bars for liquid names and keep breakouts + volume-confirmed patterns."""
    want_breakout = getattr(settings, "breakout_scan", True)
    want_pattern = getattr(settings, "pattern_scan", True)
    if not want_breakout and not want_pattern:
        return []
    from .breakout import analyze as analyze_breakout
    from .patterns import analyze as analyze_patterns

    cap = int(getattr(settings, "breakout_scan_limit", 12))
    max_hits = int(getattr(settings, "max_breakout_symbols", 4)) + int(
        getattr(settings, "max_pattern_symbols", 4)
    )
    bars_n = max(int(getattr(settings, "breakout_cons_bars", 60)) + 25, 90)
    kwargs = _breakout_settings(settings)

    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sym in candidates:
        if not valid_ticker(sym) or sym in core or sym in seen:
            continue
        if len(seen) >= cap:
            break
        seen.add(sym)
        try:
            bars = mcp.stock_bars(sym, limit=bars_n)
            sig = analyze_breakout(sym, bars, **kwargs) if want_breakout else {}
            pat = analyze_patterns(sym, bars, vol_mult=float(getattr(settings, "pattern_vol_mult", 1.15))) if want_pattern else {}
        except Exception:
            continue
        reason = None
        headline = ""
        rel = 0.0
        extra: dict[str, Any] = {}
        if sig.get("breakout"):
            reason = f"breakout-{sig.get('side')}"
            headline = sig.get("summary", "")
            rel = float(sig.get("volume_ratio") or 0)
            extra["breakout"] = sig
        elif pat.get("side") in ("up", "down") and pat.get("volume_confirmed"):
            reason = f"pattern-{pat.get('pattern')}"
            headline = pat.get("summary", "")
            rel = 1.2
            extra["pattern"] = pat
        if not reason:
            continue
        row = {
            "symbol": sym,
            "mentions": 0,
            "headline": headline,
            "volume": 0.0,
            "rel_volume": rel,
            "price": 0.0,
            "on_actives": True,
            "active_rank": 0,
            "reason": reason,
        }
        row.update(extra)
        found.append(row)
        if len(found) >= max_hits:
            break
    found.sort(key=lambda r: (-float(r.get("rel_volume") or 0), r["symbol"]))
    return found[:max_hits]


def discover_dynamic(
    mcp,
    settings,
    core: tuple[str, ...] | list[str],
) -> list[dict[str, Any]]:
    """News+volume names plus long-consolidation breakouts."""
    if not getattr(settings, "dynamic_watchlist", True):
        return []

    core_set = {s.strip().upper() for s in core if s and s != "MARKET"}
    news_limit = int(getattr(settings, "news_scan_limit", 40))
    try:
        news_raw = mcp.news(limit=news_limit)
    except Exception:
        news_raw = None
    mentions = extract_news_mentions(news_raw)

    actives: dict[str, dict[str, Any]] = {}
    try:
        actives = parse_most_actives(
            mcp.most_active_stocks(by="volume", top=int(getattr(settings, "most_active_top", 20)))
        )
    except Exception:
        actives = {}

    movers: list[str] = []
    try:
        movers = parse_movers(mcp.market_movers(top=10))
    except Exception:
        movers = []

    need_snap = [s for s in mentions if s not in core_set]
    snapshots: dict[str, dict[str, Any]] = {}
    if need_snap:
        chunk = need_snap[:40]
        try:
            snapshots = parse_snapshots(mcp.stock_snapshot(",".join(chunk)))
        except Exception:
            snapshots = {}

    news_picks = rank_dynamic(
        mentions,
        actives,
        snapshots,
        core_set,
        min_share_volume=float(getattr(settings, "min_share_volume", 1_000_000)),
        min_relative_volume=float(getattr(settings, "min_relative_volume", 1.0)),
        min_price=float(getattr(settings, "min_dynamic_price", 8.0)),
        max_symbols=int(getattr(settings, "max_dynamic_symbols", 8)),
    )

    pool = list(dict.fromkeys([*actives.keys(), *movers, *mentions.keys()]))
    breakout_picks = scan_breakouts(mcp, settings, core_set, pool)

    combined: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in [*breakout_picks, *news_picks]:
        sym = str(row.get("symbol") or "")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        combined.append(row)

    cap = int(getattr(settings, "max_dynamic_symbols", 8)) + int(
        getattr(settings, "max_breakout_symbols", 4)
    ) + int(getattr(settings, "max_pattern_symbols", 4))
    return combined[:cap]


def merge_watchlist(
    core: tuple[str, ...] | list[str],
    discovered: list[dict[str, Any]],
    sticky: dict[str, int],
    *,
    ttl_ticks: int,
    max_dynamic: int,
) -> tuple[list[str], list[str]]:
    """Return (scan list, newly added symbols). Refresh TTL for discoveries."""
    core_list = [s.strip().upper() for s in core if s and s.strip() and s != "MARKET"]
    core_set = set(core_list)
    added: list[str] = []
    discovered_syms: list[str] = []

    for row in discovered:
        sym = str(row.get("symbol") or "").upper()
        if not valid_ticker(sym) or sym in core_set:
            continue
        if sticky.get(sym, 0) <= 0:
            added.append(sym)
        sticky[sym] = max(1, ttl_ticks)
        discovered_syms.append(sym)

    for sym, left in list(sticky.items()):
        if left <= 0:
            sticky.pop(sym, None)

    ordered = list(dict.fromkeys([*discovered_syms, *sticky.keys()]))
    extras = [s for s in ordered if s not in core_set][: max(0, max_dynamic)]
    extra_set = set(extras)
    for sym in list(sticky):
        if sym not in extra_set:
            sticky.pop(sym, None)

    for sym in extras:
        sticky[sym] = max(0, sticky.get(sym, ttl_ticks) - 1)

    scan = list(dict.fromkeys([*core_list, *extras]))
    return scan, added
