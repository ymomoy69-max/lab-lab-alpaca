"""Alpaca MCP client — primary execution surface for hackathon compliance."""
from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import tempfile
import threading
import datetime as dt
from pathlib import Path
from typing import Any, Callable

from .safety import assert_paper_env

MCP_TOOLSETS = "account,trading,options-data,stock-data,assets,news"
MCP_SERVER_PIN = "alpaca-mcp-server==2.3.0"
# Same interpreter as the app — reliable in Docker/Railway (no console-script shebang).
_MCP_BOOT = (
    "import sys; "
    "from alpaca_mcp_server.cli import main; "
    "sys.argv = ['alpaca-mcp-server', '--transport', 'stdio']; "
    "main()"
)


def _mcp_server_cmd() -> tuple[str, list[str]]:
    return sys.executable, ["-c", _MCP_BOOT]


class McpError(RuntimeError):
    pass


def _coerce(text: str) -> Any:
    t = (text or "").strip()
    if t and t[0] in "[{":
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            pass
    return t


def _dig(blob: Any, key: str) -> Any:
    if isinstance(blob, dict):
        if key in blob:
            return blob[key]
        for v in blob.values():
            got = _dig(v, key)
            if got is not None:
                return got
    elif isinstance(blob, list):
        for v in blob:
            got = _dig(v, key)
            if got is not None:
                return got
    return None


