"""Market mood check: buy less when the whole market is falling.

Uses SPY (the S&P 500 fund) as the market:

* calm      SPY above its 200-day average: normal buying
* cautious  SPY below its 200-day average: half-size buys, no penny stocks
* risk-off  SPY below its 200-day average AND its 50-day average below the
            200-day (a confirmed downtrend): no new buys at all

Stocks already held are still watched and sold as usual in every mood.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from . import indicators as ind
from .models import Bar

MARKET = "SPY"


@dataclass(frozen=True)
class Mood:
    level: str  # calm, cautious, risk-off, unknown
    size_multiplier: float
    allow_penny: bool
    note: str

    @property
    def blocks_buying(self) -> bool:
        return self.size_multiplier <= 0


def market_mood(spy: Sequence[Bar] | None) -> Mood:
    closes = [b.close for b in spy or []]
    sma50, sma200 = ind.sma(closes, 50), ind.sma(closes, 200)
    if not closes or sma50 is None or sma200 is None:
        # Without market data, be careful rather than blind.
        return Mood("unknown", 0.5, False, "Market mood unknown (no SPY data): half-size buys")
    price = closes[-1]
    if price >= sma200:
        return Mood("calm", 1.0, True, f"Market calm: SPY ${price:.2f} is above its 200-day average")
    if sma50 < sma200:
        return Mood("risk-off", 0.0, False,
                    f"Market in a downtrend: SPY ${price:.2f} and its 50-day average are below "
                    f"the 200-day (${sma200:.2f}). No new buys")
    return Mood("cautious", 0.5, False,
                f"Market cautious: SPY ${price:.2f} is below its 200-day average (${sma200:.2f}). "
                "Half-size buys, no penny stocks")
