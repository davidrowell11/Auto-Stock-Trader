from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from ..models import Account, Asset, Bar, OrderRequest, Position


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
