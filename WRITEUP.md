# Nexus Agent — Hackathon Write-Up (One Page)

## AI Logic

Nexus is an autonomous **multi-signal fusion agent** that trades US equity **options** on Alpaca paper.

Each cycle:

1. **Ingest** — Pull stock bars, Alpaca news, and option chain (with Greeks) via MCP tools.
2. **Analyze** — Technical quant score (RSI, SMA trend, ATR), LLM/keyword news sentiment, and IV vs realized-vol regime.
3. **Debate** — A 3-agent committee (Bull Researcher, Bear Skeptic, Portfolio Manager) outputs `BUY`, `SELL`, `HOLD`, `VOL`, or `SELL_VOL` with confidence 0–100. Uses Gemini/OpenAI when configured; deterministic rules otherwise (AlphaSwarm pattern).
4. **Validate** — Momentum backtest gate (Odysseus-lite) and Monte Carlo VaR (AlphaSwarm RiskSentinel) on each candidate.
5. **Strategy select** — Map consensus to **options structures** with live bid/ask mids:
   - Bullish + high confidence → **Bull call spread**
   - Bearish → **Bear put spread**
   - Cheap volatility → **Long strangle** (long gamma — Vega pattern)
   - Rich IV / neutral → **Iron condor** (premium harvest)
6. **Execute** — Submit orders through **Alpaca MCP Server** (`place_option_order`); multi-leg spreads fall back to **alpaca-py REST** if needed.

The agent scans a watchlist (default SPY, QQQ, AAPL, NVDA, MSFT) and trades the **single highest-scoring setup** per tick.

## Risk Gates

All orders pass fail-closed checks before submission:

| Gate | Rule |
|------|------|
| Paper only | Hostname allowlist; `ALPACA_PAPER=true`; MCP child env forces `ALPACA_PAPER_TRADE=true` |
| Arm switch | `NEXUS_ARMED=yes` required |
| Account binding | `NEXUS_EXPECTED_ACCOUNT` must match MCP account number |
| Market hours | No orders when clock reports closed |
| Confidence | Debate confidence ≥ 65% (58% for vol structures) |
| Backtest | Momentum walk-forward win rate must pass |
| VaR | 21-day Monte Carlo VaR ≤ 12% |
| Premium cap | ≤ 15% equity per structure; ≤ 55% total premium at risk |
| Structure cap | Max 4 concurrent option structures |
| Circuit breaker | Halt new trades if day P&L ≤ −12% |
| Liquidity | Wide spreads rejected (25% of mid — Vega pattern) |
| Macro veto | Block directional trades on extreme bearish news |

Max loss on debit structures is premium paid; spreads and condors are defined-risk by construction.

## Alpaca Infrastructure

| Component | Usage |
|-----------|--------|
| **MCP Server** | Primary — `uvx alpaca-mcp-server` via Python MCP SDK. Tools: account, clock, bars, news, option chain, `place_option_order`. Audit in `data/audit/mcp-audit.jsonl`. |
| **Trading API** | REST fallback via `alpaca-py` for `order_class=mleg` multi-leg orders |
| **Market Data** | Stock bars + indicative option snapshots/Greeks through MCP |
| **Paper account** | Dedicated competition account; $100,000 starting balance |

Dashboard: FastAPI on port 8080 with SSE live stream — equity, day P&L, debates, risk blocks, and orders logged to SQLite. Proof artifact via `tools/build_proof.py`.

---

*Nexus Agent — built for the Alpaca AI Trading Agents Hackathon. Paper trading only.*
