"""Persistent config storage at %APPDATA%/KalshiBot/config.json."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "environment": "demo",
    "paper_mode": True,
    "live_trading": False,
    "key_id": "",
    "private_key_path": "",
    "mm_enabled": True,
    "sniper_enabled": True,
    "daily_stop_dollars": 200.0,
    "max_gross_exposure_dollars": 250.0,
    "max_net_exposure_dollars": 150.0,
    "max_exposure_per_market_dollars": 125.0,
    "max_order_size_contracts": 25,
}


def _appdata_dir() -> Path:
    """Return the application data directory, creating it if needed."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        d = base / "KalshiBot"
    else:
        d = Path.home() / ".kalshi_bot"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return _appdata_dir() / "config.json"


def logs_dir() -> Path:
    if sys.platform == "win32":
        d = _appdata_dir() / "logs"
    else:
        d = _appdata_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_config() -> dict[str, Any]:
    """Load persisted config, falling back to defaults for missing keys."""
    cfg = dict(_DEFAULTS)
    p = config_path()
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                stored = json.load(f)
            cfg.update(stored)
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    """Write config to disk. Never stores private key contents."""
    p = config_path()
    safe = {k: v for k, v in cfg.items() if k != "private_key_contents"}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(safe, f, indent=2, default=str)


def export_config(cfg: dict[str, Any], dest: Path) -> None:
    """Export config to a user-chosen path."""
    safe = {k: v for k, v in cfg.items() if k != "private_key_contents"}
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(safe, f, indent=2, default=str)


def import_config(src: Path) -> dict[str, Any]:
    """Import config from a user-chosen path."""
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    merged = dict(_DEFAULTS)
    merged.update(data)
    save_config(merged)
    return merged
