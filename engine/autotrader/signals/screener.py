"""Which stocks the bot scans, beyond its fixed list of well-known names.

* ``MarketScan``: once a day, the few hundred most-traded US stocks (by dollars
  traded), so the trend rules see most of the market's big, liquid names.
* ``Screener``: today's most-active stocks from Alpaca's free screener.

Top gainers used to join too; they were dropped because buying a stock right
after its biggest day tends to buy the top. Nothing here decides a buy: every
stock goes through exactly the same rules as the fixed list, and the sector
limit treats unknown sectors as one group.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Iterable
from datetime import date

from ..models import Asset

log = logging.getLogger(__name__)

SYMBOL = re.compile(r"^[A-Z]{1,5}$")  # skips warrants, units, preferreds (e.g. "ABC.WS", "XYZ-P")
EXCHANGES = {"NYSE", "NASDAQ", "ARCA", "AMEX", "BATS"}  # listed stocks and funds, never OTC
SCAN_SIZE = 500  # most-traded stocks scanned each day
SCAN_MIN_PRICE = 10.0  # the scan leaves out cheap stocks (penny stocks come only from the screener)
SNAPSHOT_CHUNK = 200

# A fetch returns two lists of today's most-active symbols (by shares, by number of trades).
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
    """Today's most-active US stocks from Alpaca, by shares and by number of trades.

    (Top gainers are no longer added: see the note at the top of this file.)
    """
    from alpaca.data.enums import MostActivesBy
    from alpaca.data.historical.screener import ScreenerClient
    from alpaca.data.requests import MostActivesRequest

    key, secret = _keys()
    client = ScreenerClient(key, secret)
    by_volume = client.get_most_actives(MostActivesRequest(top=min(top, 100), by=MostActivesBy.VOLUME))
    by_trades = client.get_most_actives(MostActivesRequest(top=min(top, 100), by=MostActivesBy.TRADES))
    return ([a.symbol for a in by_volume.most_actives], [a.symbol for a in by_trades.most_actives])


# A listing returns (symbol, name) for every tradable US stock; a trading lookup returns
# symbol -> (price, dollars traded) for a batch of symbols.
Listing = Callable[[], list[tuple[str, str]]]
Liquidity = Callable[[list[str]], dict[str, tuple[float, float]]]


def rank_liquid(listing: Iterable[tuple[str, str]], liquidity: Liquidity, top: int = SCAN_SIZE,
                min_price: float = SCAN_MIN_PRICE) -> list[str]:
    """The ``top`` stocks by dollars traded, leaving out odd symbols, cheap stocks and leveraged funds."""
    names = {}
    for symbol, name in listing:
        if SYMBOL.match(symbol) and not Asset(symbol, name=name).is_leveraged_fund:
            names[symbol] = name
    symbols = sorted(names)
    traded: dict[str, tuple[float, float]] = {}
    for i in range(0, len(symbols), SNAPSHOT_CHUNK):
        traded.update(liquidity(symbols[i:i + SNAPSHOT_CHUNK]))
    ok = [(dollars, s) for s, (price, dollars) in traded.items()
          if s in names and price >= min_price and dollars > 0]
    ok.sort(reverse=True)
    return [s for _, s in ok[:top]]


class MarketScan:
    """Once a day, the most-traded US stocks. A failed refresh keeps the last list."""

    def __init__(self, listing: Listing | None = None, liquidity: Liquidity | None = None,
                 top: int = SCAN_SIZE, today: Callable[[], date] = date.today) -> None:
        self.listing = listing or alpaca_listing
        self.liquidity = liquidity or alpaca_liquidity
        self.top = top
        self.today = today
        self._day: date | None = None
        self._symbols: list[str] = []
        self._lock = threading.Lock()  # Test and Live share one scan

    def symbols(self) -> list[str]:
        with self._lock:
            return self._refresh()

    def _refresh(self) -> list[str]:
        today = self.today()
        if self._day == today:
            return list(self._symbols)
        try:
            found = rank_liquid(self.listing(), self.liquidity, self.top)
        except Exception as exc:  # the fixed list keeps trading without it
            log.warning("Market scan unavailable: %s", exc)
            return list(self._symbols)
        if found:
            self._symbols = found
        self._day = today  # one try a day; tomorrow's refresh tries again
        log.info("Market scan: %d of the most-traded stocks", len(self._symbols))
        return list(self._symbols)


def _keys() -> tuple[str | None, str | None]:
    import os

    # Either mode's keys work for market data; prefer Test so Live keys stay for trading.
    key = os.environ.get("ALPACA_TEST_KEY_ID") or os.environ.get("ALPACA_LIVE_KEY_ID")
    secret = os.environ.get("ALPACA_TEST_SECRET_KEY") or os.environ.get("ALPACA_LIVE_SECRET_KEY")
    return key, secret


def alpaca_listing() -> list[tuple[str, str]]:
    """Every active, tradable US stock and fund on a listed exchange."""
    import os

    from alpaca.trading.client import TradingClient

    key, secret = _keys()
    paper = bool(os.environ.get("ALPACA_TEST_KEY_ID"))
    client = TradingClient(key, secret, paper=paper)
    # The raw list: alpaca-py's typed version fails outright on an exchange it doesn't know
    # yet (Alpaca added TXSE, the Texas exchange, in late 2026).
    assets = client.get("/assets", {"status": "active", "asset_class": "us_equity"})
    return listed(assets)


def listed(assets: list[dict]) -> list[tuple[str, str]]:
    """(symbol, name) of each tradable asset on a listed exchange, from Alpaca's raw asset list."""
    return [(a["symbol"], str(a.get("name") or "")) for a in assets
            if a.get("tradable") and a.get("exchange") in EXCHANGES and a.get("symbol")]


def alpaca_liquidity(symbols: list[str]) -> dict[str, tuple[float, float]]:
    """Last price and dollars traded on the last full day (free IEX data: a slice of all
    trading, but the order from most to least traded holds)."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockSnapshotRequest

    key, secret = _keys()
    client = StockHistoricalDataClient(key, secret)
    snaps = client.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=symbols,
                                                           feed=DataFeed.IEX))
    out = {}
    for s, snap in snaps.items():
        bar = getattr(snap, "previous_daily_bar", None) or getattr(snap, "daily_bar", None)
        if bar is not None and bar.close and bar.volume:
            out[s] = (float(bar.close), float(bar.close) * float(bar.volume))
    return out
