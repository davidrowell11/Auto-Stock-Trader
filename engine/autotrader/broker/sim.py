"""An in-memory broker used by tests and, later, by fast replay in the Test tab."""

from __future__ import annotations

from collections.abc import Iterable

from ..models import Account, Asset, Bar, OrderRequest, Position, Side
from .base import Broker


class SimBroker(Broker):
    def __init__(
        self,
        cash: float,
        bars: dict[str, list[Bar]],
        assets: dict[str, Asset] | None = None,
        market_open: bool = True,
    ) -> None:
        self.cash = cash
        self.bars = bars
        self.assets = assets or {}
        self.market_open = market_open
        self.holdings: dict[str, tuple[float, float]] = {}  # symbol -> (qty, avg price)
        self.orders: list[OrderRequest] = []
        self.last_equity = cash

    def price(self, symbol: str) -> float:
        return self.bars[symbol][-1].close

    def get_account(self) -> Account:
        equity = self.cash + sum(q * self.price(s) for s, (q, _) in self.holdings.items())
        return Account(equity=equity, cash=self.cash, last_equity=self.last_equity)

    def get_positions(self) -> list[Position]:
        return [
            Position(s, q, avg, self.price(s), self.assets.get(s, Asset(s)).sector)
            for s, (q, avg) in self.holdings.items()
        ]

    def get_daily_bars(self, symbols: Iterable[str], days: int) -> dict[str, list[Bar]]:
        return {s: self.bars[s][-days:] for s in symbols if s in self.bars}

    def get_asset(self, symbol: str) -> Asset:
        return self.assets.get(symbol, Asset(symbol, exchange="NASDAQ"))

    def submit_order(self, order: OrderRequest) -> str:
        price = self.price(order.symbol)
        if order.limit_price is not None:
            if order.side is Side.BUY and price > order.limit_price:
                raise ValueError("limit not reached")
            if order.side is Side.SELL and price < order.limit_price:
                raise ValueError("limit not reached")
        self.orders.append(order)
        qty, avg = self.holdings.get(order.symbol, (0.0, 0.0))
        if order.side is Side.BUY:
            new_qty = qty + order.qty
            self.holdings[order.symbol] = (new_qty, (qty * avg + order.qty * price) / new_qty)
            self.cash -= order.qty * price
        else:
            sell = min(order.qty, qty)
            self.cash += sell * price
            if qty - sell <= 0:
                self.holdings.pop(order.symbol, None)
            else:
                self.holdings[order.symbol] = (qty - sell, avg)
        return f"sim-{len(self.orders)}"

    def is_market_open(self) -> bool:
        return self.market_open
