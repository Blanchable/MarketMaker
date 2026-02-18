"""Pre-trade filters applied before quoting or sniping a market."""

from __future__ import annotations

from dataclasses import dataclass

from bot.config import BotConfig, get_config
from bot.infra.log import get_logger
from bot.kalshi.market_discovery import TradeableMarket
from bot.kalshi.ws import MarketState

log = get_logger(__name__)


@dataclass
class FilterResult:
    passed: bool
    reason: str = ""


def check_market_maker_filters(
    market: TradeableMarket,
    ws_state: MarketState | None,
    vol_ok: bool,
    cfg: BotConfig | None = None,
) -> FilterResult:
    """Return FilterResult indicating whether we may quote this market."""
    cfg = cfg or get_config()

    if market.time_to_expiry_sec < cfg.no_trade_window_seconds:
        return FilterResult(False, "too close to expiration")

    if not vol_ok:
        return FilterResult(False, "vol too high")

    if ws_state is None:
        return FilterResult(False, "no WS state")

    # Need at least some price data to quote around
    if not ws_state.has_data():
        return FilterResult(False, "no WS data received yet")

    spread = ws_state.spread
    if spread < cfg.min_spread_cents:
        return FilterResult(False, f"spread {spread}c < min {cfg.min_spread_cents}c")
    if spread > cfg.max_spread_cents:
        return FilterResult(False, f"spread {spread}c > max {cfg.max_spread_cents}c")

    return FilterResult(True)


def check_sniper_filters(
    market: TradeableMarket,
    ws_state: MarketState | None,
    cfg: BotConfig | None = None,
) -> FilterResult:
    """Minimal filters for sniper – more permissive than MM."""
    cfg = cfg or get_config()

    if market.time_to_expiry_sec < cfg.no_trade_window_seconds:
        return FilterResult(False, "too close to expiration")

    if ws_state is None:
        return FilterResult(False, "no WS state")

    if not ws_state.has_data():
        return FilterResult(False, "no WS data received yet")

    return FilterResult(True)
