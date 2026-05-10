"""
Kalshi Intelligence CLI

Usage:
    python -m src.cli list                              # Top 20 active markets
    python -m src.cli list --min-volume 100             # Filter by volume (contracts)
    python -m src.cli list --days 7                     # Close within 7 days
    python -m src.cli show <ticker>                     # Market detail
    python -m src.cli snapshot                          # Fetch and save snapshot
    python -m src.cli scan --limit 20 --min-volume 10
    python -m src.cli edges --min-edge 0.05 --confidence medium

    # Live trading (Phase 5)
    python -m src.cli live-status
    python -m src.cli live-bet <ticker> --direction BUY_YES --amount 25
    python -m src.cli live-bet <ticker> --direction BUY_YES --amount 25 --confirm
    python -m src.cli live-portfolio
    python -m src.cli live-resolve <position_id> --outcome YES --exit-price 0.95
    python -m src.cli kill-switch --activate --reason "manual stop"
    python -m src.cli kill-switch --deactivate
    python -m src.cli kill-switch
"""

import argparse
import sys
import time
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich import box
from rich.text import Text

from src.markets import fetch_active_markets, filter_markets
from src.storage import save_snapshot, load_latest_snapshot, save_analysis, load_latest_analysis
from src.betting import recommend_bet
from src.portfolio import load_portfolio, open_position, close_position, portfolio_summary
from src.momentum_portfolio import (
    load_portfolio as load_momentum_portfolio,
    close_position as close_momentum_position,
    portfolio_summary as momentum_portfolio_summary,
)
from src.live_portfolio import (
    load_live_portfolio,
    save_live_portfolio,
    open_live_position,
    close_live_position,
    live_portfolio_summary,
)
from src.safety import (
    load_state,
    check_kill_switch,
    activate_kill_switch,
    deactivate_kill_switch,
    check_daily_loss_limit,
    validate_position_size,
    record_daily_pnl,
    KillSwitchError,
    DailyLossLimitError,
    PositionSizeError,
)

console = Console(width=160)


def _fmt_volume(v) -> str:
    if v is None:
        return "-"
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v/1_000:.0f}K"
    return f"{v:.0f}"


def _fmt_pct(v) -> str:
    if v is None:
        return "  -  "
    return f"{v*100:.1f}%"


def _days_to_close(end_date: str) -> str:
    if not end_date or end_date == "unknown":
        return "-"
    try:
        end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days = (end.date() - now.date()).days
        if days < 0:
            return "closed"
        if days == 0:
            return "today"
        return f"{days}d"
    except ValueError:
        return end_date


def cmd_list(args):
    with console.status("[bold cyan]Fetching active markets...[/]"):
        markets = fetch_active_markets(limit=100)

    markets = filter_markets(
        markets,
        min_volume=args.min_volume,
        max_days_to_close=args.days,
    )

    display = markets[: args.top]

    if not display:
        console.print("[yellow]No markets match the given filters.[/]")
        return

    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold magenta",
        title=f"[bold]Kalshi — Active Markets[/] ({len(display)} shown)",
        title_style="bold white",
    )
    table.add_column("#", style="dim", width=4, justify="right", no_wrap=True)
    table.add_column("Ticker", min_width=20, max_width=35, no_wrap=True)
    table.add_column("Question", min_width=35, max_width=55, no_wrap=True)
    table.add_column("Yes%", justify="right", min_width=7, no_wrap=True)
    table.add_column("No%", justify="right", min_width=7, no_wrap=True)
    table.add_column("Volume", justify="right", min_width=9, no_wrap=True)
    table.add_column("Closes", justify="right", min_width=8, no_wrap=True)

    for i, m in enumerate(display, 1):
        ticker = m["market_id"]
        if len(ticker) > 32:
            ticker = ticker[:29] + "..."
        q = m["question"]
        if len(q) > 52:
            q = q[:49] + "..."
        table.add_row(
            str(i),
            ticker,
            q,
            _fmt_pct(m.get("yes_price")),
            _fmt_pct(m.get("no_price")),
            _fmt_volume(m.get("volume")),
            _days_to_close(m.get("end_date", "")),
        )

    console.print(table)
    console.print(
        f"[dim]Source: Kalshi API · {len(markets)} total matched "
        f"({'filtered' if args.min_volume or args.days else 'no filters'})[/]"
    )


