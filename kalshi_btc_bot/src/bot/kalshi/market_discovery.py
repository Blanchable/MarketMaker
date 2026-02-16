"""Discover and filter tradeable markets on Kalshi (sports or crypto)."""

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


# ── Strike / outcome parsing ─────────────────────────────────────────────────

@dataclass
class StrikeInfo:
    lower: float | None = None
    upper: float | None = None
    kind: str = "unknown"

    @property
    def strike(self) -> float | None:
        if self.kind == "above" and self.lower is not None:
            return self.lower
        if self.kind == "below" and self.upper is not None:
            return self.upper
        if self.kind == "range" and self.lower is not None and self.upper is not None:
            return (self.lower + self.upper) / 2.0
        return self.lower or self.upper


def _expand_k(text: str) -> str:
    def _repl(m: re.Match) -> str:
        num_str = m.group(1)
        if "." in num_str:
            return str(int(float(num_str) * 1000))
        return num_str + "000"
    return re.sub(r"(\d+(?:\.\d+)?)k\b", _repl, text, flags=re.IGNORECASE)


def _parse_number(raw: str) -> float:
    return float(raw.replace(",", "").replace("$", "").strip())


_PATTERNS_RANGE = [
    re.compile(r"\$?([\d,]+(?:\.\d+)?)\s+to\s+\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"\$?([\d,]+(?:\.\d+)?)\s*[-–]\s*\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"between\s+\$?([\d,]+(?:\.\d+)?)\s+and\s+\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE),
]
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


def parse_strike(title: str, subtitle: str = "") -> float | None:
    info = parse_strike_info(title, subtitle)
    return info.strike


def parse_strike_info(title: str, subtitle: str = "") -> StrikeInfo:
    text = _expand_k(f"{title} {subtitle}")
    for pat in _PATTERNS_RANGE:
        m = pat.search(text)
        if m:
            try:
                return StrikeInfo(lower=_parse_number(m.group(1)), upper=_parse_number(m.group(2)), kind="range")
            except (ValueError, IndexError):
                continue
    for pat in _PATTERNS_ABOVE:
        m = pat.search(text)
        if m:
            try:
                return StrikeInfo(lower=_parse_number(m.group(1)), kind="above")
            except (ValueError, IndexError):
                continue
    for pat in _PATTERNS_BELOW:
        m = pat.search(text)
        if m:
            try:
                return StrikeInfo(upper=_parse_number(m.group(1)), kind="below")
            except (ValueError, IndexError):
                continue
    return StrikeInfo()


# ── TradeableMarket ──────────────────────────────────────────────────────────

@dataclass
class TradeableMarket:
    """A market that passed discovery filters."""
    ticker: str
    event_ticker: str
    title: str
    strike: float = 0.0
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
    # Sports-specific
    outcome_label: str = ""         # e.g. "Gonzaga", "San Francisco"
    complementary_ticker: str = ""  # the other side of the same game


def _is_btc_market(market: Market) -> bool:
    return bool(
        _BTC_KEYWORDS.search(market.title) or _BTC_KEYWORDS.search(market.subtitle)
        or _BTC_KEYWORDS.search(market.event_ticker) or _BTC_KEYWORDS.search(market.ticker)
    )


# ── MarketDiscovery ──────────────────────────────────────────────────────────

class MarketDiscovery:
    """Scans Kalshi for tradeable markets (sports or crypto)."""

    def __init__(self, rest: KalshiRestClient, cfg: BotConfig | None = None) -> None:
        self.rest = rest
        self.cfg = cfg or get_config()
        self.tradeable: list[TradeableMarket] = []
        self._event_tickers: set[str] = set()
        self._series_tickers: set[str] = set()

    async def refresh_series(self) -> None:
        if self.cfg.is_sports:
            await self._refresh_sports_series()
        else:
            await self._refresh_crypto_series()

    async def discover(self) -> list[TradeableMarket]:
        if self.cfg.is_sports:
            return await self._discover_sports()
        else:
            return await self._discover_crypto()

    @property
    def tickers(self) -> list[str]:
        return [m.ticker for m in self.tradeable]

    # ── Sports discovery ─────────────────────────────────────────────

    async def _refresh_sports_series(self) -> None:
        """Scan configured sports series prefixes for open events."""
        for series_prefix in self.cfg.sports_series_list:
            try:
                events = await self.rest.get_events(series_ticker=series_prefix, limit=200)
                for ev in events:
                    self._event_tickers.add(ev.event_ticker)
                    if ev.series_ticker:
                        self._series_tickers.add(ev.series_ticker)
            except Exception as exc:
                log.warning("Failed to fetch events for series %s: %s", series_prefix, exc)

        log.info(
            "Sports discovery: series_prefixes=%d  events=%d",
            len(self.cfg.sports_series_list),
            len(self._event_tickers),
        )

    async def _discover_sports(self) -> list[TradeableMarket]:
        """Fetch markets for known sports events and apply filters."""
        candidates: list[Market] = []
        for evt in list(self._event_tickers):
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

        log.info("Sports candidate markets: %d (from %d events)", len(candidates), len(self._event_tickers))

        # Group by event_ticker to find complementary markets
        by_event: dict[str, list[Market]] = defaultdict(list)
        for m in candidates:
            by_event[m.event_ticker].append(m)

        is_demo = self.cfg.is_demo
        min_spread = self.cfg.effective_min_spread
        max_spread = self.cfg.effective_max_spread
        allow_missing_book = is_demo and self.cfg.demo_allow_missing_book

        diag: dict[str, int] = defaultdict(int)
        diag_samples: dict[str, list[str]] = defaultdict(list)
        tradeable: list[TradeableMarket] = []

        for m in candidates:
            tte_sec = seconds_until(m.expiration_time or m.close_time)
            tte_yr = years_until(m.expiration_time or m.close_time)

            if tte_sec < self.cfg.no_trade_window_seconds:
                diag["too_close_to_expiry"] += 1
                continue

            has_book = m.yes_bid > 0 and m.yes_ask > 0
            spread = (m.yes_ask - m.yes_bid) if has_book else 0

            if not has_book and not allow_missing_book:
                diag["missing_book"] += 1
                if len(diag_samples["missing_book"]) < 3:
                    diag_samples["missing_book"].append(f"ticker={m.ticker} bid={m.yes_bid} ask={m.yes_ask}")
                continue

            if has_book:
                if spread < min_spread:
                    diag["spread_too_narrow"] += 1
                    continue
                if spread > max_spread:
                    diag["spread_too_wide"] += 1
                    if len(diag_samples["spread_too_wide"]) < 3:
                        diag_samples["spread_too_wide"].append(f"ticker={m.ticker} spread={spread}c")
                    continue

            # Find complementary market (the other side of the same event)
            siblings = by_event.get(m.event_ticker, [])
            comp_ticker = ""
            for sib in siblings:
                if sib.ticker != m.ticker:
                    comp_ticker = sib.ticker
                    break

            # Outcome label from yes_sub_title or subtitle
            outcome = m.yes_sub_title or m.subtitle or ""

            tradeable.append(TradeableMarket(
                ticker=m.ticker,
                event_ticker=m.event_ticker,
                title=m.title,
                strike=0.0,
                strike_kind="sports",
                expiration_time=m.expiration_time or m.close_time,
                time_to_expiry_sec=tte_sec,
                time_to_expiry_years=tte_yr,
                yes_bid=m.yes_bid,
                yes_ask=m.yes_ask,
                spread=spread,
                volume_24h=m.volume_24h,
                open_interest=m.open_interest,
                has_book=has_book,
                outcome_label=outcome,
                complementary_ticker=comp_ticker,
            ))

        self.tradeable = tradeable
        self._log_diagnostics(tradeable, candidates, diag, diag_samples)
        return tradeable

    # ── Crypto discovery (BTC) ───────────────────────────────────────

    async def _refresh_crypto_series(self) -> None:
        try:
            series_list = await self.rest.get_series(
                category=self.cfg.series_category, tags=self.cfg.series_tags,
            )
        except Exception as exc:
            log.warning("Failed to fetch series: %s", exc)
            series_list = []

        for s in series_list:
            if _BTC_KEYWORDS.search(s.title) or any(_BTC_KEYWORDS.search(t) for t in s.tags):
                self._series_tickers.add(s.series_ticker)

        try:
            events = await self.rest.get_events(limit=200)
            for ev in events:
                ev_text = f"{ev.title} {ev.category} {ev.event_ticker}"
                if _BTC_KEYWORDS.search(ev_text):
                    self._event_tickers.add(ev.event_ticker)
                    if ev.series_ticker:
                        self._series_tickers.add(ev.series_ticker)
        except Exception as exc:
            log.warning("Failed to fetch events: %s", exc)

        for st in list(self._series_tickers):
            try:
                events = await self.rest.get_events(series_ticker=st)
                for ev in events:
                    self._event_tickers.add(ev.event_ticker)
            except Exception as exc:
                log.warning("Failed to fetch events for series %s: %s", st, exc)

        log.info("BTC series=%d  events=%d", len(self._series_tickers), len(self._event_tickers))

    async def _discover_crypto(self) -> list[TradeableMarket]:
        candidates: list[Market] = []
        for evt in list(self._event_tickers):
            try:
                batch = await self.rest.get_all_markets(
                    status=self.cfg.market_status, event_ticker=evt,
                )
                candidates.extend(batch)
            except Exception as exc:
                log.warning("Failed to fetch markets for event %s: %s", evt, exc)

        seen: set[str] = set()
        deduped: list[Market] = []
        for m in candidates:
            if m.ticker not in seen:
                seen.add(m.ticker)
                deduped.append(m)
        candidates = deduped

        log.info("BTC candidate markets: %d (from %d events)", len(candidates), len(self._event_tickers))

        is_demo = self.cfg.is_demo
        min_spread = self.cfg.effective_min_spread
        max_spread = self.cfg.effective_max_spread
        allow_missing_book = is_demo and self.cfg.demo_allow_missing_book

        diag: dict[str, int] = defaultdict(int)
        diag_samples: dict[str, list[str]] = defaultdict(list)
        tradeable: list[TradeableMarket] = []

        for m in candidates:
            tte_sec = seconds_until(m.expiration_time or m.close_time)
            tte_yr = years_until(m.expiration_time or m.close_time)

            if tte_sec < self.cfg.no_trade_window_seconds:
                diag["too_close_to_expiry"] += 1
                continue

            strike_info = parse_strike_info(m.title, m.subtitle)
            strike_val = strike_info.strike
            if strike_val is None:
                if is_demo:
                    strike_val = 0.0
                    diag["strike_unknown_kept"] += 1
                else:
                    diag["missing_strike"] += 1
                    if len(diag_samples["missing_strike"]) < 5:
                        diag_samples["missing_strike"].append(f"ticker={m.ticker} title='{m.title[:50]}'")
                    continue

            has_book = m.yes_bid > 0 and m.yes_ask > 0
            spread = (m.yes_ask - m.yes_bid) if has_book else 0

            if not has_book and not allow_missing_book:
                diag["missing_book"] += 1
                continue
            if has_book:
                if spread < min_spread:
                    diag["spread_too_narrow"] += 1
                    continue
                if spread > max_spread:
                    diag["spread_too_wide"] += 1
                    if len(diag_samples["spread_too_wide"]) < 3:
                        diag_samples["spread_too_wide"].append(f"ticker={m.ticker} spread={spread}c")
                    continue

            tradeable.append(TradeableMarket(
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
            ))

        self.tradeable = tradeable
        self._log_diagnostics(tradeable, candidates, diag, diag_samples)
        return tradeable

    # ── Shared diagnostics logging ───────────────────────────────────

    def _log_diagnostics(
        self,
        tradeable: list[TradeableMarket],
        candidates: list[Market],
        diag: dict[str, int],
        diag_samples: dict[str, list[str]],
    ) -> None:
        log.info("Tradeable markets after filters: %d", len(tradeable))
        if diag:
            parts = [f"{r}={c}" for r, c in sorted(diag.items())]
            log.info("Filter diagnostics: %s", "  ".join(parts))
            for reason, samples in diag_samples.items():
                for s in samples:
                    log.info("  Filtered (%s) %s", reason, s)
        if not tradeable:
            log.warning(
                "No tradeable tickers after filters. candidates=%d env=%s spread=[%d,%d]",
                len(candidates), self.cfg.environment,
                self.cfg.effective_min_spread, self.cfg.effective_max_spread,
            )
