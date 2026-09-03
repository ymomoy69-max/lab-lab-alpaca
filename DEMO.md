# Demo Script (2–3 min video)

1. **Contest check** — `python tools/contest_check.py`  
   Show account `PA341MN815AD`, flat book, options level 3.

2. **Preflight** — `python tools/preflight.py`  
   MCP connected, paper mode.

3. **Analysis (any time)** — `python tools/analyze.py`  
   Show historical regime + debate per symbol.

4. **Dashboard** — `python run.py` → http://localhost:8080  
   Run Once → watch debate, feedback, history events in SSE log.

5. **Live tick (market hours, armed)** — set `NEXUS_ARMED=yes`, `python run_once.py`  
   Show order_submitted → order_filled with verified status.

6. **Proof artifact** — `python tools/build_proof.py`  
   Open `data/live-trading-proof.json` — account, decisions, MCP audit path.

## Talking points for judges

- **MCP-primary** execution with REST fallback; audit in `data/audit/mcp-audit.jsonl`
- **Options structures**: bull/bear spreads, strangles, iron condors
- **AI pipeline**: multi-agent debate + 200-day history + multi-headline news
- **Risk**: VaR, combined backtest, Greeks, correlation, session filters, circuit breaker
- **Lifecycle**: take-profit / stop-loss / DTE / contest-close exits via MCP `close_position`
- **Feedback loop**: learns from realized outcomes stored in SQLite
