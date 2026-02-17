"""Tests for fills-based PnL tracker."""

from __future__ import annotations

import pytest
from pathlib import Path

from bot.pnl.tracker import (
    PnlFill,
    PnlTracker,
    TickerPnlState,
    apply_fill,
    normalise_fill,
)


# ── Normalisation tests ──────────────────────────────────────────────────────

class TestNormaliseFill:
    def test_buy_yes(self) -> None:
        f = normalise_fill(fill_key="1", ticker="T", side="yes", action="buy",
                           yes_price=60, no_price=40, count=5)
        assert f.signed_qty_yes == 5
        assert f.yes_price_cents == 60.0

    def test_sell_yes(self) -> None:
        f = normalise_fill(fill_key="2", ticker="T", side="yes", action="sell",
                           yes_price=70, no_price=30, count=3)
        assert f.signed_qty_yes == -3
        assert f.yes_price_cents == 70.0

    def test_buy_no_equals_sell_yes(self) -> None:
        f = normalise_fill(fill_key="3", ticker="T", side="no", action="buy",
                           yes_price=0, no_price=30, count=4)
        assert f.signed_qty_yes == -4
        assert f.yes_price_cents == 70.0  # 100 - 30

    def test_sell_no_equals_buy_yes(self) -> None:
        f = normalise_fill(fill_key="4", ticker="T", side="no", action="sell",
                           yes_price=0, no_price=25, count=2)
        assert f.signed_qty_yes == 2
        assert f.yes_price_cents == 75.0  # 100 - 25


# ── Accounting engine tests ──────────────────────────────────────────────────

class TestApplyFill:
    def _state(self, qty: int = 0, avg: float = 0.0) -> TickerPnlState:
        return TickerPnlState(ticker="T", pos_qty=qty, avg_price=avg)

    def _fill(self, qty: int, price: float, fee: float = 0.0) -> PnlFill:
        return PnlFill(fill_key="x", ticker="T", signed_qty_yes=qty,
                        yes_price_cents=price, fee_dollars=fee)

    def test_open_long(self) -> None:
        s = self._state()
        delta = apply_fill(s, self._fill(10, 50))
        assert s.pos_qty == 10
        assert s.avg_price == 50.0
        assert delta == 0.0

    def test_close_long_profit(self) -> None:
        s = self._state(10, 50)
        delta = apply_fill(s, self._fill(-10, 60))
        assert s.pos_qty == 0
        assert delta == pytest.approx(1.0)  # 10 * 10c / 100
        assert s.realized_total == pytest.approx(1.0)

    def test_close_long_loss(self) -> None:
        s = self._state(10, 50)
        delta = apply_fill(s, self._fill(-10, 40))
        assert s.pos_qty == 0
        assert delta == pytest.approx(-1.0)  # 10 * -10c / 100

    def test_partial_close(self) -> None:
        s = self._state(10, 50)
        delta = apply_fill(s, self._fill(-3, 60))
        assert s.pos_qty == 7
        assert s.avg_price == 50.0  # unchanged
        assert delta == pytest.approx(0.30)  # 3 * 10c / 100

    def test_flip_position(self) -> None:
        s = self._state(5, 40)
        delta = apply_fill(s, self._fill(-8, 45))
        # Close 5 long @40, realized = 5 * 5c / 100 = $0.25
        # Open 3 short @45
        assert delta == pytest.approx(0.25)
        assert s.pos_qty == -3
        assert s.avg_price == 45.0

    def test_close_short_profit(self) -> None:
        s = self._state(-5, 60)
        delta = apply_fill(s, self._fill(5, 50))
        # Shorted @60, bought back @50, profit = 5 * 10c / 100 = $0.50
        assert delta == pytest.approx(0.50)
        assert s.pos_qty == 0

    def test_close_short_loss(self) -> None:
        s = self._state(-5, 60)
        delta = apply_fill(s, self._fill(5, 70))
        assert delta == pytest.approx(-0.50)

    def test_add_to_long(self) -> None:
        s = self._state(10, 50)
        delta = apply_fill(s, self._fill(10, 60))
        assert s.pos_qty == 20
        assert s.avg_price == pytest.approx(55.0)
        assert delta == 0.0

    def test_fees_subtracted(self) -> None:
        s = self._state(10, 50)
        delta = apply_fill(s, self._fill(-10, 60, fee=0.10))
        # Gross profit $1.00, minus $0.10 fee = $0.90
        assert delta == pytest.approx(0.90)
        assert s.fees_total == pytest.approx(0.10)

    def test_no_buy_then_sell_equivalent(self) -> None:
        """Buy NO @30 then Sell NO @20 should profit via YES-equivalent."""
        s = self._state()
        # Buy NO @30 = Sell YES @70 → short YES @70
        f1 = normalise_fill(fill_key="a", ticker="T", side="no", action="buy",
                            yes_price=0, no_price=30, count=5)
        apply_fill(s, f1)
        assert s.pos_qty == -5
        assert s.avg_price == 70.0

        # Sell NO @20 = Buy YES @80 → close short by buying YES @80
        f2 = normalise_fill(fill_key="b", ticker="T", side="no", action="sell",
                            yes_price=0, no_price=20, count=5)
        delta = apply_fill(s, f2)
        # Shorted @70, covered @80, loss = 5 * -10c / 100 = -$0.50
        assert delta == pytest.approx(-0.50)
        assert s.pos_qty == 0


