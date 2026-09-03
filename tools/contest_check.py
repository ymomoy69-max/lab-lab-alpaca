#!/usr/bin/env python3
"""Hackathon contest readiness gate — flat book, correct account, options level."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from agent.config import SETTINGS
from agent.mcp_client import AlpacaMCP, parse_account_number, _dig, parse_positions, parse_orders


def _positions_list(raw) -> list:
    return parse_positions(raw)


def _orders_list(raw) -> list:
    return parse_orders(raw)


def main() -> int:
    fails: list[str] = []
    if not SETTINGS.expected_account:
        fails.append("NEXUS_EXPECTED_ACCOUNT unset")

    with AlpacaMCP() as mcp:
        acct = mcp.account()
        num = parse_account_number(acct)
        equity = float(_dig(acct, "portfolio_value") or _dig(acct, "equity") or 0)
        level = int(_dig(acct, "options_trading_level") or _dig(acct, "options_approved_level") or 0)
        blocked = bool(_dig(acct, "trading_blocked"))
        status = str(_dig(acct, "status") or "")

        print(f"  account   : {num}")
        print(f"  equity    : ${equity:,.2f} (required ${SETTINGS.required_equity:,.0f})")
        print(f"  status    : {status} · options L{level} · blocked={blocked}")

        if SETTINGS.expected_account and SETTINGS.expected_account not in num:
            fails.append(f"account {num} != {SETTINGS.expected_account}")
        if not num.startswith("PA"):
            fails.append("not a PA paper account")
        if status and status != "ACTIVE":
            fails.append(f"status {status} != ACTIVE")
        if blocked:
            fails.append("trading_blocked=true")
        if level < 3:
            fails.append(f"options level {level} < 3 (need strangles/spreads)")
        if abs(equity - SETTINGS.required_equity) > 500:
            fails.append(f"equity ${equity:,.0f} not ~${SETTINGS.required_equity:,.0f}")

        pos = _positions_list(mcp.positions())
        print(f"  positions : {len(pos)} ({'FLAT' if len(pos) == 0 else 'NOT FLAT'})")
        if pos:
            fails.append(f"{len(pos)} open position(s) — contest expects flat start")

        working = _orders_list(mcp.orders(status="open", limit=50))
        print(f"  open ord  : {len(working)}")
        if working:
            fails.append(f"{len(working)} working orders")

        hist = _orders_list(mcp.orders(status="all", limit=200))
        filled = sum(float(_dig(o, "filled_qty") or 0) for o in hist if isinstance(o, dict))
        print(f"  filled qty: {filled:g} (history {len(hist)} orders)")
        if filled > 0:
            fails.append(f"filled_qty total {filled:g} > 0 — book not pristine")

    print()
    if fails:
        print("CONTEST CHECK FAILED:")
        for f in fails:
            print(f"  · {f}")
        return 1

    print("CONTEST CHECK OK — account ready for hackathon trading")
    print("(Post-contest: keep trading with NEXUS_CONTEST_ENFORCE=no — default)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
