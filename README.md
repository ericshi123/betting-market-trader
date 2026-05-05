# Polymarket Intelligence

An ML-powered edge detection and paper trading system for Polymarket prediction markets.

## What it does
1. Fetches live markets from Polymarket's Gamma + CLOB APIs
2. Uses Claude to estimate true resolution probabilities
3. Compares model estimates to market prices to surface edges
4. Recommends paper trades sized by Kelly criterion

---

## Project Status

### ✅ Phase 1 — Data Foundation (done)
- `src/client.py` — Polymarket CLOB client wrapper (unauth + auth modes)
- `src/markets.py` — Fetch active markets from `/events` API, flatten + filter
- `src/storage.py` — Snapshot markets to `data/snapshots/YYYY-MM-DD.json`
- `src/cli.py` — `list`, `show`, `snapshot` commands

**Key API findings:**
- Use `/events` not `/markets` for volume-sorted results (markets endpoint returns tiny esports markets)
- Markets are nested inside events — need to flatten and re-sort
- `outcomes` and `outcomePrices` are JSON strings — need `json.loads()`
- CLOB order book uses `token_id` not `conditionId` — resolve via `/markets/{conditionId}` first

### ✅ Phase 2 — Edge Detection (done)
- `src/analyzer.py` — Claude-based probability estimator (claude-sonnet-4-5, max_tokens=400, prompt caching)
- `src/edge.py` — `calculate_edge()` + `rank_markets()` with confidence filtering
- `src/cli.py` — `scan` and `edges` commands

**Calibration notes:**
- LLMs default to extreme values (near 0 or near 1) on uncertain markets — added calibration instruction to system prompt
- Calibration correctly collapses noisy near-zero markets to market price while preserving signal where Claude has a concrete reason to diverge
- Current known bias: sports markets need real-time context injection (standings, form) — Claude's base rates alone are insufficient
- Best signal type so far: geopolitical/narrative markets (e.g. US-Iran deal) where base rate reasoning beats crowd narrative

**Useful scan flags:**
```bash
python -m src.cli scan --limit 10 --min-volume 50000 --min-yes-price 0.05 --max-yes-price 0.95 --days 60
python -m src.cli edges --min-edge 0.05 --confidence medium
```

### ✅ Phase 3 — Paper Trading (done)
- `src/betting.py` — Kelly criterion position sizing (quarter-Kelly, capped at $50/position)
  - `kelly_fraction(edge, model_prob, market_prob)`
  - `recommend_bet(market, analyzed, bankroll)` — min 8pp edge + medium/high confidence
- `src/portfolio.py` — Paper ledger in `data/portfolio.json`
  - `open_position()`, `close_position()`, `portfolio_summary()`
- `src/cli.py` additions:
  - `recommend --bankroll 1000 --min-edge 0.08`
  - `paper-bet <market_id> --direction BUY_YES --amount 25`
  - `portfolio`
  - `resolve <position_id> --outcome YES --price 0.95`

### ✅ Phase 4 — Automation + JoJo Integration (in progress)
- `scripts/daily_scan.py` — runs scan + recommend pipeline, saves structured report to `data/reports/YYYY-MM-DD.json`
- OpenClaw cron job triggers daily scan and delivers top edges to Xintong via Telegram
- Approval flow: Xintong replies to approve → JoJo logs paper trade automatically
- P&L dashboard on personal website (xintongshi.dev) — Phase 4b, separate task

### ✅ Phase 5 — Live Trading (built, gated on paper trading validation)
- `src/executor.py` — limit order placement via CLOB API (`place_order`, `cancel_order`, `cancel_all_orders`, `get_usdc_balance`)
- `src/safety.py` — kill switch, daily loss limit ($200 default), max position size ($100 default)
  - Custom exceptions: `KillSwitchError`, `DailyLossLimitError`, `PositionSizeError`
  - Kill switch auto-activates if daily loss limit is breached
  - State persisted in `data/live_state.json`
- `src/live_portfolio.py` — live trade ledger in `data/live_portfolio.json` (mirrors paper portfolio, adds `order_id` field)
- `src/cli.py` additions:
  - `live-bet <market_id> --direction BUY_YES|BUY_NO --amount N` — dry-run preview
  - `live-bet <market_id> --direction BUY_YES|BUY_NO --amount N --confirm` — place real order
  - `live-portfolio` — show live positions and P&L
  - `live-resolve <position_id> --outcome YES|NO --exit-price N` — close position, record P&L
  - `kill-switch [--activate [--reason "text"] | --deactivate]` — emergency stop
  - `live-status` — kill switch state, daily P&L, USDC balance

