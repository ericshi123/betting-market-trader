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

### 🔲 Phase 4 — Automation + JoJo Integration (planned)
- Daily automated scan → JoJo surfaces top edges to Xintong via Telegram
- Xintong approves → execution logged
- P&L dashboard on personal website (xintongshi.dev)

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

## Architecture

```
src/
├── client.py       # Polymarket API client
├── markets.py      # Market fetching + filtering
├── analyzer.py     # Claude probability estimator
├── edge.py         # Edge scoring + ranking
├── storage.py      # Snapshots + analysis persistence
└── cli.py          # CLI entry point

data/               # gitignored
├── snapshots/      # YYYY-MM-DD.json market snapshots
└── analysis/       # YYYY-MM-DD-HH.json Claude analysis results
```

## Risk notes
- This is a paper trading system — no real money is moved automatically
- Quarter-Kelly sizing is intentionally conservative
- Sports markets require external context (standings, injury news) — treat those signals with extra skepticism
- Always verify high-edge signals manually before acting
