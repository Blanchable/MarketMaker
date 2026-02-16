"""Paper broker – simulates order placement and fills without touching Kalshi."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from bot.config import BotConfig, get_config
from bot.infra.log import get_logger
from bot.infra.time import now_ms
from bot.kalshi.models import OrderResponse, Position
from bot.sim.fill_sim import FillSimulator, SimFill
from bot.sim.recorder import Recorder

log = get_logger(__name__)


@dataclass
class PaperOrder:
    order_id: str
    client_order_id: str
    ticker: str
    side: str
    action: str
    price_cents: int
    size: int
    remaining: int
    post_only: bool = False
    tif: str = "gtc"
    placed_ms: int = 0


@dataclass
class PaperPosition:
    ticker: str
    quantity: int = 0  # positive = long YES
    avg_price: float = 0.0
    realized_pnl: float = 0.0
    fees_paid: float = 0.0


class PaperBroker:
    """Simulated broker for paper trading. Mimics OrderManager interface."""

    def __init__(
        self,
        recorder: Recorder | None = None,
        fill_sim: FillSimulator | None = None,
        cfg: BotConfig | None = None,
    ) -> None:
        self.cfg = cfg or get_config()
        self.recorder = recorder
        self.fill_sim = fill_sim or FillSimulator()
        self._orders: dict[str, PaperOrder] = {}
        self._positions: dict[str, PaperPosition] = {}
        self._balance_cents: int = int(self.cfg.start_bankroll_dollars * 100)
        self._fills: list[SimFill] = []

    @property
    def balance_cents(self) -> int:
        return self._balance_cents

    @property
    def positions(self) -> dict[str, PaperPosition]:
        return dict(self._positions)

    def get_position(self, ticker: str) -> PaperPosition:
        if ticker not in self._positions:
            self._positions[ticker] = PaperPosition(ticker=ticker)
        return self._positions[ticker]

    def place_order(
        self,
        ticker: str,
        side: str,
        action: str,
        price_cents: int,
        size: int,
        *,
        post_only: bool = False,
        tif: str = "gtc",
    ) -> PaperOrder:
        oid = str(uuid.uuid4())
        coid = str(uuid.uuid4())
        order = PaperOrder(
            order_id=oid,
            client_order_id=coid,
            ticker=ticker,
            side=side,
            action=action,
            price_cents=price_cents,
            size=min(size, self.cfg.max_order_size_contracts),
            remaining=min(size, self.cfg.max_order_size_contracts),
            post_only=post_only,
            tif=tif,
            placed_ms=now_ms(),
        )

        if tif == "immediate_or_cancel":
            fill = self.fill_sim.compute_taker_fill(ticker, side, action, price_cents, order.size)
            self._apply_fill(fill)
            order.remaining = 0
            if self.recorder:
                self.recorder.record_order(
                    coid, ticker, side, price_cents, order.size, "limit", "filled"
                )
        else:
            self._orders[oid] = order
            if self.recorder:
                self.recorder.record_order(
                    coid, ticker, side, price_cents, order.size, "limit", "resting"
                )

        return order

    def cancel_order(self, order_id: str) -> bool:
        return self._orders.pop(order_id, None) is not None

    def cancel_all(self) -> int:
        n = len(self._orders)
        self._orders.clear()
        return n

    def cancel_market(self, ticker: str) -> int:
        to_remove = [oid for oid, o in self._orders.items() if o.ticker == ticker]
        for oid in to_remove:
            del self._orders[oid]
        return len(to_remove)

    def check_fills(self, yes_bid: int, yes_ask: int, ticker: str) -> list[SimFill]:
        """Check all resting orders for a given market against current prices."""
        fills: list[SimFill] = []
        filled_oids: list[str] = []

        for oid, order in self._orders.items():
            if order.ticker != ticker:
                continue
            fill = self.fill_sim.check_maker_fill(
                order.ticker,
                order.side,
                order.action,
                order.price_cents,
                order.remaining,
                yes_bid,
                yes_ask,
            )
            if fill:
                self._apply_fill(fill)
                filled_oids.append(oid)
                fills.append(fill)

        for oid in filled_oids:
            del self._orders[oid]

        return fills

    def _apply_fill(self, fill: SimFill) -> None:
        """Update positions and balance from a fill."""
        pos = self.get_position(fill.ticker)
        self._fills.append(fill)

        direction = 1 if fill.action == "buy" else -1
        if fill.side == "no":
            direction *= -1

        qty = fill.size * direction
        cost = fill.price_cents * fill.size / 100.0 * (1 if fill.action == "buy" else -1)

        pos.quantity += qty
        pos.fees_paid += fill.fee_dollars
        self._balance_cents -= int(cost * 100)
        self._balance_cents -= int(fill.fee_dollars * 100)

        if self.recorder:
            self.recorder.record_fill(
                fill.ticker, fill.side, fill.price_cents, fill.size, fill.fee_dollars
            )

        log.info(
            "PAPER FILL: %s %s %s %d@%dc fee=$%.4f pos=%d",
            fill.action,
            fill.side,
            fill.ticker,
            fill.size,
            fill.price_cents,
            fill.fee_dollars,
            pos.quantity,
        )

    def to_position_models(self) -> list[Position]:
        result = []
        for pp in self._positions.values():
            result.append(
                Position(
                    ticker=pp.ticker,
                    position=pp.quantity,
                    realized_pnl=pp.realized_pnl,
                    fees_paid=pp.fees_paid,
                )
            )
        return result