def cmd_show(args):
    ticker = args.market_id

    with console.status(f"[bold cyan]Fetching market {ticker}...[/]"):
        markets = fetch_active_markets(limit=200)

    match = next((m for m in markets if m["market_id"] == ticker), None)

    if match:
        console.print(
            Panel(
                f"[bold]{match['question']}[/]\n\n"
                f"  [cyan]Yes:[/] {_fmt_pct(match.get('yes_price'))}   "
                f"[red]No:[/] {_fmt_pct(match.get('no_price'))}\n"
                f"  Volume:    {_fmt_volume(match.get('volume'))} contracts\n"
                f"  Closes:    {match.get('end_date', 'unknown')}  ({_days_to_close(match.get('end_date', ''))})\n"
                f"  Ticker:    [dim]{ticker}[/]",
                title="[bold magenta]Market Detail[/]",
                expand=False,
            )
        )
    else:
        console.print(f"[yellow]Ticker {ticker!r} not found in active markets.[/]")


def cmd_snapshot(args):
    with console.status("[bold cyan]Fetching markets for snapshot...[/]"):
        markets = fetch_active_markets(limit=100)

    path = save_snapshot(markets)
    console.print(
        f"[bold green]Snapshot saved:[/] {path}\n"
        f"  {len(markets)} markets captured."
    )


def cmd_scan(args):
    from src.analyzer import estimate_probability
    from src.edge import rank_markets

    with console.status("[bold cyan]Fetching markets...[/]"):
        markets = fetch_active_markets(limit=max(200, args.limit * 10))

    markets = filter_markets(
        markets,
        min_volume=args.min_volume,
        max_days_to_close=args.days,
        min_yes_price=args.min_yes_price,
        max_yes_price=args.max_yes_price,
    )
    targets = markets[: args.limit]

    if not targets:
        console.print("[yellow]No markets match filters.[/]")
        return

    console.print(f"[bold]Scanning {len(targets)} markets with Claude...[/]\n")

    analyzed: list[dict] = []
    errors = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Analyzing", total=len(targets))

        for i, market in enumerate(targets):
            q = market["question"]
            label = q[:55] + "..." if len(q) > 55 else q
            progress.update(task, description=f"[cyan]{i+1}/{len(targets)}[/] {label}")

            result = estimate_probability(market)
            merged = {**market, **result}
            analyzed.append(merged)

            if result.get("error"):
                errors += 1
                console.print(f"  [red]✗[/] {label[:50]} — {result['rationale']}")
            else:
                mp = result.get("model_prob")
                yp = market.get("yes_price")
                edge_val = (mp - yp) if (mp is not None and yp is not None) else None
                edge_str = f"edge {edge_val:+.2f}" if edge_val is not None else "no edge"
                src = result.get("market_source", "other")
                console.print(
                    f"  [green]✓[/] [{src}] {label[:50]}\n"
                    f"    market={_fmt_pct(yp)} → model={_fmt_pct(mp)} ({edge_str}, {result['confidence']})"
                )

            progress.advance(task)
            if i < len(targets) - 1:
                time.sleep(1)

    path = save_analysis(analyzed)

    ranked = rank_markets(analyzed)
    top = ranked[:5]

    console.print(f"\n[bold green]Done.[/] {len(analyzed) - errors}/{len(analyzed)} succeeded · saved → {path}")

    if top:
        console.print("\n[bold]Top edges found:[/]")
        summary = Table(box=box.SIMPLE, show_header=True, header_style="bold magenta")
        summary.add_column("Direction", width=10, no_wrap=True)
        summary.add_column("Edge", justify="right", width=7, no_wrap=True)
        summary.add_column("Market%", justify="right", width=8, no_wrap=True)
        summary.add_column("Model%", justify="right", width=8, no_wrap=True)
        summary.add_column("Conf", width=8, no_wrap=True)
        summary.add_column("Question", min_width=40, no_wrap=True)

        for m in top:
            direction = m.get("direction", "")
            color = "green" if direction == "BUY_YES" else "red"
            q = m["question"]
            if len(q) > 55:
                q = q[:52] + "..."
            summary.add_row(
                Text(direction, style=f"bold {color}"),
                Text(f"{m['abs_edge']*100:.1f}pp", style=color),
                _fmt_pct(m.get("yes_price")),
                _fmt_pct(m.get("model_prob")),
                m.get("confidence", "-"),
                q,
            )
        console.print(summary)

    console.print(f"\n[dim]Run `python -m src.cli edges` to see full ranked list.[/]")


