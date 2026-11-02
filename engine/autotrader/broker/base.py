from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

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

    def open_order_symbols(self) -> set[str]:
        """Symbols with any order (yours or the bot's) that hasn't filled yet."""
        return set()

    def get_order(self, order_id: str) -> OrderUpdate:
        """Whether one order is finished, and how much of it filled."""
        return OrderUpdate(done=True)

    def cancel_order(self, order_id: str) -> None:
        """Cancel one of the bot's unfilled orders. Your own orders are never cancelled."""
