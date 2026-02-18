"""Market-making strategy – edge-based quoting with TP/SL exit lock."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from bot.config import BotConfig, get_config
from bot.infra.log import get_logger
from bot.infra.metrics import metrics
from bot.infra.time import now_ms
from bot.kalshi.market_discovery import TradeableMarket
from bot.kalshi.order_manager import OrderManager
from bot.kalshi.ws import MarketState
from bot.pricing.fair_value import FairValue
from bot.strategy.risk import RiskEngine, RiskSnapshot

log = get_logger(__name__)


@dataclass
class QuotePair:
    ticker: str
    bid_price: int
    ask_price: int
    size: int


@dataclass
class TrackedFill:
    ticker: str
    side: str
    entry_price: float
    quantity: int


@dataclass
class TickerStats:
    buys: int = 0
    sells: int = 0
    buy_value: float = 0.0
    sell_value: float = 0.0
    round_trips: int = 0
    round_trip_pnl: float = 0.0


def compute_quotes(
    market: TradeableMarket,
    ws: MarketState,
    fv: FairValue,
    net_position: int,
    max_size: int,
    cfg: BotConfig | None = None,
) -> QuotePair | None:
    """Compute bid/ask using mm_edge_cents from config."""
    cfg = cfg or get_config()

    fair = fv.fair_cents
    yes_bid = ws.yes_bid
    yes_ask = ws.yes_ask
    if yes_bid <= 0 or yes_ask <= 0:
        return None

    edge = cfg.mm_edge_cents

    # Inventory skew
    skew = 0
    if net_position != 0:
        skew = round(net_position * cfg.mm_inventory_skew)

    # Edge-based quotes around fair value
    desired_bid = fair - edge - skew
    desired_ask = fair + edge - skew

    # Post-only guard: never cross the book
    desired_bid = min(desired_bid, yes_ask - 1)
    desired_ask = max(desired_ask, yes_bid + 1)

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
    """Edge-based two-sided quoting with TP/SL exit lock and diagnostics."""

    def __init__(
        self,
        order_mgr: OrderManager,
        risk: RiskEngine,
        cfg: BotConfig | None = None,
    ) -> None:
        self.order_mgr = order_mgr
        self.risk = risk
        self.cfg = cfg or get_config()
        self._entries: dict[str, TrackedFill] = {}
        self._exit_lock_until: dict[str, int] = {}  # ticker -> epoch ms
        self._stats: dict[str, TickerStats] = defaultdict(TickerStats)
        self._last_diag_ts: float = 0.0

    # ── Entry / exit tracking ────────────────────────────────────────

    def record_entry(self, ticker: str, price_cents: int, quantity: int) -> None:
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
                ticker=ticker, side="yes",
                entry_price=float(price_cents), quantity=quantity,
            )
        st = self._stats[ticker]
        st.buys += quantity
        st.buy_value += price_cents * quantity

    def record_exit(self, ticker: str, quantity: int, sell_price: float = 0) -> None:
        entry = self._entries.get(ticker)
        if entry:
            st = self._stats[ticker]
            st.sells += quantity
            st.sell_value += sell_price * quantity

            old_qty = entry.quantity
            entry.quantity = max(0, entry.quantity - quantity)
            if entry.quantity == 0:
                # Round trip completed
                if st.buys > 0 and st.sells > 0:
                    avg_buy = st.buy_value / st.buys if st.buys else 0
                    avg_sell = st.sell_value / st.sells if st.sells else 0
                    capture = avg_sell - avg_buy
                    st.round_trips += 1
                    st.round_trip_pnl += capture * min(st.buys, st.sells) / 100.0
                del self._entries[ticker]

    def seed_entry_from_position(self, ticker: str, position_qty: int, mid_price: float) -> None:
        if position_qty > 0 and ticker not in self._entries:
            self._entries[ticker] = TrackedFill(
                ticker=ticker, side="yes",
                entry_price=mid_price, quantity=position_qty,
            )

    def _is_exit_locked(self, ticker: str) -> bool:
        deadline = self._exit_lock_until.get(ticker, 0)
        return now_ms() < deadline

    def _set_exit_lock(self, ticker: str) -> None:
        self._exit_lock_until[ticker] = now_ms() + self.cfg.mm_exit_cooldown_ms

    # ── TP/SL exit ───────────────────────────────────────────────────

    async def check_exit(
        self,
        ticker: str,
        ws: MarketState,
    ) -> bool:
        """Check TP/SL, cancel quotes first, exit at best bid, lock ticker."""
        entry = self._entries.get(ticker)
        if not entry or entry.quantity <= 0 or entry.entry_price <= 0:
            return False

        current_mid = ws.mid
        if current_mid <= 0:
            return False

        tp_cents = self.cfg.mm_take_profit_cents
        sl_cents = self.cfg.mm_stop_loss_cents
        diff = current_mid - entry.entry_price

        # Check if a resting ask already meets TP (let it fill naturally)
        if tp_cents > 0 and diff >= tp_cents:
            ask_order = self.order_mgr.get_ask(ticker)
            if ask_order and ask_order.price_cents >= entry.entry_price + tp_cents:
                return False  # profitable ask is resting, let it fill

        exit_reason = ""
        if tp_cents > 0 and diff >= tp_cents:
            exit_reason = "TAKE-PROFIT"
        elif sl_cents > 0 and diff <= -sl_cents:
            exit_reason = "STOP-LOSS"

        if not exit_reason:
            return False

        # Cancel resting quotes first
        await self.order_mgr.cancel_market(ticker)

        # Exit at best bid (taker, ensures fill)
        sell_price = max(1, ws.yes_bid if ws.yes_bid > 0 else round(current_mid))

        log.info(
            "MM %s: %s entry=%.1fc mid=%.1fc diff=%+.1fc -> sell %d@%dc (best_bid=%d)",
            exit_reason, ticker,
            entry.entry_price, current_mid, diff,
            entry.quantity, sell_price, ws.yes_bid,
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
            self.record_exit(ticker, entry.quantity, sell_price)

        # Lock quoting for this ticker
        self._set_exit_lock(ticker)
        return True

    # ── Main update ──────────────────────────────────────────────────

    async def update(
        self,
        market: TradeableMarket,
        ws: MarketState,
        fv: FairValue,
        net_position: int,
        risk_snap: RiskSnapshot,
    ) -> None:
        if not self.cfg.mm_enabled:
            return

        ticker = market.ticker

        # Exit lock: do not quote while cooling down after TP/SL
        if self._is_exit_locked(ticker):
            return

        # Check TP/SL on existing positions first
        if net_position > 0 and ws.mid > 0:
            self.seed_entry_from_position(ticker, net_position, ws.mid)
            exited = await self.check_exit(ticker, ws)
            if exited:
                return

        # Compute and place/update quotes
        max_contracts = self.risk.max_additional_contracts(ticker, risk_snap)
        quotes = compute_quotes(market, ws, fv, net_position, max_contracts, self.cfg)

        if quotes is None:
            await self.order_mgr.cancel_market(ticker)
            return

        # Post-only guard: do not let bid cross best ask
        if ws.yes_ask > 0 and quotes.bid_price >= ws.yes_ask:
            quotes.bid_price = ws.yes_ask - 1
        if ws.yes_bid > 0 and quotes.ask_price <= ws.yes_bid:
            quotes.ask_price = ws.yes_bid + 1
        if quotes.bid_price >= quotes.ask_price:
            return

        if self.order_mgr.should_requote(ticker, is_bid=True, desired_price=quotes.bid_price):
            await self.order_mgr.cancel_and_replace(
                ticker=ticker, is_bid=True,
                side="yes", action="buy",
                new_price=quotes.bid_price, size=quotes.size,
                post_only=True,
            )
            metrics.inc("mm_bid_updates")

        if self.order_mgr.should_requote(ticker, is_bid=False, desired_price=quotes.ask_price):
            await self.order_mgr.cancel_and_replace(
                ticker=ticker, is_bid=False,
                side="yes", action="sell",
                new_price=quotes.ask_price, size=quotes.size,
                post_only=True,
            )
            metrics.inc("mm_ask_updates")

    # ── Diagnostics ──────────────────────────────────────────────────

    def log_diagnostics(self) -> None:
        now = time.time()
        if now - self._last_diag_ts < 60:
            return
        self._last_diag_ts = now

        cancel_counts = self.order_mgr.get_cancel_counts()
        if cancel_counts:
            top = sorted(cancel_counts.items(), key=lambda x: -x[1])[:5]
            churn = ", ".join(f"{t}={c}" for t, c in top)
            log.info("MM churn (cancels/min): %s", churn)

        active_entries = [(t, e) for t, e in self._entries.items() if e.quantity > 0]
        if active_entries:
            summary = ", ".join(f"{t}:{e.quantity}@{e.entry_price:.0f}c" for t, e in active_entries[:10])
            log.info("MM positions: %s%s", summary, " ..." if len(active_entries) > 10 else "")

        rts = [(t, s) for t, s in self._stats.items() if s.round_trips > 0]
        if rts:
            total_rt = sum(s.round_trips for _, s in rts)
            total_pnl = sum(s.round_trip_pnl for _, s in rts)
            log.info(
                "MM spread capture: %d round trips, $%.4f total, $%.4f avg",
                total_rt, total_pnl, total_pnl / total_rt if total_rt else 0,
            )

    async def cancel_all(self) -> None:
        await self.order_mgr.cancel_all()
