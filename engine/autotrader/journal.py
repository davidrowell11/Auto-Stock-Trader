"""Trade journal: a SQLite file recording every trade, stop and pending approval.

Test and Live share one file but every row is tagged with its mode, so the
two never mix.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
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
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class StopState:
    entry_price: float
    stop_price: float
    highest_close: float


class Journal:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)  # server serializes access
        self.db.executescript(SCHEMA)

    # ----- trades -----
    def record_trade(self, mode: str, symbol: str, side: str, qty: float, price: float,
                     order_id: str | None, reasons: list[str]) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO trades (ts, mode, symbol, side, qty, price, order_id, reasons)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_now(), mode, symbol, side, qty, price, order_id, json.dumps(reasons)),
            )

    def trades(self, mode: str) -> list[dict]:
        cur = self.db.execute(
            "SELECT ts, symbol, side, qty, price, order_id, reasons FROM trades"
            " WHERE mode = ? ORDER BY id", (mode,),
        )
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            r["reasons"] = json.loads(r["reasons"])
        return rows

    # ----- trailing stops -----
    def get_stop(self, mode: str, symbol: str) -> StopState | None:
        row = self.db.execute(
            "SELECT entry_price, stop_price, highest_close FROM stops WHERE mode = ? AND symbol = ?",
            (mode, symbol),
        ).fetchone()
        return StopState(*row) if row else None

    def save_stop(self, mode: str, symbol: str, state: StopState) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO stops (mode, symbol, entry_price, stop_price, highest_close, opened_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(mode, symbol) DO UPDATE SET"
                " stop_price = excluded.stop_price, highest_close = excluded.highest_close",
                (mode, symbol, state.entry_price, state.stop_price, state.highest_close, _now()),
            )

    def clear_stop(self, mode: str, symbol: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM stops WHERE mode = ? AND symbol = ?", (mode, symbol))

    # ----- approvals (Live approve mode) -----
    def queue_approval(self, mode: str, symbol: str, side: str, qty: float,
                       limit_price: float | None, reasons: list[str]) -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO approvals (ts, mode, symbol, side, qty, limit_price, reasons)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_now(), mode, symbol, side, qty, limit_price, json.dumps(reasons)),
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
            "SELECT id, ts, mode, symbol, side, qty, limit_price, reasons, status FROM approvals"
            " WHERE id = ?", (approval_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        out = dict(zip([c[0] for c in cur.description], row))
        out["reasons"] = json.loads(out["reasons"])
        return out

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
                  reasoning: str, red_flags: list[str]) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO analyst_views (ts, day, mode, symbol, price, action, confidence,"
                " reasoning, red_flags) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_now(), _now()[:10], mode, symbol, price, action, confidence, reasoning,
                 json.dumps(red_flags)),
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
            "SELECT day, symbol, price, action, confidence, reasoning FROM analyst_views"
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

    # ----- equity history (for the drawdown brake) -----
    def record_equity(self, mode: str, equity: float) -> None:
        with self.db:
            self.db.execute("INSERT INTO equity (ts, mode, equity) VALUES (?, ?, ?)",
                            (_now(), mode, equity))

    def peak_equity(self, mode: str) -> float:
        row = self.db.execute("SELECT MAX(equity) FROM equity WHERE mode = ?", (mode,)).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0
