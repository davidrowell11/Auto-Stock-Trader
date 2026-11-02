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
    from .signals import QuiverClient, SecClient, SignalBook

    log = logging.getLogger("autotrader")
    broker = AlpacaBroker(mode)
    if not os.environ.get("AUTOTRADER_SEC_CONTACT"):
        log.warning("Set AUTOTRADER_SEC_CONTACT to your email; the SEC blocks anonymous requests.")
    sec = SecClient()
    quiver = QuiverClient()
    if not quiver.enabled:
        log.info("QUIVER_API_TOKEN not set: Congress trade signals are off.")
    signals = SignalBook(sec=sec, quiver=quiver, bars_fetch=broker.get_daily_bars)

    analyst = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        from .analyst import Analyst

        analyst = Analyst()
    else:
        log.info("ANTHROPIC_API_KEY not set: the AI analyst is off, so penny stocks won't be bought.")

    engine = Engine(broker, settings, Journal(settings.journal_path),
                    filings_check=sec.penny_filings_check, signals=signals, analyst=analyst)
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
