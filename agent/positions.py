"""Position lifecycle — take profit, stop loss, scalp time-stop, contest close."""
from __future__ import annotations

import datetime as dt
from datetime import date
from typing import Any

from .mcp_client import parse_positions
from .options.chain import occ_root
from .rest_client import RestExecutor


def _positions_list(raw: Any) -> list[dict]:
    return parse_positions(raw)


def _option_positions(raw: Any) -> list[dict]:
    return [
        p
        for p in _positions_list(raw)
        if str(p.get("asset_class") or "") == "us_option" or len(str(p.get("symbol") or "")) > 12
    ]


def _underlying(occ: str) -> str:
    return occ_root(occ)


def _pnl_ratio(p: dict) -> float:
    cost = abs(float(p.get("cost_basis") or p.get("avg_entry_price") or 0))
    upl = float(p.get("unrealized_pl") or 0)
    if cost > 0:
        return upl / cost
    uplpc = p.get("unrealized_plpc")
    if uplpc is not None:
        return float(uplpc)
    return 0.0


def exit_thresholds(settings, *, is_credit: bool = False) -> tuple[float, float]:
    """Take-profit and stop-loss fractions. Credit: 50% TP / 2× credit stop."""
    if is_credit:
        tp = float(getattr(settings, "apex_credit_tp_pct", 0.50))
        stop_mult = float(getattr(settings, "apex_credit_stop_mult", 2.0))
        return tp, -stop_mult
    sl = float(getattr(settings, "stop_loss_pct", -0.22) or -0.22)
    if sl > 0:
        sl = -sl
    ratio = float(getattr(settings, "reward_risk_ratio", 3.0) or 3.0)
    ratio = max(ratio, 0.01)
    tp = abs(sl) * ratio
    return tp, sl


def _is_credit_position(p: dict) -> bool:
    cost = float(p.get("cost_basis") or 0)
    return cost < 0


def _parse_ts(val: Any) -> dt.datetime | None:
    if not val:
        return None
    s = str(val).replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _hold_seconds(p: dict, settings, audit=None) -> float | None:
    occ = str(p.get("symbol") or "")
    raw = p.get("created_at")
    if not raw and audit is not None:
        raw = audit.latest_open_ts(occ, underlying=_underlying(occ))
    opened = _parse_ts(raw)
    if opened is None:
        return None
    now = dt.datetime.now(dt.timezone.utc)
    return (now - opened).total_seconds()


def _dte(p: dict) -> int | None:
    exp = p.get("expiration_date") or p.get("expiry")
    if not exp:
        return None
    try:
        d = date.fromisoformat(str(exp)[:10])
        return (d - date.today()).days
    except ValueError:
        return None


def _hold_cap_label(seconds: int) -> str:
    if seconds >= 3600:
        return f"{seconds // 3600}h"
    return f"{max(seconds // 60, 1)}m"


def _minutes_to_close(clock: Any) -> float | None:
    if not isinstance(clock, dict):
        return None
    from .mcp_client import _dig

    now = _parse_ts(_dig(clock, "timestamp") or _dig(clock, "time"))
    next_close = _parse_ts(_dig(clock, "next_close"))
    if now and next_close:
        return (next_close - now).total_seconds() / 60
    return None


def _intraday_mode(settings) -> bool:
    return bool(getattr(settings, "apex_mode", False)) and int(getattr(settings, "max_dte", 1)) <= 0


