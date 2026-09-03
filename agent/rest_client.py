"""REST fallback for multi-leg orders when MCP legs fail."""
from __future__ import annotations

import os
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import ClosePositionRequest, LimitOrderRequest, MarketOrderRequest, OptionLegRequest

from .safety import assert_paper_url


class RestExecutor:
    def __init__(self):
        key = os.environ["APCA_API_KEY_ID"]
        secret = os.environ["APCA_API_SECRET_KEY"]
        paper = os.getenv("ALPACA_PAPER", "true").lower() in ("1", "true", "yes")
        assert_paper_url("https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets")
        self.client = TradingClient(key, secret, paper=paper)

    def _legs(self, legs: list[dict]) -> list[OptionLegRequest]:
        return [
            OptionLegRequest(
                symbol=leg["symbol"],
                side=OrderSide(leg["side"]),
                ratio_qty=leg.get("ratio_qty", "1"),
                position_intent=leg["position_intent"],
            )
            for leg in legs
        ]

    def cancel_all(self) -> Any:
        return self.client.cancel_orders()

    def cancel_order(self, order_id: str) -> None:
        self.client.cancel_order_by_id(order_id)

    def submit_mleg(
        self,
        qty: int,
        legs: list[dict],
        client_order_id: str | None = None,
        *,
        market: bool = True,
        limit_price: float | None = None,
    ) -> Any:
        leg_reqs = self._legs(legs)
        if market:
            req = MarketOrderRequest(
                order_class=OrderClass.MLEG,
                qty=qty,
                time_in_force=TimeInForce.DAY,
                legs=leg_reqs,
                client_order_id=client_order_id,
            )
        else:
            req = LimitOrderRequest(
                order_class=OrderClass.MLEG,
                qty=qty,
                limit_price=limit_price,
                time_in_force=TimeInForce.DAY,
                legs=leg_reqs,
                client_order_id=client_order_id,
            )
        return self.client.submit_order(req)

    def submit_mleg_limit(
        self,
        qty: int,
        limit_price: float,
        legs: list[dict],
        client_order_id: str | None = None,
    ) -> Any:
        return self.submit_mleg(qty, legs, client_order_id, market=False, limit_price=limit_price)

    def submit_single_option(
        self,
        symbol: str,
        qty: int,
        limit_price: float | None = None,
        side: str = "buy",
        position_intent: str = "buy_to_open",
        client_order_id: str | None = None,
        *,
        market: bool = False,
    ) -> Any:
        if market:
            req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide(side),
                time_in_force=TimeInForce.DAY,
                position_intent=position_intent,
                client_order_id=client_order_id,
            )
        else:
            req = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                limit_price=limit_price,
                side=OrderSide(side),
                time_in_force=TimeInForce.DAY,
                position_intent=position_intent,
                client_order_id=client_order_id,
            )
        return self.client.submit_order(req)

    def close_position(self, symbol: str, qty: str | int | None = None) -> Any:
        if qty is None:
            return self.client.close_position(symbol)
        return self.client.close_position(
            symbol, close_options=ClosePositionRequest(qty=str(int(float(qty))))
        )