def cmd_edges(args):
    from src.edge import rank_markets

    data = load_latest_analysis()
    if not data:
        console.print("[yellow]No analysis file found. Run `python -m src.cli scan` first.[/]")
        return

    ranked = rank_markets(
        data,
        min_confidence=args.confidence,
        min_edge=args.min_edge,
    )

    if not ranked:
        console.print(
            f"[yellow]No markets meet the criteria "
            f"(min_edge={args.min_edge:.0%}, confidence≥{args.confidence}).[/]"
        )
        return

    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold magenta",
        title=f"[bold]Edge Report[/] — {len(ranked)} markets",
        title_style="bold white",
    )
    table.add_column("Rank", min_width=4, justify="right", no_wrap=True)
    table.add_column("Question", min_width=40, max_width=52, no_wrap=True)
    table.add_column("Src", min_width=8, no_wrap=True)
    table.add_column("Mkt%", justify="right", min_width=6, no_wrap=True)
    table.add_column("Mdl%", justify="right", min_width=6, no_wrap=True)
    table.add_column("Edge", justify="right", min_width=7, no_wrap=True)
    table.add_column("Direction", min_width=9, no_wrap=True)
    table.add_column("Conf", min_width=6, no_wrap=True)
    table.add_column("Rationale", min_width=28, no_wrap=True)

    for rank, m in enumerate(ranked, 1):
        direction = m.get("direction", "")
        color = "green" if direction == "BUY_YES" else "red"
        q = m["question"]
        if len(q) > 52:
            q = q[:49] + "..."
        rationale = m.get("rationale", "")
        if len(rationale) > 55:
            rationale = rationale[:52] + "..."

        edge_pct = f"{m['abs_edge']*100:.1f}pp"

        table.add_row(
            str(rank),
            q,
            m.get("market_source", "other"),
            _fmt_pct(m.get("yes_price")),
            _fmt_pct(m.get("model_prob")),
            Text(edge_pct, style=color),
            Text(direction, style=f"bold {color}"),
            m.get("confidence", "-"),
            rationale,
        )

    console.print(table)
    console.print(
        f"[dim]Source: latest analysis · "
        f"filtered confidence≥{args.confidence}, edge≥{args.min_edge:.0%}[/]"
    )


def cmd_recommend(args):
    analyzed_list = load_latest_analysis()
    if not analyzed_list:
        console.print("[yellow]No analysis found. Run `scan` first.[/]")
        return

    recommendations = []
    for item in analyzed_list:
        rec = recommend_bet(item, item, args.bankroll)
        if rec and abs(rec["edge"]) >= args.min_edge:
            recommendations.append(rec)

    recommendations.sort(key=lambda r: abs(r["edge"]), reverse=True)

    if not recommendations:
        console.print(f"[yellow]No bets meet criteria (min_edge={args.min_edge:.0%}).[/]")
        return

    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold magenta",
        title=f"[bold]Recommendations[/] — {len(recommendations)} bets",
        title_style="bold white",
    )
    table.add_column("Rank", justify="right", width=4, no_wrap=True)
    table.add_column("Question", min_width=40, max_width=52, no_wrap=True)
    table.add_column("Dir", min_width=9, no_wrap=True)
    table.add_column("Mkt%", justify="right", min_width=6, no_wrap=True)
    table.add_column("Mdl%", justify="right", min_width=6, no_wrap=True)
    table.add_column("Edge", justify="right", min_width=7, no_wrap=True)
    table.add_column("Side", justify="right", min_width=5, no_wrap=True)
    table.add_column("Count", justify="right", min_width=6, no_wrap=True)
    table.add_column("Price¢", justify="right", min_width=7, no_wrap=True)
    table.add_column("Conf", min_width=6, no_wrap=True)
    table.add_column("Rationale", min_width=28, no_wrap=True)

    for rank, r in enumerate(recommendations, 1):
        color = "green" if r["direction"] == "BUY_YES" else "red"
        q = r["question"] or ""
        if len(q) > 52:
            q = q[:49] + "..."
        rationale = r.get("rationale", "")
        if len(rationale) > 55:
            rationale = rationale[:52] + "..."
        table.add_row(
            str(rank),
            q,
            Text(r["direction"], style=f"bold {color}"),
            _fmt_pct(r["market_prob"]),
            _fmt_pct(r["model_prob"]),
            Text(f"{r['edge']*100:+.1f}pp", style=color),
            r.get("side", "-"),
            str(r.get("count", "-")),
            f"{r.get('limit_price', '-')}¢",
            r["confidence"],
            rationale,
        )

    console.print(table)
    console.print(f"[dim]Bankroll: ${args.bankroll:.0f} · Use `paper-bet <ticker> --direction DIR --amount AMT` to place.[/]")


