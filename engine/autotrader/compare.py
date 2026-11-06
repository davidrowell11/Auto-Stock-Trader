"""Compare rule sets on the same past prices, and see which small changes hold up.

    python -m autotrader.compare                 # the fixed list of large stocks
    python -m autotrader.compare --wide          # plus today's ~500 most-traded stocks
    python -m autotrader.compare --json out.json # also save the numbers

It runs, on one download of prices:

* the rules from before the Nov 6 changes (no stretch check, no daily buy limit,
  no risk sizing, no minimum size, no trims),
* the current rules,
* each one-step change the strategy lab may try (see lab.py),

and judges each change the way the lab does: better on the older two thirds of
the period, and better again on the most recent third, which played no part in
choosing it. A change that only wins on one of the two is noise, not learning.

Same caveats as backtest.py: the AI analyst and expert signals are off, and
today's stock lists only hold companies that survived, which flatters results
(more so with --wide).
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import replace

from . import indicators as ind
from .backtest import BacktestResult, run_backtest
from .config import Mode, Settings, load_settings
from .lab import KNOBS, MAX_EXTRA_DROP, MIN_SHARPE_GAIN, split_history
from .strategy import MIN_HISTORY

YEARS = 3  # tested on, after a year of warm-up

# What the rules were before the Nov 6 changes, as settings. (The close-only trend exit
# makes no difference here: a backtest only ever sees finished days.)
OLD_RULES = {"stretch_limit": 0.0, "max_new_buys_per_day": 6, "risk_per_trade_pct": 0.005,
             "min_position_fraction": 0.0, "trim_over": 1000.0}


def row(name: str, full: BacktestResult, tune: BacktestResult, unseen: BacktestResult) -> dict:
    return {
        "name": name,
        "return": round(full.overall.total_return, 4),
        "per_year": round(full.overall.annual_return, 4),
        "worst_drop": round(full.overall.max_drawdown, 4),
        "sharpe": round(full.overall.sharpe, 2),
        "tune_sharpe": round(tune.overall.sharpe, 2),
        "unseen_sharpe": round(unseen.overall.sharpe, 2),
        "unseen_return": round(unseen.overall.total_return, 4),
        "unseen_drop": round(unseen.overall.max_drawdown, 4),
        "trades": full.trades,
        "win_rate": None if full.win_rate is None else round(full.win_rate, 3),
    }


def holds_up(change: dict, base: dict) -> bool:
    """The lab's first two gates: better on the tuning stretch, and again on the unseen one."""
    return (change["tune_sharpe"] > base["tune_sharpe"]
            and change["unseen_sharpe"] >= base["unseen_sharpe"] + MIN_SHARPE_GAIN
            and change["unseen_return"] > base["unseen_return"]
            and change["unseen_drop"] <= base["unseen_drop"] + MAX_EXTRA_DROP)


def compare(history: dict, base: Settings, cost_bps: float = 10.0) -> dict:
    tune_h, unseen_h = split_history(history)

    def run(settings: Settings, name: str) -> dict:
        return row(name, run_backtest(history, settings, cost_bps=cost_bps),
                   run_backtest(tune_h, settings, cost_bps=cost_bps),
                   run_backtest(unseen_h, settings, cost_bps=cost_bps))

    spy = history.get("SPY")
    out = {"old": run(replace(base, **OLD_RULES).clamped(), "Rules before Nov 6"),
           "current": run(base, "Current rules"), "changes": []}
    for knob, values in KNOBS.items():
        here = getattr(base, knob)
        for v in values:
            if v == here:
                continue
            r = run(replace(base, **{knob: type(here)(v)}).clamped(), f"{knob} {here:g} -> {v:g}")
            r["holds_up"] = holds_up(r, out["current"])
            out["changes"].append(r)
    if spy:
        start = spy[MIN_HISTORY].time if len(spy) > MIN_HISTORY else spy[0].time
        closes = [b.close for b in spy if b.time >= start]
        out["spy_return"] = round(closes[-1] / closes[0] - 1, 4) if closes else None
    return out


def liquid_enough(history: dict, settings: Settings) -> float:
    """Share of stocks whose recent dollar volume (in this price feed) passes the volume check."""
    ok = [s for s, bars in history.items()
          if (ind.avg_dollar_volume(bars, 20) or 0) >= settings.min_dollar_volume]
    return len(ok) / max(1, len(history))


def table(result: dict) -> str:
    rows = [result["old"], result["current"], *result["changes"]]
    head = (f"{'Rules':<34}{'Return':>8}{'/yr':>7}{'Drop':>7}{'Sharpe':>7}"
            f"{'Older':>7}{'Recent':>7}{'Trades':>7}{'Wins':>6}  Holds up")
    lines = [head, "-" * len(head)]
    for r in rows:
        wins = "n/a" if r["win_rate"] is None else f"{r['win_rate']:.0%}"
        held = {True: "yes", False: "no"}.get(r.get("holds_up"), "")
        lines.append(f"{r['name']:<34}{r['return']:>8.1%}{r['per_year']:>7.1%}{r['worst_drop']:>7.1%}"
                     f"{r['sharpe']:>7.2f}{r['tune_sharpe']:>7.2f}{r['unseen_sharpe']:>7.2f}"
                     f"{r['trades']:>7}{wins:>6}  {held}")
    if result.get("spy_return") is not None:
        lines.append(f"SPY over the same {YEARS} years: {result['spy_return']:+.1%}")
    lines.append("Older / Recent: Sharpe on the first two thirds and on the last third.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autotrader.compare")
    parser.add_argument("--wide", action="store_true", help="add today's ~500 most-traded stocks")
    parser.add_argument("--cost-bps", type=float, default=10.0)
    parser.add_argument("--json", help="save the results to this file")
    args = parser.parse_args(argv)
    logging.getLogger("autotrader").setLevel(logging.ERROR)  # every replayed day logs otherwise

    from .broker.alpaca_broker import AlpacaBroker
    from .universe import build_universe

    base = load_settings(mode=Mode.TEST)
    symbols = build_universe(base.always_watch, base.never_buy)
    if args.wide:
        from .signals.screener import MarketScan
        symbols += [s for s in MarketScan().symbols() if s not in symbols]
    symbols = sorted(set(symbols) | {"SPY"})
    started = time.time()
    history = AlpacaBroker(Mode.TEST).get_daily_bars(symbols, MIN_HISTORY + YEARS * 252)
    print(f"Prices for {len(history)} of {len(symbols)} stocks in {time.time() - started:.0f}s")
    print(f"Pass the ${base.min_dollar_volume / 1e6:.0f}M volume check in this price feed: "
          f"{liquid_enough(history, base):.0%}")
    # The whole list is scanned, as the live bot scans its list plus the market scan.
    base = replace(base, always_watch=tuple(s for s in history if s != "SPY")).clamped()
    result = compare(history, base, args.cost_bps)
    result["stocks"] = len(history)
    print(table(result))
    print(f"Done in {time.time() - started:.0f}s")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(result, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
