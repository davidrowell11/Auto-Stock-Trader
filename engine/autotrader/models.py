from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

_FUND = re.compile(r"\b(ETF|ETN|Shares|Fund|Trust|ProShares|Direxion)\b", re.I)
_LEVERAGED = re.compile(r"\b(\d(\.\d+)?x|Ultra\w*|Bear|Bull|Inverse|Leveraged|Short)\b", re.I)


@dataclass(frozen=True)
class Bar:
    """One day of price data."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Asset:
    symbol: str
    exchange: str = ""
    tradable: bool = True
    sector: str = "Unknown"
    fractionable: bool = False  # can be bought in fractions of a share
    name: str = ""  # e.g. "Direxion Daily Semiconductor Bear 3X Shares"

    @property
    def is_otc(self) -> bool:
        return self.exchange.upper() in {"OTC", "OTCQX", "OTCQB", "PINK"}

    @property
    def is_leveraged_fund(self) -> bool:
        """A 2x/3x or inverse fund. They lose value over multi-week holds, so they're never bought."""
        return bool(_FUND.search(self.name) and _LEVERAGED.search(self.name))


@dataclass
class Position:
    symbol: str
    qty: float
    avg_price: float
    current_price: float
    sector: str = "Unknown"

    @property
    def market_value(self) -> float:
        return self.qty * self.current_price


@dataclass(frozen=True)
class Account:
    equity: float
    cash: float
    last_equity: float  # equity at the previous close, for the daily loss cap


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: Side
    qty: float
    limit_price: float | None = None  # always set for penny stocks and outside regular hours
    reason: str = ""
    extended_hours: bool = False  # placed before 9:30am or after 4pm New York time (limit only)


@dataclass(frozen=True)
class OrderUpdate:
    """Where an order stands. filled_qty None means the broker doesn't say (treat as all filled)."""

    done: bool
    filled_qty: float | None = None


@dataclass
class Signal:
    """A buy idea produced by the strategy, before risk checks."""

    symbol: str
    score: float  # 0..1, higher is a stronger trend
    price: float
    atr: float
    reasons: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=lambda: ["trend"])  # what pointed the bot here (for learning)
