"""Digital option probability model – P(BTC > K at expiry)."""

from __future__ import annotations

import math
from dataclasses import dataclass


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via the error function (no scipy needed)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class DigitalProb:
    """Black-Scholes-style digital call probability for BTC binary markets.

    Assumptions:
    - Lognormal spot dynamics with zero drift (short horizon).
    - No dividends.
    """

    sigma_max: float = 2.0  # cap for confidence calculation

    def probability(
        self,
        spot: float,
        strike: float,
        t_years: float,
        sigma: float,
    ) -> float:
        """P(S_T > K) under lognormal model.

        Parameters
        ----------
        spot : current BTC price
        strike : binary strike level
        t_years : time to expiration in years
        sigma : annualized realized vol
        """
        if spot <= 0 or strike <= 0:
            return 0.5
        if t_years <= 0:
            return 1.0 if spot >= strike else 0.0
        if sigma <= 0:
            return 1.0 if spot >= strike else 0.0

        sqrt_t = math.sqrt(t_years)
        d2 = (math.log(spot / strike) - 0.5 * sigma**2 * t_years) / (sigma * sqrt_t)
        return _norm_cdf(d2)

    def fair_cents(
        self,
        spot: float,
        strike: float,
        t_years: float,
        sigma: float,
    ) -> int:
        """Fair value in cents [1..99]."""
        p = self.probability(spot, strike, t_years, sigma)
        c = round(p * 100)
        return max(1, min(99, c))

    def confidence(self, sigma: float) -> float:
        """Confidence score in [0, 1]: lower vol → higher confidence."""
        if sigma <= 0:
            return 1.0
        return max(0.0, min(1.0, 1.0 - sigma / self.sigma_max))