def cmd_paper_bet(args):
    analyzed_list = load_latest_analysis()
    if not analyzed_list:
        console.print("[yellow]No analysis found. Run `scan` first.[/]")
        return

    market = next((m for m in analyzed_list if m.get("market_id") == args.market_id), None)
    if not market:
        console.print(f"[red]Ticker {args.market_id!r} not found in latest snapshot.[/]")
        return

    model_prob = market.get("model_prob") or market.get("yes_price", 0)
    market_prob = market.get("yes_price", 0)
    edge = model_prob - market_prob

    rec = {
        "market_id": args.market_id,
        "question": market.get("question", ""),
        "direction": args.direction,
        "model_prob": model_prob,
        "market_prob": market_prob,
        "edge": edge,
        "kelly_fraction": 0.0,
        "amount": args.amount,
        "confidence": market.get("confidence", "low"),
        "rationale": market.get("rationale", ""),
    }

    portfolio = load_portfolio()
    if portfolio["bankroll"] < args.amount:
        console.print(f"[red]Insufficient bankroll: ${portfolio['bankroll']:.2f} < ${args.amount:.2f}[/]")
        return

    pos = open_position(portfolio, rec)
    console.print(
        f"[bold green]Position opened![/]\n"
        f"  ID:        [cyan]{pos['id']}[/]\n"
        f"  Question:  {pos['question'][:60]}\n"
        f"  Direction: [bold]{pos['direction']}[/]\n"
        f"  Amount:    ${pos['amount']:.2f} @ {_fmt_pct(pos['entry_price'])}\n"
        f"  Bankroll:  ${portfolio['bankroll']:.2f} remaining"
    )


def cmd_portfolio(args):
    portfolio = load_portfolio()
    summary = portfolio_summary(portfolio)

    console.print(
        Panel(
            f"  Bankroll:       [bold green]${summary['bankroll']:.2f}[/]\n"
            f"  Open positions: {summary['open_count']}\n"
            f"  Closed:         {summary['closed_count']}\n"
            f"  Total P&L:      [{'green' if summary['total_pnl'] >= 0 else 'red'}]{summary['total_pnl']:+.2f}[/]\n"
            f"  Open exposure:  ${summary['open_exposure']:.2f}",
            title="[bold magenta]Portfolio Summary[/]",
            expand=False,
        )
    )

    open_positions = [p for p in portfolio["positions"] if p["status"] == "open"]
    if not open_positions:
        console.print("[dim]No open positions.[/]")
        return

    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold magenta",
        title="[bold]Open Positions[/]",
    )
    table.add_column("ID", width=8, no_wrap=True)
    table.add_column("Question", min_width=40, max_width=52, no_wrap=True)
    table.add_column("Dir", min_width=9, no_wrap=True)
    table.add_column("Amount", justify="right", min_width=8, no_wrap=True)
    table.add_column("Entry%", justify="right", min_width=7, no_wrap=True)
    table.add_column("Model%", justify="right", min_width=7, no_wrap=True)
    table.add_column("Edge", justify="right", min_width=7, no_wrap=True)
    table.add_column("Opened", min_width=10, no_wrap=True)

    for pos in open_positions:
        color = "green" if pos["direction"] == "BUY_YES" else "red"
        q = pos["question"]
        if len(q) > 52:
            q = q[:49] + "..."
        opened = pos["opened_at"][:10] if pos.get("opened_at") else "-"
        table.add_row(
            pos["id"][:8],
            q,
            Text(pos["direction"], style=f"bold {color}"),
            f"${pos['amount']:.2f}",
            _fmt_pct(pos.get("entry_price")),
            _fmt_pct(pos.get("model_prob")),
            Text(f"{pos['edge']*100:+.1f}pp", style=color),
            opened,
        )

    console.print(table)


def cmd_momentum_portfolio(args):
    """Show momentum paper portfolio."""
    portfolio = load_momentum_portfolio()
    text = momentum_portfolio_summary(portfolio)
    console.print(Panel(text, title="[bold magenta]Momentum Paper Portfolio[/]", expand=False))


def cmd_momentum_resolve(args):
    """Close a momentum position manually."""
    portfolio = load_momentum_portfolio()
    try:
        pos = close_momentum_position(portfolio, args.position_id, args.outcome, args.exit_price)
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        return
    color = "green" if pos["pnl"] >= 0 else "red"
    console.print(
        f"[bold]Momentum position resolved.[/]\n"
        f"  ID:       {pos['id'][:8]}\n"
        f"  Outcome:  {args.outcome.upper()}\n"
        f"  P&L:      [{color}]{pos['pnl']:+.2f}[/{color}]\n"
        f"  Bankroll: ${portfolio['bankroll']:.2f}"
    )


