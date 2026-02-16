"""Risk engine – exposure tracking, PnL, kill switch."""

from __future__ import annotations

from dataclasses import dataclass, field

from bot.config import BotConfig, get_config
from bot.infra.log import get_logger
from bot.infra.metrics import metrics
from bot.kalshi.models import Fill, Position
from bot.kalshi.portfolio import PortfolioSnapshot

log = get_logger(__name__)


@dataclass
class RiskSnapshot:
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    per_market_exposure: dict[str, float] = field(default_factory=dict)
    realized_pnl_today: float = 0.0
    unrealized_pnl: float = 0.0
    kill_switch_triggered: bool = False
    kill_reason: str = ""
    can_open_new: bool = True


class RiskEngine:
    """Evaluates portfolio risk and enforces hard limits."""

    def __init__(self, cfg: BotConfig | None = None) -> None:
        self.cfg = cfg or get_config()
        self._kill_active = False
        self._kill_reason = ""

    @property
    def is_killed(self) -> bool:
        return self._kill_active

    def trigger_kill(self, reason: str) -> None:
        self._kill_active = True
        self._kill_reason = reason
        log.critical("KILL SWITCH TRIGGERED: %s", reason)
        metrics.gauge("kill_switch", 1.0)

    def reset_kill(self) -> None:
        self._kill_active = False
        self._kill_reason = ""
        metrics.gauge("kill_switch", 0.0)

    def evaluate(
        self,
        portfolio: PortfolioSnapshot,
        mid_prices: dict[str, float] | None = None,
    ) -> RiskSnapshot:
        """Compute risk snapshot from current portfolio."""
        mid_prices = mid_prices or {}

        gross = 0.0
        net = 0.0
        per_market: dict[str, float] = {}
        realized_pnl = 0.0
        unrealized = 0.0

        for pos in portfolio.positions:
            qty = pos.position  # positive = long YES
            if qty == 0:
                continue

            # Worst-case exposure: max loss per contract is price paid
            # For YES long: loss = price_paid * qty (bounded by 100c * qty)
            # For YES short: loss = (100 - price_sold) * qty
            # Approximate: exposure = abs(qty) * 100 cents = abs(qty) dollars
            exposure_dollars = abs(qty) * 1.0  # each contract max $1
            gross += exposure_dollars

            # Directional: long YES is +, short YES is -
            direction = qty * 1.0
            net += direction

            per_market[pos.ticker] = exposure_dollars

            # Realized PnL from position object
            realized_pnl += pos.realized_pnl

            # Unrealized: mid_price * qty - cost (approximate)
            mid_cents = mid_prices.get(pos.ticker, 50.0)
            if qty > 0:
                unrealized += qty * (mid_cents / 100.0 - 0.50)
            else:
                unrealized += abs(qty) * (0.50 - mid_cents / 100.0)

        snap = RiskSnapshot(
            gross_exposure=gross,
            net_exposure=net,
            per_market_exposure=per_market,
            realized_pnl_today=realized_pnl,
            unrealized_pnl=unrealized,
        )

        # Kill switch check
        if self._kill_active:
            snap.kill_switch_triggered = True
            snap.kill_reason = self._kill_reason
            snap.can_open_new = False
            return snap

        if realized_pnl <= -self.cfg.daily_stop_dollars:
            self.trigger_kill(
                f"Daily stop hit: realized PnL ${realized_pnl:.2f} <= -${self.cfg.daily_stop_dollars}"
            )
            snap.kill_switch_triggered = True
            snap.kill_reason = self._kill_reason
            snap.can_open_new = False
            return snap

        # Soft limits
        if gross > self.cfg.max_gross_exposure_dollars:
            snap.can_open_new = False
            log.warning("Gross exposure $%.2f exceeds limit $%.2f", gross, self.cfg.max_gross_exposure_dollars)

        if abs(net) > self.cfg.max_net_exposure_dollars:
            snap.can_open_new = False
            log.warning("Net exposure $%.2f exceeds limit $%.2f", abs(net), self.cfg.max_net_exposure_dollars)

        # Update metrics
        metrics.gauge("gross_exposure", gross)
        metrics.gauge("net_exposure", net)
        metrics.gauge("realized_pnl", realized_pnl)
        metrics.gauge("unrealized_pnl", unrealized)

        return snap

    def can_trade_market(self, ticker: str, snap: RiskSnapshot) -> bool:
        """Check if we can add exposure to a specific market."""
        if snap.kill_switch_triggered:
            return False
        if not snap.can_open_new:
            return False
        mkt_exp = snap.per_market_exposure.get(ticker, 0.0)
        if mkt_exp >= self.cfg.max_exposure_per_market_dollars:
            return False
        return True

    def max_additional_contracts(self, ticker: str, snap: RiskSnapshot) -> int:
        """How many more contracts can we add for this market?"""
        if not self.can_trade_market(ticker, snap):
            return 0
        mkt_exp = snap.per_market_exposure.get(ticker, 0.0)
        remaining_market = self.cfg.max_exposure_per_market_dollars - mkt_exp
        remaining_gross = self.cfg.max_gross_exposure_dollars - snap.gross_exposure
        max_dollars = max(0.0, min(remaining_market, remaining_gross))
        return min(int(max_dollars), self.cfg.max_order_size_contracts)
