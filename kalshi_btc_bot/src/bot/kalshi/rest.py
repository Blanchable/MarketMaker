"""Async Kalshi REST client with retry and rate-limit awareness."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from bot.config import BotConfig, get_config
from bot.infra.log import get_logger
from bot.infra.metrics import metrics
from bot.kalshi.auth import KalshiAuth
from bot.kalshi.models import (
    Balance,
    Event,
    Fill,
    Market,
    Orderbook,
    OrderRequest,
    OrderResponse,
    Position,
    Series,
)

log = get_logger(__name__)

_MAX_RETRIES = 4
_BACKOFF_BASE = 1.0


def _safe_list(data: dict[str, Any], key: str) -> list:
    """Return data[key] if it is a list, else empty list.

    Kalshi sometimes returns null for list-valued fields
    (e.g. ``{"series": null}``).  ``dict.get(key, [])`` would
    still return None in that case because the key *exists*.
    """
    val = data.get(key)
    if val is None:
        return []
    return val


class KalshiRestClient:
    """Async REST wrapper for the Kalshi Trade API v2."""

    def __init__(self, cfg: BotConfig | None = None) -> None:
        self.cfg = cfg or get_config()
        self._auth = KalshiAuth(self.cfg.kalshi_key_id, self.cfg.kalshi_private_key_path)
        self._client = httpx.AsyncClient(
            base_url=self.cfg.rest_base,
            timeout=httpx.Timeout(15.0),
            http2=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _path(self, endpoint: str) -> str:
        """Return the full path (without host) for signature."""
        return f"/trade-api/v2{endpoint}"

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = self._path(endpoint)
        if params:
            path_with_qs = f"{path}?{urlencode(params, doseq=True)}"
        else:
            path_with_qs = path

        for attempt in range(1, _MAX_RETRIES + 1):
            headers = self._auth.headers(method.upper(), path_with_qs)
            headers["Content-Type"] = "application/json"
            headers["Accept"] = "application/json"
            try:
                resp = await self._client.request(
                    method,
                    endpoint,
                    params=params,
                    json=json_body,
                    headers=headers,
                )
                metrics.inc("kalshi_rest_requests")
                if resp.status_code == 429 or resp.status_code >= 500:
                    wait = _BACKOFF_BASE * (2 ** (attempt - 1))
                    log.warning(
                        "Kalshi %s %s returned %s – retry %d in %.1fs",
                        method,
                        endpoint,
                        resp.status_code,
                        attempt,
                        wait,
                    )
                    metrics.inc("kalshi_rest_retries")
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError:
                raise
            except httpx.HTTPError as exc:
                wait = _BACKOFF_BASE * (2 ** (attempt - 1))
                log.warning("HTTP error %s – retry %d in %.1fs", exc, attempt, wait)
                await asyncio.sleep(wait)
        raise RuntimeError(f"Kalshi request failed after {_MAX_RETRIES} retries: {method} {endpoint}")

    # ── Market endpoints ─────────────────────────────────────────────

    async def get_markets(
        self,
        *,
        status: str = "open",
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        limit: int = 200,
        cursor: str | None = None,
    ) -> tuple[list[Market], str | None]:
        params: dict[str, Any] = {"status": status, "limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if cursor:
            params["cursor"] = cursor
        data = await self._request("GET", "/markets", params=params)
        markets = [Market.model_validate(m) for m in _safe_list(data, "markets")]
        next_cursor = data.get("cursor")
        return markets, next_cursor

    async def get_all_markets(self, **kwargs: Any) -> list[Market]:
        all_markets: list[Market] = []
        cursor: str | None = None
        while True:
            batch, cursor = await self.get_markets(cursor=cursor, **kwargs)
            all_markets.extend(batch)
            if not cursor or not batch:
                break
        return all_markets

    async def get_market(self, ticker: str) -> Market:
        data = await self._request("GET", f"/markets/{ticker}")
        return Market.model_validate(data.get("market", data))

    async def get_orderbook(self, ticker: str, depth: int = 10) -> Orderbook:
        data = await self._request(
            "GET", f"/markets/{ticker}/orderbook", params={"depth": depth}
        )
        return Orderbook.model_validate(data.get("orderbook", data))

    async def get_series(
        self, *, category: str | None = None, tags: str | None = None
    ) -> list[Series]:
        params: dict[str, Any] = {}
        if category:
            params["category"] = category
        if tags:
            params["tags"] = tags
        data = await self._request("GET", "/series", params=params)
        return [Series.model_validate(s) for s in _safe_list(data, "series")]

    async def get_events(
        self,
        *,
        series_ticker: str | None = None,
        limit: int = 200,
        cursor: str | None = None,
    ) -> list[Event]:
        params: dict[str, Any] = {"limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if cursor:
            params["cursor"] = cursor
        data = await self._request("GET", "/events", params=params)
        return [Event.model_validate(e) for e in _safe_list(data, "events")]

    # ── Order endpoints ──────────────────────────────────────────────

    async def place_order(self, req: OrderRequest) -> OrderResponse:
        body = req.model_dump(exclude_none=True)
        data = await self._request("POST", "/portfolio/orders", json_body=body)
        metrics.inc("kalshi_orders_placed")
        return OrderResponse.model_validate(data.get("order", data))

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        data = await self._request("DELETE", f"/portfolio/orders/{order_id}")
        metrics.inc("kalshi_orders_cancelled")
        return data

    async def get_orders(
        self,
        *,
        ticker: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[OrderResponse]:
        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if status:
            params["status"] = status
        data = await self._request("GET", "/portfolio/orders", params=params)
        return [OrderResponse.model_validate(o) for o in _safe_list(data, "orders")]

    async def cancel_all_orders(self) -> None:
        """Best-effort cancel of all open orders."""
        orders = await self.get_orders(status="resting")
        tasks = [self.cancel_order(o.order_id) for o in orders if o.order_id]
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            cancelled = sum(1 for r in results if not isinstance(r, Exception))
            log.info("Cancelled %d / %d orders", cancelled, len(tasks))

    # ── Portfolio endpoints ──────────────────────────────────────────

    async def get_balance(self) -> Balance:
        data = await self._request("GET", "/portfolio/balance")
        return Balance.model_validate(data)

    async def get_positions(self, *, limit: int = 200) -> list[Position]:
        data = await self._request(
            "GET", "/portfolio/positions", params={"limit": limit}
        )
        raw = _safe_list(data, "market_positions") or _safe_list(data, "positions")
        return [Position.model_validate(p) for p in raw]

    async def get_fills(
        self, *, ticker: str | None = None, limit: int = 100
    ) -> list[Fill]:
        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        data = await self._request("GET", "/portfolio/fills", params=params)
        return [Fill.model_validate(f) for f in _safe_list(data, "fills")]

    # ── Utility ──────────────────────────────────────────────────────

    @staticmethod
    def new_client_order_id() -> str:
        return str(uuid.uuid4())
