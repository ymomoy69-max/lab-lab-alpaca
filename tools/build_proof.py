#!/usr/bin/env python3
"""Build live-trading-proof.json for hackathon submission — NewsFlow pattern."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from agent.config import SETTINGS
from agent.mcp_client import AlpacaMCP, parse_account_number, parse_orders, parse_positions
from agent.account import account_metrics


def _tail_jsonl(path: str, n: int = 25) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = [ln for ln in f if ln.strip()]
    out = []
    for ln in lines[-n:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def _latest_performance() -> dict | None:
    log = os.path.join(ROOT, "data", "performance-log.jsonl")
    if not os.path.exists(log):
        return None
    with open(log) as f:
        lines = [ln for ln in f if ln.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


def main() -> int:
    db = SETTINGS.db_path
    decisions = []
    outcomes = []
    if os.path.exists(db):
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        decisions = [dict(r) for r in conn.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 50")]
        try:
            outcomes = [dict(r) for r in conn.execute("SELECT * FROM trade_outcomes ORDER BY id DESC LIMIT 50")]
        except sqlite3.OperationalError:
            outcomes = []
        conn.close()

    audit_path = os.path.join(ROOT, SETTINGS.audit_dir, "mcp-audit.jsonl")

    with AlpacaMCP() as mcp:
        acct = mcp.account()
        metrics = account_metrics(acct)
        positions = mcp.positions()
        orders_raw = mcp.orders(status="all", limit=50)

    pos_list = parse_positions(positions)
    order_list = parse_orders(orders_raw)
    perf = _latest_performance()
    filled = [o for o in order_list if float(o.get("filled_qty") or 0) > 0]

    proof = {
        "schema": "nexus-agent-v1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "hackathon": "Alpaca AI Trading Agents Hackathon — Aug 28 to Sep 4, 2026",
        "agent": {
            "name": "Nexus Agent",
            "mode": "paper-live" if SETTINGS.armed else "dry-run",
            "execution": "Alpaca MCP Server + alpaca-py REST fallback",
            "options": True,
            "risk": f"premium≤{SETTINGS.max_premium_pct}%, total≤{SETTINGS.max_total_premium_pct}%, VaR≤{SETTINGS.max_var_pct}%",
        },
        "account": {
            "account_number": parse_account_number(acct),
            **metrics,
        },
        "positions": pos_list,
        "orders": order_list,
        "filled_orders": filled,
        "decisions": decisions,
        "trade_outcomes": outcomes,
        "latest_performance_run": perf,
        "mcp_audit_tail": _tail_jsonl(audit_path),
        "mcp_audit_path": SETTINGS.audit_dir + "/mcp-audit.jsonl",
    }

    out = os.path.join(ROOT, SETTINGS.proof_path)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(proof, f, indent=2, default=str)

    print(f"Proof written to {out}")
    print(f"  equity: ${metrics['equity']:,.2f}")
    print(f"  daily P&L: ${metrics['daily_pnl']:+,.2f} ({metrics['daily_pnl_pct']:+.2f}%)")
    print(f"  positions: {len(pos_list)} · orders: {len(order_list)} · filled: {len(filled)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
