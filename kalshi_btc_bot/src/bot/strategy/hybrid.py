"""Hybrid controller – orchestrates market-making and sniping."""

from __future__ import annotations

import asyncio
import json
import random
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
from bot.pnl.tracker import PnlTracker
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
        spot_feed: BtcSpotFeed | None = None,
        cfg: BotConfig | None = None,
        paper: bool = True,
    ) -> None:
        self.cfg = cfg or get_config()
        self.rest = rest
        self.ws = ws
        self.spot_feed = spot_feed
        self.paper = paper

        self.pnl_tracker = PnlTracker()
        self.order_mgr = OrderManager(rest, self.cfg)
        self.risk = RiskEngine(self.cfg, pnl_tracker=self.pnl_tracker)
        self.portfolio = PortfolioTracker(rest, self.cfg, pnl_tracker=self.pnl_tracker)
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
        self._running = True
        log.info(
            "HybridController starting | env=%s paper=%s live=%s mode=%s",
            self.cfg.environment, self.paper, self.cfg.live_trading, self.cfg.market_mode,
        )

        # Early auth check – fail fast with a clear message
        auth_ok = await self.rest.check_auth()
        if not auth_ok:
            log.error(
                "Continuing without authenticated access. "
                "Portfolio, orders, and WS will not work until credentials are fixed."
            )

        await self.discovery.refresh_series()
        await self.discovery.discover()
        await self.portfolio.refresh()

        try:
            await self.ws.connect()
            tickers = self.discovery.tickers
            if tickers:
                await self.ws.subscribe(tickers)
            else:
                log.warning("No tickers to subscribe – WS connected but idle")
        except Exception as exc:
            log.error("WS connect failed: %s – running REST-only mode", exc)

        if not self.discovery.tradeable:
            log.warning("No tradeable tickers after filters; strategy will idle until next discovery cycle.")

        tasks = [
            asyncio.create_task(self._main_loop()),
        ]
        # Only run BTC spot loop in crypto mode
        if not self.cfg.is_sports and self.spot_feed:
            tasks.append(asyncio.create_task(self._spot_loop()))
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
        if self.spot_feed:
            await self.spot_feed.close()
        await self.rest.close()
        self.pnl_tracker.close()

    async def _spot_loop(self) -> None:
        while self._running and self.spot_feed:
            try:
                quote = await self.spot_feed.get_spot()
                self.vol.update(quote.price)
                metrics.gauge("btc_spot", quote.price)
                metrics.gauge("vol_annualized", self.vol.vol_annualized)
            except Exception as exc:
                log.warning("Spot feed error: %s", exc)
            await asyncio.sleep(5.0)

    async def _main_loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                log.error("Main loop error: %s", exc)
            await asyncio.sleep(1.0)

    def _emit_status_json(self, risk_snap: Any | None = None) -> None:
        spot = self.spot_feed.last_price if self.spot_feed else 0.0
        sigma = self.vol.vol_annualized
        vol_ok = self.vol.vol_ok(self.cfg.mm_only_when_vol_below)

        mm_active = self.cfg.mm_enabled and (vol_ok or self.cfg.is_sports)
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
            "pnl_realized_today": round(self.pnl_tracker.realized_today, 2),
            "pnl_realized_total": round(self.pnl_tracker.realized_total, 2),
            "pnl_unrealized": round(risk_snap.unrealized_pnl, 2) if risk_snap else 0.0,
            "fees_today": round(self.pnl_tracker.fees_today, 2),
            "gross_exposure": round(risk_snap.gross_exposure, 2) if risk_snap else 0.0,
            "net_exposure": round(risk_snap.net_exposure, 2) if risk_snap else 0.0,
            "kill_switch": risk_snap.kill_switch_triggered if risk_snap else False,
            "markets_quoted": len(self.discovery.tradeable),
            "mode": mode,
            "market_mode": self.cfg.market_mode,
            "tradeable_tickers": len(self.discovery.tradeable),
            "subscribed_tickers": self.ws.subscribed_count,
        }
        print("STATUS_JSON: " + json.dumps(payload), flush=True)

    async def _tick(self) -> None:
        now = now_ms() / 1000.0

        refresh_interval = self.cfg.effective_discovery_refresh
        if now - self._last_market_refresh > refresh_interval:
            await self.discovery.discover()
            new_tickers = self.discovery.tickers
            if self.ws.is_connected and new_tickers:
                await self.ws.subscribe(new_tickers)
            self._last_market_refresh = now + random.uniform(0, 2)

        if now - self._last_portfolio_refresh > 10:
            await self.portfolio.refresh()
            self._last_portfolio_refresh = now

        mid_prices: dict[str, float] = {}
        for ticker in self.discovery.tickers:
            st = self.ws.get_state(ticker)
            if st and st.mid > 0:
                mid_prices[ticker] = st.mid

        risk_snap = self.risk.evaluate(self.portfolio.snapshot, mid_prices)

        if now - self._last_status_emit >= 2.0:
            try:
                self._emit_status_json(risk_snap)
            except Exception:
                pass
            self._last_status_emit = now
            self.pnl_tracker.log_diagnostics()

        if risk_snap.kill_switch_triggered:
            log.critical("Kill switch active: %s – cancelling all", risk_snap.kill_reason)
            await self.order_mgr.cancel_all()
            return

        # In crypto mode, check spot feed; in sports mode, skip
        if not self.cfg.is_sports:
            if self.spot_feed and self.spot_feed.is_stale(max_age_ms=self.cfg.stale_ms * 2):
                log.warning("Spot feed stale – cancelling all orders")
                await self.order_mgr.cancel_all()
                return

        if not self.discovery.tradeable:
            return

        # ── WS health ────────────────────────────────────────────────
        global_timeout = self.cfg.effective_ws_global_timeout
        ticker_timeout = self.cfg.effective_ws_ticker_timeout

        if self.ws.is_connected and self.ws.is_globally_stale(global_timeout):
            age = self.ws.global_age()
            log.warning("WS connection stale (%.0fs, limit %.0fs) – cancelling all", age, global_timeout)
            await self.order_mgr.cancel_all()
            return

        stale_tickers: list[str] = []
        if self.ws.is_connected:
            stale_tickers = self.ws.stale_tickers(self.discovery.tickers, ticker_timeout)
            if stale_tickers:
                log.info("WS per-ticker stale: %d ticker(s) beyond %.0fs", len(stale_tickers), ticker_timeout)
                for t in stale_tickers:
                    await self.order_mgr.cancel_market(t)
        stale_set = set(stale_tickers)

        # ── Process each market ──────────────────────────────────────
        if self.cfg.is_sports:
            await self._tick_sports(risk_snap, stale_set)
        else:
            await self._tick_crypto(risk_snap, stale_set)

    async def _tick_sports(self, risk_snap: RiskSnapshot, stale_set: set[str]) -> None:
        """Per-market logic for sports mode (mid-based fair value)."""
        for mkt in self.discovery.tradeable:
            if mkt.ticker in stale_set:
                continue

            ws_state = self.ws.get_state(mkt.ticker)

            # Get complementary market state for cross-check
            comp_bid, comp_ask = 0, 0
            if mkt.complementary_ticker:
                comp_state = self.ws.get_state(mkt.complementary_ticker)
                if comp_state:
                    comp_bid = comp_state.yes_bid
                    comp_ask = comp_state.yes_ask

            bid = ws_state.yes_bid if ws_state else mkt.yes_bid
            ask = ws_state.yes_ask if ws_state else mkt.yes_ask

            fv = self.fv_engine.compute_sports(
                mkt.ticker, bid, ask, comp_bid, comp_ask,
            )

            # Market-making (vol check skipped in sports mode)
            if self.cfg.mm_enabled:
                filt = check_market_maker_filters(mkt, ws_state, True, self.cfg)
                if filt.passed and ws_state:
                    net_pos = self.portfolio.snapshot.net_position_contracts(mkt.ticker)
                    await self.mm.update(mkt, ws_state, fv, net_pos, risk_snap)
                else:
                    await self.order_mgr.cancel_market(mkt.ticker)
            else:
                await self.order_mgr.cancel_market(mkt.ticker)

            # Sniper
            if self.cfg.sniper_enabled and ws_state:
                filt = check_sniper_filters(mkt, ws_state, self.cfg)
                if filt.passed:
                    signal = self.sniper.evaluate(mkt, ws_state, fv, risk_snap)
                    if signal:
                        await self.sniper.execute(signal)

    async def _tick_crypto(self, risk_snap: RiskSnapshot, stale_set: set[str]) -> None:
        """Per-market logic for crypto mode (BTC spot + vol based fair value)."""
        spot = self.spot_feed.last_price if self.spot_feed else None
        if spot is None:
            return

        sigma = self.vol.vol_annualized
        vol_ok = self.vol.vol_ok(self.cfg.mm_only_when_vol_below)

        for mkt in self.discovery.tradeable:
            if mkt.ticker in stale_set:
                continue

            ws_state = self.ws.get_state(mkt.ticker)
            t_years = years_until(mkt.expiration_time)
            fv = self.fv_engine.compute(mkt.ticker, spot, mkt.strike, t_years, sigma)

            if vol_ok and self.cfg.mm_enabled:
                filt = check_market_maker_filters(mkt, ws_state, vol_ok, self.cfg)
                if filt.passed and ws_state:
                    net_pos = self.portfolio.snapshot.net_position_contracts(mkt.ticker)
                    await self.mm.update(mkt, ws_state, fv, net_pos, risk_snap)
                else:
                    await self.order_mgr.cancel_market(mkt.ticker)
            else:
                await self.order_mgr.cancel_market(mkt.ticker)

            if self.cfg.sniper_enabled and ws_state:
                filt = check_sniper_filters(mkt, ws_state, self.cfg)
                if filt.passed:
                    signal = self.sniper.evaluate(mkt, ws_state, fv, risk_snap)
                    if signal and not vol_ok:
                        if signal.edge_cents < self.cfg.sniper_min_edge_cents * 2:
                            signal = None
                    if signal:
                        await self.sniper.execute(signal)
