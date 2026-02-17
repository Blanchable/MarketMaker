"""Market-making strategy – provide two-sided liquidity with auto take-profit / stop-loss."""

from __future__ import annotations

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


@dataclass
class QuotePair:
    ticker: str
    bid_price: int  # cents, YES side
    ask_price: int  # cents, YES side
    size: int


@dataclass
class TrackedFill:
    """Records the average entry price for a position acquired via MM."""
    ticker: str
    side: str           # "yes"
    entry_price: float  # cents, weighted average
    quantity: int       # contracts held


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
    """Manages two-sided quotes with automatic take-profit / stop-loss exits."""

    def __init__(
        self,
        order_mgr: OrderManager,
        risk: RiskEngine,
        cfg: BotConfig | None = None,
    ) -> None:
        self.order_mgr = order_mgr
        self.risk = risk
        self.cfg = cfg or get_config()
        # ticker -> TrackedFill for positions entered via MM bids
        self._entries: dict[str, TrackedFill] = {}

    def record_entry(self, ticker: str, price_cents: int, quantity: int) -> None:
        """Record or update the average entry price when a MM bid is filled."""
        existing = self._entries.get(ticker)
        if existing and existing.quantity > 0:
            total_qty = existing.quantity + quantity
            existing.entry_price = (
                (existing.entry_price * existing.quantity + price_cents * quantity)
                / total_qty
            )
            existing.quantity = total_qty
        else:
            self._entries[ticker] = TrackedFill(
                ticker=ticker,
                side="yes",
                entry_price=float(price_cents),
                quantity=quantity,
            )
        log.info(
            "MM entry recorded: %s %d@%dc (avg=%.1fc qty=%d)",
            ticker, quantity, price_cents,
            self._entries[ticker].entry_price,
            self._entries[ticker].quantity,
        )

    def record_exit(self, ticker: str, quantity: int) -> None:
        """Reduce tracked position after a sell fill."""
        entry = self._entries.get(ticker)
        if entry:
            entry.quantity = max(0, entry.quantity - quantity)
            if entry.quantity == 0:
                del self._entries[ticker]

    def seed_entry_from_position(self, ticker: str, position_qty: int, mid_price: float) -> None:
        """Seed entry tracking from an existing portfolio position (e.g. on startup).

        Uses the current mid as the assumed entry price since we don't know
        the actual fill price for positions opened before this session.
        """
        if position_qty > 0 and ticker not in self._entries:
            self._entries[ticker] = TrackedFill(
                ticker=ticker,
                side="yes",
                entry_price=mid_price,
                quantity=position_qty,
            )

    async def check_exit(
        self,
        ticker: str,
        current_mid: float,
    ) -> bool:
        """Check if a held position should be exited (take-profit or stop-loss).

        Returns True if an exit order was sent.
        """
        entry = self._entries.get(ticker)
        if not entry or entry.quantity <= 0 or entry.entry_price <= 0:
            return False

        tp_pct = self.cfg.mm_take_profit_pct
        sl_pct = self.cfg.mm_stop_loss_pct

        pct_change = ((current_mid - entry.entry_price) / entry.entry_price) * 100.0

        exit_reason = ""
        if tp_pct > 0 and pct_change >= tp_pct:
            exit_reason = "TAKE-PROFIT"
        elif sl_pct > 0 and pct_change <= -sl_pct:
            exit_reason = "STOP-LOSS"

        if not exit_reason:
            return False

        sell_price = max(1, min(99, round(current_mid)))

        log.info(
            "MM %s: %s entry=%.1fc mid=%.1fc pct=%+.1f%% -> sell %d@%dc",
            exit_reason, ticker,
            entry.entry_price, current_mid, pct_change,
            entry.quantity, sell_price,
        )
        metrics.inc(f"mm_{exit_reason.lower().replace('-', '_')}")

        resp = await self.order_mgr.place_order(
            ticker=ticker,
            side="yes",
            action="sell",
            price_cents=sell_price,
            size=entry.quantity,
            tif="immediate_or_cancel",
        )

        if resp:
            self.record_exit(ticker, entry.quantity)

        return True

    async def update(
        self,
        market: TradeableMarket,
        ws: MarketState,
        fv: FairValue,
        net_position: int,
        risk_snap: RiskSnapshot,
    ) -> None:
        """Compute and send/update quotes for one market.

        Also checks take-profit / stop-loss for any held inventory.
        """
        if not self.cfg.mm_enabled:
            return

        # ── Check TP/SL on existing positions first ──────────────────
        if net_position > 0 and ws.mid > 0:
            self.seed_entry_from_position(market.ticker, net_position, ws.mid)
            exited = await self.check_exit(market.ticker, ws.mid)
            if exited:
                return  # position just exited, skip quoting this tick

        # ── Compute and place/update quotes ──────────────────────────
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
