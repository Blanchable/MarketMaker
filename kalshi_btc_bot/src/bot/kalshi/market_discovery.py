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

# ── Strike extraction patterns ───────────────────────────────────────────────
# Ordered from most specific to least.  Applied against the combined
# title + subtitle text for each market.

_STRIKE_PATTERNS = [
    # "$78,750 or above"  /  "$54,749.99 or below"
    re.compile(
        r"\$?([\d,]+(?:\.\d+)?)\s+or\s+(?:above|more|higher)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\$?([\d,]+(?:\.\d+)?)\s+or\s+(?:below|less|lower)",
        re.IGNORECASE,
    ),
    # "$77,250 to 78,249.99"  –  range bracket → use midpoint
    re.compile(
        r"\$?([\d,]+(?:\.\d+)?)\s+to\s+\$?([\d,]+(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    # "above $50,000"  /  "over $50,000"
    re.compile(
        r"(?:above|over|>=?)\s*\$?([\d,]+(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    # "below $45,000"  /  "under $45,000"
    re.compile(
        r"(?:below|under|<=?)\s*\$?([\d,]+(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    # "BTC > 55,000.50"
    re.compile(
        r"(?:BTC|Bitcoin)\s*[><=]+\s*\$?([\d,]+(?:\.\d+)?)",
        re.IGNORECASE,
    ),
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


def _parse_number(raw: str) -> float:
    """Convert a string like '78,250.50' to a float."""
    return float(raw.replace(",", "").replace("$", ""))


def parse_strike(title: str, subtitle: str = "") -> float | None:
    """Extract a numeric strike from market title / subtitle text.

    For range brackets ("$77,250 to 78,249.99") we return the midpoint.
    """
    text = f"{title} {subtitle}"
    for pat in _STRIKE_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                if m.lastindex and m.lastindex >= 2:
                    # Range pattern – return midpoint
                    lo = _parse_number(m.group(1))
                    hi = _parse_number(m.group(2))
                    return (lo + hi) / 2.0
                return _parse_number(m.group(1))
            except (ValueError, IndexError):
                continue
    return None


def _is_btc_market(market: Market) -> bool:
    return bool(
        _BTC_KEYWORDS.search(market.title)
        or _BTC_KEYWORDS.search(market.subtitle)
        or _BTC_KEYWORDS.search(market.event_ticker)
        or _BTC_KEYWORDS.search(market.ticker)
    )


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
        # Strategy 1: try the /series endpoint (may return null on demo)
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

        # Strategy 2: scan /events directly for crypto category
        # This works even when /series returns null.
        try:
            events = await self.rest.get_events(limit=200)
            for ev in events:
                ev_text = f"{ev.title} {ev.category} {ev.event_ticker}"
                if _BTC_KEYWORDS.search(ev_text):
                    self._btc_event_tickers.add(ev.event_ticker)
                    if ev.series_ticker:
                        self._btc_series_tickers.add(ev.series_ticker)
        except Exception as exc:
            log.warning("Failed to fetch events: %s", exc)

        # Strategy 3: fetch events for any known series tickers
        for st in list(self._btc_series_tickers):
            try:
                events = await self.rest.get_events(series_ticker=st)
                for ev in events:
                    self._btc_event_tickers.add(ev.event_ticker)
            except Exception as exc:
                log.warning("Failed to fetch events for series %s: %s", st, exc)

        log.info(
            "BTC series=%d  events=%d",
            len(self._btc_series_tickers),
            len(self._btc_event_tickers),
        )

    async def discover(self) -> list[TradeableMarket]:
        """Fetch BTC markets by known event tickers (avoids scanning all 30k+ markets)."""
        candidates: list[Market] = []

        # Fetch markets only for known BTC event tickers (targeted, fast)
        for evt in list(self._btc_event_tickers):
            try:
                batch = await self.rest.get_all_markets(
                    status=self.cfg.market_status, event_ticker=evt,
                )
                candidates.extend(batch)
            except Exception as exc:
                log.warning("Failed to fetch markets for event %s: %s", evt, exc)

        # Deduplicate by ticker
        seen: set[str] = set()
        deduped: list[Market] = []
        for m in candidates:
            if m.ticker not in seen:
                seen.add(m.ticker)
                deduped.append(m)
        candidates = deduped

        log.info("BTC candidate markets: %d (from %d events)", len(candidates), len(self._btc_event_tickers))

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
