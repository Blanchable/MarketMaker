"""Kalshi WebSocket client – real-time orderbook and fill streaming."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Coroutine

import websockets
import websockets.client

from bot.config import BotConfig, get_config
from bot.infra.log import get_logger
from bot.infra.metrics import metrics
from bot.infra.time import now_ms
from bot.kalshi.auth import KalshiAuth

log = get_logger(__name__)

Callback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class MarketState:
    """In-memory best-bid/ask + last update time for one market."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        self.yes_bid: int = 0
        self.yes_ask: int = 0
        self.last_update_ms: int = 0
        self.volume: int = 0

    def update(self, data: dict[str, Any]) -> None:
        if "yes_bid" in data:
            self.yes_bid = int(data["yes_bid"])
        if "yes_ask" in data:
            self.yes_ask = int(data["yes_ask"])
        if "volume" in data:
            self.volume = int(data["volume"])
        self.last_update_ms = now_ms()

    def is_stale(self, stale_ms: int) -> bool:
        if self.last_update_ms == 0:
            return True
        return (now_ms() - self.last_update_ms) > stale_ms

    @property
    def mid(self) -> float:
        if self.yes_bid > 0 and self.yes_ask > 0:
            return (self.yes_bid + self.yes_ask) / 2.0
        return 0.0

    @property
    def spread(self) -> int:
        if self.yes_bid > 0 and self.yes_ask > 0:
            return self.yes_ask - self.yes_bid
        return 0


class KalshiWsClient:
    """Manages a persistent WebSocket connection to Kalshi."""

    def __init__(self, cfg: BotConfig | None = None) -> None:
        self.cfg = cfg or get_config()
        self._auth = KalshiAuth(self.cfg.kalshi_key_id, self.cfg.kalshi_private_key_path)
        self._ws: websockets.client.WebSocketClientProtocol | None = None
        self._running = False
        self._market_states: dict[str, MarketState] = {}
        self._fill_callback: Callback | None = None
        self._tick_callback: Callback | None = None
        self._subscribed_tickers: set[str] = set()

    @property
    def market_states(self) -> dict[str, MarketState]:
        return self._market_states

    def set_fill_callback(self, cb: Callback) -> None:
        self._fill_callback = cb

    def set_tick_callback(self, cb: Callback) -> None:
        self._tick_callback = cb

    def get_state(self, ticker: str) -> MarketState | None:
        return self._market_states.get(ticker)

    def any_stale(self) -> bool:
        stale_ms = self.cfg.stale_ms
        return any(s.is_stale(stale_ms) for s in self._market_states.values())

    async def connect(self) -> None:
        ws_path = "/trade-api/ws/v2"
        headers = self._auth.headers("GET", ws_path)
        url = self.cfg.ws_url
        log.info("Connecting WS to %s", url)
        self._ws = await websockets.client.connect(
            url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        )
        self._running = True
        log.info("WS connected")

    async def subscribe(self, tickers: list[str]) -> None:
        if not self._ws:
            raise RuntimeError("WS not connected")
        new_tickers = [t for t in tickers if t not in self._subscribed_tickers]
        if not new_tickers:
            return
        for ticker in new_tickers:
            if ticker not in self._market_states:
                self._market_states[ticker] = MarketState(ticker)
            self._subscribed_tickers.add(ticker)

        msg = {
            "id": 1,
            "cmd": "subscribe",
            "params": {
                "channels": ["ticker", "orderbook_delta", "fill"],
                "market_tickers": new_tickers,
            },
        }
        await self._ws.send(json.dumps(msg))
        log.info("Subscribed to %d tickers", len(new_tickers))

    async def unsubscribe(self, tickers: list[str]) -> None:
        if not self._ws:
            return
        msg = {
            "id": 2,
            "cmd": "unsubscribe",
            "params": {
                "channels": ["ticker", "orderbook_delta", "fill"],
                "market_tickers": tickers,
            },
        }
        await self._ws.send(json.dumps(msg))
        for t in tickers:
            self._subscribed_tickers.discard(t)
            self._market_states.pop(t, None)

    async def listen(self) -> None:
        """Main receive loop – call in a task."""
        if not self._ws:
            raise RuntimeError("WS not connected")
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle(msg)
        except websockets.exceptions.ConnectionClosed as exc:
            log.warning("WS disconnected: %s", exc)
        finally:
            self._running = False

    async def _handle(self, msg: dict[str, Any]) -> None:
        msg_type = msg.get("type", "")
        channel = msg.get("channel", "")
        data = msg.get("msg", msg.get("data", {}))

        if channel == "ticker" or msg_type == "ticker":
            ticker = data.get("market_ticker", "")
            if ticker and ticker in self._market_states:
                self._market_states[ticker].update(data)
                metrics.inc("ws_ticker_updates")
                if self._tick_callback:
                    await self._tick_callback(data)

        elif channel == "orderbook_delta" or msg_type == "orderbook_delta":
            ticker = data.get("market_ticker", "")
            if ticker and ticker in self._market_states:
                self._market_states[ticker].update(data)
                metrics.inc("ws_orderbook_updates")

        elif channel == "fill" or msg_type == "fill":
            metrics.inc("ws_fill_updates")
            if self._fill_callback:
                await self._fill_callback(data)

    async def close(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None

    @property
    def is_connected(self) -> bool:
        return self._running and self._ws is not None and self._ws.open
