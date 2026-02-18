"""SQLite storage for recording ticks, orders, fills, and PnL."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from bot.infra.log import get_logger

log = get_logger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS ticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    yes_bid INTEGER,
    yes_ask INTEGER,
    volume INTEGER,
    payload TEXT
);

CREATE TABLE IF NOT EXISTS markets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    title TEXT,
    strike REAL,
    expiration TEXT,
    payload TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    client_order_id TEXT,
    ticker TEXT NOT NULL,
    side TEXT,
    price_cents INTEGER,
    size INTEGER,
    order_type TEXT,
    status TEXT,
    payload TEXT
);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    side TEXT,
    price_cents INTEGER,
    size INTEGER,
    fee_dollars REAL,
    payload TEXT
);

CREATE TABLE IF NOT EXISTS pnl (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    realized REAL,
    unrealized REAL,
    gross_exposure REAL,
    net_exposure REAL,
    payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_ticks_ticker ON ticks(ticker);
CREATE INDEX IF NOT EXISTS idx_orders_ticker ON orders(ticker);
CREATE INDEX IF NOT EXISTS idx_fills_ticker ON fills(ticker);
"""


class Storage:
    """Thin wrapper around sqlite3 for the bot recorder."""

    def __init__(self, db_path: str | Path = "bot_data.db") -> None:
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_DDL)
        self._conn.commit()
        log.info("Storage opened at %s", self.db_path)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()
        assert self._conn is not None
        return self._conn

    def insert(self, table: str, row: dict[str, Any]) -> None:
        cols = list(row.keys())
        placeholders = ",".join(["?"] * len(cols))
        sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
        self.conn.execute(sql, [row[c] for c in cols])
        self.conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        cur = self.conn.execute(sql, params)
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, r)) for r in cur.fetchall()]
