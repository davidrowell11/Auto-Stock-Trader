"""Starting list of stocks the bot scans, with sectors for the sector limit.

These are large, heavily traded US stocks and ETFs. Step 2 adds a screener
that finds more candidates (including penny stocks) automatically. Add your
own with ``AUTOTRADER_ALWAYS_WATCH``; block any with ``AUTOTRADER_NEVER_BUY``.
"""

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


def sector_of(symbol: str) -> str:
    return SECTORS.get(symbol, "Unknown")


def build_universe(always_watch: tuple[str, ...], never_buy: tuple[str, ...]) -> list[str]:
    blocked = {s.upper() for s in never_buy}
    seen: dict[str, None] = {}
    for s in (*DEFAULT_UNIVERSE, *always_watch):
        s = s.upper()
        if s not in blocked:
            seen[s] = None
    return list(seen)
