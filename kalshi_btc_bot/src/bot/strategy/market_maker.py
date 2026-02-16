"""Market-making strategy – provide two-sided liquidity in BTC binary markets."""

from __future__ import annotations

from dataclasses import dataclass

from bot.config import BotConfig, get_config
from bot.infra.log import get_logger
from bot.infra.metrics import metrics
from bot.kalshi.market_discovery import TradeableMarket
from bot.kalshi.order_manager import OrderManager
from bot.kalshi.ws import MarketState
from bot.pricing.fair_value import FairValue
from bot.strategy.risk import RiskEngine, RiskSnapshot

log = get_logger(__name__)


@dataclass
class QuotePair:
    ticker: str
    bid_price: int  # cents, YES side
    ask_price: int  # cents, YES side
    size: int


def compute_quotes(
    market: TradeableMarket,
    ws: MarketState,
    fv: FairValue,
    net_position: int,
    max_size: int,
    cfg: BotConfig | None = None,
) -> QuotePair | None:
    """Determine desired bid/ask for one market.

    Returns None if no valid quote can be computed.
    """
    cfg = cfg or get_config()

    fair = fv.fair_cents
    yes_bid = ws.yes_bid
    yes_ask = ws.yes_ask
    if yes_bid <= 0 or yes_ask <= 0:
        return None

    # Inventory skew: shift both bid and ask towards reducing inventory
    skew = 0
    if net_position != 0:
        skew = round(net_position * cfg.mm_inventory_skew)

    # Desired quotes
    desired_bid = min(yes_ask - 1, fair - 1) - skew
    desired_ask = max(yes_bid + 1, fair + 1) - skew

    # Clamp to valid range
    desired_bid = max(1, min(98, desired_bid))
    desired_ask = max(2, min(99, desired_ask))

    # Ensure bid < ask
    if desired_bid >= desired_ask:
        mid = (desired_bid + desired_ask) // 2
        desired_bid = max(1, mid - 1)
        desired_ask = min(99, mid + 1)

    if desired_bid >= desired_ask:
        return None

    size = min(cfg.mm_quote_size_contracts, max_size)
    if size <= 0:
        return None

    return QuotePair(
        ticker=market.ticker,
        bid_price=desired_bid,
        ask_price=desired_ask,
        size=size,
    )


class MarketMakerStrategy:
    """Manages two-sided quotes across all tradeable BTC markets."""

    def __init__(
        self,
        order_mgr: OrderManager,
        risk: RiskEngine,
        cfg: BotConfig | None = None,
    ) -> None:
        self.order_mgr = order_mgr
        self.risk = risk
        self.cfg = cfg or get_config()

    async def update(
        self,
        market: TradeableMarket,
        ws: MarketState,
        fv: FairValue,
        net_position: int,
        risk_snap: RiskSnapshot,
    ) -> None:
        """Compute and send/update quotes for one market."""
        if not self.cfg.mm_enabled:
            return

        max_contracts = self.risk.max_additional_contracts(market.ticker, risk_snap)
        quotes = compute_quotes(market, ws, fv, net_position, max_contracts, self.cfg)

        if quotes is None:
            await self.order_mgr.cancel_market(market.ticker)
            return

        # Update bid (buy YES)
        if self.order_mgr.should_requote(market.ticker, is_bid=True, desired_price=quotes.bid_price):
            await self.order_mgr.cancel_and_replace(
                ticker=market.ticker,
                is_bid=True,
                side="yes",
                action="buy",
                new_price=quotes.bid_price,
                size=quotes.size,
                post_only=True,
            )
            metrics.inc("mm_bid_updates")

        # Update ask (sell YES)
        if self.order_mgr.should_requote(market.ticker, is_bid=False, desired_price=quotes.ask_price):
            await self.order_mgr.cancel_and_replace(
                ticker=market.ticker,
                is_bid=False,
                side="yes",
                action="sell",
                new_price=quotes.ask_price,
                size=quotes.size,
                post_only=True,
            )
            metrics.inc("mm_ask_updates")

    async def cancel_all(self) -> None:
        await self.order_mgr.cancel_all()
