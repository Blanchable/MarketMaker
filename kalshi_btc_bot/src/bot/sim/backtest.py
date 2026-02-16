"""Backtest runner – replays recorded tick data through strategy logic."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bot.config import BotConfig, get_config
from bot.infra.log import get_logger
from bot.infra.storage import Storage
from bot.pricing.digital_prob import DigitalProb
from bot.pricing.vol import VolEstimator
from bot.sim.fill_sim import FillSimulator
from bot.sim.paper_broker import PaperBroker

log = get_logger(__name__)


@dataclass
class BacktestStats:
    total_ticks: int = 0
    total_fills: int = 0
    total_return_dollars: float = 0.0
    max_drawdown_dollars: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    total_fees: float = 0.0
    final_balance_dollars: float = 0.0

    @property
    def win_rate(self) -> float:
        total = self.win_count + self.loss_count
        return self.win_count / total if total > 0 else 0.0

    @property
    def avg_trade_dollars(self) -> float:
        total = self.win_count + self.loss_count
        return self.total_return_dollars / total if total > 0 else 0.0

    def summary(self) -> str:
        return (
            f"Backtest Results:\n"
            f"  Ticks processed: {self.total_ticks}\n"
            f"  Total fills: {self.total_fills}\n"
            f"  Total return: ${self.total_return_dollars:.2f}\n"
            f"  Max drawdown: ${self.max_drawdown_dollars:.2f}\n"
            f"  Win rate: {self.win_rate:.1%}\n"
            f"  Avg trade: ${self.avg_trade_dollars:.4f}\n"
            f"  Total fees: ${self.total_fees:.2f}\n"
            f"  Final balance: ${self.final_balance_dollars:.2f}\n"
        )


class BacktestRunner:
    """Replays tick data from SQLite and runs a simplified strategy."""

    def __init__(self, db_path: str | Path, cfg: BotConfig | None = None) -> None:
        self.db_path = Path(db_path)
        self.cfg = cfg or get_config()
        self.storage = Storage(db_path)
        self.prob_model = DigitalProb()
        self.vol = VolEstimator(sample_interval_sec=5.0)

    def run(self) -> BacktestStats:
        """Execute the backtest and return summary statistics."""
        self.storage.open()
        broker = PaperBroker(cfg=self.cfg)
        stats = BacktestStats()

        ticks = self.storage.query(
            "SELECT * FROM ticks ORDER BY ts_ms ASC"
        )
        markets_info = self.storage.query(
            "SELECT ticker, strike, expiration FROM markets GROUP BY ticker"
        )
        market_map: dict[str, dict[str, Any]] = {}
        for m in markets_info:
            market_map[m["ticker"]] = m

        peak_balance = broker.balance_cents / 100.0
        max_dd = 0.0

        for tick in ticks:
            stats.total_ticks += 1
            ticker = tick["ticker"]
            yes_bid = tick.get("yes_bid", 0) or 0
            yes_ask = tick.get("yes_ask", 0) or 0

            # Check fills on resting orders
            fills = broker.check_fills(yes_bid, yes_ask, ticker)
            for f in fills:
                stats.total_fills += 1
                stats.total_fees += f.fee_dollars
                if f.action == "buy":
                    stats.win_count += 1
                else:
                    stats.loss_count += 1

            # Track drawdown
            bal = broker.balance_cents / 100.0
            if bal > peak_balance:
                peak_balance = bal
            dd = peak_balance - bal
            if dd > max_dd:
                max_dd = dd

        stats.final_balance_dollars = broker.balance_cents / 100.0
        stats.total_return_dollars = stats.final_balance_dollars - self.cfg.start_bankroll_dollars
        stats.max_drawdown_dollars = max_dd
        self.storage.close()
        return stats
