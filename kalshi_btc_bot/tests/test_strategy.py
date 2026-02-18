"""Tests for strategy quote generation."""

from __future__ import annotations

import pytest

from bot.config import BotConfig
from bot.kalshi.market_discovery import TradeableMarket, parse_strike, parse_strike_info
from bot.kalshi.ws import MarketState
from bot.pricing.fair_value import FairValue
from bot.strategy.market_maker import compute_quotes


@pytest.fixture
def cfg() -> BotConfig:
    return BotConfig(
        mm_quote_size_contracts=10,
        mm_edge_cents=2,
        mm_inventory_skew=0.25,
        max_order_size_contracts=25,
    )


def _market(ticker: str = "BTC-TEST") -> TradeableMarket:
    return TradeableMarket(
        ticker=ticker,
        event_ticker="EVT-1",
        title="Bitcoin above 50000",
        strike=50000.0,
        expiration_time="2026-12-31T00:00:00Z",
        time_to_expiry_sec=86400,
        time_to_expiry_years=1 / 365,
        yes_bid=45,
        yes_ask=55,
        spread=10,
        volume_24h=5000,
        open_interest=1000,
    )


def _ws_state(bid: int = 45, ask: int = 55) -> MarketState:
    ms = MarketState("BTC-TEST")
    ms.yes_bid = bid
    ms.yes_ask = ask
    ms.last_update_ms = 9999999999999
    return ms


def _fair_value(fair_cents: int = 50) -> FairValue:
    return FairValue(
        ticker="BTC-TEST",
        spot=50000,
        strike=50000,
        t_years=1 / 365,
        sigma=0.4,
        prob=fair_cents / 100.0,
        fair_cents=fair_cents,
        confidence=0.8,
    )


class TestComputeQuotes:
    def test_bid_less_than_ask(self, cfg: BotConfig) -> None:
        """Bid must always be less than ask."""
        for fair in range(10, 91, 5):
            for bid in range(max(1, fair - 20), fair):
                ask = bid + 5
                if ask > 99:
                    continue
                ws = _ws_state(bid, ask)
                fv = _fair_value(fair)
                q = compute_quotes(_market(), ws, fv, 0, 25, cfg)
                if q is not None:
                    assert q.bid_price < q.ask_price, (
                        f"bid={q.bid_price} >= ask={q.ask_price} "
                        f"for fair={fair} ws_bid={bid} ws_ask={ask}"
                    )

    def test_quotes_within_bounds(self, cfg: BotConfig) -> None:
        """Quotes should be in [1, 99]."""
        ws = _ws_state(45, 55)
        fv = _fair_value(50)
        q = compute_quotes(_market(), ws, fv, 0, 25, cfg)
        assert q is not None
        assert 1 <= q.bid_price <= 98
        assert 2 <= q.ask_price <= 99

    def test_inventory_skew_long(self, cfg: BotConfig) -> None:
        """When long, bid and ask should be lower (eager to sell)."""
        ws = _ws_state(45, 55)
        fv = _fair_value(50)
        q_flat = compute_quotes(_market(), ws, fv, 0, 25, cfg)
        q_long = compute_quotes(_market(), ws, fv, 10, 25, cfg)
        assert q_flat is not None
        assert q_long is not None
        assert q_long.bid_price <= q_flat.bid_price
        assert q_long.ask_price <= q_flat.ask_price

    def test_inventory_skew_short(self, cfg: BotConfig) -> None:
        """When short, bid and ask should be higher (eager to buy)."""
        ws = _ws_state(45, 55)
        fv = _fair_value(50)
        q_flat = compute_quotes(_market(), ws, fv, 0, 25, cfg)
        q_short = compute_quotes(_market(), ws, fv, -10, 25, cfg)
        assert q_flat is not None
        assert q_short is not None
        assert q_short.bid_price >= q_flat.bid_price
        assert q_short.ask_price >= q_flat.ask_price

    def test_size_capped(self, cfg: BotConfig) -> None:
        ws = _ws_state(45, 55)
        fv = _fair_value(50)
        q = compute_quotes(_market(), ws, fv, 0, 5, cfg)
        assert q is not None
        assert q.size <= 5

    def test_no_quotes_when_zero_max(self, cfg: BotConfig) -> None:
        ws = _ws_state(45, 55)
        fv = _fair_value(50)
        q = compute_quotes(_market(), ws, fv, 0, 0, cfg)
        assert q is None

    def test_no_quotes_when_no_book(self, cfg: BotConfig) -> None:
        ws = _ws_state(0, 0)
        fv = _fair_value(50)
        q = compute_quotes(_market(), ws, fv, 0, 25, cfg)
        assert q is None


