"""BTC spot price feed – pulls from public REST APIs (no key required)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from bot.infra.log import get_logger
from bot.infra.metrics import metrics
from bot.infra.time import now_ms

log = get_logger(__name__)


@dataclass
class SpotQuote:
    price: float
    timestamp_ms: int
    source: str


class BtcSpotFeed:
    """Async BTC spot price fetcher with simple rate limiter."""

    _MIN_INTERVAL_MS = 500  # at most 2 requests per second

    def __init__(self, source: str = "coinbase", symbol: str = "BTC-USD") -> None:
        self.source = source.lower()
        self.symbol = symbol
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._last_request_ms: int = 0
        self._last_quote: SpotQuote | None = None

    async def close(self) -> None:
        await self._client.aclose()

    async def _rate_limit(self) -> None:
        elapsed = now_ms() - self._last_request_ms
        if elapsed < self._MIN_INTERVAL_MS:
            await asyncio.sleep((self._MIN_INTERVAL_MS - elapsed) / 1000.0)
        self._last_request_ms = now_ms()

    async def get_spot(self) -> SpotQuote:
        """Fetch current BTC spot price."""
        await self._rate_limit()
        try:
            if self.source == "coinbase":
                return await self._fetch_coinbase()
            elif self.source == "coingecko":
                return await self._fetch_coingecko()
            else:
                return await self._fetch_coinbase()
        except Exception as exc:
            log.warning("Spot feed error (%s): %s", self.source, exc)
            metrics.inc("spot_feed_errors")
            if self._last_quote:
                return self._last_quote
            raise

    async def _fetch_coinbase(self) -> SpotQuote:
        resp = await self._client.get(
            f"https://api.coinbase.com/v2/prices/{self.symbol}/spot"
        )
        resp.raise_for_status()
        data = resp.json()
        price = float(data["data"]["amount"])
        q = SpotQuote(price=price, timestamp_ms=now_ms(), source="coinbase")
        self._last_quote = q
        metrics.gauge("btc_spot", price)
        return q

    async def _fetch_coingecko(self) -> SpotQuote:
        resp = await self._client.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin", "vs_currencies": "usd"},
        )
        resp.raise_for_status()
        data = resp.json()
        price = float(data["bitcoin"]["usd"])
        q = SpotQuote(price=price, timestamp_ms=now_ms(), source="coingecko")
        self._last_quote = q
        metrics.gauge("btc_spot", price)
        return q

    @property
    def last_price(self) -> float | None:
        return self._last_quote.price if self._last_quote else None

    @property
    def last_quote(self) -> SpotQuote | None:
        return self._last_quote

    def is_stale(self, max_age_ms: int = 5000) -> bool:
        if self._last_quote is None:
            return True
        return (now_ms() - self._last_quote.timestamp_ms) > max_age_ms