def exits_needed(
    positions: Any,
    settings,
    *,
    contest_close: date | None = None,
    audit=None,
    clock: Any | None = None,
) -> list[dict[str, Any]]:
    """Return list of {symbol, reason, qty} to close."""
    close_date = contest_close
    if close_date is None and settings.contest_expiry_exit:
        close_date = settings.contest_close
    days_to_contest = (close_date - date.today()).days if close_date else 999
    actions: list[dict[str, Any]] = []
    scalp = bool(getattr(settings, "scalp_mode", False))
    intraday = _intraday_mode(settings)
    hold_cap = int(getattr(settings, "scalp_hold_sec", 600) or 600)
    giveup = int(getattr(settings, "scalp_giveup_sec", 300) or 300)
    if intraday:
        hold_cap = int(getattr(settings, "apex_hold_sec", hold_cap) or hold_cap)
        giveup = int(getattr(settings, "apex_giveup_sec", giveup) or giveup)
    close_buffer = int(getattr(settings, "session_close_buffer_min", 45) or 45)
    mins_to_close = _minutes_to_close(clock) if clock else None

    for p in _option_positions(positions):
        sym = str(p.get("symbol") or "")
        if not sym:
            continue
        qty = abs(float(p.get("qty") or p.get("quantity") or 0))
        if qty <= 0:
            continue

        ratio = _pnl_ratio(p)
        dte = _dte(p)
        is_credit = _is_credit_position(p)
        take_profit, stop_loss = exit_thresholds(settings, is_credit=is_credit)

        if settings.contest_expiry_exit and close_date and days_to_contest <= 1:
            actions.append({"symbol": sym, "qty": qty, "reason": "contest close window"})
            continue
        if intraday and mins_to_close is not None and 0 < mins_to_close <= close_buffer:
            actions.append({"symbol": sym, "qty": qty, "reason": f"intraday EOD flat ({mins_to_close:.0f}m to close)"})
            continue
        if (not scalp and not intraday) and dte is not None and dte <= settings.exit_dte:
            actions.append({"symbol": sym, "qty": qty, "reason": f"DTE {dte} <= {settings.exit_dte}"})
            continue

        if is_credit:
            cost_basis = abs(float(p.get("cost_basis") or 0))
            if cost_basis > 0:
                upl = float(p.get("unrealized_pl") or 0)
                if upl >= cost_basis * take_profit:
                    actions.append({"symbol": sym, "qty": qty, "reason": f"credit take profit {ratio:.0%}"})
                    continue
                if upl <= -cost_basis * abs(stop_loss):
                    actions.append({"symbol": sym, "qty": qty, "reason": f"credit stop {abs(stop_loss):.0f}× entry credit"})
                    continue
            if dte is not None and dte <= 0 and not intraday:
                actions.append({"symbol": sym, "qty": qty, "reason": "expiry day — buy back spread"})
                continue
            if intraday:
                held = _hold_seconds(p, settings, audit)
                cap_label = _hold_cap_label(hold_cap)
                if held is not None and held >= hold_cap:
                    actions.append(
                        {"symbol": sym, "qty": qty, "reason": f"0DTE time-stop {held / 3600:.1f}h >= {cap_label}"}
                    )
                    continue
                if held is not None and held >= giveup and ratio <= 0:
                    actions.append(
                        {"symbol": sym, "qty": qty, "reason": f"0DTE give-up {held / 60:.0f}m red/flat"}
                    )
                    continue
            continue

        if ratio >= take_profit:
            actions.append({"symbol": sym, "qty": qty, "reason": f"take profit {ratio:.0%} (3:1)"})
            continue
        if ratio <= stop_loss:
            actions.append({"symbol": sym, "qty": qty, "reason": f"stop loss {ratio:.0%} (controlled)"})
            continue
        if scalp or intraday:
            held = _hold_seconds(p, settings, audit)
            cap_label = _hold_cap_label(hold_cap)
            if held is not None and held >= hold_cap:
                label = "0DTE" if intraday else "scalp"
                actions.append(
                    {"symbol": sym, "qty": qty, "reason": f"{label} time-stop {held / 3600:.1f}h >= {cap_label}"}
                )
                continue
            if held is not None and held >= giveup and ratio <= 0:
                label = "0DTE" if intraday else "scalp"
                actions.append(
                    {"symbol": sym, "qty": qty, "reason": f"{label} give-up {held / 60:.0f}m red/flat"}
                )

    # dedupe by symbol (keep first reason)
    seen: set[str] = set()
    out = []
    for a in actions:
        if a["symbol"] in seen:
            continue
        seen.add(a["symbol"])
        out.append(a)
    return out


class PositionManager:
    def __init__(self, mcp, audit, settings):
        self.mcp = mcp
        self.audit = audit
        self.settings = settings

    def _clock(self) -> Any | None:
        try:
            return self.mcp.clock()
        except Exception:
            return None

    def manage(self, positions: Any) -> list[dict]:
        """Close positions that hit exit rules. Returns close results."""
        if not self.settings.armed:
            return []

        to_close = exits_needed(positions, self.settings, audit=self.audit, clock=self._clock())
        results = []
        for item in to_close:
            sym = item["symbol"]
            try:
                try:
                    r = self.mcp.close_position(sym, qty=str(int(item["qty"])))
                except Exception as mcp_err:
                    r = RestExecutor().close_position(sym, qty=int(item["qty"]))
                    self.audit.event("warn", f"MCP close failed, REST flatten: {mcp_err}", _underlying(sym), {"symbol": sym})
                pos = next((p for p in _option_positions(positions) if p.get("symbol") == sym), {})
                self.audit.record_outcome(
                    _underlying(sym),
                    strategy="exit",
                    symbol=sym,
                    status="CLOSED",
                    pnl_ratio=_pnl_ratio(pos),
                    reason=item["reason"],
                )
                self.audit.event("position_close", item["reason"], _underlying(sym), {"symbol": sym, "result": str(r)[:500]})
                results.append({"symbol": sym, "ok": True, "reason": item["reason"]})
            except Exception as e:
                self.audit.event("error", f"close failed: {e}", _underlying(sym), {"symbol": sym})
                results.append({"symbol": sym, "ok": False, "error": str(e)})
        return results

    def liquidate_all(self, positions: Any, *, reason: str) -> list[dict]:
        """Cancel open orders and close every position (options + stocks)."""
        results: list[dict] = []
        try:
            self.mcp.cancel_all()
        except Exception as e:
            try:
                RestExecutor().cancel_all()
            except Exception as e2:
                self.audit.event("warn", f"cancel before liquidate failed: {e}; REST: {e2}", None, {})

        for p in _positions_list(positions):
            sym = str(p.get("symbol") or "")
            qty = abs(float(p.get("qty") or p.get("quantity") or 0))
            if not sym or qty <= 0:
                continue
            try:
                try:
                    r = self.mcp.close_position(sym, qty=str(int(qty)))
                except Exception as mcp_err:
                    r = RestExecutor().close_position(sym, qty=int(qty))
                    self.audit.event("warn", f"MCP close failed, REST: {mcp_err}", _underlying(sym), {"symbol": sym})
                self.audit.record_outcome(
                    _underlying(sym),
                    strategy="liquidate",
                    symbol=sym,
                    status="CLOSED",
                    pnl_ratio=_pnl_ratio(p),
                    reason=reason,
                )
                self.audit.event("position_close", reason, _underlying(sym), {"symbol": sym, "result": str(r)[:500]})
                results.append({"symbol": sym, "ok": True, "reason": reason})
            except Exception as e:
                self.audit.event("error", f"liquidate failed: {e}", _underlying(sym), {"symbol": sym})
                results.append({"symbol": sym, "ok": False, "error": str(e), "reason": reason})
        return results
