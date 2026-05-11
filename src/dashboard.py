"""
Lightweight Flask dashboard for the ws_trader daemon.

Accessible at http://localhost:8080 (mobile-responsive, Tailscale-accessible).

Endpoints:
  GET  /            — HTML dashboard
  GET  /api/status  — JSON portfolio snapshot + system status
  GET  /api/positions — JSON open positions list
  POST /pause       — set TRADING_PAUSED = True
  POST /resume      — set TRADING_PAUSED = False
"""

import logging
import threading
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from flask import Flask, jsonify, render_template_string, request

from src.momentum_portfolio import load_portfolio, portfolio_summary

logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── Shared state (set by ws_trader.py before starting dashboard thread) ───────
_state: Dict = {
    "trading_paused": False,
    "last_heartbeat": None,
    "daemon_start": None,
    "signal_feed": [],       # list of last 20 signals
    "set_paused_fn": None,   # Callable[[bool], None]
    "get_paused_fn": None,   # Callable[[], bool]
}
_state_lock = threading.Lock()


def configure(
    set_paused: Callable[[bool], None],
    get_paused: Callable[[], bool],
) -> None:
    """Wire in callbacks from ws_trader so dashboard can read/write pause state."""
    with _state_lock:
        _state["set_paused_fn"] = set_paused
        _state["get_paused_fn"] = get_paused
        _state["daemon_start"] = datetime.now(timezone.utc).isoformat()


def record_heartbeat() -> None:
    """Called periodically from the daemon to indicate it's still alive."""
    with _state_lock:
        _state["last_heartbeat"] = datetime.now(timezone.utc).isoformat()


def record_signal(signal: dict) -> None:
    """Append a signal to the rolling feed (max 20 entries)."""
    with _state_lock:
        _state["signal_feed"].append(signal)
        if len(_state["signal_feed"]) > 20:
            _state["signal_feed"] = _state["signal_feed"][-20:]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_paused() -> bool:
    fn = _state.get("get_paused_fn")
    if fn:
        return fn()
    return _state.get("trading_paused", False)


def _set_paused(value: bool) -> None:
    fn = _state.get("set_paused_fn")
    if fn:
        fn(value)
    else:
        with _state_lock:
            _state["trading_paused"] = value


def _get_positions() -> List[dict]:
    try:
        portfolio = load_portfolio()
        return [p for p in portfolio.get("positions", []) if p.get("status") == "open"]
    except Exception:
        return []


def _get_pnl_by_strategy(portfolio: dict) -> Dict[str, float]:
    pnl: Dict[str, float] = {}
    for pos in portfolio.get("positions", []):
        strat = pos.get("strategy", "unknown")
        if pos.get("pnl") is not None:
            pnl[strat] = round(pnl.get(strat, 0.0) + pos["pnl"], 2)
    return pnl


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    try:
        portfolio = load_portfolio()
        summary = portfolio_summary(portfolio)
    except Exception as exc:
        portfolio = {}
        summary = f"Error loading portfolio: {exc}"

    return jsonify({
        "trading_paused": _is_paused(),
        "daemon_start": _state.get("daemon_start"),
        "last_heartbeat": _state.get("last_heartbeat"),
        "portfolio_summary": summary,
        "bankroll": portfolio.get("bankroll"),
        "total_trades": portfolio.get("total_trades"),
        "wins": portfolio.get("wins"),
        "losses": portfolio.get("losses"),
        "pnl_by_strategy": _get_pnl_by_strategy(portfolio),
    })


@app.route("/api/positions")
def api_positions():
    return jsonify({"positions": _get_positions()})


@app.route("/pause", methods=["POST"])
def pause():
    _set_paused(True)
    logger.info("Dashboard: trading PAUSED")
    return jsonify({"status": "paused"})


