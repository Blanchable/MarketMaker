"""Pydantic models for Kalshi API objects."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class Market(BaseModel):
    ticker: str
    event_ticker: str = ""
    title: str = ""
    subtitle: str = ""
    yes_sub_title: str = ""
    no_sub_title: str = ""
    status: str = ""
    close_time: str = ""
    expiration_time: str = ""
    expected_expiration_time: str = ""
    yes_bid: int = 0
    yes_ask: int = 0
    no_bid: int = 0
    no_ask: int = 0
    last_price: int = 0
    volume: int = 0
    volume_24h: int = 0
    open_interest: int = 0

    model_config = {"extra": "ignore"}


class OrderbookLevel(BaseModel):
    price: int
    quantity: int


class Orderbook(BaseModel):
    ticker: str = ""
    yes: list[OrderbookLevel] = Field(default_factory=list)
    no: list[OrderbookLevel] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class OrderRequest(BaseModel):
    ticker: str
    client_order_id: str
    side: str  # "yes" or "no"
    action: str  # "buy" or "sell"
    count: int
    type: str = "limit"
    yes_price: int | None = None
    no_price: int | None = None
    expiration_time: str | None = None
    sell_position_floor: int | None = None
    buy_max_cost: int | None = None

    model_config = {"extra": "ignore"}


class OrderResponse(BaseModel):
    order_id: str = ""
    client_order_id: str = ""
    ticker: str = ""
    status: str = ""
    side: str = ""
    action: str = ""
    yes_price: int = 0
    no_price: int = 0
    count: int = 0
    remaining_count: int = 0
    maker_fees: float = 0.0
    taker_fees: float = 0.0
    created_time: str = ""

    model_config = {"extra": "ignore"}


class Position(BaseModel):
    ticker: str = ""
    market_exposure: int = 0
    rest_exposure: int = 0
    fees_paid: float = 0.0
    total_traded: int = 0
    realized_pnl: float = 0.0
    position: int = 0  # positive = long YES, negative = short YES

    model_config = {"extra": "ignore"}


class Fill(BaseModel):
    trade_id: str = ""
    ticker: str = ""
    side: str = ""
    action: str = ""
    count: int = 0
    yes_price: int = 0
    no_price: int = 0
    is_taker: bool = False
    created_time: str = ""

    model_config = {"extra": "ignore"}


class Balance(BaseModel):
    balance: int = 0  # cents
    payout: int = 0

    model_config = {"extra": "ignore"}


class Series(BaseModel):
    series_ticker: str = ""
    title: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class Event(BaseModel):
    event_ticker: str = ""
    series_ticker: str = ""
    title: str = ""
    category: str = ""

    model_config = {"extra": "ignore"}
