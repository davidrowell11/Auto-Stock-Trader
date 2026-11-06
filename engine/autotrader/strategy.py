"""Trend-following strategy.

Buy stocks in strong, steady uptrends (price above the 50-day average, which
is above the 200-day average, near their 1-year high, with positive 3- and
6-month momentum). Sell when the trend breaks (close below the 50-day
average) or a trailing stop is hit.

The trend-break sell looks only at finished days: a stock that dips under its
50-day average for an hour and recovers by the close is still in its uptrend.
Stops act on the live price.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, time
from zoneinfo import ZoneInfo

from . import indicators as ind
from .config import HARD_LIMITS, Settings
from .models import Bar, Signal

MIN_HISTORY = 252  # about one year of trading days
STRETCH_LIMIT = 3.0  # this many average daily moves above the 20-day average: too far, too fast
NEW_YORK = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)


def score_trend(symbol: str, bars: Sequence[Bar]) -> Signal | None:
    """Return a buy signal if the stock is in a qualifying uptrend."""
    if len(bars) < MIN_HISTORY:
        return None
    closes = [b.close for b in bars]
    price = closes[-1]
    sma50 = ind.sma(closes, 50)
    sma200 = ind.sma(closes, 200)
    atr = ind.atr(bars, 14)
    r63 = ind.pct_change(closes, 63)
    r126 = ind.pct_change(closes, 126)
    high252 = ind.highest_close(bars, 252)
    if None in (sma50, sma200, atr, r63, r126, high252) or price <= 0:
        return None
    if not (price > sma50 > sma200):
        return None
    if r63 <= 0 or r126 <= 0:
        return None

    momentum = 0.5 * r63 + 0.5 * r126
    proximity = price / high252  # 1.0 means at the 1-year high
    momentum_part = min(1.0, max(0.0, momentum / 0.40))
    proximity_part = min(1.0, max(0.0, (proximity - 0.75) / 0.25))
    score = round(0.6 * momentum_part + 0.4 * proximity_part, 4)
    if score <= 0:
        return None

    reasons = [
        f"Price ${price:.2f} is above its 50-day (${sma50:.2f}) and 200-day (${sma200:.2f}) averages",
        f"Up {r63:.0%} over 3 months and {r126:.0%} over 6 months",
        f"Trading at {proximity:.0%} of its 1-year high",
    ]
    return Signal(symbol=symbol, score=score, price=price, atr=atr, reasons=reasons)


def initial_stop(entry_price: float, atr: float, multiple: float) -> float:
    """Stop price for a new position, never more than the hard-limit % below entry."""
    floor = entry_price * (1 - HARD_LIMITS.max_stop_loss_pct)
    return max(entry_price - multiple * atr, floor)


def trail_stop(
    current_stop: float, highest_close: float, atr: float, multiple: float
) -> float:
    """Move the stop up as the price rises. A stop never moves down."""
    return max(current_stop, highest_close - multiple * atr)


def stretched(bars: Sequence[Bar], atr: float, limit: float = STRETCH_LIMIT) -> str | None:
    """Why a stock has run up too far to buy right now (it tends to fall back), or None.

    ``limit`` is in average daily moves above the 20-day average; 0 turns the check off.
    """
    if not limit or limit <= 0:
        return None
    closes = [b.close for b in bars]
    sma20 = ind.sma(closes, 20)
    if sma20 is None or not atr or atr <= 0:
        return None
    gap = (closes[-1] - sma20) / atr
    if gap > limit:
        return (f"up too far too fast ({gap:.1f} average daily moves above its 20-day average); "
                "waiting for a pull-back")
    return None


def finished_days(bars: Sequence[Bar], now: datetime) -> Sequence[Bar]:
    """The bars for days that have closed: today's is left out until 4pm New York time."""
    if not bars:
        return bars
    last = bars[-1].time
    last_day = (last.astimezone(NEW_YORK) if last.tzinfo else last).date()
    here = now.astimezone(NEW_YORK) if now.tzinfo else now
    if last_day == here.date() and here.time() < MARKET_CLOSE:
        return bars[:-1]
    return bars


def exit_reason(bars: Sequence[Bar], stop_price: float,
                closed: Sequence[Bar] | None = None) -> str | None:
    """Why the bot should sell now, or None to keep holding.

    ``closed`` is the history up to the last finished day (``finished_days``); the trend
    check uses it, so a dip during the day doesn't sell. Without it, every bar counts.
    """
    if not bars:
        return None
    price = bars[-1].close
    if price <= stop_price:
        return f"Hit stop at ${stop_price:.2f} (price ${price:.2f})"
    closed = bars if closed is None else closed
    if not closed:
        return None
    close = closed[-1].close
    sma50 = ind.sma([b.close for b in closed], 50)
    if sma50 is not None and close < sma50:
        return f"Trend broke: closed at ${close:.2f}, below its 50-day average ${sma50:.2f}"
    return None


def stop_multiple(price: float, settings: Settings) -> float:
    if price < settings.penny_price:
        return settings.penny_atr_stop_multiple
    return settings.atr_stop_multiple