---

## Setup

```bash
cd ~/projects/polymarket-intel
python -m venv .venv
source .venv/bin/activate   # or .venv/bin/activate.fish
pip install -r requirements.txt
cp .env.example .env        # add ANTHROPIC_API_KEY
```

## Usage

```bash
# Browse markets
python -m src.cli list --min-volume 50000 --days 60
python -m src.cli show <market_id>

# Run edge scan
python -m src.cli scan --limit 10 --min-volume 50000 --min-yes-price 0.05 --max-yes-price 0.95 --days 60
python -m src.cli edges --min-edge 0.05 --confidence medium

# Paper trading (Phase 3 — not yet built)
python -m src.cli recommend --bankroll 1000 --min-edge 0.08
python -m src.cli paper-bet <market_id> --direction BUY_YES --amount 25
python -m src.cli portfolio
python -m src.cli resolve <position_id> --outcome YES --price 0.95
```

## Going Live (Phase 5)

Before placing real orders:

1. **Set credentials** — copy `.env.example` → `.env` and fill in all `POLY_` values
   (API key from polymarket.com account settings, private key from your Polygon wallet)

2. **Verify connection**
   ```bash
   python -m src.cli live-status
   ```
   Should show your USDC balance. If it shows "credentials not configured", check your `.env`.

3. **Dry-run a bet** — always do this first
   ```bash
   python -m src.cli live-bet <market_id> --direction BUY_YES --amount 25
   ```
   Shows direction, estimated price, and all safety check results. No order is placed.

4. **Place the real order** — add `--confirm`
   ```bash
   python -m src.cli live-bet <market_id> --direction BUY_YES --amount 25 --confirm
   ```
   Runs all safety checks, places a GTC limit order, and logs to `data/live_portfolio.json`.

5. **Emergency stop** — the kill switch cancels all open CLOB orders immediately
   ```bash
   python -m src.cli kill-switch --activate --reason "manual stop"
   python -m src.cli kill-switch --deactivate   # re-enable when ready
   ```

6. **Resolve a position** when the market settles
   ```bash
   python -m src.cli live-resolve <position_id> --outcome YES --exit-price 0.97
   ```
   Records P&L and updates the daily loss tracker. Kill switch auto-fires if the daily limit is breached.

### Phase 5 CLI reference

```bash
# Status
python -m src.cli live-status

# Betting
python -m src.cli live-bet <market_id> --direction BUY_YES --amount 25
python -m src.cli live-bet <market_id> --direction BUY_NO  --amount 25 --confirm

# Portfolio
python -m src.cli live-portfolio
python -m src.cli live-resolve <position_id> --outcome YES --exit-price 0.95

# Kill switch
python -m src.cli kill-switch                              # show status
python -m src.cli kill-switch --activate --reason "manual stop"
python -m src.cli kill-switch --deactivate
```

### Safety defaults (edit `data/live_state.json` to change)
| Setting | Default | Description |
|---|---|---|
| `daily_loss_limit` | $200 | Max daily loss before kill switch auto-fires |
| `max_position_size` | $100 | Max USDC per single bet |

---

## Architecture

```
src/
├── client.py           # Polymarket API client (auth + unauth modes)
├── markets.py          # Market fetching + filtering
├── analyzer.py         # Claude probability estimator
├── edge.py             # Edge scoring + ranking
├── storage.py          # Snapshots + analysis persistence
├── betting.py          # Kelly criterion sizing
├── portfolio.py        # Paper trading ledger
├── executor.py         # Live order placement (Phase 5)
├── live_portfolio.py   # Live trade ledger (Phase 5)
├── safety.py           # Kill switch + safety rails (Phase 5)
└── cli.py              # CLI entry point

data/               # gitignored
├── snapshots/          # YYYY-MM-DD.json market snapshots
├── analysis/           # YYYY-MM-DD-HH.json Claude analysis results
├── portfolio.json      # Paper trading ledger
├── live_portfolio.json # Live trading ledger (Phase 5)
└── live_state.json     # Kill switch + daily P&L state (Phase 5)
```

## Risk notes
- Paper trading and live trading are independent — paper trades are unaffected by live activity
- Quarter-Kelly sizing is intentionally conservative (max $50/position in paper mode)
- Live mode hard limits: $100/position, $200/day loss, kill switch on breach
- Sports markets require external context (standings, injury news) — treat those signals with extra skepticism
- Always dry-run (`live-bet` without `--confirm`) before placing real orders
- Always verify high-edge signals manually before acting
