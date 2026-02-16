"""Discover and filter tradeable BTC markets on Kalshi."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bot.config import BotConfig, get_config
from bot.infra.log import get_logger
from bot.infra.time import seconds_until, years_until
from bot.kalshi.models import Market
from bot.kalshi.rest import KalshiRestClient

log = get_logger(__name__)

_STRIKE_PATTERNS = [
    re.compile(r"(?:above|over|>=?)\s*\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"(?:below|under|<=?)\s*\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"(?:BTC|Bitcoin)\s*[><=]+\s*\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"\$?([\d]{4,}(?:,\d{3})*(?:\.\d+)?)\s*(?:or\s+(?:more|higher|above))", re.IGNORECASE),
]

_BTC_KEYWORDS = re.compile(r"bitcoin|btc", re.IGNORECASE)


@dataclass
class TradeableMarket:
    """A market that passed all filters and is ready for quoting."""

    ticker: str
    event_ticker: str
    title: str
    strike: float
    expiration_time: str
    time_to_expiry_sec: float
    time_to_expiry_years: float
    yes_bid: int
    yes_ask: int
    spread: int
    volume_24h: int
    open_interest: int


def parse_strike(title: str, subtitle: str = "") -> float | None:
    """Extract a numeric strike from market title / subtitle text."""
    text = f"{title} {subtitle}"
    for pat in _STRIKE_PATTERNS:
        m = pat.search(text)
        if m:
            raw = m.group(1).replace(",", "").replace("$", "")
            try:
                return float(raw)
            except ValueError:
                continue
    return None


def _is_btc_market(market: Market) -> bool:
    return bool(_BTC_KEYWORDS.search(market.title) or _BTC_KEYWORDS.search(market.subtitle))


class MarketDiscovery:
    """Scans Kalshi for tradeable BTC binary markets."""

    def __init__(
        self, rest: KalshiRestClient, cfg: BotConfig | None = None
    ) -> None:
        self.rest = rest
        self.cfg = cfg or get_config()
        self.tradeable: list[TradeableMarket] = []
        self._btc_series_tickers: set[str] = set()
        self._btc_event_tickers: set[str] = set()

    async def refresh_series(self) -> None:
        """Fetch crypto series + events, cache BTC-related tickers."""
        try:
            series_list = await self.rest.get_series(
                category=self.cfg.series_category,
                tags=self.cfg.series_tags,
            )
        except Exception as exc:
            log.warning("Failed to fetch series: %s", exc)
            series_list = []

        for s in series_list:
            if _BTC_KEYWORDS.search(s.title) or any(
                _BTC_KEYWORDS.search(t) for t in s.tags
            ):
                self._btc_series_tickers.add(s.series_ticker)

        for st in list(self._btc_series_tickers):
            try:
                events = await self.rest.get_events(series_ticker=st)
                for ev in events:
                    self._btc_event_tickers.add(ev.event_ticker)
            except Exception as exc:
                log.warning("Failed to fetch events for %s: %s", st, exc)

        log.info(
            "BTC series=%d  events=%d",
            len(self._btc_series_tickers),
            len(self._btc_event_tickers),
        )

    async def discover(self) -> list[TradeableMarket]:
        """Full scan: fetch markets, filter, extract strikes, return tradeable list."""
        all_markets = await self.rest.get_all_markets(status=self.cfg.market_status)

        candidates: list[Market] = []
        for m in all_markets:
            if m.event_ticker in self._btc_event_tickers or _is_btc_market(m):
                candidates.append(m)

        log.info("BTC candidate markets: %d / %d total", len(candidates), len(all_markets))

        tradeable: list[TradeableMarket] = []
        for m in candidates:
            tte_sec = seconds_until(m.expiration_time or m.close_time)
            tte_yr = years_until(m.expiration_time or m.close_time)

            if tte_sec < self.cfg.no_trade_window_seconds:
                continue

            strike = parse_strike(m.title, m.subtitle)
            if strike is None:
                continue

            spread = (m.yes_ask - m.yes_bid) if m.yes_bid > 0 and m.yes_ask > 0 else 0
            if spread < self.cfg.min_spread_cents or spread > self.cfg.max_spread_cents:
                continue

            tradeable.append(
                TradeableMarket(
                    ticker=m.ticker,
                    event_ticker=m.event_ticker,
                    title=m.title,
                    strike=strike,
                    expiration_time=m.expiration_time or m.close_time,
                    time_to_expiry_sec=tte_sec,
                    time_to_expiry_years=tte_yr,
                    yes_bid=m.yes_bid,
                    yes_ask=m.yes_ask,
                    spread=spread,
                    volume_24h=m.volume_24h,
                    open_interest=m.open_interest,
                )
            )

        self.tradeable = tradeable
        log.info("Tradeable BTC markets after filters: %d", len(tradeable))
        return tradeable

    @property
    def tickers(self) -> list[str]:
        return [m.ticker for m in self.tradeable]
