"""Combine pricing inputs to produce a fair value for each market.

For crypto (BTC): spot + vol + Black-Scholes digital call model.
For sports: mid-price of the book, cross-checked against the complementary market.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.pricing.digital_prob import DigitalProb


@dataclass
class FairValue:
    """Fair value output for one market."""
    ticker: str
    fair_cents: int
    confidence: float
    # Crypto-specific
    spot: float = 0.0
    strike: float = 0.0
    t_years: float = 0.0
    sigma: float = 0.0
    prob: float = 0.0


class FairValueEngine:
    """Computes fair value. Works in both crypto and sports mode."""

    def __init__(self) -> None:
        self._prob_model = DigitalProb()

    def compute(
        self,
        ticker: str,
        spot: float,
        strike: float,
        t_years: float,
        sigma: float,
    ) -> FairValue:
        """Crypto mode: Black-Scholes digital call fair value."""
        prob = self._prob_model.probability(spot, strike, t_years, sigma)
        fair_c = self._prob_model.fair_cents(spot, strike, t_years, sigma)
        conf = self._prob_model.confidence(sigma)
        return FairValue(
            ticker=ticker,
            fair_cents=fair_c,
            confidence=conf,
            spot=spot,
            strike=strike,
            t_years=t_years,
            sigma=sigma,
            prob=prob,
        )

    def compute_sports(
        self,
        ticker: str,
        yes_bid: int,
        yes_ask: int,
        comp_yes_bid: int = 0,
        comp_yes_ask: int = 0,
    ) -> FairValue:
        """Sports mode: fair value from mid, cross-checked against complementary market.

        For a winner market (Team A vs Team B):
          - P(A) + P(B) should equal ~100 cents (minus vig).
          - If we know the other side's mid, we can anchor:
            fair_A = 100 - comp_mid  (then average with own mid).
        """
        own_mid = 0.0
        if yes_bid > 0 and yes_ask > 0:
            own_mid = (yes_bid + yes_ask) / 2.0

        comp_mid = 0.0
        if comp_yes_bid > 0 and comp_yes_ask > 0:
            comp_mid = (comp_yes_bid + comp_yes_ask) / 2.0

        if own_mid > 0 and comp_mid > 0:
            implied_from_comp = 100.0 - comp_mid
            # Average own mid with the complementary-implied value
            fair = (own_mid + implied_from_comp) / 2.0
            confidence = 0.9  # both sides have data
        elif own_mid > 0:
            fair = own_mid
            confidence = 0.6  # only own book
        elif comp_mid > 0:
            fair = 100.0 - comp_mid
            confidence = 0.5  # only complementary
        else:
            fair = 50.0  # no data at all
            confidence = 0.1

        fair_c = max(1, min(99, round(fair)))

        return FairValue(
            ticker=ticker,
            fair_cents=fair_c,
            confidence=confidence,
        )
