from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime

from ..models import Account, Asset, Bar, OrderRequest, OrderUpdate, Position


class Broker(ABC):
    """What the engine needs from a brokerage. Alpaca and the simulator both implement it."""

    @abstractmethod
    def get_account(self) -> Account: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def get_daily_bars(self, symbols: Iterable[str], days: int) -> dict[str, list[Bar]]: ...

    @abstractmethod
    def get_asset(self, symbol: str) -> Asset: ...

    @abstractmethod
    def submit_order(self, order: OrderRequest) -> str:
        """Place the order and return the broker's order id."""

    @abstractmethod
    def is_market_open(self) -> bool: ...

    def market_session(self) -> str:
        """"regular" (9:30am-4pm New York time), "pre" (4am-9:30am), "after" (4pm-8pm),
        "overnight" (8pm-4am, Sunday night to Friday morning) or "closed"."""
        return "regular" if self.is_market_open() else "closed"

    def latest_prices(self, symbols: Iterable[str]) -> dict[str, float]:
        """Most recent trade price for each symbol, including pre-market and after-hours trades."""
        return {}

    def equity_history(self, days: int) -> list[tuple[datetime, float]]:
        """The account's value at the end of each recent day, oldest first (if the broker keeps it)."""
        return []

    def open_order_symbols(self) -> set[str]:
        """Symbols with any order (yours or the bot's) that hasn't filled yet."""
        return set()

    def get_order(self, order_id: str) -> OrderUpdate:
        """Whether one order is finished, and how much of it filled."""
        return OrderUpdate(done=True)

    def cancel_order(self, order_id: str) -> None:
        """Cancel one of the bot's unfilled orders. Your own orders are never cancelled."""
