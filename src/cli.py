"""
Polymarket Intelligence CLI

Usage:
    python -m src.cli list                              # Top 20 active markets
    python -m src.cli list --min-volume 10000           # Filter by volume
    python -m src.cli list --days 7                     # Close within 7 days
    python -m src.cli show <market_id>                  # Market detail + order book
    python -m src.cli snapshot                          # Fetch and save snapshot
    python -m src.cli scan --limit 20 --min-volume 50000
    python -m src.cli edges --min-edge 0.05 --confidence medium
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

from src.markets import fetch_active_markets, fetch_market_orderbook, filter_markets
from src.storage import save_snapshot, load_latest_snapshot, save_analysis, load_latest_analysis

console = Console(width=160)


def _fmt_volume(v) -> str:
    if v is None:
        return "-"
    if v >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v/1_000:.0f}K"
    return f"${v:.0f}"


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
        title=f"[bold]Polymarket — Active Markets[/] ({len(display)} shown)",
        title_style="bold white",
    )
    table.add_column("#", style="dim", width=4, justify="right", no_wrap=True)
    table.add_column("Question", min_width=50, max_width=65, no_wrap=True)
    table.add_column("Yes%", justify="right", min_width=7, no_wrap=True)
    table.add_column("No%", justify="right", min_width=7, no_wrap=True)
    table.add_column("Volume", justify="right", min_width=9, no_wrap=True)
    table.add_column("Closes", justify="right", min_width=8, no_wrap=True)

    for i, m in enumerate(display, 1):
        q = m["question"]
        if len(q) > 62:
            q = q[:59] + "..."
        table.add_row(
            str(i),
            q,
            _fmt_pct(m.get("yes_price")),
            _fmt_pct(m.get("no_price")),
            _fmt_volume(m.get("volume")),
            _days_to_close(m.get("end_date", "")),
        )

    console.print(table)
    console.print(
        f"[dim]Source: Gamma API · {len(markets)} total matched "
        f"({'filtered' if args.min_volume or args.days else 'no filters'})[/]"
    )


def cmd_show(args):
    market_id = args.market_id

    with console.status(f"[bold cyan]Fetching market {market_id}...[/]"):
        markets = fetch_active_markets(limit=200)

    match = next((m for m in markets if m["market_id"] == market_id), None)

    if match:
        console.print(
            Panel(
                f"[bold]{match['question']}[/]\n\n"
                f"  [cyan]Yes:[/] {_fmt_pct(match.get('yes_price'))}   "
                f"[red]No:[/] {_fmt_pct(match.get('no_price'))}\n"
                f"  Volume:    {_fmt_volume(match.get('volume'))}\n"
                f"  Liquidity: {_fmt_volume(match.get('liquidity'))}\n"
                f"  Closes:    {match.get('end_date', 'unknown')}  ({_days_to_close(match.get('end_date', ''))})\n"
                f"  Outcomes:  {', '.join(match.get('outcomes', []))}\n"
                f"  Market ID: [dim]{market_id}[/]",
                title="[bold magenta]Market Detail[/]",
                expand=False,
            )
        )
    else:
        console.print(f"[yellow]Market {market_id!r} not found in active markets.[/]")

    # Order book
    with console.status("[bold cyan]Fetching order book...[/]"):
        try:
            book = fetch_market_orderbook(market_id)
        except Exception as e:
            console.print(f"[red]Order book unavailable:[/] {e}")
            return

    if book.get("error"):
        console.print(f"[yellow]Order book:[/] {book['error']}")
        return

    bids = book.get("bids", [])[:10]
    asks = book.get("asks", [])[:10]

    ob_table = Table(box=box.SIMPLE, title="[bold]Order Book (Top 10)[/]", expand=False)
    ob_table.add_column("Bid Size", justify="right", style="green")
    ob_table.add_column("Bid Price", justify="right", style="green")
    ob_table.add_column("Ask Price", justify="right", style="red")
    ob_table.add_column("Ask Size", justify="right", style="red")

    for i in range(max(len(bids), len(asks))):
        bid = bids[i] if i < len(bids) else None
        ask = asks[i] if i < len(asks) else None
        ob_table.add_row(
            f"{bid['size']:.0f}" if bid else "",
            f"{bid['price']:.4f}" if bid else "",
            f"{ask['price']:.4f}" if ask else "",
            f"{ask['size']:.0f}" if ask else "",
        )

    console.print(ob_table)


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

    # Fetch a large pool — price + days filters can discard most candidates
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


def main():
    parser = argparse.ArgumentParser(
        prog="polymarket-intel",
        description="Polymarket Intelligence CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = sub.add_parser("list", help="List active markets")
    p_list.add_argument("--min-volume", type=float, default=None, metavar="N",
                        help="Minimum volume in USD")
    p_list.add_argument("--days", type=int, default=None,
                        help="Close within N days")
    p_list.add_argument("--top", type=int, default=20,
                        help="Number of markets to display (default 20)")

    # show
    p_show = sub.add_parser("show", help="Show market detail + order book")
    p_show.add_argument("market_id", help="Polymarket condition ID")

    # snapshot
    sub.add_parser("snapshot", help="Fetch and save a snapshot to disk")

    # scan
    p_scan = sub.add_parser("scan", help="Run LLM edge analysis on live markets")
    p_scan.add_argument("--limit", type=int, default=20,
                        help="Number of markets to analyze (default 20)")
    p_scan.add_argument("--min-volume", type=float, default=50_000, metavar="N",
                        help="Minimum volume filter (default 50000)")
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

    args = parser.parse_args()

    dispatch = {
        "list": cmd_list,
        "show": cmd_show,
        "snapshot": cmd_snapshot,
        "scan": cmd_scan,
        "edges": cmd_edges,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