@app.route("/resume", methods=["POST"])
def resume():
    _set_paused(False)
    logger.info("Dashboard: trading RESUMED")
    return jsonify({"status": "active"})


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WS Trader Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0f1117; color: #e2e8f0; font-size: 14px; }
  h1 { font-size: 1.4rem; font-weight: 700; }
  h2 { font-size: 1rem; font-weight: 600; color: #94a3b8; margin-bottom: 0.5rem; }
  .header { background: #1e293b; padding: 1rem 1.5rem;
            display: flex; align-items: center; justify-content: space-between; }
  .badge { display: inline-block; padding: 0.25rem 0.75rem; border-radius: 999px;
           font-size: 0.75rem; font-weight: 700; }
  .badge-active { background: #166534; color: #4ade80; }
  .badge-paused { background: #7f1d1d; color: #f87171; }
  .container { padding: 1rem 1.5rem; max-width: 960px; margin: 0 auto; }
  .card { background: #1e293b; border-radius: 8px; padding: 1rem;
          margin-bottom: 1rem; }
  table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
  th { text-align: left; color: #64748b; font-weight: 600;
       padding: 0.4rem 0.5rem; border-bottom: 1px solid #334155; }
  td { padding: 0.4rem 0.5rem; border-bottom: 1px solid #1e293b; }
  tr:last-child td { border-bottom: none; }
  .positive { color: #4ade80; }
  .negative { color: #f87171; }
  .neutral { color: #94a3b8; }
  .btn { display: inline-block; padding: 0.75rem 1.5rem; border: none;
         border-radius: 6px; font-size: 0.9rem; font-weight: 600;
         cursor: pointer; min-height: 44px; }
  .btn-pause { background: #dc2626; color: white; }
  .btn-resume { background: #16a34a; color: white; }
  .btn:hover { opacity: 0.85; }
  .controls { display: flex; gap: 0.75rem; flex-wrap: wrap; }
  .meta { font-size: 0.7rem; color: #64748b; margin-top: 0.25rem; }
  .signal-action-trade { color: #4ade80; }
  .signal-action-skip { color: #64748b; }
  .signal-action-error { color: #f87171; }
  @media (max-width: 600px) {
    .header { flex-direction: column; gap: 0.5rem; align-items: flex-start; }
    table { font-size: 0.7rem; }
  }
</style>
</head>
<body>

<div class="header">
  <h1>WS Trader</h1>
  <div>
    <span id="status-badge" class="badge">Loading...</span>
  </div>
</div>

<div class="container">

  <div class="card">
    <h2>System Status</h2>
    <div id="system-status" class="meta">Loading...</div>
    <br>
    <div class="controls">
      <button class="btn btn-pause" onclick="postAction('/pause')">Pause Trading</button>
      <button class="btn btn-resume" onclick="postAction('/resume')">Resume Trading</button>
    </div>
  </div>

  <div class="card">
    <h2>Open Positions</h2>
    <div id="positions-table"><em class="neutral">Loading...</em></div>
  </div>

  <div class="card">
    <h2>P&amp;L by Strategy</h2>
    <div id="pnl-table"><em class="neutral">Loading...</em></div>
  </div>

  <div class="card">
    <h2>Recent Signal Feed (last 20)</h2>
    <div id="signal-feed"><em class="neutral">Loading...</em></div>
  </div>

</div>

<script>
function postAction(url) {
  fetch(url, {method: 'POST'})
    .then(() => refreshAll())
    .catch(e => alert('Error: ' + e));
}

function pct(v) {
  if (v == null) return '—';
  return (v * 100).toFixed(1) + '%';
}
function money(v) {
  if (v == null) return '—';
  return '$' + v.toFixed(2);
}
function colorClass(v) {
  if (v == null) return '';
  return v > 0 ? 'positive' : v < 0 ? 'negative' : 'neutral';
}

function refreshAll() {
  fetch('/api/status').then(r => r.json()).then(d => {
    const paused = d.trading_paused;
    const badge = document.getElementById('status-badge');
    badge.textContent = paused ? 'PAUSED' : 'ACTIVE';
    badge.className = 'badge ' + (paused ? 'badge-paused' : 'badge-active');

    const meta = [
      'Bankroll: ' + money(d.bankroll),
      'Trades: ' + (d.total_trades || 0),
      'W/L: ' + (d.wins||0) + '/' + (d.losses||0),
      'Started: ' + (d.daemon_start ? d.daemon_start.slice(0,19).replace('T',' ') : '—'),
      'Heartbeat: ' + (d.last_heartbeat ? d.last_heartbeat.slice(0,19).replace('T',' ') : '—'),
    ].join(' | ');
    document.getElementById('system-status').textContent = meta;

    const pnlEl = document.getElementById('pnl-table');
    const pnl = d.pnl_by_strategy || {};
    const keys = Object.keys(pnl);
    if (!keys.length) {
      pnlEl.innerHTML = '<em class="neutral">No closed P&L yet</em>';
    } else {
      let html = '<table><tr><th>Strategy</th><th>Realized P&L</th></tr>';
      keys.forEach(k => {
        const v = pnl[k];
        html += '<tr><td>' + k + '</td><td class="' + colorClass(v) + '">' + money(v) + '</td></tr>';
      });
      pnlEl.innerHTML = html + '</table>';
    }
  });

  fetch('/api/positions').then(r => r.json()).then(d => {
    const el = document.getElementById('positions-table');
    const pos = d.positions || [];
    if (!pos.length) {
      el.innerHTML = '<em class="neutral">No open positions</em>';
      return;
    }
    let html = '<table><tr><th>ID</th><th>Ticker</th><th>Dir</th><th>$</th><th>Entry</th><th>Strat</th></tr>';
    pos.forEach(p => {
      html += '<tr>'
        + '<td>' + (p.id||'').slice(0,8) + '</td>'
        + '<td>' + (p.ticker||p.market_id||'') + '</td>'
        + '<td>' + (p.direction||'') + '</td>'
        + '<td>' + money(p.amount) + '</td>'
        + '<td>' + pct(p.entry_price) + '</td>'
        + '<td>' + (p.strategy||'—') + '</td>'
        + '</tr>';
    });
    el.innerHTML = html + '</table>';
  });
}

// Signal feed rendered from server-side injection
const signals = {{ signals | tojson }};
(function() {
  const el = document.getElementById('signal-feed');
  if (!signals || !signals.length) {
    el.innerHTML = '<em class="neutral">No signals yet</em>';
    return;
  }
  let html = '<table><tr><th>Time</th><th>Ticker</th><th>Action</th><th>Reason</th></tr>';
  [...signals].reverse().forEach(s => {
    const cls = 'signal-action-' + (s.action||'skip');
    const ts = s.timestamp ? new Date(s.timestamp*1000).toISOString().slice(11,19) : '—';
    html += '<tr>'
      + '<td>' + ts + '</td>'
      + '<td>' + (s.ticker||'') + '</td>'
      + '<td class="' + cls + '">' + (s.action||'') + '</td>'
      + '<td>' + (s.reason||s.direction||'') + '</td>'
      + '</tr>';
  });
  el.innerHTML = html + '</table>';
})();

refreshAll();
setInterval(refreshAll, 10000);
</script>
</body>
</html>"""


@app.route("/")
def index():
    with _state_lock:
        signals = list(_state.get("signal_feed", []))
    return render_template_string(_DASHBOARD_HTML, signals=signals)


# ── Launcher ──────────────────────────────────────────────────────────────────

def start_dashboard(host: str = "0.0.0.0", port: int = 8080) -> threading.Thread:
    """Start the Flask dashboard in a daemon thread. Returns the thread."""
    import os
    os.environ.setdefault("WERKZEUG_RUN_MAIN", "true")

    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        daemon=True,
        name="dashboard",
    )
    thread.start()
    logger.info("Dashboard started at http://%s:%d", host, port)
    return thread
