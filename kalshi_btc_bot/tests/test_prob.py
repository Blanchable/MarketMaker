"""Tests for digital probability model."""

from __future__ import annotations

import pytest

from bot.pricing.digital_prob import DigitalProb, _norm_cdf


@pytest.fixture
def model() -> DigitalProb:
    return DigitalProb()


class TestNormCDF:
    def test_zero(self) -> None:
        assert abs(_norm_cdf(0.0) - 0.5) < 1e-10

    def test_large_positive(self) -> None:
        assert _norm_cdf(10.0) > 0.9999

    def test_large_negative(self) -> None:
        assert _norm_cdf(-10.0) < 0.0001

    def test_symmetry(self) -> None:
        assert abs(_norm_cdf(1.0) + _norm_cdf(-1.0) - 1.0) < 1e-10


class TestDigitalProb:
    def test_monotonicity_spot(self, model: DigitalProb) -> None:
        """Higher spot => higher probability of finishing above strike."""
        strike = 50000.0
        t = 1 / 365.0
        sigma = 0.5

        probs = [model.probability(s, strike, t, sigma) for s in [40000, 45000, 50000, 55000, 60000]]
        for i in range(len(probs) - 1):
            assert probs[i] < probs[i + 1], f"prob({probs[i]}) should be < prob({probs[i+1]})"

    def test_monotonicity_strike(self, model: DigitalProb) -> None:
        """Higher strike => lower probability."""
        spot = 50000.0
        t = 1 / 365.0
        sigma = 0.5

        probs = [model.probability(spot, k, t, sigma) for k in [40000, 45000, 50000, 55000, 60000]]
        for i in range(len(probs) - 1):
            assert probs[i] > probs[i + 1], f"prob(K={40000+5000*i}) should be > prob(K={45000+5000*i})"

    def test_at_the_money(self, model: DigitalProb) -> None:
        """ATM with low vol and short expiry should be close to 50%."""
        p = model.probability(50000, 50000, 1 / 365, 0.3)
        assert 0.35 < p < 0.65

    def test_deep_itm(self, model: DigitalProb) -> None:
        p = model.probability(60000, 40000, 1 / 365, 0.3)
        assert p > 0.95

    def test_deep_otm(self, model: DigitalProb) -> None:
        p = model.probability(40000, 60000, 1 / 365, 0.3)
        assert p < 0.05

    def test_expired_above(self, model: DigitalProb) -> None:
        assert model.probability(55000, 50000, 0.0, 0.5) == 1.0

    def test_expired_below(self, model: DigitalProb) -> None:
        assert model.probability(45000, 50000, 0.0, 0.5) == 0.0

    def test_fair_cents_range(self, model: DigitalProb) -> None:
        """Fair cents always in [1, 99]."""
        for spot in [30000, 50000, 70000]:
            for strike in [40000, 50000, 60000]:
                c = model.fair_cents(spot, strike, 1 / 365, 0.5)
                assert 1 <= c <= 99

    def test_confidence_low_vol(self, model: DigitalProb) -> None:
        assert model.confidence(0.1) > 0.9

    def test_confidence_high_vol(self, model: DigitalProb) -> None:
        assert model.confidence(1.5) < 0.5

    def test_confidence_bounds(self, model: DigitalProb) -> None:
        assert model.confidence(0.0) == 1.0
        assert model.confidence(10.0) == 0.0
