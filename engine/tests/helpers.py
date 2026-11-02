from datetime import datetime, timedelta

from autotrader.models import Bar


def make_bars(closes, volume=2_000_000, spread=0.01):
    start = datetime(2024, 1, 1)
    return [
        Bar(start + timedelta(days=i), c, c * (1 + spread), c * (1 - spread), c, volume)
        for i, c in enumerate(closes)
    ]


def uptrend(n=260, start=50.0, daily=0.002):
    closes, price = [], start
    for _ in range(n):
        price *= 1 + daily
        closes.append(price)
    return closes


def downtrend(n=260, start=100.0, daily=-0.002):
    return uptrend(n, start, daily)
