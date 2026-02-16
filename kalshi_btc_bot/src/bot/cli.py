"""CLI interface using Typer – entry point for all bot operations."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from bot.config import BotConfig, get_config, reset_config
from bot.infra.log import setup_logging

app = typer.Typer(name="bot", help="Kalshi BTC Hybrid Bot – Market-Make + Sniper")
console = Console()


@app.command()
def run(
    env: str = typer.Option("demo", help="Environment: demo or prod"),
    paper: bool = typer.Option(True, help="Paper trading mode (no real orders)"),
    live: bool = typer.Option(False, help="Enable live trading (requires LIVE_TRADING=true)"),
    log_level: str = typer.Option("INFO", help="Log level"),
) -> None:
    """Start the hybrid bot."""
    os.environ["ENVIRONMENT"] = env
    if live:
        os.environ["LIVE_TRADING"] = "true"
    reset_config()
    setup_logging(log_level)
    cfg = get_config()

    if cfg.environment == "prod" and not cfg.live_trading:
        console.print("[bold red]ERROR:[/] Cannot run prod without LIVE_TRADING=true")
        raise typer.Exit(1)

    if cfg.live_trading and paper:
        console.print("[yellow]WARNING:[/] LIVE_TRADING=true but paper=True – running in paper mode")

    console.print(f"[bold green]Starting bot[/]  env={env}  paper={paper}  live={live}")
    console.print(f"  REST base: {cfg.rest_base}")
    console.print(f"  WS URL:    {cfg.ws_url}")

    from bot.main import run_bot

    asyncio.run(run_bot(cfg, paper=paper))


@app.command()
def cancel_all(
    env: str = typer.Option("demo", help="Environment: demo or prod"),
) -> None:
    """Cancel all open orders."""
    os.environ["ENVIRONMENT"] = env
    reset_config()
    setup_logging("INFO")
    cfg = get_config()

    async def _cancel() -> None:
        from bot.kalshi.rest import KalshiRestClient

        client = KalshiRestClient(cfg)
        try:
            await client.cancel_all_orders()
            console.print("[green]All orders cancelled[/]")
        finally:
            await client.close()

    asyncio.run(_cancel())


@app.command()
def status(
    env: str = typer.Option("demo", help="Environment: demo or prod"),
) -> None:
    """Show current portfolio status."""
    os.environ["ENVIRONMENT"] = env
    reset_config()
    setup_logging("WARNING")
    cfg = get_config()

    async def _status() -> None:
        from bot.kalshi.portfolio import PortfolioTracker
        from bot.kalshi.rest import KalshiRestClient
        from bot.pricing.btc_feed import BtcSpotFeed

        client = KalshiRestClient(cfg)
        try:
            tracker = PortfolioTracker(client, cfg)
            snap = await tracker.refresh()

            feed = BtcSpotFeed(cfg.btc_spot_feed, cfg.btc_symbol)
            try:
                quote = await feed.get_spot()
                spot_str = f"${quote.price:,.2f}"
            except Exception:
                spot_str = "N/A"
            finally:
                await feed.close()

            table = Table(title="Bot Status")
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="green")

            table.add_row("Environment", cfg.environment)
            table.add_row("Balance", f"${snap.balance_dollars:,.2f}")
            table.add_row("Positions", str(len([p for p in snap.positions if p.position != 0])))
            table.add_row("BTC Spot", spot_str)
            table.add_row("Live Trading", str(cfg.live_trading))
            table.add_row("Kill Switch", "INACTIVE")

            console.print(table)

            if snap.positions:
                pos_table = Table(title="Open Positions")
                pos_table.add_column("Ticker")
                pos_table.add_column("Qty")
                pos_table.add_column("Realized PnL")
                for p in snap.positions:
                    if p.position != 0:
                        pos_table.add_row(
                            p.ticker,
                            str(p.position),
                            f"${p.realized_pnl:.2f}",
                        )
                console.print(pos_table)
        finally:
            await client.close()

    asyncio.run(_status())


@app.command()
def backtest(
    db: str = typer.Argument(..., help="Path to SQLite database with recorded data"),
) -> None:
    """Run backtest on recorded data."""
    setup_logging("INFO")
    from bot.sim.backtest import BacktestRunner

    runner = BacktestRunner(db)
    stats = runner.run()
    console.print(stats.summary())


def main() -> None:
    app()


if __name__ == "__main__":
    main()
