"""Starting list of stocks the bot scans, with sectors for the sector limit.

Stocks outside this list (from the market scan) get their sector from their
SEC industry code (``set_sector_lookup``). Without one they all counted as one
"Unknown" sector, so the 25% sector limit capped every scanned stock together.

These are large, heavily traded US stocks and ETFs. Step 2 adds a screener
that finds more candidates (including penny stocks) automatically. Add your
own with ``AUTOTRADER_ALWAYS_WATCH``; block any with ``AUTOTRADER_NEVER_BUY``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger(__name__)

SECTORS: dict[str, str] = {
    # Technology
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology", "AVGO": "Technology",
    "AMD": "Technology", "ORCL": "Technology", "CRM": "Technology", "ADBE": "Technology",
    "INTC": "Technology", "QCOM": "Technology", "MU": "Technology", "PLTR": "Technology",
    # Communication
    "GOOGL": "Communication", "META": "Communication", "NFLX": "Communication",
    "DIS": "Communication", "T": "Communication",
    # Consumer
    "AMZN": "Consumer", "TSLA": "Consumer", "HD": "Consumer", "MCD": "Consumer",
    "NKE": "Consumer", "COST": "Consumer", "WMT": "Consumer", "PG": "Consumer",
    "KO": "Consumer", "PEP": "Consumer",
    # Financials
    "JPM": "Financials", "BAC": "Financials", "GS": "Financials", "V": "Financials",
    "MA": "Financials", "BRK.B": "Financials",
    # Healthcare
    "UNH": "Healthcare", "LLY": "Healthcare", "JNJ": "Healthcare", "PFE": "Healthcare",
    "MRK": "Healthcare", "ABBV": "Healthcare",
    # Energy
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy",
    # Industrials
    "CAT": "Industrials", "GE": "Industrials", "BA": "Industrials", "UBER": "Industrials",
    # ETFs (treated as their own sector)
    "SPY": "ETF", "QQQ": "ETF", "IWM": "ETF",
}

DEFAULT_UNIVERSE: tuple[str, ...] = tuple(SECTORS)

UNKNOWN = "Unknown"
_lookup: Callable[[str], int | None] | None = None
_found: dict[str, str] = {}


def set_sector_lookup(lookup: Callable[[str], int | None] | None) -> None:
    """Where to find a stock's SEC industry (SIC) code, e.g. ``SecClient.sic``."""
    global _lookup
    _lookup = lookup
    _found.clear()


def sector_from_sic(code: int | None) -> str:
    """A broad sector for an SEC industry code (the same groups as ``SECTORS``)."""
    if code is None:
        return UNKNOWN
    ranges = [
        ((1311, 1311), "Energy"), ((1381, 1389), "Energy"), ((2911, 2911), "Energy"),
        ((4610, 4619), "Energy"), ((5171, 5172), "Energy"),
        ((2830, 2836), "Healthcare"), ((3840, 3851), "Healthcare"), ((5122, 5122), "Healthcare"),
        ((8000, 8099), "Healthcare"),
        ((3570, 3579), "Technology"), ((3600, 3629), "Technology"), ((3640, 3699), "Technology"),
        ((3800, 3839), "Technology"), ((3860, 3899), "Technology"), ((7370, 7379), "Technology"),
        ((3630, 3639), "Consumer"), ((3711, 3716), "Consumer"),
        ((4800, 4899), "Communication"), ((7800, 7899), "Communication"),
        ((2700, 2799), "Communication"),
        ((4900, 4999), "Utilities"),
        ((6500, 6553), "Real Estate"), ((6798, 6798), "Real Estate"),
        ((6000, 6799), "Financials"),
        ((100, 999), "Consumer"), ((1000, 1499), "Materials"), ((1500, 1799), "Industrials"),
        ((2000, 2699), "Consumer"), ((2800, 2899), "Materials"), ((2900, 2999), "Materials"),
        ((3000, 3399), "Materials"), ((3400, 3799), "Industrials"), ((3900, 3999), "Consumer"),
        ((4000, 4799), "Industrials"), ((5000, 5199), "Industrials"), ((5200, 5999), "Consumer"),
        ((7000, 7099), "Consumer"), ((7200, 7299), "Consumer"), ((7900, 7999), "Consumer"),
        ((7000, 8999), "Industrials"),
    ]
    for (lo, hi), sector in ranges:
        if lo <= code <= hi:
            return sector
    return UNKNOWN


def sector_of(symbol: str) -> str:
    if symbol in SECTORS:
        return SECTORS[symbol]
    if symbol in _found:
        return _found[symbol]
    if _lookup is None:
        return UNKNOWN
    try:
        sector = sector_from_sic(_lookup(symbol))
    except Exception as exc:  # unknown for now; asked again next time
        log.info("Couldn't look up the sector of %s: %s", symbol, exc)
        return UNKNOWN
    _found[symbol] = sector
    return sector


def build_universe(always_watch: tuple[str, ...], never_buy: tuple[str, ...]) -> list[str]:
    blocked = {s.upper() for s in never_buy}
    seen: dict[str, None] = {}
    for s in (*DEFAULT_UNIVERSE, *always_watch):
        s = s.upper()
        if s not in blocked:
            seen[s] = None
    return list(seen)
