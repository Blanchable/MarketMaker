"""Tests for market maker take-profit / stop-loss exit logic (in cents)."""

from __future__ import annotations

import pytest

from bot.config import BotConfig
from bot.strategy.market_maker import MarketMakerStrategy, TrackedFill


class TestTrackedFills:
    def test_record_single_entry(self) -> None:
        mm = MarketMakerStrategy.__new__(MarketMakerStrategy)
        mm._entries = {}
        mm.cfg = BotConfig()
        mm.record_entry("TICK-1", 50, 10)
        assert mm._entries["TICK-1"].entry_price == 50.0
        assert mm._entries["TICK-1"].quantity == 10

    def test_record_multiple_entries_averages(self) -> None:
        mm = MarketMakerStrategy.__new__(MarketMakerStrategy)
        mm._entries = {}
        mm.cfg = BotConfig()
        mm.record_entry("TICK-1", 50, 10)
        mm.record_entry("TICK-1", 60, 10)
        assert mm._entries["TICK-1"].quantity == 20
        assert mm._entries["TICK-1"].entry_price == pytest.approx(55.0)

    def test_record_exit_reduces_quantity(self) -> None:
        mm = MarketMakerStrategy.__new__(MarketMakerStrategy)
        mm._entries = {}
        mm.cfg = BotConfig()
        mm.record_entry("TICK-1", 50, 10)
        mm.record_exit("TICK-1", 5)
        assert mm._entries["TICK-1"].quantity == 5

    def test_record_exit_full_removes_entry(self) -> None:
        mm = MarketMakerStrategy.__new__(MarketMakerStrategy)
        mm._entries = {}
        mm.cfg = BotConfig()
        mm.record_entry("TICK-1", 50, 10)
        mm.record_exit("TICK-1", 10)
        assert "TICK-1" not in mm._entries

    def test_seed_does_not_overwrite(self) -> None:
        mm = MarketMakerStrategy.__new__(MarketMakerStrategy)
        mm._entries = {}
        mm.cfg = BotConfig()
        mm.record_entry("TICK-1", 50, 10)
        mm.seed_entry_from_position("TICK-1", 10, 70.0)
        assert mm._entries["TICK-1"].entry_price == 50.0

    def test_seed_creates_new(self) -> None:
        mm = MarketMakerStrategy.__new__(MarketMakerStrategy)
        mm._entries = {}
        mm.cfg = BotConfig()
        mm.seed_entry_from_position("TICK-1", 5, 60.0)
        assert mm._entries["TICK-1"].entry_price == 60.0
        assert mm._entries["TICK-1"].quantity == 5


class TestExitConditions:
    def _make_mm(self, tp: int = 5, sl: int = 5) -> MarketMakerStrategy:
        mm = MarketMakerStrategy.__new__(MarketMakerStrategy)
        mm._entries = {}
        mm.cfg = BotConfig(mm_take_profit_cents=tp, mm_stop_loss_cents=sl)
        return mm

    def test_take_profit_triggered(self) -> None:
        mm = self._make_mm(tp=5)
        mm.record_entry("T", 50, 5)
        # Mid at 56 = +6c above entry → should trigger (>=5c)
        entry = mm._entries["T"]
        diff = 56 - entry.entry_price
        assert diff >= 5

    def test_stop_loss_triggered(self) -> None:
        mm = self._make_mm(sl=5)
        mm.record_entry("T", 50, 5)
        # Mid at 44 = -6c below entry → should trigger (<=-5c)
        entry = mm._entries["T"]
        diff = 44 - entry.entry_price
        assert diff <= -5

    def test_no_exit_in_range(self) -> None:
        mm = self._make_mm(tp=5, sl=5)
        mm.record_entry("T", 50, 5)
        # Mid at 52 = +2c → no exit
        entry = mm._entries["T"]
        diff = 52 - entry.entry_price
        assert -5 < diff < 5

    def test_zero_tp_disables(self) -> None:
        mm = self._make_mm(tp=0, sl=5)
        mm.record_entry("T", 50, 5)
        assert mm.cfg.mm_take_profit_cents == 0

    def test_zero_sl_disables(self) -> None:
        mm = self._make_mm(tp=5, sl=0)
        mm.record_entry("T", 50, 5)
        assert mm.cfg.mm_stop_loss_cents == 0

    def test_exact_threshold_triggers_tp(self) -> None:
        mm = self._make_mm(tp=5, sl=5)
        mm.record_entry("T", 50, 3)
        entry = mm._entries["T"]
        diff = 55 - entry.entry_price  # exactly +5c
        assert diff >= 5

    def test_exact_threshold_triggers_sl(self) -> None:
        mm = self._make_mm(tp=5, sl=5)
        mm.record_entry("T", 50, 3)
        entry = mm._entries["T"]
        diff = 45 - entry.entry_price  # exactly -5c
        assert diff <= -5
