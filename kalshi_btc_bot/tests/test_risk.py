"""Tests for the risk engine."""

from __future__ import annotations

import pytest

from bot.config import BotConfig
from bot.kalshi.models import Position
from bot.kalshi.portfolio import PortfolioSnapshot
from bot.strategy.risk import RiskEngine


@pytest.fixture
def cfg() -> BotConfig:
    return BotConfig(
        daily_stop_dollars=100,
        max_gross_exposure_dollars=200,
        max_net_exposure_dollars=100,
        max_exposure_per_market_dollars=75,
        max_order_size_contracts=20,
    )


@pytest.fixture
def risk(cfg: BotConfig) -> RiskEngine:
    return RiskEngine(cfg)


def _snapshot(positions: list[Position] | None = None) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        balance_cents=50000,
        positions=positions or [],
    )


class TestRiskEngine:
    def test_empty_portfolio_allows_trading(self, risk: RiskEngine) -> None:
        snap = risk.evaluate(_snapshot())
        assert not snap.kill_switch_triggered
        assert snap.can_open_new
        assert snap.gross_exposure == 0.0

    def test_kill_switch_on_daily_loss(self, risk: RiskEngine) -> None:
        positions = [
            Position(ticker="TEST-1", position=10, realized_pnl=-150.0),
        ]
        snap = risk.evaluate(_snapshot(positions))
        assert snap.kill_switch_triggered
        assert not snap.can_open_new
        assert risk.is_killed

    def test_gross_exposure_cap(self, risk: RiskEngine, cfg: BotConfig) -> None:
        positions = [
            Position(ticker=f"MKT-{i}", position=50) for i in range(5)
        ]
        snap = risk.evaluate(_snapshot(positions))
        assert snap.gross_exposure > cfg.max_gross_exposure_dollars
        assert not snap.can_open_new

    def test_per_market_cap(self, risk: RiskEngine, cfg: BotConfig) -> None:
        positions = [
            Position(ticker="BIG-MARKET", position=100),
        ]
        snap = risk.evaluate(_snapshot(positions))
        assert not risk.can_trade_market("BIG-MARKET", snap)

    def test_max_additional_contracts(self, risk: RiskEngine) -> None:
        snap = risk.evaluate(_snapshot())
        max_c = risk.max_additional_contracts("NEW-MKT", snap)
        assert max_c > 0
        assert max_c <= 20  # max_order_size_contracts

    def test_kill_switch_manual_trigger(self, risk: RiskEngine) -> None:
        risk.trigger_kill("manual test")
        assert risk.is_killed
        snap = risk.evaluate(_snapshot())
        assert snap.kill_switch_triggered
        assert snap.kill_reason == "manual test"

    def test_kill_switch_reset(self, risk: RiskEngine) -> None:
        risk.trigger_kill("test")
        assert risk.is_killed
        risk.reset_kill()
        assert not risk.is_killed

    def test_net_exposure_cap(self, risk: RiskEngine, cfg: BotConfig) -> None:
        positions = [
            Position(ticker=f"MKT-{i}", position=30) for i in range(4)
        ]
        snap = risk.evaluate(_snapshot(positions))
        assert abs(snap.net_exposure) > cfg.max_net_exposure_dollars
        assert not snap.can_open_new
