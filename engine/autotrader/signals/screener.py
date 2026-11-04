"""Daily stock screener: today's most-active and top-gaining stocks join the scan.

The bot's fixed list covers large, well-known names. Alpaca's free screener
(the same account the bot trades with) adds the stocks the market is actually
moving today, so the trend and safety checks get to see them too. Nothing here
decides a buy: every screened stock goes through exactly the same rules as the
fixed list, and the sector limit treats unknown sectors as one group.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date

log = logging.getLogger(__name__)

SYMBOL = re.compile(r"^[A-Z]{1,5}$")  # skips warrants, units, preferreds (e.g. "ABC.WS", "XYZ-P")

# A fetch returns (most_active_symbols, top_gainer_symbols) for today.
Fetch = Callable[[int], tuple[list[str], list[str]]]


class Screener:
    """Once a day, a short list of extra stocks worth scanning."""

    def __init__(self, fetch: Fetch | None = None, top: int = 50,
                 today: Callable[[], date] = date.today) -> None:
        self.fetch = fetch or alpaca_fetch
        self.top = top
        self.today = today
        self._day: date | None = None
        self._symbols: list[str] = []

    def symbols(self) -> list[str]:
        today = self.today()
        if self._day == today:
            return list(self._symbols)
        try:
            actives, gainers = self.fetch(self.top)
        except Exception as exc:  # a data outage must never stop the fixed list from trading
            log.warning("Screener unavailable: %s", exc)
            return list(self._symbols)  # yesterday's list is better than none
        seen: dict[str, None] = {}
        for s in (*actives, *gainers):
            s = s.upper()
            if SYMBOL.match(s):
                seen[s] = None
        self._day, self._symbols = today, list(seen)
        log.info("Screener found %d stocks to scan today", len(self._symbols))
        return list(self._symbols)


def alpaca_fetch(top: int) -> tuple[list[str], list[str]]:
    """Today's most-active (by volume) and top-gaining US stocks from Alpaca."""
    import os

    from alpaca.data.enums import MarketType, MostActivesBy
    from alpaca.data.historical.screener import ScreenerClient
    from alpaca.data.requests import MarketMoversRequest, MostActivesRequest

    # Either mode's keys work for market data; prefer Test so Live keys stay for trading.
    key = os.environ.get("ALPACA_TEST_KEY_ID") or os.environ.get("ALPACA_LIVE_KEY_ID")
    secret = os.environ.get("ALPACA_TEST_SECRET_KEY") or os.environ.get("ALPACA_LIVE_SECRET_KEY")
    client = ScreenerClient(key, secret)
    actives = client.get_most_actives(MostActivesRequest(top=min(top, 100), by=MostActivesBy.VOLUME))
    movers = client.get_market_movers(MarketMoversRequest(top=min(top, 50), market_type=MarketType.STOCKS))
    return ([a.symbol for a in actives.most_actives], [g.symbol for g in movers.gainers])
