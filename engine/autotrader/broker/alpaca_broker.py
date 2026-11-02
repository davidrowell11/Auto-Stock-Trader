"""Alpaca brokerage connection (https://alpaca.markets).

Test mode uses your Alpaca paper account and Live mode uses your real
account. Each mode has its own keys, read from environment variables, so a
Test key can never place a real-money trade:

* Test: ``ALPACA_TEST_KEY_ID`` and ``ALPACA_TEST_SECRET_KEY``
* Live: ``ALPACA_LIVE_KEY_ID`` and ``ALPACA_LIVE_SECRET_KEY``
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from ..config import Mode
from ..models import Account, Asset, Bar, OrderRequest, Position, Side
from .base import Broker


class MissingKeys(RuntimeError):
    pass


def _keys(mode: Mode) -> tuple[str, str]:
    prefix = "ALPACA_TEST" if mode is Mode.TEST else "ALPACA_LIVE"
    key, secret = os.environ.get(f"{prefix}_KEY_ID"), os.environ.get(f"{prefix}_SECRET_KEY")
    if not key or not secret:
        raise MissingKeys(f"Set {prefix}_KEY_ID and {prefix}_SECRET_KEY to use {mode.value} mode")
    return key, secret


class AlpacaBroker(Broker):
    def __init__(self, mode: Mode, data_feed: str = "iex") -> None:
        # Imported here so tests and the simulator don't need alpaca-py installed.
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient

        key, secret = _keys(mode)
        self.mode = mode
        self.data_feed = data_feed  # "iex" is free; "sip" needs a paid plan
        self.trading = TradingClient(key, secret, paper=(mode is Mode.TEST))
        self.data = StockHistoricalDataClient(key, secret)

    def get_account(self) -> Account:
        a = self.trading.get_account()
        return Account(equity=float(a.equity), cash=float(a.cash), last_equity=float(a.last_equity))

    def get_positions(self) -> list[Position]:
        return [
            Position(
                symbol=p.symbol,
                qty=float(p.qty),
                avg_price=float(p.avg_entry_price),
                current_price=float(p.current_price),
            )
            for p in self.trading.get_all_positions()
        ]

    def get_daily_bars(self, symbols: Iterable[str], days: int) -> dict[str, list[Bar]]:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        symbols = list(symbols)
        if not symbols:
            return {}
        # Calendar days back, padded for weekends and holidays.
        start = datetime.now(timezone.utc) - timedelta(days=int(days * 1.5) + 10)
        req = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=start,
            feed=DataFeed(self.data_feed),
        )
        result = self.data.get_stock_bars(req).data
        return {
            sym: [
                Bar(b.timestamp, float(b.open), float(b.high), float(b.low), float(b.close), float(b.volume))
                for b in bars
            ][-days:]
            for sym, bars in result.items()
        }

    def get_asset(self, symbol: str) -> Asset:
        a = self.trading.get_asset(symbol)
        exchange = getattr(a.exchange, "value", str(a.exchange))
        return Asset(symbol=a.symbol, exchange=exchange, tradable=bool(a.tradable),
                     fractionable=bool(getattr(a, "fractionable", False)))

    def submit_order(self, order: OrderRequest) -> str:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        side = OrderSide.BUY if order.side is Side.BUY else OrderSide.SELL
        if order.limit_price is not None:
            req = LimitOrderRequest(
                symbol=order.symbol, qty=order.qty, side=side,
                time_in_force=TimeInForce.DAY, limit_price=order.limit_price,
            )
        else:
            req = MarketOrderRequest(
                symbol=order.symbol, qty=order.qty, side=side, time_in_force=TimeInForce.DAY
            )
        return str(self.trading.submit_order(req).id)

    def open_order_symbols(self) -> set[str]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500))
        return {o.symbol for o in orders}

    def cancel_open_orders(self) -> None:
        self.trading.cancel_orders()

    def is_market_open(self) -> bool:
        return bool(self.trading.get_clock().is_open)
