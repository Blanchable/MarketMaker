"""Main entry point – wires up all components and starts the hybrid controller."""

from __future__ import annotations

import asyncio
import signal
from typing import Any

from bot.config import BotConfig, get_config
from bot.infra.log import get_logger, setup_logging
from bot.kalshi.rest import KalshiRestClient
from bot.kalshi.ws import KalshiWsClient
from bot.pricing.btc_feed import BtcSpotFeed
from bot.strategy.hybrid import HybridController

log = get_logger(__name__)


async def run_bot(cfg: BotConfig | None = None, paper: bool = True) -> None:
    """Start the full hybrid bot."""
    cfg = cfg or get_config()

    rest = KalshiRestClient(cfg)
    ws = KalshiWsClient(cfg)

    spot_feed = None
    if not cfg.is_sports:
        spot_feed = BtcSpotFeed(cfg.btc_spot_feed, cfg.btc_symbol)

    controller = HybridController(
        rest=rest,
        ws=ws,
        spot_feed=spot_feed,
        cfg=cfg,
        paper=paper,
    )

    loop = asyncio.get_running_loop()

    def _shutdown_handler() -> None:
        log.info("Shutdown signal received")
        loop.create_task(controller.shutdown())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown_handler)
        except NotImplementedError:
            pass  # Windows

    try:
        await controller.start()
    except KeyboardInterrupt:
        pass
    finally:
        await controller.shutdown()


def main() -> None:
    setup_logging("INFO")
    cfg = get_config()
    asyncio.run(run_bot(cfg))


if __name__ == "__main__":
    main()
