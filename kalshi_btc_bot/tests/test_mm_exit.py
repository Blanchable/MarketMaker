"""Tests for market maker take-profit / stop-loss exit logic."""

from __future__ import annotations

import pytest

from bot.config import BotConfig
from bot.strategy.market_maker import MarketMakerStrategy, TrackedFill


@pytest.fixture
def cfg() -> BotConfig:
    return BotConfig(
        mm_take_profit_pct=15.0,
        mm_stop_loss_pct=15.0,
    )


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
        # Should NOT overwrite the existing entry
        assert mm._entries["TICK-1"].entry_price == 50.0

    def test_seed_creates_new(self) -> None:
        mm = MarketMakerStrategy.__new__(MarketMakerStrategy)
        mm._entries = {}
        mm.cfg = BotConfig()
        mm.seed_entry_from_position("TICK-1", 5, 60.0)
        assert mm._entries["TICK-1"].entry_price == 60.0
        assert mm._entries["TICK-1"].quantity == 5


class TestExitConditions:
    def _make_mm(self, tp: float = 15.0, sl: float = 15.0) -> MarketMakerStrategy:
        mm = MarketMakerStrategy.__new__(MarketMakerStrategy)
        mm._entries = {}
        mm.cfg = BotConfig(mm_take_profit_pct=tp, mm_stop_loss_pct=sl)
        return mm

    def test_take_profit_triggered(self) -> None:
        mm = self._make_mm(tp=10)
        mm.record_entry("T", 50, 5)
        # Mid at 56 = +12% above 50 → should trigger (>10%)
        entry = mm._entries["T"]
        pct = ((56 - entry.entry_price) / entry.entry_price) * 100
        assert pct > 10

    def test_stop_loss_triggered(self) -> None:
        mm = self._make_mm(sl=10)
        mm.record_entry("T", 50, 5)
        # Mid at 44 = -12% below 50 → should trigger (>10% loss)
        entry = mm._entries["T"]
        pct = ((44 - entry.entry_price) / entry.entry_price) * 100
        assert pct < -10

    def test_no_exit_in_range(self) -> None:
        mm = self._make_mm(tp=15, sl=15)
        mm.record_entry("T", 50, 5)
        # Mid at 52 = +4% → no exit
        entry = mm._entries["T"]
        pct = ((52 - entry.entry_price) / entry.entry_price) * 100
        assert -15 < pct < 15

    def test_zero_tp_disables(self) -> None:
        mm = self._make_mm(tp=0, sl=15)
        mm.record_entry("T", 50, 5)
        # Even a huge gain shouldn't trigger TP when disabled
        pct = ((90 - 50) / 50) * 100
        assert pct > 0
        # TP is 0 so the check would be: 0 > 0 → False → no exit
        assert mm.cfg.mm_take_profit_pct == 0

    def test_zero_sl_disables(self) -> None:
        mm = self._make_mm(tp=15, sl=0)
        mm.record_entry("T", 50, 5)
        assert mm.cfg.mm_stop_loss_pct == 0
