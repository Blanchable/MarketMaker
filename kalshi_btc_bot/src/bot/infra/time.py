"""Time helpers used across the bot."""

from __future__ import annotations

import time
from datetime import datetime, timezone


def now_ms() -> int:
    """Current epoch in milliseconds."""
    return int(time.time() * 1000)


def now_sec() -> int:
    """Current epoch in seconds (for Kalshi signature)."""
    return int(time.time())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def seconds_until(iso_ts: str) -> float:
    """Seconds from now until an ISO-8601 timestamp."""
    try:
        target = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0.0
    delta = target - utcnow()
    return max(delta.total_seconds(), 0.0)


def years_until(iso_ts: str) -> float:
    secs = seconds_until(iso_ts)
    return secs / (365.25 * 24 * 3600)
