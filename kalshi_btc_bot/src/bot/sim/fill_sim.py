"""Simulated fill logic for paper trading."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class SimFill:
    ticker: str
    side: str
    action: str
    price_cents: int
    size: int
    fee_dollars: float


class FillSimulator:
    """Determines whether a resting paper order would be filled.

    Parameters
    ----------
    fill_probability : probability a maker order fills when the market crosses.
    taker_fee_bps : fee in basis points of notional for taker orders.
    maker_fee_bps : fee in basis points of notional for maker orders.
    """

    def __init__(
        self,
        fill_probability: float = 0.6,
        taker_fee_bps: float = 200,
        maker_fee_bps: float = 100,
    ) -> None:
        self.fill_probability = fill_probability
        self.taker_fee_bps = taker_fee_bps
        self.maker_fee_bps = maker_fee_bps

    def check_maker_fill(
        self,
        ticker: str,
        side: str,
        action: str,
        order_price: int,
        order_size: int,
        yes_bid: int,
        yes_ask: int,
    ) -> SimFill | None:
        """Check if a resting maker order would be filled given current book.

        For a buy-YES order at `order_price`, the order fills if yes_ask <= order_price.
        For a sell-YES order at `order_price`, the order fills if yes_bid >= order_price.
        """
        crossed = False
        if action == "buy" and side == "yes":
            crossed = yes_ask > 0 and yes_ask <= order_price
        elif action == "sell" and side == "yes":
            crossed = yes_bid > 0 and yes_bid >= order_price
        elif action == "buy" and side == "no":
            crossed = (100 - yes_bid) > 0 and (100 - yes_bid) <= order_price
        elif action == "sell" and side == "no":
            crossed = (100 - yes_ask) > 0 and (100 - yes_ask) >= order_price

        if not crossed:
            return None

        if random.random() > self.fill_probability:
            return None

        notional = order_price * order_size / 100.0
        fee = notional * self.maker_fee_bps / 10_000.0

        return SimFill(
            ticker=ticker,
            side=side,
            action=action,
            price_cents=order_price,
            size=order_size,
            fee_dollars=fee,
        )

    def compute_taker_fill(
        self,
        ticker: str,
        side: str,
        action: str,
        price_cents: int,
        size: int,
    ) -> SimFill:
        """Immediate taker fill (IOC) – always fills at given price."""
        notional = price_cents * size / 100.0
        fee = notional * self.taker_fee_bps / 10_000.0
        return SimFill(
            ticker=ticker,
            side=side,
            action=action,
            price_cents=price_cents,
            size=size,
            fee_dollars=fee,
        )
