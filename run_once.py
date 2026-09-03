#!/usr/bin/env python3
"""CLI entry — run one tick without dashboard."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"), override=True)

from agent.orchestrator import NexusOrchestrator


def main():
    orch = NexusOrchestrator(on_event=lambda t, m, s, p: print(f"[{t}] {s or ''} {m}"))
    orch.run_once()


if __name__ == "__main__":
    main()
