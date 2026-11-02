"""Combines the expert signals for a stock into one score boost plus reasons.

Signals only ever add to a stock the trend strategy already likes; they
can't make the bot buy a stock in a downtrend, and they never bypass risk
rules. Results are cached for the day so the SEC and Quiver aren't asked
the same thing every 5 minutes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from ..models import Bar
from .congress import CongressTrade, MemberRecord, QuiverClient, build_leaderboard, top_member_buys
from .sec import InsiderActivity, SecClient

log = logging.getLogger("autotrader.signals")

BarsFetch = Callable[[list[str], int], dict[str, list[Bar]]]

INSIDER_BOOST_PER_BUYER = 0.05
INSIDER_BOOST_MAX = 0.15
INSIDER_MIN_VALUE = 100_000.0  # ignore token purchases
CONGRESS_BOOST_PER_BUY = 0.05
CONGRESS_BOOST_MAX = 0.10
TOTAL_BOOST_MAX = 0.20


@dataclass
class ExpertSignals:
    insider: InsiderActivity
    congress_buys: list[CongressTrade] = field(default_factory=list)
    boost: float = 0.0
    reasons: list[str] = field(default_factory=list)


def price_lookup(bars: dict[str, list[Bar]]):
    def price_on(symbol: str, day: date) -> float | None:
        for b in bars.get(symbol, []):
            if b.time.date() >= day:
                return b.close
        return None
    return price_on


class SignalBook:
    def __init__(self, sec: SecClient | None = None, quiver: QuiverClient | None = None,
                 bars_fetch: BarsFetch | None = None, today: Callable[[], date] = date.today) -> None:
        self.sec = sec or SecClient()
        self.quiver = quiver or QuiverClient()
        self.bars_fetch = bars_fetch
        self.today = today
        self._day: date | None = None
        self._trades: list[CongressTrade] = []
        self.leaderboard: dict[str, MemberRecord] = {}
        self._cache: dict[str, ExpertSignals] = {}

    def _refresh(self) -> None:
        today = self.today()
        if self._day == today:
            return
        self._day, self._cache = today, {}
        try:
            self._trades = self.quiver.recent_trades()
        except Exception as exc:  # a data outage shouldn't stop trading
            log.warning("Congress trades unavailable: %s", exc)
            self._trades = []
        if self._trades and self.bars_fetch:
            symbols = sorted({t.symbol for t in self._trades if t.is_purchase})
            try:
                bars = self.bars_fetch(symbols, 500)
                self.leaderboard = build_leaderboard(self._trades, price_lookup(bars), today)
            except Exception as exc:
                log.warning("Couldn't build the Congress leaderboard: %s", exc)

    def for_symbol(self, symbol: str) -> ExpertSignals:
        self._refresh()
        if symbol in self._cache:
            return self._cache[symbol]
        try:
            insider = self.sec.insider_buying(symbol)
        except Exception as exc:
            log.warning("Insider data unavailable for %s: %s", symbol, exc)
            insider = InsiderActivity()
        congress = top_member_buys(self._trades, self.leaderboard, symbol, self.today())

        sig = ExpertSignals(insider=insider, congress_buys=congress)
        if insider.buyers and insider.total_value >= INSIDER_MIN_VALUE:
            sig.boost += min(INSIDER_BOOST_MAX, INSIDER_BOOST_PER_BUYER * len(insider.buyers))
            sig.reasons.append(insider.summary)
        if congress:
            sig.boost += min(CONGRESS_BOOST_MAX, CONGRESS_BOOST_PER_BUY * len(congress))
            for t in congress[:3]:
                rec = self.leaderboard[t.member]
                sig.reasons.append(
                    f"{t.member} bought on {t.traded} (reported {t.reported}); their past buys "
                    f"rose {rec.hit_rate:.0%} of the time, averaging {rec.avg_return:+.1%}"
                )
        sig.boost = min(sig.boost, TOTAL_BOOST_MAX)
        self._cache[symbol] = sig
        return sig
