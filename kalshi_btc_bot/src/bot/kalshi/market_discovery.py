"""Discover and filter tradeable BTC markets on Kalshi."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from bot.config import BotConfig, get_config
from bot.infra.log import get_logger
from bot.infra.time import seconds_until, years_until
from bot.kalshi.models import Market
from bot.kalshi.rest import KalshiRestClient

log = get_logger(__name__)

_BTC_KEYWORDS = re.compile(r"bitcoin|btc", re.IGNORECASE)


# ── StrikeInfo ───────────────────────────────────────────────────────────────

@dataclass
class StrikeInfo:
    """Parsed strike information from a market title/subtitle."""
    lower: float | None = None
    upper: float | None = None
    kind: str = "unknown"  # "above", "below", "range", "unknown"

    @property
    def strike(self) -> float | None:
        if self.kind == "above" and self.lower is not None:
            return self.lower
        if self.kind == "below" and self.upper is not None:
            return self.upper
        if self.kind == "range" and self.lower is not None and self.upper is not None:
            return (self.lower + self.upper) / 2.0
        return self.lower or self.upper


# ── Number parsing helpers ───────────────────────────────────────────────────

def _expand_k(text: str) -> str:
    """Replace k/K suffix with 000, handling decimals like 67.5k -> 67500."""
    def _repl(m: re.Match) -> str:
        num_str = m.group(1)
        if "." in num_str:
            return str(int(float(num_str) * 1000))
        return num_str + "000"
    return re.sub(r"(\d+(?:\.\d+)?)k\b", _repl, text, flags=re.IGNORECASE)


def _parse_number(raw: str) -> float:
    """Convert a string like '78,250.50' or '67500' to a float."""
    cleaned = raw.replace(",", "").replace("$", "").strip()
    return float(cleaned)


# ── Strike extraction patterns ───────────────────────────────────────────────

_PATTERNS_ABOVE = [
    re.compile(r"\$?([\d,]+(?:\.\d+)?)\s+or\s+(?:above|more|higher)", re.IGNORECASE),
    re.compile(r"(?:above|over|>=?)\s*\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"(?:BTC|Bitcoin)\s*(?:>=?|>)\s*\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE),
]

_PATTERNS_BELOW = [
    re.compile(r"\$?([\d,]+(?:\.\d+)?)\s+or\s+(?:below|less|lower)", re.IGNORECASE),
    re.compile(r"(?:below|under|<=?)\s*\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"(?:BTC|Bitcoin)\s*(?:<=?|<)\s*\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE),
]

_PATTERNS_RANGE = [
    re.compile(r"\$?([\d,]+(?:\.\d+)?)\s+to\s+\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"\$?([\d,]+(?:\.\d+)?)\s*[-–]\s*\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"between\s+\$?([\d,]+(?:\.\d+)?)\s+and\s+\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE),
]


def parse_strike(title: str, subtitle: str = "") -> float | None:
    """Extract a numeric strike. Returns the float value or None."""
    info = parse_strike_info(title, subtitle)
    return info.strike


def parse_strike_info(title: str, subtitle: str = "") -> StrikeInfo:
    """Full strike parser returning structured StrikeInfo."""
    text = _expand_k(f"{title} {subtitle}")

    # Try range patterns first (most specific)
    for pat in _PATTERNS_RANGE:
        m = pat.search(text)
        if m:
            try:
                lo = _parse_number(m.group(1))
                hi = _parse_number(m.group(2))
                return StrikeInfo(lower=lo, upper=hi, kind="range")
            except (ValueError, IndexError):
                continue

    # Above patterns
    for pat in _PATTERNS_ABOVE:
        m = pat.search(text)
        if m:
            try:
                val = _parse_number(m.group(1))
                return StrikeInfo(lower=val, kind="above")
            except (ValueError, IndexError):
                continue

    # Below patterns
    for pat in _PATTERNS_BELOW:
        m = pat.search(text)
        if m:
            try:
                val = _parse_number(m.group(1))
                return StrikeInfo(upper=val, kind="below")
            except (ValueError, IndexError):
                continue

    return StrikeInfo()


# ── TradeableMarket ──────────────────────────────────────────────────────────

@dataclass
class TradeableMarket:
    """A market that passed discovery filters and is ready for quoting."""
    ticker: str
    event_ticker: str
    title: str
    strike: float
    strike_kind: str = "unknown"
    expiration_time: str = ""
    time_to_expiry_sec: float = 0.0
    time_to_expiry_years: float = 0.0
    yes_bid: int = 0
    yes_ask: int = 0
    spread: int = 0
    volume_24h: int = 0
    open_interest: int = 0
    has_book: bool = False


def _is_btc_market(market: Market) -> bool:
    return bool(
        _BTC_KEYWORDS.search(market.title)
        or _BTC_KEYWORDS.search(market.subtitle)
        or _BTC_KEYWORDS.search(market.event_ticker)
        or _BTC_KEYWORDS.search(market.ticker)
    )


# ── MarketDiscovery ──────────────────────────────────────────────────────────

class MarketDiscovery:
    """Scans Kalshi for tradeable BTC binary markets."""

    def __init__(self, rest: KalshiRestClient, cfg: BotConfig | None = None) -> None:
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

        for st in list(self._btc_series_tickers):
            try:
                events = await self.rest.get_events(series_ticker=st)
                for ev in events:
                    self._btc_event_tickers.add(ev.event_ticker)
            except Exception as exc:
                log.warning("Failed to fetch events for series %s: %s", st, exc)

        log.info("BTC series=%d  events=%d", len(self._btc_series_tickers), len(self._btc_event_tickers))

    async def discover(self) -> list[TradeableMarket]:
        """Fetch BTC markets by known event tickers and apply filters with diagnostics."""
        candidates: list[Market] = []
        for evt in list(self._btc_event_tickers):
            try:
                batch = await self.rest.get_all_markets(
                    status=self.cfg.market_status, event_ticker=evt,
                )
                candidates.extend(batch)
            except Exception as exc:
                log.warning("Failed to fetch markets for event %s: %s", evt, exc)

        # Deduplicate
        seen: set[str] = set()
        deduped: list[Market] = []
        for m in candidates:
            if m.ticker not in seen:
                seen.add(m.ticker)
                deduped.append(m)
        candidates = deduped

        log.info("BTC candidate markets: %d (from %d events)", len(candidates), len(self._btc_event_tickers))

        is_demo = self.cfg.is_demo
        min_spread = self.cfg.effective_min_spread
        max_spread = self.cfg.effective_max_spread
        allow_missing_book = is_demo and self.cfg.demo_allow_missing_book

        # Filter with diagnostics
        diag: dict[str, int] = defaultdict(int)
        diag_samples: dict[str, list[str]] = defaultdict(list)
        tradeable: list[TradeableMarket] = []

        for m in candidates:
            tte_sec = seconds_until(m.expiration_time or m.close_time)
            tte_yr = years_until(m.expiration_time or m.close_time)

            # Filter: too close to expiry
            if tte_sec < self.cfg.no_trade_window_seconds:
                diag["too_close_to_expiry"] += 1
                if len(diag_samples["too_close_to_expiry"]) < 3:
                    diag_samples["too_close_to_expiry"].append(
                        f"ticker={m.ticker} tte={tte_sec:.0f}s"
                    )
                continue

            # Strike parsing
            strike_info = parse_strike_info(m.title, m.subtitle)
            strike_val = strike_info.strike

            if strike_val is None:
                if is_demo:
                    # In demo: keep as tradeable but with strike=0 and kind=unknown
                    strike_val = 0.0
                    diag["strike_unknown_kept"] += 1
                else:
                    diag["missing_strike"] += 1
                    if len(diag_samples["missing_strike"]) < 5:
                        diag_samples["missing_strike"].append(
                            f"ticker={m.ticker} title='{m.title[:50]}' sub='{m.subtitle[:50]}'"
                        )
                    continue

            # Book / spread check
            has_book = m.yes_bid > 0 and m.yes_ask > 0
            spread = (m.yes_ask - m.yes_bid) if has_book else 0

            if not has_book:
                if not allow_missing_book:
                    diag["missing_book"] += 1
                    if len(diag_samples["missing_book"]) < 3:
                        diag_samples["missing_book"].append(
                            f"ticker={m.ticker} bid={m.yes_bid} ask={m.yes_ask}"
                        )
                    continue
                # demo: allow through with spread=0
            else:
                if spread < min_spread:
                    diag["spread_too_narrow"] += 1
                    if len(diag_samples["spread_too_narrow"]) < 3:
                        diag_samples["spread_too_narrow"].append(
                            f"ticker={m.ticker} spread={spread}c"
                        )
                    continue
                if spread > max_spread:
                    diag["spread_too_wide"] += 1
                    if len(diag_samples["spread_too_wide"]) < 3:
                        diag_samples["spread_too_wide"].append(
                            f"ticker={m.ticker} spread={spread}c"
                        )
                    continue

            tradeable.append(
                TradeableMarket(
                    ticker=m.ticker,
                    event_ticker=m.event_ticker,
                    title=m.title,
                    strike=strike_val,
                    strike_kind=strike_info.kind,
                    expiration_time=m.expiration_time or m.close_time,
                    time_to_expiry_sec=tte_sec,
                    time_to_expiry_years=tte_yr,
                    yes_bid=m.yes_bid,
                    yes_ask=m.yes_ask,
                    spread=spread,
                    volume_24h=m.volume_24h,
                    open_interest=m.open_interest,
                    has_book=has_book,
                )
            )

        self.tradeable = tradeable

        # Log diagnostics
        log.info("Tradeable BTC markets after filters: %d", len(tradeable))
        if diag:
            parts = [f"{reason}={count}" for reason, count in sorted(diag.items())]
            log.info("Filter diagnostics: %s", "  ".join(parts))
            for reason, samples in diag_samples.items():
                for s in samples:
                    log.info("  Filtered (%s) %s", reason, s)

        if not tradeable:
            log.warning(
                "No tradeable tickers after filters. "
                "Check filter diagnostics above. "
                "Total candidates=%d, env=%s, spread_range=[%d,%d], allow_missing_book=%s",
                len(candidates), self.cfg.environment, min_spread, max_spread, allow_missing_book,
            )

        return tradeable

    @property
    def tickers(self) -> list[str]:
        return [m.ticker for m in self.tradeable]
