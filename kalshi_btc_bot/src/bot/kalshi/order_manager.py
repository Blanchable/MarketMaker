"""Order lifecycle manager – tracks open orders and handles cancel/replace."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from bot.config import BotConfig, get_config
from bot.infra.log import get_logger
from bot.infra.metrics import metrics
from bot.infra.time import now_ms
from bot.kalshi.models import OrderRequest, OrderResponse
from bot.kalshi.rest import KalshiRestClient

log = get_logger(__name__)


@dataclass
class LiveOrder:
    """Tracks a resting order on Kalshi."""

    order_id: str
    client_order_id: str
    ticker: str
    side: str  # "yes" or "no"
    action: str  # "buy" or "sell"
    price_cents: int
    size: int
    placed_ms: int = 0
    post_only: bool = False


class OrderManager:
    """Manages open orders per market, enforcing at most 1 bid + 1 ask per ticker."""

    def __init__(self, rest: KalshiRestClient, cfg: BotConfig | None = None) -> None:
        self.rest = rest
        self.cfg = cfg or get_config()
        self._open: dict[str, LiveOrder] = {}  # order_id -> LiveOrder
        self._bid_by_ticker: dict[str, str] = {}  # ticker -> order_id
        self._ask_by_ticker: dict[str, str] = {}  # ticker -> order_id

    @property
    def open_orders(self) -> dict[str, LiveOrder]:
        return dict(self._open)

    def get_bid(self, ticker: str) -> LiveOrder | None:
        oid = self._bid_by_ticker.get(ticker)
        return self._open.get(oid) if oid else None

    def get_ask(self, ticker: str) -> LiveOrder | None:
        oid = self._ask_by_ticker.get(ticker)
        return self._open.get(oid) if oid else None

    async def place_order(
        self,
        ticker: str,
        side: str,
        action: str,
        price_cents: int,
        size: int,
        *,
        post_only: bool = False,
        tif: str | None = None,
    ) -> OrderResponse | None:
        """Place an order and track it internally."""
        coid = str(uuid.uuid4())
        req = OrderRequest(
            ticker=ticker,
            client_order_id=coid,
            side=side,
            action=action,
            count=min(size, self.cfg.max_order_size_contracts),
            type="limit",
        )
        if side == "yes":
            req.yes_price = price_cents
        else:
            req.no_price = price_cents

        if tif == "immediate_or_cancel":
            req.expiration_time = None  # IOC handled via TIF in some APIs
        try:
            resp = await self.rest.place_order(req)
        except Exception as exc:
            log.error("Failed to place order: %s", exc)
            return None

        if resp.order_id:
            lo = LiveOrder(
                order_id=resp.order_id,
                client_order_id=coid,
                ticker=ticker,
                side=side,
                action=action,
                price_cents=price_cents,
                size=size,
                placed_ms=now_ms(),
                post_only=post_only,
            )
            self._open[resp.order_id] = lo
            if action == "buy" and side == "yes":
                self._bid_by_ticker[ticker] = resp.order_id
            elif action == "sell" and side == "yes":
                self._ask_by_ticker[ticker] = resp.order_id
            elif action == "buy" and side == "no":
                self._ask_by_ticker[ticker] = resp.order_id
            elif action == "sell" and side == "no":
                self._bid_by_ticker[ticker] = resp.order_id

        return resp

    async def cancel_order(self, order_id: str) -> bool:
        lo = self._open.pop(order_id, None)
        if lo:
            for d in (self._bid_by_ticker, self._ask_by_ticker):
                if d.get(lo.ticker) == order_id:
                    del d[lo.ticker]
        try:
            await self.rest.cancel_order(order_id)
            return True
        except Exception as exc:
            log.warning("Cancel order %s failed: %s", order_id, exc)
            return False

    async def cancel_market(self, ticker: str) -> None:
        to_cancel = [oid for oid, lo in self._open.items() if lo.ticker == ticker]
        for oid in to_cancel:
            await self.cancel_order(oid)

    async def cancel_all(self) -> None:
        oids = list(self._open.keys())
        for oid in oids:
            await self.cancel_order(oid)
        log.info("Cancelled all %d tracked orders", len(oids))

    async def cancel_and_replace(
        self,
        ticker: str,
        is_bid: bool,
        side: str,
        action: str,
        new_price: int,
        size: int,
        *,
        post_only: bool = True,
    ) -> OrderResponse | None:
        """Cancel existing quote on one side and replace with new price."""
        existing_oid = (self._bid_by_ticker if is_bid else self._ask_by_ticker).get(ticker)
        if existing_oid:
            existing = self._open.get(existing_oid)
            if existing and existing.price_cents == new_price and existing.size == size:
                return None  # no change needed
            await self.cancel_order(existing_oid)
        return await self.place_order(
            ticker, side, action, new_price, size, post_only=post_only
        )

    def should_requote(self, ticker: str, is_bid: bool, desired_price: int) -> bool:
        """Check if resting order differs from desired price or is stale."""
        existing_oid = (self._bid_by_ticker if is_bid else self._ask_by_ticker).get(ticker)
        if not existing_oid:
            return True
        existing = self._open.get(existing_oid)
        if not existing:
            return True
        if abs(existing.price_cents - desired_price) >= 1:
            return True
        if (now_ms() - existing.placed_ms) > self.cfg.mm_cancel_requote_ms:
            return True
        return False

    async def reconcile(self) -> None:
        """Sync internal state with Kalshi portfolio orders."""
        try:
            live = await self.rest.get_orders(status="resting")
        except Exception as exc:
            log.warning("Reconcile failed: %s", exc)
            return
        live_ids = {o.order_id for o in live}
        stale = [oid for oid in self._open if oid not in live_ids]
        for oid in stale:
            lo = self._open.pop(oid, None)
            if lo:
                for d in (self._bid_by_ticker, self._ask_by_ticker):
                    if d.get(lo.ticker) == oid:
                        del d[lo.ticker]
        if stale:
            log.info("Reconcile removed %d stale tracked orders", len(stale))
