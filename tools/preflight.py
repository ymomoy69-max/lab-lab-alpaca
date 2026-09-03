#!/usr/bin/env python3
"""Pre-flight checks before arming the agent."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from agent.config import SETTINGS
from agent.mcp_client import AlpacaMCP, market_open, parse_account_number
from agent.safety import assert_paper_env


def main() -> int:
    errors = []
    try:
        assert_paper_env(dict(os.environ))
    except Exception as e:
        errors.append(str(e))

    if not os.getenv("APCA_API_KEY_ID") or not os.getenv("APCA_API_SECRET_KEY"):
        errors.append("Missing APCA_API_KEY_ID / APCA_API_SECRET_KEY")

    if SETTINGS.armed and not SETTINGS.expected_account:
        errors.append("NEXUS_ARMED=yes but NEXUS_EXPECTED_ACCOUNT unset")

    try:
        with AlpacaMCP() as mcp:
            acct = mcp.account()
            clock = mcp.clock()
            num = parse_account_number(acct)
            print(f"Account: {num}")
            print(f"Market open: {market_open(clock)}")
            if SETTINGS.expected_account and SETTINGS.expected_account not in num:
                errors.append(f"Account mismatch: got {num or '(empty)'}")
    except Exception as e:
        errors.append(f"MCP connect failed: {e}")

    if errors:
        print("PREFLIGHT FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("PREFLIGHT OK — ready to arm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
