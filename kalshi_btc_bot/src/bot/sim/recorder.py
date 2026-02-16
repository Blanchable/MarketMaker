"""Records ticks, orders, fills, and PnL snapshots to SQLite."""

from __future__ import annotations

import json
from typing import Any

from bot.infra.log import get_logger
from bot.infra.storage import Storage
from bot.infra.time import now_ms

log = get_logger(__name__)


class Recorder:
    """Persists all trading activity to SQLite for replay and analysis."""

    def __init__(self, db_path: str = "bot_data.db") -> None:
        self.storage = Storage(db_path)

    def open(self) -> None:
        self.storage.open()

    def close(self) -> None:
        self.storage.close()

    def record_tick(
        self,
        ticker: str,
        yes_bid: int,
        yes_ask: int,
        volume: int = 0,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.storage.insert(
            "ticks",
            {
                "ts_ms": now_ms(),
                "ticker": ticker,
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "volume": volume,
                "payload": json.dumps(extra) if extra else None,
            },
        )

    def record_market(
        self,
        ticker: str,
        title: str,
        strike: float | None,
        expiration: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.storage.insert(
            "markets",
            {
                "ts_ms": now_ms(),
                "ticker": ticker,
                "title": title,
                "strike": strike,
                "expiration": expiration,
                "payload": json.dumps(extra) if extra else None,
            },
        )

    def record_order(
        self,
        client_order_id: str,
        ticker: str,
        side: str,
        price_cents: int,
        size: int,
        order_type: str = "limit",
        status: str = "submitted",
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.storage.insert(
            "orders",
            {
                "ts_ms": now_ms(),
                "client_order_id": client_order_id,
                "ticker": ticker,
                "side": side,
                "price_cents": price_cents,
                "size": size,
                "order_type": order_type,
                "status": status,
                "payload": json.dumps(extra) if extra else None,
            },
        )

    def record_fill(
        self,
        ticker: str,
        side: str,
        price_cents: int,
        size: int,
        fee_dollars: float = 0.0,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.storage.insert(
            "fills",
            {
                "ts_ms": now_ms(),
                "ticker": ticker,
                "side": side,
                "price_cents": price_cents,
                "size": size,
                "fee_dollars": fee_dollars,
                "payload": json.dumps(extra) if extra else None,
            },
        )

    def record_pnl(
        self,
        realized: float,
        unrealized: float,
        gross_exposure: float,
        net_exposure: float,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.storage.insert(
            "pnl",
            {
                "ts_ms": now_ms(),
                "realized": realized,
                "unrealized": unrealized,
                "gross_exposure": gross_exposure,
                "net_exposure": net_exposure,
                "payload": json.dumps(extra) if extra else None,
            },
        )
