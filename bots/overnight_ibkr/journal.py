"""SQLite journal: the bot's single source of truth for what it has done.

Tables
------
nights   one row per symbol per overnight cycle (buy at close d, sell at
         next open). status: PENDING_BUY -> HELD -> PLACING_SELL ->
         PENDING_SELL_FILL -> CLOSED, or ERROR. PLACING_SELL means "a
         sell order tagged sell_ref MAY have reached the broker" - it is
         written BEFORE the order is transmitted, so a crash can never
         leave an order the journal has not heard of.
events   append-only operational log (also mirrored to stderr/logfile).
meta     key/value: halted flag + reason, schema version.

All prices are per share in USD; qty is whole shares; pnl is net of
recorded commissions.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS nights (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  buy_date TEXT NOT NULL,
  sell_date TEXT,
  qty INTEGER NOT NULL DEFAULT 0,
  buy_order_id INTEGER,
  sell_order_id INTEGER,
  sell_ref TEXT DEFAULT '',
  buy_fill REAL,
  sell_fill REAL,
  buy_commission REAL DEFAULT 0,
  sell_commission REAL DEFAULT 0,
  prev_close REAL,
  pnl REAL,
  status TEXT NOT NULL,
  detail TEXT DEFAULT '',
  UNIQUE(symbol, buy_date)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  level TEXT NOT NULL,
  msg TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

OPEN_STATUSES = ("PENDING_BUY", "HELD", "PLACING_SELL", "PENDING_SELL_FILL")


@dataclass
class Night:
    id: int
    symbol: str
    buy_date: str
    sell_date: str | None
    qty: int
    buy_order_id: int | None
    sell_order_id: int | None
    sell_ref: str
    buy_fill: float | None
    sell_fill: float | None
    buy_commission: float
    sell_commission: float
    prev_close: float | None
    pnl: float | None
    status: str
    detail: str


def _row_to_night(r: sqlite3.Row) -> Night:
    return Night(**{k: r[k] for k in r.keys()})


class Journal:
    def __init__(self, path: str) -> None:
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.con.commit()

    # -- events ------------------------------------------------------
    def log(self, level: str, msg: str) -> None:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.con.execute("INSERT INTO events(ts, level, msg) VALUES (?,?,?)",
                         (ts, level, msg))
        self.con.commit()
        print(f"{ts} {level:<5} {msg}", flush=True)

    # -- halt flag ---------------------------------------------------
    def halted(self) -> str | None:
        r = self.con.execute("SELECT value FROM meta WHERE key='halted'").fetchone()
        if not r:
            return None
        v = json.loads(r["value"])
        return v["reason"] if v.get("on") else None

    def set_halted(self, reason: str | None) -> None:
        val = json.dumps({"on": reason is not None, "reason": reason or ""})
        self.con.execute(
            "INSERT INTO meta(key,value) VALUES('halted',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (val,))
        self.con.commit()

    # -- nights ------------------------------------------------------
    def open_nights(self, symbol: str | None = None) -> list[Night]:
        q = f"SELECT * FROM nights WHERE status IN ({','.join('?'*len(OPEN_STATUSES))})"
        args: list = list(OPEN_STATUSES)
        if symbol:
            q += " AND symbol=?"
            args.append(symbol)
        return [_row_to_night(r) for r in self.con.execute(q, args)]

    def night_for(self, symbol: str, buy_date: date) -> Night | None:
        r = self.con.execute(
            "SELECT * FROM nights WHERE symbol=? AND buy_date=?",
            (symbol, buy_date.isoformat())).fetchone()
        return _row_to_night(r) if r else None

    def create_night(self, symbol: str, buy_date: date, qty: int,
                     buy_order_id: int | None, prev_close: float | None,
                     status: str = "PENDING_BUY", detail: str = "") -> int:
        cur = self.con.execute(
            "INSERT INTO nights(symbol, buy_date, qty, buy_order_id, prev_close,"
            " status, detail) VALUES (?,?,?,?,?,?,?)",
            (symbol, buy_date.isoformat(), qty, buy_order_id, prev_close,
             status, detail))
        self.con.commit()
        return int(cur.lastrowid)

    def update_night(self, night_id: int, **fields) -> None:
        keys = ", ".join(f"{k}=?" for k in fields)
        self.con.execute(f"UPDATE nights SET {keys} WHERE id=?",
                         (*fields.values(), night_id))
        self.con.commit()

    def close_night(self, night_id: int, sell_date: date, sell_fill: float,
                    sell_commission: float) -> float:
        n = self.get_night(night_id)
        pnl = None
        if n.buy_fill is not None:
            pnl = (sell_fill - n.buy_fill) * n.qty \
                  - n.buy_commission - sell_commission
        self.update_night(night_id, sell_date=sell_date.isoformat(),
                          sell_fill=sell_fill, sell_commission=sell_commission,
                          pnl=pnl, status="CLOSED")
        return pnl if pnl is not None else 0.0

    def get_night(self, night_id: int) -> Night:
        r = self.con.execute("SELECT * FROM nights WHERE id=?", (night_id,)).fetchone()
        if not r:
            raise KeyError(f"night {night_id} not found")
        return _row_to_night(r)

    def closed_nights(self, symbol: str | None = None, limit: int = 100000) -> list[Night]:
        q = "SELECT * FROM nights WHERE status='CLOSED'"
        args: list = []
        if symbol:
            q += " AND symbol=?"
            args.append(symbol)
        q += " ORDER BY buy_date ASC LIMIT ?"
        args.append(limit)
        return [_row_to_night(r) for r in self.con.execute(q, args)]
