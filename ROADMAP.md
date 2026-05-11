# Betting Market Trader — Engineering Roadmap

**Goal:** Build the ultimate autonomous Kalshi trading agent: real-time, intelligent, safe, self-improving.

---

## Architecture Overview

```
WebSocket Feed (Kalshi) → Event Filter → LLM Evaluator (Claude) → Trade Executor
                                                                        ↓
                                               Paper Portfolio / Live Portfolio
                                                                        ↓
                                               Telegram Alerts / Dashboard
```

---

## Phase 6 — WebSocket + LLM Hybrid (Real-Time Trading) 🔨 NEXT

**Goal:** Replace polling with a persistent event-driven daemon. React to price moves in real-time, use Claude to evaluate before trading.

### Components

#### `src/ws_client.py` — Kalshi WebSocket Client
- Connect to `wss://api.elections.kalshi.com/trade-api/ws/v2`
- Auth: same RSA-PSS-SHA256 as REST (timestamp_ms + METHOD + path)
- Subscribe to `ticker` channel for all active markets
- Handle reconnects with exponential backoff (max 60s)
- Emit normalized price update events: `{ticker, yes_price, prev_yes_price, delta, timestamp}`
- Heartbeat/ping handling to keep connection alive

#### `src/ws_handler.py` — Event Handler + Trade Logic
- Filter: only process events where `abs(delta) >= MIN_TRIGGER_PP` (default 3pp)
- Dedupe: skip if same market evaluated in last 30 min
- Call `analyzer.estimate_probability(market)` for qualifying events
- If edge confirmed (≥8pp, confidence medium/high): call `momentum_portfolio.open_position()`
- Log all decisions (trade or skip + reason)

#### `scripts/ws_trader.py` — Daemon Entry Point
- Main event loop
- Graceful shutdown on SIGTERM/SIGINT
- Writes PID to `data/ws_trader.pid`
- Logs to `logs/ws_trader.log` (rotating, max 10MB)
- Sends Telegram ping on start, stop, each trade placed, and errors
- Daily summary at midnight: positions opened, P&L delta, signal count

#### `launchd/ai.openclaw.ws-trader.plist` — Auto-Start
- LaunchDaemon plist to keep daemon running on Mac mini
- Auto-restart on crash (KeepAlive: true)
- Load with: `launchctl load ~/Library/LaunchAgents/ai.openclaw.ws-trader.plist`

### Tests
- `tests/test_ws_client.py` — mock WS server, verify connect/reconnect/auth
- `tests/test_ws_handler.py` — mock price events, verify filter/dedupe/trade logic
- Integration: dry-run daemon for 60s against live Kalshi WS, verify events received

### Success Criteria
- Daemon starts, connects to Kalshi WS, receives live price events
- 3pp+ moves trigger Claude evaluation
- Paper trades placed in `data/momentum_portfolio.json`
- Telegram pinged on each trade
- Daemon survives a simulated disconnect and reconnects

---

## Phase 7 — Web Search Augmentation

**Goal:** Give Claude real-world context before evaluating markets. Biggest signal quality improvement.

### Components
- `src/enricher.py` — before `analyzer.estimate_probability()`, call `web_search(market.question)`, append top 3 results to prompt
- Cache search results per market for 1 hour (avoid duplicate searches)
- Flag markets where search found breaking news (priority evaluation)

### Tests
- Mock search results, verify enriched prompt format
- Compare Claude confidence scores with/without enrichment on same markets

---

## Phase 8 — Auto-Resolve Checker

**Goal:** Automatically close paper positions when Kalshi markets settle. Accurate P&L without manual input.

### Components
- `src/resolver.py` — poll `/markets/{ticker}` for `status == "finalized"`, read `result`
- Run every 15 min as a lightweight cron
- Close matching positions in both `portfolio.json` and `momentum_portfolio.json`
- Send Telegram: "Market X resolved YES — position closed, P&L: +$12.50"

### Tests
- Mock finalized market response, verify position closed correctly

---

## Phase 9 — News-Triggered Trading

**Goal:** Monitor news feeds. When relevant news breaks, evaluate affected Kalshi markets before crowd prices update. Highest alpha potential.

### Components
- `src/news_monitor.py` — poll RSS feeds (Reuters, AP, Politico, ESPN) every 5 min
- NLP match: news headline → relevant Kalshi market tickers
- Trigger immediate Claude evaluation on matched markets
- Separate paper portfolio: `data/news_portfolio.json` ($1000 isolated)

### Feeds
- Politics: Reuters Politics, AP Politics, Politico
- Sports: ESPN, CBS Sports
- Economics: Bloomberg, WSJ Economy
- Crypto: CoinDesk, The Block

### Tests
- Mock RSS feed with known headline, verify correct market matched and evaluated

---

## Phase 10 — Calibration Tracker + Backtester

**Goal:** Measure how well Claude's estimates perform over time. Required before live trading.

### Components
- `src/calibration.py` — on position close, record `{model_prob, market_prob, outcome, edge, category}`
- `scripts/calibration_report.py` — Brier score, calibration curve, win rate by confidence level
- `scripts/backtest.py` — replay historical snapshots through current strategy, report hypothetical P&L

### Gate for Phase 5 (live trading)
- 30+ resolved positions
- Brier score < 0.20 (well-calibrated)
- Positive expected value across resolved trades

---

## Phase 11 — Dashboard + Telegram Commands

**Goal:** Full visibility and control without touching a terminal.

### Components
- `src/dashboard.py` — lightweight Flask/FastAPI app (localhost:8080)
  - Live positions, P&L by strategy, signal feed, kill switch button
- Telegram command handler (in ws_daemon):
  - `/status` — portfolio snapshot
  - `/close <id>` — manually close a position
  - `/pause` — halt all trading
  - `/resume` — re-enable trading
  - `/signal` — force a manual market scan

---

## Phase 12 — Correlation Arbitrage

**Goal:** Find related markets that should be correlated but have diverged. Bet the convergence.

### Components
- `src/correlation.py` — map market pairs (e.g. "Trump wins WH" ↔ "Republican wins WH")
- Detect divergence > 10pp, flag as arb opportunity
- Size position based on divergence magnitude

---

## Risk Management (applies to all phases)

- **Kill switch**: already built (Phase 5) — extend to cover all strategies
- **Per-strategy daily loss limit**: $100/strategy/day
- **Correlation cap**: max 3 open positions that share a common resolution event
- **Drawdown circuit breaker**: pause all trading if any portfolio drops >15% from peak
- **Max open positions per portfolio**: 10

---

## Current Status

| Phase | Status | Notes |
|---|---|---|
| 1 — Data Foundation | ✅ Done | Kalshi REST client, market fetching |
| 2 — Edge Detection | ✅ Done | Claude probability estimator |
| 3 — Paper Trading | ✅ Done | Kelly sizing, paper ledger |
| 4 — Automation | ✅ Done | Daily scan cron |
| 5 — Live Trading | ✅ Done | Executor, safety rails, kill switch |
| Momentum Trader | ✅ Done | Separate $1000 paper account, 4h cron |
| **6 — WebSocket Hybrid** | **🔨 In Progress** | |
| 7 — Web Search | ⏳ Planned | |
| 8 — Auto-Resolve | ⏳ Planned | |
| 9 — News Trading | ⏳ Planned | |
| 10 — Calibration | ⏳ Planned | |
| 11 — Dashboard | ⏳ Planned | |
| 12 — Correlation Arb | ⏳ Planned | |
