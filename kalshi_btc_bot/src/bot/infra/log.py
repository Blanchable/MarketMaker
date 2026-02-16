"""Structured logging with Rich console handler + optional rotating file handler."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

_CONSOLE = Console(stderr=True)


def _appdata_logs_dir() -> Path:
    """Return %APPDATA%/KalshiBot/logs on Windows, ~/.kalshi_bot/logs elsewhere."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path.home() / ".kalshi_bot"
    logs = base / "KalshiBot" / "logs" if sys.platform == "win32" else base / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return logs


def setup_logging(level: str = "INFO", *, file_logging: bool = True) -> None:
    """Configure root logger with Rich handler and optional rotating file handler."""
    root = logging.getLogger()
    root.handlers.clear()

    # Rich stderr handler
    rich_handler = RichHandler(
        console=_CONSOLE,
        show_path=False,
        rich_tracebacks=True,
        tracebacks_show_locals=False,
        markup=True,
    )
    rich_handler.setFormatter(logging.Formatter("%(name)s | %(message)s"))
    root.addHandler(rich_handler)

    # Plain stdout handler so subprocess stdout capture works for the GUI
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s | %(message)s")
    )
    root.addHandler(stdout_handler)

    # Rotating file handler
    if file_logging:
        try:
            logs_dir = _appdata_logs_dir()
            fh = RotatingFileHandler(
                logs_dir / "bot.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            fh.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)-8s %(name)s | %(message)s")
            )
            root.addHandler(fh)
        except Exception:
            pass  # if we can't write logs to disk, continue anyway

    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def get_logs_dir() -> Path:
    """Public accessor for the logs directory path."""
    return _appdata_logs_dir()
