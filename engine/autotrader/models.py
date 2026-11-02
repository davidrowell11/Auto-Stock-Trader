from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


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

    @property
    def is_otc(self) -> bool:
        return self.exchange.upper() in {"OTC", "OTCQX", "OTCQB", "PINK"}


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
    limit_price: float | None = None  # always set for penny stocks
    reason: str = ""


@dataclass
class Signal:
    """A buy idea produced by the strategy, before risk checks."""

    symbol: str
    score: float  # 0..1, higher is a stronger trend
    price: float
    atr: float
    reasons: list[str] = field(default_factory=list)
