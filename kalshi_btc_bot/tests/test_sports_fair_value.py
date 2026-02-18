"""Tests for sports fair value engine."""

from __future__ import annotations

import pytest

from bot.pricing.fair_value import FairValueEngine


@pytest.fixture
def engine() -> FairValueEngine:
    return FairValueEngine()


class TestSportsFairValue:
    def test_mid_based_with_both_sides(self, engine: FairValueEngine) -> None:
        """When both own book and complementary book are present, cross-check."""
        # Team A: bid=77, ask=89 → mid=83
        # Team B: bid=11, ask=13 → mid=12
        # Implied from comp: 100-12=88
        # Fair = avg(83, 88) = 85.5 → 86
        fv = engine.compute_sports("TEAM-A", 77, 89, 11, 13)
        assert 84 <= fv.fair_cents <= 87
        assert fv.confidence >= 0.8

    def test_mid_only_own_book(self, engine: FairValueEngine) -> None:
        """When only own book available."""
        fv = engine.compute_sports("TEAM-A", 60, 70, 0, 0)
        assert fv.fair_cents == 65
        assert fv.confidence < 0.8

    def test_mid_only_complementary(self, engine: FairValueEngine) -> None:
        """When only complementary book available."""
        # Comp mid = 30 → implied own = 70
        fv = engine.compute_sports("TEAM-A", 0, 0, 25, 35)
        assert fv.fair_cents == 70
        assert fv.confidence < 0.6

    def test_no_data(self, engine: FairValueEngine) -> None:
        """No data → default to 50."""
        fv = engine.compute_sports("TEAM-A", 0, 0, 0, 0)
        assert fv.fair_cents == 50
        assert fv.confidence <= 0.2

    def test_fair_cents_clamped(self, engine: FairValueEngine) -> None:
        """Fair cents always in [1, 99]."""
        fv = engine.compute_sports("X", 98, 100, 0, 0)
        assert 1 <= fv.fair_cents <= 99
        fv2 = engine.compute_sports("Y", 1, 3, 0, 0)
        assert 1 <= fv2.fair_cents <= 99

    def test_complementary_cross_consistency(self, engine: FairValueEngine) -> None:
        """P(A) + P(B) should be close to 100 when both sides are computed."""
        fv_a = engine.compute_sports("A", 60, 70, 30, 40)
        fv_b = engine.compute_sports("B", 30, 40, 60, 70)
        total = fv_a.fair_cents + fv_b.fair_cents
        assert 95 <= total <= 105  # allowing for rounding
