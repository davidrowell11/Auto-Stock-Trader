"""Backtesting and fast replay.

Runs the real bot (the same Engine as Test and Live) day by day over past
prices, with trading costs charged on every fill. The bot only ever sees
prices up to the day it is on, so it can't peek ahead.

    python -m autotrader.backtest --years 5             # full backtest
    python -m autotrader.backtest --months 12 --budget 10000   # fast replay for the Test tab

Honest-testing rules built in:
* Costs default to 10 basis points (0.10%) per trade, roughly double a
  realistic cost for liquid stocks. Use ``--cost-bps`` to change it.
* Results are split into the first 70% of the period and the last 30%, so you
  can see whether it held up on later data, not just on average.
* The AI analyst and expert signals are off in backtests: Claude already
  knows what happened in the past, and the signal feeds aren't point-in-time.
  Those are measured going forward instead (see the analyst scorecard).

Known limit: the default stock list is today's large companies, which
survived by definition. That flatters results (survivorship bias), so treat
backtest returns as an upper bound.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from datetime import datetime

from .broker.sim import SimBroker
from .config import Settings, load_settings
from .engine import HISTORY_DAYS, Engine
from .journal import Journal
from .models import Bar, Side
from .strategy import MIN_HISTORY


class ReplayBroker(SimBroker):
    """A simulator that walks forward through history one trading day at a time."""

    def __init__(self, cash: float, history: dict[str, list[Bar]], slippage_bps: float) -> None:
        super().__init__(cash, {}, slippage_bps=slippage_bps)
        self.history = history
        self.dates = sorted({b.time for bars in history.values() for b in bars})
        self._ptr = {s: 0 for s in history}  # index of the next unseen bar per symbol

    def advance_to(self, day: datetime) -> None:
        """Show the bot every bar up to and including ``day``."""
        self.last_equity = self.get_account().equity if self.bars else self.cash
        for symbol, bars in self.history.items():
            i = self._ptr[symbol]
            while i < len(bars) and bars[i].time <= day:
                i += 1
            self._ptr[symbol] = i
            if i:
                self.bars[symbol] = bars[max(0, i - HISTORY_DAYS) : i]

    def get_daily_bars(self, symbols, days):
        # Only symbols that have traded by now; never anything later.
        return {s: self.bars[s][-days:] for s in symbols if s in self.bars}

    def price(self, symbol: str) -> float:
        return self.bars[symbol][-1].close


@dataclass
class Stats:
    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe: float

    def line(self) -> str:
        return (f"return {self.total_return:+.1%}, per year {self.annual_return:+.1%}, "
                f"worst drop {self.max_drawdown:.1%}, Sharpe {self.sharpe:.2f}")


@dataclass
class BacktestResult:
    start: datetime
    end: datetime
    equity_curve: list[tuple[datetime, float]]
    overall: Stats
    first_part: Stats
    last_part: Stats
    benchmark: Stats | None
    trades: int
    win_rate: float | None
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Backtest {self.start:%Y-%m-%d} to {self.end:%Y-%m-%d}",
            f"  Bot:        {self.overall.line()}",
            f"  Benchmark:  {self.benchmark.line() if self.benchmark else 'SPY not in data'}",
            f"  First 70%:  {self.first_part.line()}",
            f"  Last 30%:   {self.last_part.line()}",
            f"  Trades: {self.trades}, winners: "
            + ("n/a" if self.win_rate is None else f"{self.win_rate:.0%}"),
        ]
        lines += [f"  Note: {n}" for n in self.notes]
        return "\n".join(lines)


def stats(curve: list[tuple[datetime, float]]) -> Stats:
    if len(curve) < 2 or curve[0][1] <= 0:
        return Stats(0.0, 0.0, 0.0, 0.0)
    values = [v for _, v in curve]
    total = values[-1] / values[0] - 1
    years = max((curve[-1][0] - curve[0][0]).days / 365.25, 1 / 365.25)
    annual = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    peak, worst = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        worst = max(worst, 1 - v / peak)
    rets = [b / a - 1 for a, b in zip(values, values[1:]) if a > 0]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    sharpe = mean / math.sqrt(var) * math.sqrt(252) if var > 0 else 0.0
    return Stats(total, annual, worst, sharpe)


def run_backtest(history: dict[str, list[Bar]], settings: Settings, cash: float = 100_000,
                 cost_bps: float = 10.0, warmup: int = MIN_HISTORY,
                 journal: Journal | None = None) -> BacktestResult:
    """Replay ``history`` through the bot. The first ``warmup`` days only build indicators.

    Pass a journal to read the replayed trades afterwards (e.g. which ones lost money).
    """
    broker = ReplayBroker(cash, history, cost_bps)
    day_start = [0]  # where today's orders begin in broker.orders

    def buys_today() -> int:  # the journal's clock is today's, not the replayed day's
        return len({o.symbol for o in broker.orders[day_start[0]:] if o.side is Side.BUY})

    engine = Engine(broker, settings, journal if journal is not None else Journal(),
                    penny_needs_analyst=False, buys_today=buys_today)
    dates = broker.dates
    if len(dates) <= warmup + 1:
        raise ValueError(f"Need more than {warmup + 1} trading days of history, got {len(dates)}")

    curve: list[tuple[datetime, float]] = []
    for day in dates[warmup:]:
        broker.advance_to(day)
        day_start[0] = len(broker.orders)
        engine.run_cycle()
        curve.append((day, broker.get_account().equity))

    split = int(len(curve) * 0.7)
    bench = None
    spy = history.get("SPY")
    if spy:
        spy_curve = [(b.time, b.close) for b in spy if b.time >= curve[0][0]]
        bench = stats(spy_curve)
    wins = [p for p in broker.realized if p > 0]
    return BacktestResult(
        start=curve[0][0], end=curve[-1][0], equity_curve=curve,
        overall=stats(curve), first_part=stats(curve[: split + 1]),
        last_part=stats(curve[split:]), benchmark=bench,
        trades=len(broker.orders),
        win_rate=len(wins) / len(broker.realized) if broker.realized else None,
        notes=[
            f"Trading costs charged: {cost_bps:.0f} basis points per fill",
            "Stock list is today's large companies, so results are likely flattered (survivorship bias)",
            "AI analyst and expert signals are off in backtests; they are scored going forward instead",
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autotrader.backtest")
    span = parser.add_mutually_exclusive_group()
    span.add_argument("--years", type=float, default=5.0)
    span.add_argument("--months", type=float, help="fast replay of the last N months")
    parser.add_argument("--budget", type=float, help="money the bot may use")
    parser.add_argument("--cash", type=float, default=100_000)
    parser.add_argument("--cost-bps", type=float, default=10.0)
    args = parser.parse_args(argv)

    from .broker.alpaca_broker import AlpacaBroker
    from .config import Mode
    from .universe import build_universe

    settings = load_settings(mode=Mode.TEST, **({"budget": args.budget} if args.budget is not None else {}))
    test_days = round((args.months / 12 if args.months else args.years) * 252)
    symbols = build_universe(settings.always_watch, settings.never_buy)
    if "SPY" not in symbols:
        symbols.append("SPY")
    history = AlpacaBroker(Mode.TEST).get_daily_bars(symbols, test_days + MIN_HISTORY)
    print(run_backtest(history, settings, cash=args.cash, cost_bps=args.cost_bps).summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
