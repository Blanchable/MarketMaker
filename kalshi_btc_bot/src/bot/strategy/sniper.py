"""Sniper strategy – take mispriced quotes when edge exceeds threshold."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from bot.config import BotConfig, get_config
from bot.infra.log import get_logger
from bot.infra.metrics import metrics
from bot.kalshi.market_discovery import TradeableMarket
from bot.kalshi.order_manager import OrderManager
from bot.kalshi.ws import MarketState
from bot.pricing.fair_value import FairValue
from bot.strategy.risk import RiskEngine, RiskSnapshot

log = get_logger(__name__)

# Default fee estimate in cents per contract (taker)
_DEFAULT_TAKER_FEE_CENTS = 2


@dataclass
class SniperSignal:
    ticker: str
    direction: str  # "buy_yes" or "sell_yes"
    price: int
    size: int
    edge_cents: float
    fair_cents: int


class SniperStrategy:
    """Detects and takes mispriced quotes on BTC binary markets."""

    def __init__(
        self,
        order_mgr: OrderManager,
        risk: RiskEngine,
        cfg: BotConfig | None = None,
    ) -> None:
        self.order_mgr = order_mgr
        self.risk = risk
        self.cfg = cfg or get_config()
        self._cooldowns: dict[str, float] = {}
        self._empirical_taker_fee: float | None = None

    def set_empirical_fee(self, fee_cents: float) -> None:
        self._empirical_taker_fee = fee_cents

    @property
    def fee_estimate(self) -> float:
        if self._empirical_taker_fee is not None:
            return self._empirical_taker_fee
        return _DEFAULT_TAKER_FEE_CENTS

    def _on_cooldown(self, ticker: str) -> bool:
        last = self._cooldowns.get(ticker, 0)
        return (time.time() - last) < self.cfg.sniper_cooldown_seconds

    def _set_cooldown(self, ticker: str) -> None:
        self._cooldowns[ticker] = time.time()

    def evaluate(
        self,
        market: TradeableMarket,
        ws: MarketState,
        fv: FairValue,
        risk_snap: RiskSnapshot,
    ) -> SniperSignal | None:
        """Check if a sniper opportunity exists for this market."""
        if not self.cfg.sniper_enabled:
            return None

        if self._on_cooldown(market.ticker):
            return None

        fair = fv.fair_cents
        yes_ask = ws.yes_ask
        yes_bid = ws.yes_bid
        fee = self.fee_estimate
        min_edge = self.cfg.sniper_min_edge_cents

        max_contracts = self.risk.max_additional_contracts(market.ticker, risk_snap)
        if max_contracts <= 0:
            return None

        # Buy YES if ask is cheap enough
        if yes_ask > 0:
            edge = fair - yes_ask - fee
            if edge >= min_edge:
                price = min(
                    yes_ask + self.cfg.sniper_max_slippage_cents,
                    fair - 1,
                )
                price = max(1, min(99, price))
                size = min(max_contracts, self.cfg.max_order_size_contracts)
                return SniperSignal(
                    ticker=market.ticker,
                    direction="buy_yes",
                    price=price,
                    size=size,
                    edge_cents=edge,
                    fair_cents=fair,
                )

        # Sell YES if bid is rich enough
        if yes_bid > 0:
            edge = yes_bid - fair - fee
            if edge >= min_edge:
                price = max(
                    yes_bid - self.cfg.sniper_max_slippage_cents,
                    fair + 1,
                )
                price = max(1, min(99, price))
                size = min(max_contracts, self.cfg.max_order_size_contracts)
                return SniperSignal(
                    ticker=market.ticker,
                    direction="sell_yes",
                    price=price,
                    size=size,
                    edge_cents=edge,
                    fair_cents=fair,
                )

        return None

    async def execute(self, signal: SniperSignal) -> None:
        """Fire an IOC order for the sniper signal."""
        if signal.direction == "buy_yes":
            action, side = "buy", "yes"
        else:
            action, side = "sell", "yes"

        log.info(
            "SNIPER %s %s %d@%dc (fair=%dc edge=%.1fc)",
            signal.direction,
            signal.ticker,
            signal.size,
            signal.price,
            signal.fair_cents,
            signal.edge_cents,
        )
        resp = await self.order_mgr.place_order(
            ticker=signal.ticker,
            side=side,
            action=action,
            price_cents=signal.price,
            size=signal.size,
            tif=self.cfg.sniper_order_tif,
        )
        self._set_cooldown(signal.ticker)
        metrics.inc("sniper_shots")
        if resp and resp.taker_fees > 0:
            self.set_empirical_fee(resp.taker_fees / max(resp.count, 1) * 100)
