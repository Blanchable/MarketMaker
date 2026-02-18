"""Realized volatility estimator using EWMA of log returns."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from bot.infra.log import get_logger
from bot.infra.metrics import metrics

log = get_logger(__name__)

SECONDS_PER_YEAR = 365.25 * 24 * 3600


@dataclass
class VolEstimator:
    """EWMA realized vol from streaming spot prices.

    Parameters
    ----------
    lam : EWMA decay factor (closer to 1 → slower decay).
    sample_interval_sec : expected interval between observations.
    max_window : max observations kept for diagnostics.
    """

    lam: float = 0.94
    sample_interval_sec: float = 5.0
    max_window: int = 2000

    _prev_price: float | None = field(default=None, init=False, repr=False)
    _ewma_var: float = field(default=0.0, init=False, repr=False)
    _n: int = field(default=0, init=False, repr=False)
    _returns: deque[float] = field(default_factory=lambda: deque(maxlen=2000), init=False, repr=False)

    def update(self, price: float) -> None:
        """Feed a new spot observation."""
        if price <= 0:
            return
        if self._prev_price is not None and self._prev_price > 0:
            r = math.log(price / self._prev_price)
            self._returns.append(r)
            self._ewma_var = (1 - self.lam) * r * r + self.lam * self._ewma_var
            self._n += 1
        self._prev_price = price

    @property
    def variance_per_interval(self) -> float:
        return self._ewma_var

    @property
    def vol_annualized(self) -> float:
        """Annualized volatility (σ)."""
        if self._ewma_var <= 0 or self.sample_interval_sec <= 0:
            return 0.0
        intervals_per_year = SECONDS_PER_YEAR / self.sample_interval_sec
        return math.sqrt(self._ewma_var * intervals_per_year)

    def vol_ok(self, threshold: float) -> bool:
        """True if current annualized vol is below threshold."""
        if self._n < 5:
            return False  # not enough data yet
        return self.vol_annualized <= threshold

    @property
    def n_obs(self) -> int:
        return self._n

    def reset(self) -> None:
        self._prev_price = None
        self._ewma_var = 0.0
        self._n = 0
        self._returns.clear()
