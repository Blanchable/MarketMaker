"""Fills-based PnL tracker with average-cost accounting, persistence, and daily rollover.

All positions are normalised to YES-equivalent:
  BUY  YES @p  →  signed_qty=+count, yes_price=p
  SELL YES @p  →  signed_qty=-count, yes_price=p
  BUY  NO  @p  →  signed_qty=-count, yes_price=100-p   (equivalent to SELL YES)
  SELL NO  @p  →  signed_qty=+count, yes_price=100-p   (equivalent to BUY YES)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from bot.infra.log import get_logger

log = get_logger(__name__)

from datetime import timezone as _tz

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:
    # Windows without tzdata, or very old Python: fall back to fixed UTC-5
    _ET = _tz(timedelta(hours=-5))


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class PnlFill:
    """Normalised fill input."""
    fill_key: str           # dedupe key
    ticker: str
    signed_qty_yes: int     # +buy YES, -sell YES
    yes_price_cents: float
    fee_dollars: float = 0.0
    ts: float = 0.0         # epoch seconds


@dataclass
class TickerPnlState:
    """Per-ticker average-cost inventory + realised P&L."""
    ticker: str = ""
    pos_qty: int = 0            # signed YES-equivalent contracts
    avg_price: float = 0.0      # cents, average entry price
    realized_total: float = 0.0 # dollars, cumulative
    fees_total: float = 0.0     # dollars, cumulative


def _today_et() -> str:
    """Return today's date as YYYY-MM-DD in America/New_York."""
    return datetime.now(_ET).strftime("%Y-%m-%d")


# ── Fill normalisation ───────────────────────────────────────────────────────

def normalise_fill(
    *,
    fill_key: str,
    ticker: str,
    side: str,
    action: str,
    yes_price: int,
    no_price: int,
    count: int,
    fee_dollars: float = 0.0,
    ts: float = 0.0,
) -> PnlFill:
    """Convert a Kalshi fill into YES-equivalent PnlFill.

    ``side`` is 'yes' or 'no'.
    ``action`` is 'buy' or 'sell'.
    """
    side_l = side.lower()
    action_l = action.lower()

    if side_l == "yes":
        price = float(yes_price)
        signed_qty = count if action_l == "buy" else -count
    elif side_l == "no":
        price = 100.0 - float(no_price) if no_price else 100.0 - float(yes_price)
        signed_qty = -count if action_l == "buy" else count
    else:
        price = float(yes_price) if yes_price else 50.0
        signed_qty = count if action_l == "buy" else -count

    return PnlFill(
        fill_key=fill_key,
        ticker=ticker,
        signed_qty_yes=signed_qty,
        yes_price_cents=price,
        fee_dollars=fee_dollars,
        ts=ts,
    )


# ── Accounting engine ────────────────────────────────────────────────────────

def apply_fill(state: TickerPnlState, fill: PnlFill) -> float:
    """Apply one fill to ticker state. Returns realised delta in dollars."""
    q0 = state.pos_qty
    q1 = fill.signed_qty_yes
    price = fill.yes_price_cents
    realized_delta = 0.0

    if q0 == 0 or (q0 > 0 and q1 > 0) or (q0 < 0 and q1 < 0):
        # Opening or adding to position
        total_qty = q0 + q1
        if total_qty != 0:
            state.avg_price = (abs(q0) * state.avg_price + abs(q1) * price) / abs(total_qty)
        state.pos_qty = total_qty
    else:
        # Opposite direction – closing some or all
        closed_qty = min(abs(q0), abs(q1))
        if q0 > 0:
            profit_per = price - state.avg_price
        else:
            profit_per = state.avg_price - price
        realized_delta = (closed_qty * profit_per) / 100.0

        remaining = q0 + q1
        if remaining == 0:
            state.pos_qty = 0
            state.avg_price = 0.0
        elif (remaining > 0) != (q0 > 0):
            # Flip: leftover is a new position at fill price
            state.pos_qty = remaining
            state.avg_price = price
        else:
            # Partial close, avg_price unchanged
            state.pos_qty = remaining

    # Fees
    state.fees_total += fill.fee_dollars
    realized_delta -= fill.fee_dollars

    state.realized_total += realized_delta
    return realized_delta


# ── Persistence helpers ──────────────────────────────────────────────────────

def _db_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        d = base / "KalshiBot"
    else:
        d = Path.home() / ".kalshi_bot"
    d.mkdir(parents=True, exist_ok=True)
    return d / "pnl_tracker.db"


_PNL_DDL = """
CREATE TABLE IF NOT EXISTS pnl_state (
    ticker TEXT PRIMARY KEY,
    pos_qty INTEGER NOT NULL DEFAULT 0,
    avg_price REAL NOT NULL DEFAULT 0,
    realized_total REAL NOT NULL DEFAULT 0,
    fees_total REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pnl_fills_seen (
    fill_key TEXT PRIMARY KEY,
    ts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pnl_day_snapshot (
    snap_date TEXT PRIMARY KEY,
    realized_total REAL NOT NULL DEFAULT 0,
    fees_total REAL NOT NULL DEFAULT 0
);
"""


# ── PnlTracker ───────────────────────────────────────────────────────────────

