"""Small, dependency-free technical indicators.

Every function only looks at data up to and including the last bar it is
given, so nothing can peek into the future during backtests.
"""

from __future__ import annotations

from collections.abc import Sequence

from .models import Bar


def sma(values: Sequence[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def atr(bars: Sequence[Bar], period: int = 14) -> float | None:
    """Average True Range: the stock's typical daily move in dollars."""
    if len(bars) < period + 1:
        return None
    trs = []
    for prev, cur in zip(bars[-period - 1 : -1], bars[-period:]):
        trs.append(
            max(
                cur.high - cur.low,
                abs(cur.high - prev.close),
                abs(cur.low - prev.close),
            )
        )
    return sum(trs) / period


def pct_change(values: Sequence[float], lookback: int) -> float | None:
    if len(values) <= lookback or values[-lookback - 1] <= 0:
        return None
    return values[-1] / values[-lookback - 1] - 1.0


def avg_dollar_volume(bars: Sequence[Bar], period: int = 20) -> float | None:
    if len(bars) < period:
        return None
    return sum(b.close * b.volume for b in bars[-period:]) / period


def highest_close(bars: Sequence[Bar], period: int) -> float | None:
    if len(bars) < period:
        return None
    return max(b.close for b in bars[-period:])
