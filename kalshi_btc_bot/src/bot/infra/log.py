"""Structured logging with Rich console handler."""

from __future__ import annotations

import logging
import sys

from rich.console import Console
from rich.logging import RichHandler

_CONSOLE = Console(stderr=True)


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with Rich handler."""
    handler = RichHandler(
        console=_CONSOLE,
        show_path=False,
        rich_tracebacks=True,
        tracebacks_show_locals=False,
        markup=True,
    )
    handler.setFormatter(logging.Formatter("%(name)s | %(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