def cmd_resolve(args):
    portfolio = load_portfolio()
    try:
        pos = close_position(portfolio, args.position_id, args.outcome, args.exit_price)
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        return

    color = "green" if pos["pnl"] >= 0 else "red"
    console.print(
        f"[bold]Position resolved.[/]\n"
        f"  ID:       {pos['id'][:8]}\n"
        f"  Outcome:  {args.outcome.upper()}\n"
        f"  P&L:      [{color}]{pos['pnl']:+.2f}[/{color}]\n"
        f"  Bankroll: ${portfolio['bankroll']:.2f}"
    )


def cmd_live_bet(args):
    ticker = args.market_id
    direction = args.direction
    amount = args.amount
    side = "yes" if direction == "BUY_YES" else "no"

    # --- Dry-run: show preview without placing ---
    if not args.confirm:
        with console.status(f"[bold cyan]Fetching market {ticker}...[/]"):
            markets = fetch_active_markets(limit=200)
        market = next((m for m in markets if m["market_id"] == ticker), None)

        price_key = "yes_price" if direction == "BUY_YES" else "no_price"
        est_price = market.get(price_key) if market else None
        est_limit_price = max(1, min(99, int(round(est_price * 100)))) if est_price else None
        count = max(1, int(amount))
        est_cost = round(count * est_limit_price / 100.0, 2) if est_limit_price else None

        question = market["question"][:70] if market else ticker

        state = load_state()
        ks_active = state.get("kill_switch", False)
        ks_reason = state.get("kill_switch_reason") or ""
        daily_pnl = state.get("daily_pnl", 0.0)
        limit = state.get("daily_loss_limit", 200.0)
        max_size = state.get("max_position_size", 100.0)
        remaining = limit + daily_pnl

        safety_ok = (
            not ks_active
            and amount <= max_size
            and (daily_pnl - amount) >= -limit
        )

        price_str = f"{est_limit_price}¢ (est. ${est_cost:.2f})" if est_limit_price else "unknown"
        console.print(
            Panel(
                f"  [bold]DRY RUN — no order placed[/]\n\n"
                f"  Market:      {question}\n"
                f"  Ticker:      [dim]{ticker}[/]\n"
                f"  Side:        [bold {'green' if side == 'yes' else 'red'}]{side}[/]\n"
                f"  Count:       [bold]{count}[/] contracts\n"
                f"  Limit price: [bold]{price_str}[/]\n\n"
                f"  [bold]Safety checks:[/]\n"
                f"    Kill switch:       [{'red' if ks_active else 'green'}]{'ACTIVE — ' + ks_reason if ks_active else 'off'}[/]\n"
                f"    Position size:     [{'red' if amount > max_size else 'green'}]${amount:.2f} / max ${max_size:.2f}[/]\n"
                f"    Daily loss room:   [{'red' if remaining < amount else 'green'}]${remaining:.2f} remaining[/]\n\n"
                f"  Add [bold cyan]--confirm[/] to place the real order.",
                title="[bold yellow]Live Bet Preview[/]",
                expand=False,
            )
        )
        if not safety_ok:
            console.print("[red]Safety checks failed — order would be blocked.[/]")
        return

    # --- Confirmed: run safety checks and place real order ---
    try:
        check_kill_switch()
        validate_position_size(amount)
        check_daily_loss_limit(amount)
    except KillSwitchError as e:
        console.print(f"[bold red]Kill switch active:[/] {e}")
        return
    except (DailyLossLimitError, PositionSizeError) as e:
        console.print(f"[bold red]Safety check failed:[/] {e}")
        return

    from src.executor import place_order

    # Fetch market info for price and metadata
    with console.status("[bold cyan]Fetching market data...[/]"):
        all_markets = fetch_active_markets(limit=200)
    market = next((m for m in all_markets if m["market_id"] == ticker), None)

    if not market:
        console.print(f"[red]Ticker {ticker!r} not found in active markets.[/]")
        return

    price_key = "yes_price" if direction == "BUY_YES" else "no_price"
    price = market.get(price_key) or 0
    if not price:
        console.print("[red]Cannot determine price for order.[/]")
        return

    limit_price = max(1, min(99, int(round(price * 100))))
    count = max(1, int(amount))
    question = market["question"]
    model_prob = market.get("model_prob")
    market_prob = market.get("yes_price", price)
    edge = (model_prob - market_prob) if model_prob else 0.0

    with console.status("[bold cyan]Placing order...[/]"):
        try:
            result = place_order(ticker, side, count, limit_price)
        except KillSwitchError as e:
            console.print(f"[bold red]Kill switch active:[/] {e}")
            return
        except Exception as e:
            console.print(f"[bold red]Order failed:[/] {e}")
            return

    order = result.get("order", result)
    order_id = order.get("order_id") or order.get("id") or "unknown"

    rec = {
        "market_id": ticker,
        "question": question,
        "direction": direction,
        "amount": round(count * limit_price / 100.0, 2),
        "market_prob": price,
        "model_prob": model_prob or price,
        "edge": edge,
        "confidence": market.get("confidence", "n/a"),
        "rationale": market.get("rationale", ""),
    }

    portfolio = load_live_portfolio()
    pos = open_live_position(portfolio, rec, order_id)

    console.print(
        Panel(
            f"  [bold green]Order placed![/]\n\n"
            f"  Position ID:  [cyan]{pos['id']}[/]\n"
            f"  Order ID:     [dim]{order_id}[/]\n"
            f"  Question:     {question[:65]}\n"
            f"  Ticker:       [dim]{ticker}[/]\n"
            f"  Side:         [bold {'green' if side == 'yes' else 'red'}]{side}[/]\n"
            f"  Count:        {count} contracts @ {limit_price}¢\n"
            f"  Bankroll:     ${portfolio['bankroll']:.2f} remaining",
            title="[bold green]Live Order Confirmed[/]",
            expand=False,
        )
    )


