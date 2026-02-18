"""Portfolio tracker – positions, balance, fills snapshot + PnL ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.config import BotConfig, get_config
from bot.infra.log import get_logger
from bot.kalshi.models import Balance, Fill, Position
from bot.kalshi.rest import KalshiRestClient
from bot.pnl.tracker import PnlTracker

log = get_logger(__name__)


@dataclass
class PortfolioSnapshot:
    balance_cents: int = 0
    positions: list[Position] = field(default_factory=list)
    fills_today: list[Fill] = field(default_factory=list)

    @property
    def balance_dollars(self) -> float:
        return self.balance_cents / 100.0

    def position_for(self, ticker: str) -> Position | None:
        for p in self.positions:
            if p.ticker == ticker:
                return p
        return None

    def net_position_contracts(self, ticker: str) -> int:
        p = self.position_for(ticker)
        return p.position if p else 0


class PortfolioTracker:
    """Periodically polls Kalshi portfolio endpoints and feeds fills to PnlTracker."""

    def __init__(
        self,
        rest: KalshiRestClient,
        cfg: BotConfig | None = None,
        pnl_tracker: PnlTracker | None = None,
    ) -> None:
        self.rest = rest
        self.cfg = cfg or get_config()
        self.snapshot = PortfolioSnapshot()
        self.pnl = pnl_tracker

    async def refresh(self) -> PortfolioSnapshot:
        try:
            bal = await self.rest.get_balance()
            self.snapshot.balance_cents = bal.balance
        except Exception as exc:
            log.warning("Failed to refresh balance: %s", exc)

        try:
            self.snapshot.positions = await self.rest.get_positions()
        except Exception as exc:
            log.warning("Failed to refresh positions: %s", exc)

        try:
            fills = await self.rest.get_fills(limit=200)
            self.snapshot.fills_today = fills
            if self.pnl:
                for f in fills:
                    self.pnl.ingest_kalshi_fill(f)
        except Exception as exc:
            log.warning("Failed to refresh fills: %s", exc)

        return self.snapshot
