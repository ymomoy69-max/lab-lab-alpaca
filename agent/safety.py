"""Paper-only safety guards (from Vega patterns)."""
from __future__ import annotations

from urllib.parse import urlsplit

ALLOWED_HOSTS = frozenset({"paper-api.alpaca.markets", "data.alpaca.markets"})
REDIRECT_VARS = (
    "DATA_API_URL",
    "APCA_API_BASE_URL",
    "ALPACA_API_BASE_URL",
    "APCA_API_DATA_URL",
    "ALPACA_BASE_URL",
    "TRADE_API_URL",
)


class LiveTradingBlocked(RuntimeError):
    pass


def assert_paper_url(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise LiveTradingBlocked(f"Blocked host {host!r} — paper-only agent")
    return url


def assert_paper_env(env: dict) -> None:
    def truthy(v) -> bool:
        return str(v).strip().lower() in ("true", "1", "yes", "y", "on")

    if "ALPACA_PAPER_TRADE" in env and not truthy(env.get("ALPACA_PAPER_TRADE")):
        raise LiveTradingBlocked("ALPACA_PAPER_TRADE must be true")
    if truthy(env.get("ALPACA_LIVE_TRADE", "false")):
        raise LiveTradingBlocked("ALPACA_LIVE_TRADE not permitted")
    for var in REDIRECT_VARS:
        if var not in env:
            continue
        host = (urlsplit(str(env[var])).hostname or "").lower()
        if host and host not in ALLOWED_HOSTS:
            raise LiveTradingBlocked(f"{var} redirects to non-paper host {host!r}")
