"""CLI interface using Typer – entry point for all bot operations."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env from the project root (two levels up from src/bot/cli.py)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)

import typer
from rich.console import Console
from rich.table import Table

from bot.config import BotConfig, get_config, reset_config
from bot.infra.log import setup_logging

app = typer.Typer(name="bot", help="Kalshi BTC Hybrid Bot – Market-Make + Sniper")
console = Console()


def _apply_config_file(config_path: str) -> None:
    """Load a JSON config file and inject its values as environment variables."""
    path = Path(config_path)
    if not path.exists():
        console.print(f"[bold red]ERROR:[/] Config file not found: {path}")
        raise typer.Exit(1)
    data = json.loads(path.read_text(encoding="utf-8"))
    env_map = {
        "environment": "ENVIRONMENT",
        "key_id": "KALSHI_KEY_ID",
        "private_key_path": "KALSHI_PRIVATE_KEY_PATH",
        "live_trading": "LIVE_TRADING",
        "paper_mode": "PAPER_MODE",
        "mm_enabled": "MM_ENABLED",
        "sniper_enabled": "SNIPER_ENABLED",
        "daily_stop_dollars": "DAILY_STOP_DOLLARS",
        "max_gross_exposure_dollars": "MAX_GROSS_EXPOSURE_DOLLARS",
        "max_net_exposure_dollars": "MAX_NET_EXPOSURE_DOLLARS",
        "max_exposure_per_market_dollars": "MAX_EXPOSURE_PER_MARKET_DOLLARS",
        "max_order_size_contracts": "MAX_ORDER_SIZE_CONTRACTS",
    }
    for json_key, env_key in env_map.items():
        if json_key in data and data[json_key] is not None:
            os.environ[env_key] = str(data[json_key])


@app.command()
def run(
    env: str = typer.Option("demo", help="Environment: demo or prod"),
    paper: bool = typer.Option(True, help="Paper trading mode (no real orders)"),
    live: bool = typer.Option(False, help="Enable live trading (requires LIVE_TRADING=true)"),
    log_level: str = typer.Option("INFO", help="Log level"),
    config: Optional[str] = typer.Option(None, help="Path to JSON config file"),
) -> None:
    """Start the hybrid bot."""
    if config:
        _apply_config_file(config)
        # Override env/paper/live from config file if present
        env = os.environ.get("ENVIRONMENT", env)
        paper_env = os.environ.get("PAPER_MODE")
        if paper_env is not None:
            paper = paper_env.lower() in ("true", "1", "yes")
        live_env = os.environ.get("LIVE_TRADING")
        if live_env is not None:
            live = live_env.lower() in ("true", "1", "yes")

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
    config: Optional[str] = typer.Option(None, help="Path to JSON config file"),
) -> None:
    """Cancel all open orders."""
    if config:
        _apply_config_file(config)
        env = os.environ.get("ENVIRONMENT", env)

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
    config: Optional[str] = typer.Option(None, help="Path to JSON config file"),
) -> None:
    """Show current portfolio status."""
    if config:
        _apply_config_file(config)
        env = os.environ.get("ENVIRONMENT", env)

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
