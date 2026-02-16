"""Combine spot, vol, and probability model to produce a fair value for each market."""

from __future__ import annotations

from dataclasses import dataclass

from bot.pricing.btc_feed import BtcSpotFeed
from bot.pricing.digital_prob import DigitalProb
from bot.pricing.vol import VolEstimator


@dataclass
class FairValue:
    """Fair value output for one market."""

    ticker: str
    spot: float
    strike: float
    t_years: float
    sigma: float
    prob: float
    fair_cents: int
    confidence: float


class FairValueEngine:
    """Stateless calculator that combines spot + vol + model."""

    def __init__(self) -> None:
        self.model = DigitalProb()

    def compute(
        self,
        ticker: str,
        spot: float,
        strike: float,
        t_years: float,
        sigma: float,
    ) -> FairValue:
        prob = self.model.probability(spot, strike, t_years, sigma)
        fair_c = self.model.fair_cents(spot, strike, t_years, sigma)
        conf = self.model.confidence(sigma)
        return FairValue(
            ticker=ticker,
            spot=spot,
            strike=strike,
            t_years=t_years,
            sigma=sigma,
            prob=prob,
            fair_cents=fair_c,
            confidence=conf,
        )
