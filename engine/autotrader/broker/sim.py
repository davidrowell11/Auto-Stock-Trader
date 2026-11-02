"""An in-memory broker used by tests and, later, by fast replay in the Test tab."""

from __future__ import annotations

from collections.abc import Iterable

from ..models import Account, Asset, Bar, OrderRequest, OrderUpdate, Position, Side
from .base import Broker


class SimBroker(Broker):
    def __init__(
        self,
        cash: float,
        bars: dict[str, list[Bar]],
        assets: dict[str, Asset] | None = None,
        market_open: bool = True,
        slippage_bps: float = 0.0,
        session: str | None = None,
    ) -> None:
        self.session = session  # None: "regular" when market_open, else "closed"
        self.cash = cash
        self.bars = bars
        self.assets = assets or {}
        self.market_open = market_open
        self.holdings: dict[str, tuple[float, float]] = {}  # symbol -> (qty, avg price)
        self.orders: list[OrderRequest] = []
        self.last_equity = cash
        self.slippage = slippage_bps / 10_000  # trading cost charged on every fill
        self.realized: list[float] = []  # profit or loss of each closed sale
        self.fills: dict[str, float] = {}  # order id -> quantity filled (sim orders fill at once)

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
        if order.extended_hours and order.limit_price is None:
            raise ValueError("Orders outside regular hours need a limit price")
        price = self.price(order.symbol)
        if order.limit_price is not None:
            if order.side is Side.BUY and price > order.limit_price:
                raise ValueError("limit not reached")
            if order.side is Side.SELL and price < order.limit_price:
                raise ValueError("limit not reached")
        self.orders.append(order)
        qty, avg = self.holdings.get(order.symbol, (0.0, 0.0))
        if order.side is Side.BUY:
            price *= 1 + self.slippage
            new_qty = qty + order.qty
            self.holdings[order.symbol] = (new_qty, (qty * avg + order.qty * price) / new_qty)
            self.cash -= order.qty * price
        else:
            price *= 1 - self.slippage
            sell = min(order.qty, qty)
            self.cash += sell * price
            self.realized.append(sell * (price - avg))
            if qty - sell <= 0:
                self.holdings.pop(order.symbol, None)
            else:
                self.holdings[order.symbol] = (qty - sell, avg)
        order_id = f"sim-{len(self.orders)}"
        self.fills[order_id] = order.qty if order.side is Side.BUY else sell
        return order_id

    def get_order(self, order_id: str) -> OrderUpdate:
        return OrderUpdate(done=True, filled_qty=self.fills.get(order_id))

    def market_session(self) -> str:
        if self.session is not None:
            return self.session
        return "regular" if self.market_open else "closed"

    def latest_prices(self, symbols: Iterable[str]) -> dict[str, float]:
        return {s: self.price(s) for s in symbols if s in self.bars}

    def is_market_open(self) -> bool:
        if self.session is not None:
            return self.session == "regular"
        return self.market_open
