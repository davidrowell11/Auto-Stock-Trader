"""Alpaca brokerage connection (https://alpaca.markets).

Test mode uses your Alpaca paper account and Live mode uses your real
account. Each mode has its own keys, read from environment variables, so a
Test key can never place a real-money trade:

* Test: ``ALPACA_TEST_KEY_ID`` and ``ALPACA_TEST_SECRET_KEY``
* Live: ``ALPACA_LIVE_KEY_ID`` and ``ALPACA_LIVE_SECRET_KEY``
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterable
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..config import Mode
from ..models import Account, Asset, Bar, OrderRequest, OrderUpdate, Position, Side
from .base import Broker


NEW_YORK = ZoneInfo("America/New_York")
EXTENDED_START = time(4, 0)  # pre-market opens
EXTENDED_END = time(20, 0)  # after-hours trading ends


def _naive_ny(value: datetime) -> datetime:
    """A calendar time as naive New York time (Alpaca returns it naive already)."""
    return value.astimezone(NEW_YORK).replace(tzinfo=None) if value.tzinfo else value


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
        self._calendar_day = None
        self._calendar: tuple[datetime, datetime] | None = None

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
        """Place an order. Each one carries its own ID, so an order whose reply got lost on the
        way back (a timeout, a dropped connection) is found again instead of being sent twice
        or left without a safety stop."""
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        side = OrderSide.BUY if order.side is Side.BUY else OrderSide.SELL
        if order.extended_hours and order.limit_price is None:
            raise ValueError("Orders outside regular hours need a limit price")
        client_id = f"autotrader-{uuid.uuid4().hex}"
        if order.limit_price is not None:
            req = LimitOrderRequest(
                symbol=order.symbol, qty=order.qty, side=side,
                time_in_force=TimeInForce.DAY, limit_price=order.limit_price,
                extended_hours=order.extended_hours, client_order_id=client_id,
            )
        else:
            req = MarketOrderRequest(
                symbol=order.symbol, qty=order.qty, side=side, time_in_force=TimeInForce.DAY,
                client_order_id=client_id,
            )
        try:
            return str(self.trading.submit_order(req).id)
        except Exception:
            # Did Alpaca take it anyway? If so, it's placed; if not (or we can't tell), it failed.
            try:
                return str(self.trading.get_order_by_client_id(client_id).id)
            except Exception:
                pass
            raise

    def open_order_symbols(self) -> set[str]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500))
        return {o.symbol for o in orders}

    # Finished for good. "done_for_day" isn't here: a day order in that state still expires later.
    FINISHED = {"filled", "canceled", "expired", "rejected", "replaced"}

    def get_order(self, order_id: str) -> OrderUpdate:
        o = self.trading.get_order_by_id(order_id)
        status = getattr(o.status, "value", str(o.status))
        return OrderUpdate(done=status in self.FINISHED, filled_qty=float(o.filled_qty or 0))

    def cancel_order(self, order_id: str) -> None:
        self.trading.cancel_order_by_id(order_id)

    def is_market_open(self) -> bool:
        return bool(self.trading.get_clock().is_open)

    def market_session(self) -> str:
        if self.is_market_open():
            return "regular"
        now = datetime.now(NEW_YORK)
        day = self._trading_day(now.date())
        if day is None:  # weekend or market holiday: no extended hours either
            return "closed"
        open_, close = day
        t = now.replace(tzinfo=None)
        if datetime.combine(t.date(), EXTENDED_START) <= t < open_:
            return "pre"
        if close <= t < datetime.combine(t.date(), EXTENDED_END):
            return "after"
        return "closed"

    def _trading_day(self, day) -> tuple[datetime, datetime] | None:
        """Today's regular open and close (New York time, naive), or None if the market is shut all day.

        Looked up once a day; it knows holidays and early-close days.
        """
        if self._calendar_day != day:
            from alpaca.trading.requests import GetCalendarRequest

            days = self.trading.get_calendar(GetCalendarRequest(start=day, end=day))
            hit = next((d for d in days if d.date == day), None)
            self._calendar = None if hit is None else (_naive_ny(hit.open), _naive_ny(hit.close))
            self._calendar_day = day
        return self._calendar

    def latest_prices(self, symbols: Iterable[str]) -> dict[str, float]:
        from alpaca.data.requests import StockLatestTradeRequest

        symbols = sorted(set(symbols))
        if not symbols:
            return {}
        trades = self.data.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=symbols, feed=self.data_feed))
        return {s: float(t.price) for s, t in trades.items() if t is not None and t.price}
