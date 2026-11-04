"""Trade journal: a SQLite file recording every trade, stop and pending approval.

Test and Live share one file but every row is tagged with its mode, so the
two never mix.
"""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL, mode TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
    qty REAL NOT NULL, price REAL NOT NULL, order_id TEXT, reasons TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stops (
    mode TEXT NOT NULL, symbol TEXT NOT NULL,
    entry_price REAL NOT NULL, stop_price REAL NOT NULL, highest_close REAL NOT NULL,
    opened_at TEXT NOT NULL,
    PRIMARY KEY (mode, symbol)
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL, mode TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
    qty REAL NOT NULL, limit_price REAL, reasons TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS analyst_views (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL, day TEXT NOT NULL, mode TEXT NOT NULL, symbol TEXT NOT NULL,
    price REAL NOT NULL, action TEXT NOT NULL, confidence REAL NOT NULL,
    reasoning TEXT NOT NULL, red_flags TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL, mode TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS equity (
    ts TEXT NOT NULL, mode TEXT NOT NULL, equity REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Added after the first release; older journals get them on startup.
STOP_COLUMNS = {"qty": "REAL", "order_id": "TEXT", "order_side": "TEXT"}
# What pointed the bot at a stock ("trend", "screener", "insider", ...), so it can learn which work.
LATER_COLUMNS = {"stops": STOP_COLUMNS, "trades": {"sources": "TEXT"}, "approvals": {"sources": "TEXT"},
                 "analyst_views": {"model": "TEXT"}}


@dataclass
class StopState:
    entry_price: float
    stop_price: float
    highest_close: float
    qty: float | None = None  # shares the bot itself owns (None: from before this was tracked)
    order_id: str | None = None  # the bot's order for this stock that hasn't finished yet
    order_side: str | None = None  # "buy" or "sell"


def _locked(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class Journal:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # The Test bot, the Live bot and the app's requests all share this connection,
        # so every method takes the lock.
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        for table, columns in LATER_COLUMNS.items():
            have = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
            for name, kind in columns.items():
                if name not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
        self.db.commit()

    # ----- trades -----
    def record_trade(self, mode: str, symbol: str, side: str, qty: float, price: float,
                     order_id: str | None, reasons: list[str],
                     sources: list[str] | None = None) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO trades (ts, mode, symbol, side, qty, price, order_id, reasons, sources)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_now(), mode, symbol, side, qty, price, order_id, json.dumps(reasons),
                 json.dumps(sources) if sources is not None else None),
            )

    def last_protective_sell(self, mode: str, symbol: str) -> datetime | None:
        """When the bot last sold this stock to protect you (stop, trend break). Kill-switch sells don't count."""
        cur = self.db.execute(
            "SELECT ts FROM trades WHERE mode = ? AND symbol = ? AND side = 'sell'"
            " AND reasons != ? ORDER BY id DESC LIMIT 1",
            (mode, symbol, json.dumps(["Kill switch"])),
        )
        row = cur.fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def trades(self, mode: str) -> list[dict]:
        cur = self.db.execute(
            "SELECT ts, symbol, side, qty, price, order_id, reasons, sources FROM trades"
            " WHERE mode = ? ORDER BY id", (mode,),
        )
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            r["reasons"] = json.loads(r["reasons"])
            r["sources"] = json.loads(r["sources"]) if r["sources"] else ["trend"]
        return rows

    # ----- trailing stops -----
    def get_stop(self, mode: str, symbol: str) -> StopState | None:
        row = self.db.execute(
            "SELECT entry_price, stop_price, highest_close, qty, order_id, order_side FROM stops"
            " WHERE mode = ? AND symbol = ?",
            (mode, symbol),
        ).fetchone()
        return StopState(*row) if row else None

    def save_stop(self, mode: str, symbol: str, state: StopState) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO stops (mode, symbol, entry_price, stop_price, highest_close, opened_at,"
                " qty, order_id, order_side) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(mode, symbol) DO UPDATE SET"
                " stop_price = excluded.stop_price, highest_close = excluded.highest_close,"
                " qty = excluded.qty, order_id = excluded.order_id, order_side = excluded.order_side",
                (mode, symbol, state.entry_price, state.stop_price, state.highest_close, _now(),
                 state.qty, state.order_id, state.order_side),
            )

    def owned_symbols(self, mode: str) -> set[str]:
        """Stocks the bot bought and still tracks. Anything else in the account is yours."""
        cur = self.db.execute("SELECT symbol FROM stops WHERE mode = ?", (mode,))
        return {r[0] for r in cur.fetchall()}

    def cancel_pending(self, mode: str) -> int:
        with self.db:
            cur = self.db.execute(
                "UPDATE approvals SET status = 'cancelled' WHERE mode = ? AND status = 'pending'",
                (mode,))
        return cur.rowcount

    def clear_stop(self, mode: str, symbol: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM stops WHERE mode = ? AND symbol = ?", (mode, symbol))

    # ----- approvals (Live approve mode) -----
    def queue_approval(self, mode: str, symbol: str, side: str, qty: float,
                       limit_price: float | None, reasons: list[str],
                       sources: list[str] | None = None) -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO approvals (ts, mode, symbol, side, qty, limit_price, reasons, sources)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_now(), mode, symbol, side, qty, limit_price, json.dumps(reasons),
                 json.dumps(sources) if sources is not None else None),
            )
        return int(cur.lastrowid)

    def pending_approvals(self, mode: str) -> list[dict]:
        cur = self.db.execute(
            "SELECT id, ts, symbol, side, qty, limit_price, reasons FROM approvals"
            " WHERE mode = ? AND status = 'pending' ORDER BY id", (mode,),
        )
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            r["reasons"] = json.loads(r["reasons"])
        return rows

    def get_approval(self, approval_id: int) -> dict | None:
        cur = self.db.execute(
            "SELECT id, ts, mode, symbol, side, qty, limit_price, reasons, status, sources"
            " FROM approvals WHERE id = ?", (approval_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        out = dict(zip([c[0] for c in cur.description], row))
        out["reasons"] = json.loads(out["reasons"])
        out["sources"] = json.loads(out["sources"]) if out["sources"] else ["trend"]
        return out

    def expire_approvals(self, mode: str, max_age: timedelta) -> int:
        """Mark suggestions older than max_age as expired, so they stop showing in the app."""
        cutoff = (datetime.now(timezone.utc) - max_age).isoformat(timespec="seconds")
        with self.db:
            cur = self.db.execute(
                "UPDATE approvals SET status = 'expired'"
                " WHERE mode = ? AND status = 'pending' AND ts < ?", (mode, cutoff))
        return cur.rowcount

    def set_approval_status(self, approval_id: int, status: str) -> None:
        with self.db:
            self.db.execute("UPDATE approvals SET status = ? WHERE id = ?", (status, approval_id))

    def has_pending(self, mode: str, symbol: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM approvals WHERE mode = ? AND symbol = ? AND status = 'pending'",
            (mode, symbol),
        ).fetchone() is not None

    # ----- AI analyst views (cached per day; also the analyst's scorecard) -----
    def save_view(self, mode: str, symbol: str, price: float, action: str, confidence: float,
                  reasoning: str, red_flags: list[str], model: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO analyst_views (ts, day, mode, symbol, price, action, confidence,"
                " reasoning, red_flags, model) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_now(), _now()[:10], mode, symbol, price, action, confidence, reasoning,
                 json.dumps(red_flags), model),
            )

    def todays_view(self, mode: str, symbol: str) -> dict | None:
        cur = self.db.execute(
            "SELECT action, confidence, reasoning, red_flags FROM analyst_views"
            " WHERE mode = ? AND symbol = ? AND day = ? ORDER BY id DESC LIMIT 1",
            (mode, symbol, _now()[:10]),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"action": row[0], "confidence": row[1], "reasoning": row[2],
                "red_flags": json.loads(row[3])}

    def views(self, mode: str) -> list[dict]:
        cur = self.db.execute(
            "SELECT day, symbol, price, action, confidence, reasoning, model FROM analyst_views"
            " WHERE mode = ? ORDER BY id", (mode,),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    # ----- activity feed (what the app shows as alerts) -----
    def add_event(self, mode: str, kind: str, message: str) -> None:
        with self.db:
            self.db.execute("INSERT INTO events (ts, mode, kind, message) VALUES (?, ?, ?, ?)",
                            (_now(), mode, kind, message))

    def events(self, mode: str, limit: int = 100) -> list[dict]:
        cur = self.db.execute(
            "SELECT id, ts, kind, message FROM events WHERE mode = ? ORDER BY id DESC LIMIT ?",
            (mode, limit),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def equity_history(self, mode: str, limit: int = 2000) -> list[dict]:
        cur = self.db.execute(
            "SELECT ts, equity FROM equity WHERE mode = ? ORDER BY rowid DESC LIMIT ?",
            (mode, limit),
        )
        return [{"ts": ts, "equity": eq} for ts, eq in reversed(cur.fetchall())]

    def daily_equity(self, mode: str) -> list[tuple[str, float]]:
        """Last recorded account value of each day, oldest first."""
        cur = self.db.execute(
            "SELECT substr(ts, 1, 10) AS day, equity FROM equity WHERE rowid IN ("
            " SELECT MAX(rowid) FROM equity WHERE mode = ? GROUP BY substr(ts, 1, 10))"
            " ORDER BY day", (mode,))
        return [(d, float(e)) for d, e in cur.fetchall()]

    # ----- equity history (for the drawdown brake) -----
    def record_equity(self, mode: str, equity: float) -> None:
        with self.db:
            self.db.execute("INSERT INTO equity (ts, mode, equity) VALUES (?, ?, ?)",
                            (_now(), mode, equity))

    def peak_equity(self, mode: str) -> float:
        row = self.db.execute("SELECT MAX(equity) FROM equity WHERE mode = ?", (mode,)).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0

    # ----- what the bot learned (see learning.py) -----
    def save_memory(self, mode: str, status: str, data: dict) -> int:
        """Store a memory. A new "active" or "proposed" one replaces the previous one of that status."""
        with self.db:
            self.db.execute("UPDATE memory SET status = 'replaced' WHERE mode = ? AND status = ?",
                            (mode, status))
            cur = self.db.execute("INSERT INTO memory (ts, mode, status, data) VALUES (?, ?, ?, ?)",
                                  (_now(), mode, status, json.dumps(data)))
        return int(cur.lastrowid)

    def memory(self, mode: str, status: str = "active") -> dict | None:
        row = self.db.execute(
            "SELECT id, ts, data FROM memory WHERE mode = ? AND status = ? ORDER BY id DESC LIMIT 1",
            (mode, status)).fetchone()
        if not row:
            return None
        return {"id": row[0], "ts": row[1], **json.loads(row[2])}

    def set_memory_status(self, memory_id: int, status: str) -> None:
        with self.db:
            self.db.execute("UPDATE memory SET status = ? WHERE id = ?", (status, memory_id))


for _name, _fn in list(vars(Journal).items()):
    if callable(_fn) and not _name.startswith("_"):
        setattr(Journal, _name, _locked(_fn))
