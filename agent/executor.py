"""Order execution via MCP primary, REST fallback for mleg."""
from __future__ import annotations

import uuid
from typing import Any

from .mcp_client import AlpacaMCP
from .orders import extract_order_ids, summarize_fills, wait_for_orders
from .rest_client import RestExecutor
from .strategy import TradePlan, marketable_limit


class Executor:
    def __init__(self, mcp: AlpacaMCP, audit, settings=None):
        self.mcp = mcp
        self.audit = audit
        self.settings = settings
        self._rest: RestExecutor | None = None

    def _rest_client(self) -> RestExecutor:
        if self._rest is None:
            self._rest = RestExecutor()
        return self._rest

    def _timeout(self) -> int:
        return getattr(self.settings, "fill_timeout_sec", 90) if self.settings else 90

    def _use_market(self) -> bool:
        return str(getattr(self.settings, "order_type", "market") or "market").lower() == "market"

    def _cancel_ids(self, order_ids: list[str]) -> None:
        rest = self._rest_client()
        for oid in order_ids:
            try:
                rest.cancel_order(oid)
            except Exception:
                pass

    def execute(self, plan: TradePlan, qty: int, limit_price: float | None = None) -> dict[str, Any]:
        cid_base = f"nexus-{uuid.uuid4().hex[:12]}"
        raw_results: list[Any] = []
        # Alpaca rejects options market mleg without a quote — cross the spread with a limit instead.
        lp = marketable_limit(plan) if self._use_market() else (limit_price or plan.limit_price or 1.0)
        self.audit.event(
            "order_pricing",
            f"{'marketable' if self._use_market() else 'limit'} ${lp:.2f} qty={qty}",
            plan.symbol,
            {"limit": lp, "qty": qty, "mode": "marketable_limit" if self._use_market() else "limit"},
        )

        if plan.legs:
            try:
                r = self.mcp.place_option(
                    order_class="mleg",
                    qty=str(qty),
                    order_type="limit",
                    limit_price=f"{lp:.2f}",
                    legs=plan.legs,
                    client_order_id=cid_base,
                )
                raw_results.append(r)
            except Exception as e:
                self.audit.event("warn", f"MCP mleg failed, REST fallback: {e}", plan.symbol)
                order = self._rest_client().submit_mleg(
                    qty, plan.legs, cid_base, market=False, limit_price=lp
                )
                raw_results.append({"id": str(order.id), "status": str(order.status)})

        elif plan.single_legs and plan.leg_quotes:
            raw_results = self._execute_strangle_sequential(plan, qty, cid_base)
        else:
            raise RuntimeError("plan has no executable legs")

        order_ids = extract_order_ids(raw_results)
        if not order_ids:
            self.audit.event("error", "order submit returned no id (rejected)", plan.symbol, {"raw": str(raw_results)[:800]})
            return {"raw": raw_results, "verified": {"all_filled": False, "orders": raw_results, "failed_count": 1}}
        verified = wait_for_orders(self.mcp, order_ids, timeout_sec=self._timeout())
        summary = summarize_fills(verified)

        if not summary.get("all_filled"):
            self._cancel_ids(order_ids)
            self.audit.event("warn", "unfilled order(s) cancelled after timeout", plan.symbol)

        if not summary.get("all_filled") and plan.single_legs:
            self.audit.event("warn", "strangle leg(s) not fully filled — review exposure", plan.symbol)

        return {"raw": raw_results, "verified": summary}

    def _execute_strangle_sequential(self, plan: TradePlan, qty: int, cid_base: str) -> list[Any]:
        """Submit strangle legs one at a time; abort if first leg fails to fill."""
        results = []
        timeout = self._timeout()
        cushion = 1.20 if self._use_market() else 1.02

        for i, (leg, quote) in enumerate(zip(plan.single_legs, plan.leg_quotes)):
            cid = f"{cid_base}-{i}"
            if leg["side"] == "buy":
                lp = round((quote.ask or quote.mid) * cushion + 0.05, 2)
            else:
                lp = round(max((quote.bid or quote.mid) / cushion, 0.01), 2)
            try:
                r = self.mcp.place_option(
                    symbol=leg["symbol"],
                    side=leg["side"],
                    qty=str(qty),
                    order_type="limit",
                    limit_price=f"{lp:.2f}",
                    position_intent=leg["position_intent"],
                    client_order_id=cid,
                )
            except Exception as e:
                self.audit.event("warn", f"MCP single-leg failed, REST fallback: {e}", plan.symbol)
                order = self._rest_client().submit_single_option(
                    leg["symbol"],
                    qty,
                    lp,
                    side=leg["side"],
                    position_intent=leg["position_intent"],
                    client_order_id=cid,
                    market=False,
                )
                r = {"id": str(order.id), "status": str(order.status)}
            results.append(r)
            oids = extract_order_ids([r])
            if oids:
                verified = wait_for_orders(self.mcp, oids, timeout_sec=timeout)
                summary = summarize_fills(verified)
                if not summary.get("all_filled") and i == 0:
                    self._cancel_ids(oids)
                    self.audit.event("warn", "strangle leg 1 not filled — skipping leg 2", plan.symbol)
                    return results
                if not summary.get("all_filled") and i == 1:
                    self._cancel_ids(oids)
                    leg0 = plan.single_legs[0]["symbol"]
                    try:
                        self.mcp.close_position(leg0)
                        self.audit.event("warn", "strangle leg 2 not filled — unwound leg 1", plan.symbol)
                    except Exception as e:
                        self.audit.event("error", f"strangle unwind failed: {e}", plan.symbol)
                    return results
        return results
