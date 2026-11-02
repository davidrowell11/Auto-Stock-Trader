"""Run the bot from the command line.

    python -m autotrader --mode test --once     # one cycle, then exit
    python -m autotrader --mode test            # keep running during market hours
    python -m autotrader --mode test --budget 5000

Live mode also requires AUTOTRADER_LIVE_ENABLED=true, so it can't be started by accident.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from .config import Mode, load_settings
from .engine import Engine
from .journal import Journal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autotrader")
    parser.add_argument("--mode", choices=[m.value for m in Mode], default="test")
    parser.add_argument("--budget", type=float, help="money the bot may use")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    mode = Mode(args.mode)
    if mode is Mode.LIVE and os.environ.get("AUTOTRADER_LIVE_ENABLED", "").lower() != "true":
        print("Live trading is off. Set AUTOTRADER_LIVE_ENABLED=true to turn it on.", file=sys.stderr)
        return 2

    overrides = {"mode": mode}
    if args.budget is not None:
        overrides["budget"] = args.budget
    settings = load_settings(**overrides)

    from .broker.alpaca_broker import AlpacaBroker

    engine = Engine(AlpacaBroker(mode), settings, Journal(settings.journal_path))
    log = logging.getLogger("autotrader")
    log.info("Starting in %s mode, budget %s", mode.value,
             "whole account" if settings.budget is None else f"${settings.budget:,.0f}")

    while True:
        if engine.broker.is_market_open():
            report = engine.run_cycle()
            log.info("Cycle done: %d buys, %d sells, %d awaiting approval",
                     len(report.buys), len(report.sells), len(report.queued))
        elif args.once:
            log.info("Market is closed.")
        if args.once:
            return 0
        time.sleep(settings.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