def cmd_live_portfolio(args):
    portfolio = load_live_portfolio()
    summary = live_portfolio_summary(portfolio)

    pnl_color = "green" if summary["total_pnl"] >= 0 else "red"
    console.print(
        Panel(
            f"  Bankroll:       [bold green]${summary['bankroll']:.2f}[/]\n"
            f"  Open positions: {summary['open_count']}\n"
            f"  Closed:         {summary['closed_count']}\n"
            f"  Total P&L:      [{pnl_color}]{summary['total_pnl']:+.2f}[/{pnl_color}]\n"
            f"  Open exposure:  ${summary['open_exposure']:.2f}",
            title="[bold magenta]Live Portfolio Summary[/]",
            expand=False,
        )
    )

    open_positions = [p for p in portfolio["positions"] if p["status"] == "open"]
    if not open_positions:
        console.print("[dim]No open live positions.[/]")
        return

    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold magenta",
        title="[bold]Open Live Positions[/]",
    )
    table.add_column("ID", width=8, no_wrap=True)
    table.add_column("Order ID", width=12, no_wrap=True)
    table.add_column("Question", min_width=38, max_width=50, no_wrap=True)
    table.add_column("Dir", min_width=9, no_wrap=True)
    table.add_column("Amount", justify="right", min_width=8, no_wrap=True)
    table.add_column("Entry%", justify="right", min_width=7, no_wrap=True)
    table.add_column("Opened", min_width=10, no_wrap=True)

    for pos in open_positions:
        color = "green" if pos["direction"] == "BUY_YES" else "red"
        q = pos["question"]
        if len(q) > 50:
            q = q[:47] + "..."
        opened = pos["opened_at"][:10] if pos.get("opened_at") else "-"
        order_short = (pos.get("order_id") or "")[:12]
        table.add_row(
            pos["id"][:8],
            order_short,
            q,
            Text(pos["direction"], style=f"bold {color}"),
            f"${pos['amount']:.2f}",
            _fmt_pct(pos.get("entry_price")),
            opened,
        )

    console.print(table)


def cmd_live_resolve(args):
    portfolio = load_live_portfolio()
    try:
        pos = close_live_position(portfolio, args.position_id, args.outcome, args.exit_price)
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        return

    record_daily_pnl(pos["pnl"])

    color = "green" if pos["pnl"] >= 0 else "red"
    console.print(
        f"[bold]Live position resolved.[/]\n"
        f"  ID:       {pos['id'][:8]}\n"
        f"  Outcome:  {args.outcome.upper()}\n"
        f"  P&L:      [{color}]{pos['pnl']:+.2f}[/{color}]\n"
        f"  Bankroll: ${portfolio['bankroll']:.2f}"
    )

    from src.safety import load_state as _load_state
    state = _load_state()
    if state.get("kill_switch"):
        console.print(
            f"[bold red]Kill switch auto-activated:[/] {state.get('kill_switch_reason', '')}"
        )