class TestStrikeParsing:
    def test_above_pattern(self) -> None:
        assert parse_strike("Bitcoin above 50000") == 50000.0

    def test_above_with_dollar(self) -> None:
        assert parse_strike("Bitcoin above $50,000") == 50000.0

    def test_btc_gt_pattern(self) -> None:
        assert parse_strike("BTC > 55,000.50") == 55000.50

    def test_below_pattern(self) -> None:
        assert parse_strike("Bitcoin below $45,000") == 45000.0

    def test_or_more(self) -> None:
        assert parse_strike("$60,000 or more") == 60000.0

    def test_no_match(self) -> None:
        assert parse_strike("What is going on?") is None

    def test_subtitle_fallback(self) -> None:
        assert parse_strike("Some title", "above $70,000") == 70000.0

    # ── Real Kalshi subtitle patterns ──

    def test_kalshi_or_above(self) -> None:
        assert parse_strike("Bitcoin price range  on Feb 20, 2026?", "$78,750 or above") == 78750.0

    def test_kalshi_or_below(self) -> None:
        assert parse_strike("Bitcoin price range  on Feb 20, 2026?", "$54,749.99 or below") == 54749.99

    def test_kalshi_range_bracket(self) -> None:
        result = parse_strike("Bitcoin price range  on Feb 20, 2026?", "$77,250 to 78,249.99")
        assert result is not None
        assert 77250 < result < 78250  # midpoint ~77,750

    def test_kalshi_range_midpoint(self) -> None:
        result = parse_strike("", "$74,750 to 75,249.99")
        expected_mid = (74750 + 75249.99) / 2.0
        assert result == pytest.approx(expected_mid, abs=0.01)

    # ── k-suffix and additional variations ──

    def test_k_suffix_above(self) -> None:
        assert parse_strike("above 67k") == 67000.0

    def test_k_suffix_decimal(self) -> None:
        assert parse_strike("above 67.5k") == 67500.0

    def test_k_range(self) -> None:
        result = parse_strike("67k-68k")
        assert result is not None
        assert result == pytest.approx(67500.0, abs=1.0)

    def test_between_and(self) -> None:
        result = parse_strike("between 67000 and 67999.99")
        assert result is not None
        assert 67000 < result < 68000

    def test_gte_pattern(self) -> None:
        assert parse_strike(">= 67500") == 67500.0

    def test_or_higher(self) -> None:
        assert parse_strike("$67,500 or higher") == 67500.0

    def test_dash_range(self) -> None:
        result = parse_strike("$67,000 – $67,999")
        assert result is not None
        assert 67000 < result < 68000


class TestStrikeInfo:
    def test_above_kind(self) -> None:
        info = parse_strike_info("", "$78,750 or above")
        assert info.kind == "above"
        assert info.lower == 78750.0

    def test_below_kind(self) -> None:
        info = parse_strike_info("", "$54,749.99 or below")
        assert info.kind == "below"
        assert info.upper == 54749.99

    def test_range_kind(self) -> None:
        info = parse_strike_info("", "$77,250 to 78,249.99")
        assert info.kind == "range"
        assert info.lower == 77250.0
        assert info.upper == 78249.99
        assert info.strike == pytest.approx(77749.995, abs=0.01)

    def test_unknown_kind(self) -> None:
        info = parse_strike_info("Nothing here")
        assert info.kind == "unknown"
        assert info.strike is None
