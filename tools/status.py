#!/usr/bin/env python3
"""Print latest performance snapshot from data/performance-log.jsonl."""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "data", "performance-log.jsonl")


def main() -> int:
    if not os.path.exists(LOG):
        print("No performance log yet — run: python tools/performance.py")
        return 1

    with open(LOG) as f:
        lines = [ln for ln in f if ln.strip()]
    snap = json.loads(lines[-1])

    print(f"=== NEXUS PERFORMANCE ({snap.get('ts', '?')}) ===")
    print(f"Account : {snap.get('account')}  armed={snap.get('armed')}")
    fb = snap.get("feedback") or {}
    print(f"Feedback: {fb.get('summary', 'n/a')}")

    analyses = [e for e in snap.get("events_this_run", []) if e.get("type") == "analysis"]
    if analyses:
        print("\nWould trade:")
        for a in analyses:
            print(f"  · {a.get('message')}")
    else:
        print("\nWould trade: (none — below confidence or no chain)")

    debates = [e for e in snap.get("events_this_run", []) if e.get("type") == "debate"]
    print(f"\nDebates ({len(debates)}):")
    for d in debates:
        sym = d.get("symbol") or "?"
        print(f"  · {sym}: {d.get('message')}")

    db = snap.get("db") or {}
    if db.get("decision_counts"):
        print(f"\nDecisions: {db['decision_counts']}")
    print(f"\nFull log: {LOG} ({len(lines)} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
