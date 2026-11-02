"""Congressional stock trades (via Quiver Quantitative) and a performance leaderboard.

Members of Congress must report trades under the STOCK Act, up to 45 days
after the trade. The bot ranks members by how their past purchases actually
did, and only counts new purchases from members with a good track record.

Needs a Quiver API token in ``QUIVER_API_TOKEN`` (paid subscription).
"""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .http import RateLimited

LIVE_URL = "https://api.quiverquant.com/beta/live/congresstrading"
HOLD_DAYS = 60  # how a past purchase is scored: its return over the next 60 days


@dataclass(frozen=True)
class CongressTrade:
    member: str
    symbol: str
    is_purchase: bool
    traded: date
    reported: date


@dataclass(frozen=True)
class MemberRecord:
    member: str
    scored_buys: int
    hit_rate: float  # share of purchases that went up over HOLD_DAYS
    avg_return: float

    @property
    def is_top(self) -> bool:
        return self.scored_buys >= 5 and self.hit_rate >= 0.55 and self.avg_return > 0


def _date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def parse_trades(rows: list[dict]) -> list[CongressTrade]:
    trades = []
    for r in rows:
        symbol = (r.get("Ticker") or "").upper().strip()
        traded = _date(r.get("TransactionDate") or r.get("Traded"))
        reported = _date(r.get("ReportDate") or r.get("Filed")) or traded
        kind = (r.get("Transaction") or "").lower()
        if not symbol or traded is None or not kind:
            continue
        trades.append(CongressTrade(
            member=(r.get("Representative") or r.get("Name") or "Unknown").strip(),
            symbol=symbol,
            is_purchase=kind.startswith("purchase") or kind == "buy",
            traded=traded,
            reported=reported,
        ))
    return trades


class QuiverClient:
    def __init__(self, token: str | None = None, fetch: RateLimited | None = None) -> None:
        self.token = token or os.environ.get("QUIVER_API_TOKEN", "")
        self.fetch = fetch or RateLimited(min_interval=0.5)

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def recent_trades(self) -> list[CongressTrade]:
        if not self.enabled:
            return []
        headers = {"Accept": "application/json", "Authorization": f"Token {self.token}"}
        return parse_trades(self.fetch.json(LIVE_URL, headers))


# Looks up the closing price of a symbol on (or just after) a date.
PriceOn = Callable[[str, date], float | None]


def build_leaderboard(trades: list[CongressTrade], price_on: PriceOn, today: date) -> dict[str, MemberRecord]:
    """Score each member by how their purchases did over the following HOLD_DAYS."""
    returns: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        if not t.is_purchase or t.traded > today - timedelta(days=HOLD_DAYS):
            continue
        start, end = price_on(t.symbol, t.traded), price_on(t.symbol, t.traded + timedelta(days=HOLD_DAYS))
        if start and end:
            returns[t.member].append(end / start - 1)
    return {
        m: MemberRecord(m, len(rs), sum(r > 0 for r in rs) / len(rs), sum(rs) / len(rs))
        for m, rs in returns.items() if rs
    }


def top_member_buys(trades: list[CongressTrade], board: dict[str, MemberRecord], symbol: str,
                    today: date, days: int = 60) -> list[CongressTrade]:
    """Recent purchases of ``symbol`` by members with a strong track record."""
    cutoff = today - timedelta(days=days)
    return [
        t for t in trades
        if t.symbol == symbol and t.is_purchase and t.reported >= cutoff
        and board.get(t.member) is not None and board[t.member].is_top
    ]