def cmd_kill_switch(args):
    if args.activate and args.deactivate:
        console.print("[red]Cannot use --activate and --deactivate together.[/]")
        return

    if args.activate:
        reason = args.reason or "manually activated via CLI"
        activate_kill_switch(reason)

        from src.executor import cancel_all_orders
        try:
            with console.status("[bold red]Cancelling all open Kalshi orders...[/]"):
                cancelled = cancel_all_orders()
            console.print(
                f"[bold red]Kill switch activated.[/] Reason: {reason}\n"
                f"  Cancelled orders: {cancelled}"
            )
        except Exception as e:
            console.print(
                f"[bold red]Kill switch activated.[/] Reason: {reason}\n"
                f"  [yellow]Warning: could not cancel Kalshi orders:[/] {e}"
            )
        return

    if args.deactivate:
        deactivate_kill_switch()
        console.print("[bold green]Kill switch deactivated.[/] Trading is now enabled.")
        return

    state = load_state()
    ks = state.get("kill_switch", False)
    reason = state.get("kill_switch_reason") or "-"
    daily_pnl = state.get("daily_pnl", 0.0)
    limit = state.get("daily_loss_limit", 200.0)
    max_size = state.get("max_position_size", 100.0)

    status_color = "red" if ks else "green"
    status_label = "ACTIVE" if ks else "off"

    console.print(
        Panel(
            f"  Status:         [{status_color}][bold]{status_label}[/bold][/{status_color}]\n"
            f"  Reason:         {reason}\n"
            f"  Daily P&L:      {daily_pnl:+.2f}\n"
            f"  Daily limit:    ${limit:.2f}\n"
            f"  Max position:   ${max_size:.2f}",
            title="[bold]Kill Switch Status[/]",
            expand=False,
        )
    )


def cmd_live_status(args):
    state = load_state()
    ks = state.get("kill_switch", False)
    ks_reason = state.get("kill_switch_reason") or "-"
    daily_pnl = state.get("daily_pnl", 0.0)
    limit = state.get("daily_loss_limit", 200.0)
    max_size = state.get("max_position_size", 100.0)
    remaining = limit + daily_pnl

    ks_color = "red" if ks else "green"
    ks_label = "ACTIVE" if ks else "off"
    pnl_color = "green" if daily_pnl >= 0 else "red"

    # Account balance (requires credentials — graceful fallback)
    balance = None
    balance_error = None
    try:
        from src.executor import get_balance
        balance = get_balance()
    except KillSwitchError:
        balance_error = "kill switch active"
    except EnvironmentError:
        balance_error = "credentials not configured"
    except Exception as e:
        balance_error = str(e)[:60]

    balance_str = (
        f"${balance:.2f}"
        if balance is not None
        else f"[dim]unavailable ({balance_error})[/dim]"
    )

    console.print(
        Panel(
            f"  Kill switch:         [{ks_color}][bold]{ks_label}[/bold][/{ks_color}]  {ks_reason if ks else ''}\n"
            f"  Daily P&L:           [{pnl_color}]{daily_pnl:+.2f}[/{pnl_color}]\n"
            f"  Daily loss limit:    ${limit:.2f}  (${remaining:.2f} remaining)\n"
            f"  Max position size:   ${max_size:.2f}\n"
            f"  Kalshi balance:      {balance_str}",
            title="[bold magenta]Live Trading Status[/]",
            expand=False,
        )
    )