class AlpacaMCP:
    def __init__(self, audit_fn: Callable[[dict], None] | None = None):
        self._key = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY")
        self._secret = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY")
        if not self._key or not self._secret:
            raise McpError("Missing APCA_API_KEY_ID / APCA_API_SECRET_KEY")
        self._audit = audit_fn or (lambda _: None)
        self._loop = None
        self._session = None
        self._thread = None
        self._ready: queue.Queue = queue.Queue()
        self._stop = None
        self._err_path: str | None = None

    def __enter__(self):
        assert_paper_env(dict(os.environ))
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        status, payload = self._ready.get(timeout=120)
        if status != "ok":
            detail = self._read_stderr()
            raise McpError(
                f"MCP start failed: {payload}"
                + (f" | stderr: {detail}" if detail else "")
            )
        return self

    def __exit__(self, *_):
        loop = self._loop
        stop = self._stop
        try:
            if loop and loop.is_running() and stop is not None:
                loop.call_soon_threadsafe(stop.set)
        except RuntimeError:
            pass
        if self._thread:
            self._thread.join(timeout=20)
        self._session = None
        self._loop = None
        self._stop = None
        if self._err_path:
            try:
                os.unlink(self._err_path)
            except OSError:
                pass
            self._err_path = None

    def _read_stderr(self) -> str:
        if not self._err_path:
            return ""
        try:
            return Path(self._err_path).read_text(errors="replace")[-2500:].strip()
        except OSError:
            return ""

    def _child_env(self) -> dict:
        # Do not pass Railway's public PORT — FastMCP may mis-handle it.
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": os.environ.get("HOME") or "/tmp",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PYTHONUNBUFFERED": "1",
            "ALPACA_API_KEY": self._key,
            "ALPACA_SECRET_KEY": self._secret,
            "ALPACA_PAPER_TRADE": "true",
            "ALPACA_TOOLSETS": MCP_TOOLSETS,
            # Banner on stdout corrupts MCP JSON-RPC stdio.
            "FASTMCP_SHOW_SERVER_BANNER": "false",
            "NO_COLOR": "1",
            "TERM": "dumb",
        }
        for var in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "VIRTUAL_ENV", "PYTHONPATH"):
            if os.environ.get(var):
                env[var] = os.environ[var]
        return env

    def _run(self):
        try:
            asyncio.run(self._serve())
        except Exception as e:
            self._ready.put(("err", repr(e)))

    async def _serve(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        cmd, args = _mcp_server_cmd()
        params = StdioServerParameters(
            command=cmd,
            args=args,
            env=self._child_env(),
        )
        err_file = tempfile.NamedTemporaryFile(
            mode="w+", prefix="nexus-mcp-", suffix=".log", delete=False
        )
        self._err_path = err_file.name
        try:
            async with stdio_client(params, errlog=err_file) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.put(("ok", "connected"))
                    await self._stop.wait()
        finally:
            try:
                err_file.flush()
                err_file.close()
            except Exception:
                pass

    def call(self, tool: str, **kwargs) -> Any:
        loop = self._loop
        if not self._session or not loop or not loop.is_running():
            raise McpError("MCP not connected")
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._session.call_tool(tool, kwargs), loop
            )
        except RuntimeError as e:
            raise McpError(f"{tool}: {e}") from e
        try:
            res = fut.result(timeout=90)
        except Exception as e:
            self._audit({"tool": tool, "args": kwargs, "error": repr(e)})
            raise McpError(f"{tool}: {e}") from e
        text = "".join(getattr(c, "text", "") for c in (res.content or []))
        payload = _coerce(text)
        self._audit(
            {
                "tool": tool,
                "args": kwargs,
                "isError": bool(getattr(res, "isError", False)),
                "result": text[:3000],
            }
        )
        err = None
        if isinstance(payload, dict):
            err = payload.get("error")
            data = payload.get("data")
            if err is None and isinstance(data, dict):
                err = data.get("error")
        if err:
            if isinstance(err, dict):
                msg = str(err.get("message") or err)
                detail = err.get("detail")
                if isinstance(detail, dict) and detail.get("message"):
                    msg = f"{msg}: {detail['message']}"
            else:
                msg = str(err)
            raise McpError(f"{tool}: {msg}")
        if getattr(res, "isError", False):
            raise McpError(text[:400])
        return payload

    def account(self) -> dict:
        return self.call("get_account_info")

    def clock(self) -> dict:
        return self.call("get_clock")

    def positions(self) -> Any:
        return self.call("get_all_positions")

    def portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> Any:
        return self.call("get_portfolio_history", period=period, timeframe=timeframe)

    def stock_bars(self, symbol: str, timeframe: str = "1Day", limit: int = 30) -> Any:
        days = max(400, int(limit * 2)) if timeframe == "1Day" else max(1, min(5, (limit // 78) + 1))
        return self.call(
            "get_stock_bars",
            symbols=symbol,
            timeframe=timeframe,
            limit=limit,
            days=days,
            feed="iex",
        )

    def news(self, symbols: str | None = None, limit: int = 10) -> Any:
        kw: dict[str, Any] = {"limit": limit, "sort": "desc"}
        if symbols:
            kw["symbols"] = symbols
        return self.call("get_news", **kw)

    def most_active_stocks(self, by: str = "volume", top: int = 20) -> Any:
        return self.call("get_most_active_stocks", by=by, top=top)

    def stock_snapshot(self, symbols: str, feed: str = "iex") -> Any:
        return self.call("get_stock_snapshot", symbols=symbols, feed=feed)

    def market_movers(self, top: int = 10, market_type: str = "stocks") -> Any:
        return self.call("get_market_movers", market_type=market_type, top=top)

    def option_chain(self, underlying: str, expiration_date: str | None = None) -> Any:
        kw = {"underlying_symbol": underlying, "feed": "indicative"}
        if expiration_date:
            kw["expiration_date"] = expiration_date
        return self.call("get_option_chain", **kw)

    def option_contracts(
        self,
        underlying: str,
        *,
        expiration_date_gte: str | None = None,
        expiration_date_lte: str | None = None,
        limit: int = 200,
    ) -> Any:
        kw: dict[str, Any] = {"underlying_symbols": underlying, "limit": limit}
        if expiration_date_gte:
            kw["expiration_date_gte"] = expiration_date_gte
        if expiration_date_lte:
            kw["expiration_date_lte"] = expiration_date_lte
        return self.call("get_option_contracts", **kw)

    def option_latest_quotes(self, symbols: list[str]) -> Any:
        if not symbols:
            return {}
        # Alpaca accepts comma-separated OCC symbols
        return self.call("get_option_latest_quote", symbols=",".join(symbols))

    def resolve_option_chain(self, underlying: str, settings, *, spot: float | None = None) -> Any:
        """Full put+call chain for one expiry via contract list + batched quote fetch."""
        from datetime import date, timedelta

        from .options.chain import parse_chain, _parse_occ
        from .strategy import pick_apex_expiry

        u = underlying.upper()
        if spot is None or spot <= 0:
            try:
                bars = self.stock_bars(u, limit=5)
                from .signals.technical import bars_list, closes_from_bars

                closes = closes_from_bars(bars_list(bars))
                spot = float(closes[-1]) if closes else 0.0
            except Exception:
                spot = 0.0

        expiry = pick_apex_expiry(
            settings.min_dte,
            settings.max_dte,
            contest_close=settings.contest_close if settings.contest_expiry_exit else None,
        )
        raw = self.option_contracts(u, expiration_date_gte=expiry, expiration_date_lte=expiry, limit=500)
        contracts_meta: list[dict] = []
        if isinstance(raw, dict):
            data = raw.get("data") or raw
            contracts_meta = data.get("option_contracts") or data.get("contracts") or []

        if not contracts_meta:
            chain = self.option_chain(u, expiration_date=expiry)
            if _chain_has_quotes(chain):
                return chain
            return self.option_chain(u)

        # Select strikes near the money so we get both puts and calls (MCP chain truncates to ~100 calls).
        lo = spot * 0.88 if spot > 0 else 0
        hi = spot * 1.12 if spot > 0 else 1e9
        symbols: list[str] = []
        for c in contracts_meta:
            sym = str(c.get("symbol") or "")
            if not sym:
                continue
            parsed = _parse_occ(sym, u)
            if parsed:
                _, strike, _ = parsed
                if spot > 0 and not (lo <= strike <= hi):
                    continue
            symbols.append(sym)

        if len(symbols) < 20 and contracts_meta:
            symbols = [str(c.get("symbol") or "") for c in contracts_meta if c.get("symbol")][:200]

        snapshots: dict[str, dict] = {}
        chunk_size = 40
        for i in range(0, len(symbols), chunk_size):
            chunk = symbols[i : i + chunk_size]
            try:
                quoted = self.option_latest_quotes(chunk)
            except Exception:
                continue
            items = _quote_items(quoted)
            for sym, item in items.items():
                snapshots[sym] = item

        if len(snapshots) >= 10:
            return {"snapshots": snapshots, "expiration": expiry, "underlying": u}

        chain = self.option_chain(u, expiration_date=expiry)
        if _chain_has_quotes(chain):
            return chain
        return {"snapshots": snapshots, "expiration": expiry, "underlying": u}

    def option_contracts_legacy(self, underlying: str, expiration_date_lte: str | None = None) -> Any:
        kw = {"underlying_symbols": underlying, "limit": 200}
        if expiration_date_lte:
            kw["expiration_date_lte"] = expiration_date_lte
        return self.call("get_option_contracts", **kw)

    def place_option(
        self,
        *,
        symbol: str | None = None,
        side: str = "buy",
        qty: str = "1",
        order_type: str = "limit",
        limit_price: str | None = None,
        position_intent: str = "buy_to_open",
        client_order_id: str | None = None,
        legs: list[dict] | None = None,
        order_class: str | None = None,
    ) -> Any:
        kw: dict[str, Any] = {
            "side": side,
            "qty": str(qty),
            "type": order_type,
            "time_in_force": "day",
        }
        if symbol:
            kw["symbol"] = symbol
        if limit_price is not None:
            kw["limit_price"] = limit_price
        if position_intent:
            kw["position_intent"] = position_intent
        if client_order_id:
            kw["client_order_id"] = client_order_id
        if legs:
            kw["legs"] = legs
        if order_class:
            kw["order_class"] = order_class
        return self.call("place_option_order", **kw)

    def cancel_all(self) -> Any:
        return self.call("cancel_all_orders")

    def close_position(self, symbol: str, qty: str | None = None) -> Any:
        kw: dict[str, Any] = {"symbol": symbol}
        if qty:
            kw["qty"] = qty
        return self.call("close_position", **kw)

    def orders(self, status: str = "all", limit: int = 100) -> Any:
        return self.call("get_orders", status=status, limit=limit)


def parse_account_number(acct: Any) -> str:
    return str(_dig(acct, "account_number") or "")


def parse_mcp_list(raw: Any) -> list[dict]:
    """Normalize MCP list payloads: data.result, data.positions, orders, etc."""
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for key in ("result", "positions", "orders", "option_contracts"):
                v = data.get(key)
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]
        for key in ("positions", "orders", "result"):
            v = raw.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


def parse_positions(raw: Any) -> list[dict]:
    return parse_mcp_list(raw)


def parse_orders(raw: Any) -> list[dict]:
    return parse_mcp_list(raw)


def parse_equity(acct: Any) -> float:
    if isinstance(acct, dict):
        for k in ("equity", "portfolio_value"):
            v = _dig(acct, k)
            if v is not None:
                return float(v)
    return 0.0


def parse_buying_power(acct: Any) -> float:
    v = _dig(acct, "buying_power")
    return float(v) if v is not None else 0.0


def market_open(clock: Any) -> bool:
    if isinstance(clock, dict):
        return bool(_dig(clock, "is_open") or _dig(clock, "is_open_now"))
    return False


def _chain_has_quotes(chain: Any) -> bool:
    from .options.chain import _snapshot_items

    return len(_snapshot_items(chain)) > 0


def _quote_items(raw: Any) -> dict[str, dict]:
    """Normalize get_option_latest_quote payload to {symbol: {latestQuote: ...}}."""
    out: dict[str, dict] = {}
    if isinstance(raw, dict):
        data = raw.get("data") or raw
        if isinstance(data, dict):
            for sym, q in data.items():
                if sym.startswith("_"):
                    continue
                if isinstance(q, dict):
                    out[sym] = {"symbol": sym, "latestQuote": q}
            quotes = data.get("quotes") or data.get("option_quotes")
            if isinstance(quotes, dict):
                for sym, q in quotes.items():
                    if isinstance(q, dict):
                        out[sym] = {"symbol": sym, "latestQuote": q}
        snaps = data.get("snapshots") if isinstance(data, dict) else None
        if isinstance(snaps, dict):
            for sym, item in snaps.items():
                if isinstance(item, dict):
                    out[sym] = item
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                sym = str(item.get("symbol") or "")
                if sym:
                    out[sym] = item
    return out
