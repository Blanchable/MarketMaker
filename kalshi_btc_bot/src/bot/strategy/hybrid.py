"""Hybrid controller – orchestrates market-making and sniping."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from bot.config import BotConfig, get_config
from bot.infra.log import get_logger
from bot.infra.metrics import metrics
from bot.infra.time import now_ms, years_until
from bot.kalshi.market_discovery import MarketDiscovery, TradeableMarket
from bot.kalshi.order_manager import OrderManager
from bot.kalshi.portfolio import PortfolioTracker
from bot.kalshi.rest import KalshiRestClient
from bot.kalshi.ws import KalshiWsClient
from bot.pricing.btc_feed import BtcSpotFeed
from bot.pricing.fair_value import FairValueEngine
from bot.pricing.vol import VolEstimator
from bot.strategy.filters import check_market_maker_filters, check_sniper_filters
from bot.strategy.market_maker import MarketMakerStrategy
from bot.strategy.risk import RiskEngine, RiskSnapshot
from bot.strategy.sniper import SniperStrategy

log = get_logger(__name__)


class HybridController:
    """Main loop: refresh data, evaluate risk, run MM + sniper."""

    def __init__(
        self,
        rest: KalshiRestClient,
        ws: KalshiWsClient,
        spot_feed: BtcSpotFeed,
        cfg: BotConfig | None = None,
        paper: bool = True,
    ) -> None:
        self.cfg = cfg or get_config()
        self.rest = rest
        self.ws = ws
        self.spot_feed = spot_feed
        self.paper = paper

        self.order_mgr = OrderManager(rest, self.cfg)
        self.risk = RiskEngine(self.cfg)
        self.portfolio = PortfolioTracker(rest, self.cfg)
        self.discovery = MarketDiscovery(rest, self.cfg)
        self.fv_engine = FairValueEngine()
        self.vol = VolEstimator(sample_interval_sec=5.0)

        self.mm = MarketMakerStrategy(self.order_mgr, self.risk, self.cfg)
        self.sniper = SniperStrategy(self.order_mgr, self.risk, self.cfg)

        self._running = False
        self._last_market_refresh: float = 0
        self._last_portfolio_refresh: float = 0
        self._last_status_emit: float = 0

    async def start(self) -> None:
        """Initialize connections and begin main loop."""
        self._running = True
        log.info(
            "HybridController starting | env=%s paper=%s live=%s",
            self.cfg.environment,
            self.paper,
            self.cfg.live_trading,
        )

        # Initial data load
        await self.discovery.refresh_series()
        await self.discovery.discover()
        await self.portfolio.refresh()

        # Connect WS and subscribe
        try:
            await self.ws.connect()
            tickers = self.discovery.tickers
            if tickers:
                await self.ws.subscribe(tickers)
        except Exception as exc:
            log.error("WS connect failed: %s – running REST-only mode", exc)

        # Run main loops concurrently
        tasks = [
            asyncio.create_task(self._main_loop()),
            asyncio.create_task(self._spot_loop()),
        ]
        if self.ws.is_connected:
            tasks.append(asyncio.create_task(self.ws.listen()))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        self._running = False
        log.info("Shutting down – cancelling all orders")
        try:
            await self.order_mgr.cancel_all()
        except Exception as exc:
            log.error("Error cancelling orders on shutdown: %s", exc)
        await self.ws.close()
        await self.spot_feed.close()
        await self.rest.close()

    async def _spot_loop(self) -> None:
        """Continuously poll BTC spot and update vol estimator."""
        while self._running:
            try:
                quote = await self.spot_feed.get_spot()
                self.vol.update(quote.price)
                metrics.gauge("btc_spot", quote.price)
                metrics.gauge("vol_annualized", self.vol.vol_annualized)
            except Exception as exc:
                log.warning("Spot feed error: %s", exc)
            await asyncio.sleep(5.0)

    async def _main_loop(self) -> None:
        """Core strategy loop."""
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                log.error("Main loop error: %s", exc)
            await asyncio.sleep(1.0)

    def _emit_status_json(self, risk_snap: Any | None = None) -> None:
        """Print a STATUS_JSON line to stdout for the GUI to parse."""
        spot = self.spot_feed.last_price
        sigma = self.vol.vol_annualized
        vol_ok = self.vol.vol_ok(self.cfg.mm_only_when_vol_below)

        mm_active = vol_ok and self.cfg.mm_enabled
        sniper_active = self.cfg.sniper_enabled
        if mm_active and sniper_active:
            mode = "HYBRID"
        elif mm_active:
            mode = "MM"
        elif sniper_active:
            mode = "SNIPER"
        else:
            mode = "OFF"

        payload = {
            "connected": self.ws.is_connected,
            "spot": round(spot, 2) if spot else 0.0,
            "vol": round(sigma, 4),
            "pnl_realized_today": round(risk_snap.realized_pnl_today, 2) if risk_snap else 0.0,
            "pnl_unrealized": round(risk_snap.unrealized_pnl, 2) if risk_snap else 0.0,
            "gross_exposure": round(risk_snap.gross_exposure, 2) if risk_snap else 0.0,
            "net_exposure": round(risk_snap.net_exposure, 2) if risk_snap else 0.0,
            "kill_switch": risk_snap.kill_switch_triggered if risk_snap else False,
            "markets_quoted": len(self.discovery.tradeable),
            "mode": mode,
        }
        line = "STATUS_JSON: " + json.dumps(payload)
        print(line, flush=True)

    async def _tick(self) -> None:
        now = now_ms() / 1000.0

        # Periodic market refresh
        if now - self._last_market_refresh > self.cfg.refresh_markets_seconds:
            await self.discovery.discover()
            new_tickers = self.discovery.tickers
            if self.ws.is_connected and new_tickers:
                await self.ws.subscribe(new_tickers)
            self._last_market_refresh = now

        # Periodic portfolio refresh
        if now - self._last_portfolio_refresh > 10:
            await self.portfolio.refresh()
            self._last_portfolio_refresh = now

        # Build risk snapshot
        mid_prices: dict[str, float] = {}
        for ticker in self.discovery.tickers:
            st = self.ws.get_state(ticker)
            if st and st.mid > 0:
                mid_prices[ticker] = st.mid

        risk_snap = self.risk.evaluate(self.portfolio.snapshot, mid_prices)

        # Emit STATUS_JSON every ~2 seconds for the GUI
        if now - self._last_status_emit >= 2.0:
            try:
                self._emit_status_json(risk_snap)
            except Exception:
                pass
            self._last_status_emit = now

        # Kill switch
        if risk_snap.kill_switch_triggered:
            log.critical("Kill switch active: %s – cancelling all", risk_snap.kill_reason)
            await self.order_mgr.cancel_all()
            return

        # Check spot feed health
        if self.spot_feed.is_stale(max_age_ms=self.cfg.stale_ms * 2):
            log.warning("Spot feed stale – cancelling all orders")
            await self.order_mgr.cancel_all()
            return

        spot = self.spot_feed.last_price
        if spot is None:
            return

        sigma = self.vol.vol_annualized
        vol_ok = self.vol.vol_ok(self.cfg.mm_only_when_vol_below)

        # ── WS health: global + per-ticker stale checks ─────────────
        global_timeout = 180.0 if self.cfg.is_demo else 60.0
        ticker_timeout = 180.0 if self.cfg.is_demo else 60.0

        if self.ws.is_connected and self.ws.is_globally_stale(global_timeout):
            age = self.ws.global_age()
            log.warning(
                "WS connection stale (no message in %.0fs, limit %.0fs) – cancelling all orders",
                age, global_timeout,
            )
            await self.order_mgr.cancel_all()
            return

        stale_tickers: list[str] = []
        if self.ws.is_connected:
            stale_tickers = self.ws.stale_tickers(self.discovery.tickers, ticker_timeout)
            if stale_tickers:
                display = stale_tickers[:10]
                log.info(
                    "WS per-ticker stale: %d ticker(s) beyond %.0fs – cancelling those only: %s%s",
                    len(stale_tickers),
                    ticker_timeout,
                    display,
                    " ..." if len(stale_tickers) > 10 else "",
                )
                for t in stale_tickers:
                    await self.order_mgr.cancel_market(t)

        stale_set = set(stale_tickers)

        # Process each market
        for mkt in self.discovery.tradeable:
            # Skip tickers already cancelled above as per-ticker stale
            if mkt.ticker in stale_set:
                continue

            ws_state = self.ws.get_state(mkt.ticker)
            t_years = years_until(mkt.expiration_time)
            fv = self.fv_engine.compute(mkt.ticker, spot, mkt.strike, t_years, sigma)

            # Market-making
            if vol_ok and self.cfg.mm_enabled:
                filt = check_market_maker_filters(mkt, ws_state, vol_ok, self.cfg)
                if filt.passed and ws_state:
                    net_pos = self.portfolio.snapshot.net_position_contracts(mkt.ticker)
                    await self.mm.update(mkt, ws_state, fv, net_pos, risk_snap)
                else:
                    await self.order_mgr.cancel_market(mkt.ticker)
            else:
                await self.order_mgr.cancel_market(mkt.ticker)

            # Sniper (always check, but with tighter edge in high-vol)
            if self.cfg.sniper_enabled and ws_state:
                sniper_filt = check_sniper_filters(mkt, ws_state, self.cfg)
                if sniper_filt.passed:
                    signal = self.sniper.evaluate(mkt, ws_state, fv, risk_snap)
                    # In high-vol mode, require 2x edge
                    if signal and not vol_ok:
                        if signal.edge_cents < self.cfg.sniper_min_edge_cents * 2:
                            signal = None
                    if signal:
                        await self.sniper.execute(signal)