def main():
    parser = argparse.ArgumentParser(
        prog="kalshi-intel",
        description="Kalshi Intelligence CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = sub.add_parser("list", help="List active markets")
    p_list.add_argument("--min-volume", type=float, default=None, metavar="N",
                        help="Minimum volume in contracts")
    p_list.add_argument("--days", type=int, default=None,
                        help="Close within N days")
    p_list.add_argument("--top", type=int, default=20,
                        help="Number of markets to display (default 20)")

    # show
    p_show = sub.add_parser("show", help="Show market detail")
    p_show.add_argument("market_id", help="Kalshi market ticker")

    # snapshot
    sub.add_parser("snapshot", help="Fetch and save a snapshot to disk")

    # scan
    p_scan = sub.add_parser("scan", help="Run LLM edge analysis on live markets")
    p_scan.add_argument("--limit", type=int, default=20,
                        help="Number of markets to analyze (default 20)")
    p_scan.add_argument("--min-volume", type=float, default=10, metavar="N",
                        help="Minimum volume filter in contracts (default 10)")
    p_scan.add_argument("--days", type=int, default=None,
                        help="Only include markets closing within N days")
    p_scan.add_argument("--min-yes-price", type=float, default=0.05, metavar="P",
                        help="Min Yes price to include (default 0.05)")
    p_scan.add_argument("--max-yes-price", type=float, default=0.95, metavar="P",
                        help="Max Yes price to include (default 0.95)")

    # edges
    p_edges = sub.add_parser("edges", help="Show ranked edge report from latest analysis")
    p_edges.add_argument("--min-edge", type=float, default=0.05, metavar="N",
                         help="Minimum absolute edge to display (default 0.05 = 5pp)")
    p_edges.add_argument("--confidence", choices=["low", "medium", "high"], default="low",
                         help="Minimum confidence level (default low)")

    # recommend
    p_rec = sub.add_parser("recommend", help="Show bet recommendations from latest analysis")
    p_rec.add_argument("--bankroll", type=float, default=1000.0, help="Bankroll in USD (default 1000)")
    p_rec.add_argument("--min-edge", type=float, default=0.08, metavar="N",
                       help="Minimum absolute edge (default 0.08 = 8pp)")

    # paper-bet
    p_pb = sub.add_parser("paper-bet", help="Open a paper trade position")
    p_pb.add_argument("market_id", help="Kalshi market ticker")
    p_pb.add_argument("--direction", choices=["BUY_YES", "BUY_NO"], required=True)
    p_pb.add_argument("--amount", type=float, required=True, help="Dollar amount to bet")

    # portfolio
    sub.add_parser("portfolio", help="Show portfolio summary and open positions")

    # resolve
    p_res = sub.add_parser("resolve", help="Close a position with outcome")
    p_res.add_argument("position_id", help="Position ID or 8-char prefix")
    p_res.add_argument("--outcome", choices=["YES", "NO"], required=True)
    p_res.add_argument("--exit-price", type=float, required=True, dest="exit_price")

    # live-bet
    p_lb = sub.add_parser("live-bet", help="Place a live order (dry-run without --confirm)")
    p_lb.add_argument("market_id", help="Kalshi market ticker")
    p_lb.add_argument("--direction", choices=["BUY_YES", "BUY_NO"], required=True)
    p_lb.add_argument("--amount", type=float, required=True, help="Dollar amount (converted to contracts)")
    p_lb.add_argument(
        "--confirm",
        action="store_true",
        default=False,
        help="Actually place the order (without this flag: dry-run only)",
    )

    # live-portfolio
    sub.add_parser("live-portfolio", help="Show live portfolio summary and open positions")

    # live-resolve
    p_lr = sub.add_parser("live-resolve", help="Close a live position and record P&L")
    p_lr.add_argument("position_id", help="Live position ID or 8-char prefix")
    p_lr.add_argument("--outcome", choices=["YES", "NO"], required=True)
    p_lr.add_argument("--exit-price", type=float, required=True, dest="exit_price")

    # kill-switch
    p_ks = sub.add_parser("kill-switch", help="Manage the live trading kill switch")
    ks_group = p_ks.add_mutually_exclusive_group()
    ks_group.add_argument("--activate", action="store_true", default=False)
    ks_group.add_argument("--deactivate", action="store_true", default=False)
    p_ks.add_argument("--reason", type=str, default=None, help="Reason for activation")

    # momentum-portfolio
    sub.add_parser("momentum-portfolio", help="Show momentum paper portfolio")

    # momentum-resolve
    p_mr = sub.add_parser("momentum-resolve", help="Close a momentum position with outcome")
    p_mr.add_argument("position_id", help="Position ID or 8-char prefix")
    p_mr.add_argument("--outcome", choices=["YES", "NO"], required=True)
    p_mr.add_argument("--exit-price", type=float, required=True, dest="exit_price")

    # live-status
    sub.add_parser("live-status", help="Show kill switch, daily P&L, and Kalshi balance")

    args = parser.parse_args()

    dispatch = {
        "list": cmd_list,
        "show": cmd_show,
        "snapshot": cmd_snapshot,
        "scan": cmd_scan,
        "edges": cmd_edges,
        "recommend": cmd_recommend,
        "paper-bet": cmd_paper_bet,
        "portfolio": cmd_portfolio,
        "resolve": cmd_resolve,
        "live-bet": cmd_live_bet,
        "live-portfolio": cmd_live_portfolio,
        "live-resolve": cmd_live_resolve,
        "kill-switch": cmd_kill_switch,
        "live-status": cmd_live_status,
        "momentum-portfolio": cmd_momentum_portfolio,
        "momentum-resolve": cmd_momentum_resolve,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
