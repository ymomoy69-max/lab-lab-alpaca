#!/usr/bin/env python3
"""Run agent tick, capture performance snapshot, append to data/performance-log.jsonl."""
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
from agent.orchestrator import NexusOrchestrator

LOG_PATH = os.path.join(ROOT, "data", "performance-log.jsonl")


def _db_summary() -> dict:
    db = SETTINGS.db_path
    if not os.path.exists(db):
        return {"events": 0, "decisions": 0}
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    events = conn.execute("SELECT type, COUNT(*) AS n FROM events GROUP BY type").fetchall()
    decisions = conn.execute(
        "SELECT status, COUNT(*) AS n FROM decisions GROUP BY status"
    ).fetchall()
    recent = conn.execute(
        "SELECT type, message, symbol, ts FROM events ORDER BY id DESC LIMIT 15"
    ).fetchall()
    conn.close()
    return {
        "event_counts": {r["type"]: r["n"] for r in events},
        "decision_counts": {r["status"]: r["n"] for r in decisions},
        "recent_events": [dict(r) for r in recent],
    }


def main() -> int:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    captured: list[dict] = []

    def on_event(type_: str, message: str, symbol: str | None, payload: dict):
        captured.append(
            {
                "type": type_,
                "message": message,
                "symbol": symbol,
                "payload_keys": list((payload or {}).keys()),
            }
        )

    orch = NexusOrchestrator(on_event=on_event)
    print(f"Running Nexus tick (armed={SETTINGS.armed}, paper={SETTINGS.paper})...")
    try:
        orch.run_once()
    except Exception as e:
        print(f"TICK ERROR: {e}")
        return 1

    snap = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "armed": SETTINGS.armed,
        "account": SETTINGS.expected_account,
        "feedback": orch.last_feedback,
        "events_this_run": captured,
        "db": _db_summary(),
    }

    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(snap, default=str) + "\n")

    print("\n=== RUN SUMMARY ===")
    for ev in captured:
        sym = f"[{ev['symbol']}] " if ev.get("symbol") else ""
        print(f"  {ev['type']:16} {sym}{ev['message'][:100]}")

    fb = orch.last_feedback or {}
    print(f"\nFeedback: {fb.get('summary', 'n/a')}")
    print(f"Log appended → {LOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
