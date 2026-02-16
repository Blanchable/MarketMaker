"""Central configuration loaded from environment / .env file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class BotConfig(BaseSettings):
    """All tuneable knobs for the Kalshi BTC hybrid bot."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # ── Environment ──────────────────────────────────────────────────
    environment: Literal["demo", "prod"] = "demo"
    kalshi_key_id: str = ""
    kalshi_private_key_path: Path = Path("./kalshi.key")
    live_trading: bool = False

    # ── Market Selection ─────────────────────────────────────────────
    series_category: str = "crypto"
    series_tags: str = "btc,bitcoin"
    btc_spot_feed: str = "coinbase"
    btc_symbol: str = "BTC-USD"

    refresh_markets_seconds: int = 30
    stale_ms: int = 3000

    market_status: str = "open"
    min_24h_volume: int = 1000
    max_spread_cents: int = 8
    min_spread_cents: int = 2
    min_depth_contracts: int = 10

    # ── Demo-relaxed discovery filters ───────────────────────────────
    demo_min_spread_cents: int = 0
    demo_max_spread_cents: int = 30
    demo_allow_missing_book: bool = True

    # ── Discovery refresh cadence ────────────────────────────────────
    discovery_refresh_seconds_demo: int = 180
    discovery_refresh_seconds_prod: int = 60

    # ── WS stale timeouts ────────────────────────────────────────────
    ws_global_timeout_demo: int = 300
    ws_global_timeout_prod: int = 60
    ws_ticker_timeout_demo: int = 300
    ws_ticker_timeout_prod: int = 60

    # ── Risk / Capital ───────────────────────────────────────────────
    start_bankroll_dollars: float = 500.0
    daily_stop_dollars: float = 200.0
    max_gross_exposure_dollars: float = 250.0
    max_net_exposure_dollars: float = 150.0
    max_exposure_per_market_dollars: float = 125.0
    max_order_size_contracts: int = 25
    no_trade_window_seconds: int = 300

    # ── Market-Making ────────────────────────────────────────────────
    mm_enabled: bool = True
    mm_quote_size_contracts: int = 10
    mm_edge_cents: int = 1
    mm_inventory_skew: float = 0.25
    mm_cancel_requote_ms: int = 800
    mm_only_when_vol_below: float = 0.55

    # ── Sniper ───────────────────────────────────────────────────────
    sniper_enabled: bool = True
    sniper_min_edge_cents: int = 6
    sniper_max_slippage_cents: int = 2
    sniper_order_tif: str = "immediate_or_cancel"
    sniper_cooldown_seconds: int = 10

    # ── Derived helpers ──────────────────────────────────────────────
    @property
    def rest_base(self) -> str:
        if self.environment == "prod":
            return "https://api.elections.kalshi.com/trade-api/v2"
        return "https://demo-api.kalshi.co/trade-api/v2"

    @property
    def ws_url(self) -> str:
        if self.environment == "prod":
            return "wss://api.elections.kalshi.com/trade-api/ws/v2"
        return "wss://demo-api.kalshi.co/trade-api/ws/v2"

    @property
    def is_demo(self) -> bool:
        return self.environment == "demo"

    @property
    def effective_min_spread(self) -> int:
        return self.demo_min_spread_cents if self.is_demo else self.min_spread_cents

    @property
    def effective_max_spread(self) -> int:
        return self.demo_max_spread_cents if self.is_demo else self.max_spread_cents

    @property
    def effective_discovery_refresh(self) -> int:
        return self.discovery_refresh_seconds_demo if self.is_demo else self.discovery_refresh_seconds_prod

    @property
    def effective_ws_global_timeout(self) -> float:
        return float(self.ws_global_timeout_demo if self.is_demo else self.ws_global_timeout_prod)

    @property
    def effective_ws_ticker_timeout(self) -> float:
        return float(self.ws_ticker_timeout_demo if self.is_demo else self.ws_ticker_timeout_prod)

    @field_validator("series_tags")
    @classmethod
    def _tags_lower(cls, v: str) -> str:
        return v.lower().strip()

    def to_gui_dict(self) -> dict:
        """Serialize config fields to a dict suitable for the GUI config store."""
        return {
            "environment": self.environment,
            "key_id": self.kalshi_key_id,
            "private_key_path": str(self.kalshi_private_key_path),
            "live_trading": self.live_trading,
            "mm_enabled": self.mm_enabled,
            "sniper_enabled": self.sniper_enabled,
            "daily_stop_dollars": self.daily_stop_dollars,
            "max_gross_exposure_dollars": self.max_gross_exposure_dollars,
            "max_net_exposure_dollars": self.max_net_exposure_dollars,
            "max_exposure_per_market_dollars": self.max_exposure_per_market_dollars,
            "max_order_size_contracts": self.max_order_size_contracts,
        }


_cfg: BotConfig | None = None


def get_config() -> BotConfig:
    global _cfg
    if _cfg is None:
        _cfg = BotConfig()
    return _cfg


def reset_config() -> None:
    global _cfg
    _cfg = None
