"""Parse option chains and enforce liquidity gates."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from .pricing import bs_delta, implied_vol

_OCC_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


def occ_root(sym: str) -> str:
    """Extract underlying ticker from OCC option symbol (e.g. MSFT260909C00500000 → MSFT)."""
    m = _OCC_RE.match(str(sym or "").upper())
    return m.group(1) if m else str(sym or "")[:6].strip().upper()


@dataclass
class OptionContract:
    symbol: str
    underlying: str
    opt_type: str
    strike: float
    expiration: str
    bid: float
    ask: float
    mid: float
    spread_frac: float
    delta: float | None
    iv: float | None
    liquid: bool


def _parse_occ(sym: str, underlying: str) -> tuple[str, float, str] | None:
    m = _OCC_RE.match(sym.upper())
    if not m:
        return None
    root, yymmdd, cp, strike_raw = m.groups()
    if root != underlying.upper()[: len(root)]:
        pass
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    exp = f"20{yy:02d}-{mm:02d}-{dd:02d}"
    opt_type = "call" if cp == "C" else "put"
    strike = int(strike_raw) / 1000.0
    return opt_type, strike, exp


def _snapshot_items(raw: Any) -> list[dict]:
    """Normalize MCP chain payloads (list or snapshots dict)."""
    if isinstance(raw, list):
        return raw

    def walk(obj: Any) -> dict | None:
        if isinstance(obj, dict):
            snaps = obj.get("snapshots")
            if isinstance(snaps, dict):
                return snaps
            data = obj.get("data")
            if isinstance(data, dict):
                inner = walk(data)
                if inner:
                    return inner
                if isinstance(data.get("snapshots"), dict):
                    return data["snapshots"]
        return None

    snaps = walk(raw) if isinstance(raw, dict) else None
    if isinstance(snaps, dict):
        return [{"symbol": k, **(v if isinstance(v, dict) else {})} for k, v in snaps.items()]

    items = []
    if isinstance(raw, dict):
        for k in ("options", "contracts", "option_contracts"):
            v = raw.get(k)
            if isinstance(v, list):
                return v
    return items


def _quote_from_item(item: dict) -> tuple[float, float, float]:
    q = item.get("latestQuote") or item.get("latest_quote") or item.get("quote") or {}
    b = float(q.get("bp") or q.get("bid_price") or q.get("bid") or item.get("bid") or 0)
    a = float(q.get("ap") or q.get("ask_price") or q.get("ask") or item.get("ask") or 0)
    if b <= 0 and a <= 0:
        lt = item.get("latestTrade") or item.get("last_trade") or {}
        p = float(lt.get("p") or lt.get("price") or item.get("close_price") or 0)
        if p > 0:
            return p * 0.99, p * 1.01, p
        return 0.0, 0.0, 0.0
    mid = (b + a) / 2 if b > 0 and a > 0 else max(b, a)
    return b, a, mid


def parse_chain(
    raw: Any,
    underlying: str,
    spot: float,
    *,
    max_spread_frac: float = 0.25,
    target_expiry: str | None = None,
    min_mid: float = 0.05,
) -> list[OptionContract]:
    items = _snapshot_items(raw)
    out: list[OptionContract] = []
    today = date.today()
    u = underlying.upper()

    for item in items:
        sym = str(item.get("symbol") or item.get("contract_symbol") or "")
        if not sym:
            continue

        parsed = _parse_occ(sym, u)
        if parsed:
            opt_type, strike, exp = parsed
        else:
            opt_type = str(item.get("type") or ("call" if "C" in sym[-9:] else "put")).lower()
            strike = float(item.get("strike_price") or item.get("strike") or 0)
            exp = str(item.get("expiration_date") or item.get("expiration") or "")

        if target_expiry and exp and not exp.startswith(target_expiry[:10]):
            continue
        if exp:
            try:
                dte = (date.fromisoformat(exp[:10]) - today).days
                if dte < 0:
                    continue
            except ValueError:
                pass

        bid, ask, mid = _quote_from_item(item)
        if bid <= 0 and ask > 0:
            bid = max(ask * 0.92, 0.01)
            mid = (bid + ask) / 2
        spread_frac = (ask - bid) / mid if mid > 0 and ask > bid else 1.0
        g = item.get("greeks") or {}
        delta = g.get("delta")
        if delta is not None:
            delta = float(delta)
        iv = (
            g.get("implied_volatility")
            or g.get("iv")
            or item.get("impliedVolatility")
            or item.get("implied_volatility")
        )
        if iv is not None:
            iv = float(iv)
        elif mid > 0 and spot > 0 and strike > 0 and exp:
            try:
                dte = max(1, (date.fromisoformat(exp[:10]) - today).days)
                T = dte / 365.0
                iv = implied_vol(mid, spot, strike, T, opt_type)
                if delta is None and iv:
                    delta = bs_delta(spot, strike, T, iv, opt_type)
            except ValueError:
                pass

        liquid = mid >= min_mid and ask > 0 and spread_frac <= max_spread_frac
        out.append(
            OptionContract(
                symbol=sym,
                underlying=u,
                opt_type=opt_type,
                strike=strike,
                expiration=exp,
                bid=bid,
                ask=ask,
                mid=mid,
                spread_frac=spread_frac,
                delta=delta,
                iv=iv,
                liquid=liquid,
            )
        )
    return out
