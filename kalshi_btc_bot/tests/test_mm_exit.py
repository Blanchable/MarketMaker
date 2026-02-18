"""Tests for market maker take-profit / stop-loss exit logic (in cents)."""

from __future__ import annotations

from collections import defaultdict

import pytest

from bot.config import BotConfig
from bot.strategy.market_maker import MarketMakerStrategy, TrackedFill, TickerStats


def _make_mm(tp: int = 5, sl: int = 5) -> MarketMakerStrategy:
    mm = MarketMakerStrategy.__new__(MarketMakerStrategy)
    mm._entries = {}
    mm._exit_lock_until = {}
    mm._stats = defaultdict(TickerStats)
    mm._last_diag_ts = 0.0
    mm.cfg = BotConfig(mm_take_profit_cents=tp, mm_stop_loss_cents=sl)
    return mm


class TestTrackedFills:
    def test_record_single_entry(self) -> None:
        mm = _make_mm()
        mm.record_entry("TICK-1", 50, 10)
        assert mm._entries["TICK-1"].entry_price == 50.0
        assert mm._entries["TICK-1"].quantity == 10

    def test_record_multiple_entries_averages(self) -> None:
        mm = _make_mm()
        mm.record_entry("TICK-1", 50, 10)
        mm.record_entry("TICK-1", 60, 10)
        assert mm._entries["TICK-1"].quantity == 20
        assert mm._entries["TICK-1"].entry_price == pytest.approx(55.0)

    def test_record_exit_reduces_quantity(self) -> None:
        mm = _make_mm()
        mm.record_entry("TICK-1", 50, 10)
        mm.record_exit("TICK-1", 5)
        assert mm._entries["TICK-1"].quantity == 5

    def test_record_exit_full_removes_entry(self) -> None:
        mm = _make_mm()
        mm.record_entry("TICK-1", 50, 10)
        mm.record_exit("TICK-1", 10, 55)
        assert "TICK-1" not in mm._entries

    def test_seed_does_not_overwrite(self) -> None:
        mm = _make_mm()
        mm.record_entry("TICK-1", 50, 10)
        mm.seed_entry_from_position("TICK-1", 10, 70.0)
        assert mm._entries["TICK-1"].entry_price == 50.0

    def test_seed_creates_new(self) -> None:
        mm = _make_mm()
        mm.seed_entry_from_position("TICK-1", 5, 60.0)
        assert mm._entries["TICK-1"].entry_price == 60.0
        assert mm._entries["TICK-1"].quantity == 5


class TestExitConditions:
    def test_take_profit_triggered(self) -> None:
        mm = _make_mm(tp=5)
        mm.record_entry("T", 50, 5)
        entry = mm._entries["T"]
        diff = 56 - entry.entry_price
        assert diff >= 5

    def test_stop_loss_triggered(self) -> None:
        mm = _make_mm(sl=5)
        mm.record_entry("T", 50, 5)
        entry = mm._entries["T"]
        diff = 44 - entry.entry_price
        assert diff <= -5

    def test_no_exit_in_range(self) -> None:
        mm = _make_mm(tp=5, sl=5)
        mm.record_entry("T", 50, 5)
        entry = mm._entries["T"]
        diff = 52 - entry.entry_price
        assert -5 < diff < 5

    def test_zero_tp_disables(self) -> None:
        mm = _make_mm(tp=0, sl=5)
        mm.record_entry("T", 50, 5)
        assert mm.cfg.mm_take_profit_cents == 0

    def test_zero_sl_disables(self) -> None:
        mm = _make_mm(tp=5, sl=0)
        mm.record_entry("T", 50, 5)
        assert mm.cfg.mm_stop_loss_cents == 0

    def test_exact_threshold_triggers_tp(self) -> None:
        mm = _make_mm(tp=5, sl=5)
        mm.record_entry("T", 50, 3)
        diff = 55 - mm._entries["T"].entry_price
        assert diff >= 5

    def test_exact_threshold_triggers_sl(self) -> None:
        mm = _make_mm(tp=5, sl=5)
        mm.record_entry("T", 50, 3)
        diff = 45 - mm._entries["T"].entry_price
        assert diff <= -5


class TestExitLock:
    def test_lock_blocks_quoting(self) -> None:
        mm = _make_mm()
        assert not mm._is_exit_locked("T")
        mm._set_exit_lock("T")
        assert mm._is_exit_locked("T")

    def test_lock_expires(self) -> None:
        mm = _make_mm()
        mm._exit_lock_until["T"] = 0  # already expired
        assert not mm._is_exit_locked("T")


class TestEdgeQuotes:
    def test_edge_affects_quotes(self) -> None:
        """Changing mm_edge_cents should widen/narrow the quotes."""
        from bot.kalshi.ws import MarketState
        from bot.pricing.fair_value import FairValue
        from bot.kalshi.market_discovery import TradeableMarket
        from bot.strategy.market_maker import compute_quotes

        mkt = TradeableMarket(
            ticker="T", event_ticker="E", title="test",
            yes_bid=45, yes_ask=55, spread=10,
        )
        ws = MarketState("T")
        ws.yes_bid = 45
        ws.yes_ask = 55
        fv = FairValue(ticker="T", fair_cents=50, confidence=0.9)

        # edge=2: bid=48, ask=52
        cfg2 = BotConfig(mm_edge_cents=2, mm_quote_size_contracts=5, max_order_size_contracts=25)
        q2 = compute_quotes(mkt, ws, fv, 0, 25, cfg2)
        assert q2 is not None
        assert q2.bid_price == 48
        assert q2.ask_price == 52

        # edge=4: bid=46, ask=54
        cfg4 = BotConfig(mm_edge_cents=4, mm_quote_size_contracts=5, max_order_size_contracts=25)
        q4 = compute_quotes(mkt, ws, fv, 0, 25, cfg4)
        assert q4 is not None
        assert q4.bid_price == 46
        assert q4.ask_price == 54

    def test_post_only_guard(self) -> None:
        """Quotes must never cross the book."""
        from bot.kalshi.ws import MarketState
        from bot.pricing.fair_value import FairValue
        from bot.kalshi.market_discovery import TradeableMarket
        from bot.strategy.market_maker import compute_quotes

        mkt = TradeableMarket(ticker="T", event_ticker="E", title="test",
                              yes_bid=49, yes_ask=51, spread=2)
        ws = MarketState("T")
        ws.yes_bid = 49
        ws.yes_ask = 51
        fv = FairValue(ticker="T", fair_cents=50, confidence=0.9)

        cfg = BotConfig(mm_edge_cents=1, mm_quote_size_contracts=5, max_order_size_contracts=25)
        q = compute_quotes(mkt, ws, fv, 0, 25, cfg)
        if q is not None:
            assert q.bid_price < q.ask_price
            assert q.bid_price < ws.yes_ask
            assert q.ask_price > ws.yes_bid