# ── PnlTracker integration tests ────────────────────────────────────────────

class TestPnlTracker:
    def _tracker(self, tmp_path: Path) -> PnlTracker:
        return PnlTracker(db_path=tmp_path / "test_pnl.db")

    def test_basic_round_trip(self, tmp_path: Path) -> None:
        t = self._tracker(tmp_path)
        f1 = PnlFill("f1", "T", 10, 50.0)
        f2 = PnlFill("f2", "T", -10, 55.0)
        t.ingest_fill(f1)
        d = t.ingest_fill(f2)
        assert d == pytest.approx(0.50)  # 10 * 5c / 100
        assert t.realized_total == pytest.approx(0.50)
        t.close()

    def test_dedupe(self, tmp_path: Path) -> None:
        t = self._tracker(tmp_path)
        f = PnlFill("same_key", "T", 10, 50.0)
        t.ingest_fill(f)
        t.ingest_fill(f)  # duplicate
        assert t.fills_seen_count == 1
        s = t.state_for("T")
        assert s is not None
        assert s.pos_qty == 10
        t.close()

    def test_persistence(self, tmp_path: Path) -> None:
        db = tmp_path / "persist.db"
        t1 = PnlTracker(db_path=db)
        t1.ingest_fill(PnlFill("f1", "T", 10, 50.0))
        t1.ingest_fill(PnlFill("f2", "T", -10, 60.0))
        assert t1.realized_total == pytest.approx(1.0)
        t1.close()

        # Reload from same DB
        t2 = PnlTracker(db_path=db)
        assert t2.realized_total == pytest.approx(1.0)
        assert t2.fills_seen_count == 2
        # Same fill keys should be deduped
        t2.ingest_fill(PnlFill("f1", "T", 10, 50.0))
        assert t2.fills_seen_count == 2  # no change
        t2.close()

    def test_realized_today(self, tmp_path: Path) -> None:
        t = self._tracker(tmp_path)
        t.ingest_fill(PnlFill("f1", "T", 10, 50.0))
        t.ingest_fill(PnlFill("f2", "T", -10, 55.0))
        # Since this is all "today", realized_today == realized_total
        assert t.realized_today == pytest.approx(0.50)
        t.close()

    def test_multiple_tickers(self, tmp_path: Path) -> None:
        t = self._tracker(tmp_path)
        t.ingest_fill(PnlFill("a1", "A", 5, 40.0))
        t.ingest_fill(PnlFill("a2", "A", -5, 50.0))
        t.ingest_fill(PnlFill("b1", "B", 3, 60.0))
        t.ingest_fill(PnlFill("b2", "B", -3, 55.0))
        # A: profit = 5 * 10c / 100 = $0.50
        # B: loss = 3 * -5c / 100 = -$0.15
        assert t.realized_total == pytest.approx(0.35)
        t.close()