class PnlTracker:
    """Fills-based PnL tracker with SQLite persistence and daily rollover."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else _db_path()
        self._conn: sqlite3.Connection | None = None
        self._states: dict[str, TickerPnlState] = {}
        self._seen: set[str] = set()
        self._snap_date: str = ""
        self._snap_realized: float = 0.0
        self._snap_fees: float = 0.0
        self._last_diag_ts: float = 0.0
        self._open()

    # ── Public API ───────────────────────────────────────────────────

    def ingest_fill(self, fill: PnlFill) -> float:
        """Process one normalised fill. Returns realised delta (dollars). Skips dupes."""
        if fill.fill_key in self._seen:
            return 0.0
        self._seen.add(fill.fill_key)

        state = self._states.get(fill.ticker)
        if state is None:
            state = TickerPnlState(ticker=fill.ticker)
            self._states[fill.ticker] = state

        delta = apply_fill(state, fill)

        self._persist_fill_key(fill.fill_key, fill.ts)
        self._persist_state(state)

        if abs(delta) > 0.001:
            log.info(
                "PnL fill: %s qty=%+d @%.1fc -> realized %+.4f$ (total %.2f$) pos=%d@%.1fc",
                fill.ticker, fill.signed_qty_yes, fill.yes_price_cents,
                delta, state.realized_total, state.pos_qty, state.avg_price,
            )
        return delta

    def ingest_kalshi_fill(self, f: Any) -> float:
        """Convenience: ingest a Kalshi Fill model object."""
        key = f.trade_id or f"{f.ticker}:{f.created_time}:{f.count}:{f.yes_price}"
        fee = 0.0  # Kalshi fill objects don't expose per-fill fee; track as 0
        pf = normalise_fill(
            fill_key=key,
            ticker=f.ticker,
            side=f.side,
            action=f.action,
            yes_price=f.yes_price,
            no_price=f.no_price,
            count=f.count,
            fee_dollars=fee,
            ts=time.time(),
        )
        return self.ingest_fill(pf)

    @property
    def realized_total(self) -> float:
        return sum(s.realized_total for s in self._states.values())

    @property
    def fees_total(self) -> float:
        return sum(s.fees_total for s in self._states.values())

    @property
    def realized_today(self) -> float:
        self._ensure_snapshot()
        return self.realized_total - self._snap_realized

    @property
    def fees_today(self) -> float:
        self._ensure_snapshot()
        return self.fees_total - self._snap_fees

    @property
    def fills_seen_count(self) -> int:
        return len(self._seen)

    def state_for(self, ticker: str) -> TickerPnlState | None:
        return self._states.get(ticker)

    def log_diagnostics(self) -> None:
        now = time.time()
        if now - self._last_diag_ts < 60:
            return
        self._last_diag_ts = now
        active = sum(1 for s in self._states.values() if s.pos_qty != 0)
        log.info(
            "PnL tracker: fills_seen=%d realized_total=$%.2f realized_today=$%.2f "
            "fees_today=$%.2f active_tickers=%d",
            self.fills_seen_count, self.realized_total, self.realized_today,
            self.fees_today, active,
        )

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Snapshot / daily rollover ────────────────────────────────────

    def _ensure_snapshot(self) -> None:
        today = _today_et()
        if self._snap_date == today:
            return
        # Day changed – load existing snapshot or inherit from previous day
        row = self._query_one(
            "SELECT realized_total, fees_total FROM pnl_day_snapshot WHERE snap_date=?",
            (today,),
        )
        if row:
            self._snap_realized = row[0]
            self._snap_fees = row[1]
        else:
            # Get the most recent previous snapshot as the rollover baseline.
            # If none exists (first ever run), baseline is 0.
            prev = self._query_one(
                "SELECT realized_total, fees_total FROM pnl_day_snapshot "
                "ORDER BY snap_date DESC LIMIT 1",
            )
            if prev:
                self._snap_realized = prev[0]
                self._snap_fees = prev[1]
            else:
                self._snap_realized = 0.0
                self._snap_fees = 0.0
            self._exec(
                "INSERT OR REPLACE INTO pnl_day_snapshot(snap_date, realized_total, fees_total) VALUES(?,?,?)",
                (today, self._snap_realized, self._snap_fees),
            )
        self._snap_date = today

    # ── Persistence ──────────────────────────────────────────────────

    def _open(self) -> None:
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_PNL_DDL)
        self._conn.commit()
        self._load()

    def _load(self) -> None:
        assert self._conn
        for row in self._conn.execute("SELECT ticker, pos_qty, avg_price, realized_total, fees_total FROM pnl_state"):
            self._states[row[0]] = TickerPnlState(
                ticker=row[0], pos_qty=row[1], avg_price=row[2],
                realized_total=row[3], fees_total=row[4],
            )
        for row in self._conn.execute("SELECT fill_key FROM pnl_fills_seen"):
            self._seen.add(row[0])
        # Load today's snapshot
        today = _today_et()
        row = self._query_one(
            "SELECT realized_total, fees_total FROM pnl_day_snapshot WHERE snap_date=?",
            (today,),
        )
        if row:
            self._snap_date = today
            self._snap_realized = row[0]
            self._snap_fees = row[1]
        log.info(
            "PnL tracker loaded: %d tickers, %d seen fills, realized_total=$%.2f",
            len(self._states), len(self._seen), self.realized_total,
        )

    def _persist_state(self, state: TickerPnlState) -> None:
        self._exec(
            "INSERT OR REPLACE INTO pnl_state(ticker, pos_qty, avg_price, realized_total, fees_total) VALUES(?,?,?,?,?)",
            (state.ticker, state.pos_qty, state.avg_price, state.realized_total, state.fees_total),
        )

    def _persist_fill_key(self, key: str, ts: float) -> None:
        self._exec(
            "INSERT OR IGNORE INTO pnl_fills_seen(fill_key, ts) VALUES(?,?)",
            (key, int(ts)),
        )

    def _exec(self, sql: str, params: tuple = ()) -> None:
        if self._conn:
            self._conn.execute(sql, params)
            self._conn.commit()

    def _query_one(self, sql: str, params: tuple = ()) -> tuple | None:
        if self._conn:
            cur = self._conn.execute(sql, params)
            return cur.fetchone()
        return None
