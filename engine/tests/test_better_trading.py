"""The trading fixes from the Nov 5 review: close-only trend exits, no chasing, no crumbs,
risk-based sizing, a daily buy limit, trimming oversized holdings, and a wider scan."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from autotrader import strategy
from autotrader.backtest import run_backtest
from autotrader.broker.sim import SimBroker
from autotrader.config import Settings
from autotrader.engine import Engine
from autotrader.journal import Journal
from autotrader.learning import outcomes
from autotrader.models import Account, Bar, Position, Side
from autotrader.risk import RiskManager
from autotrader.signals.screener import MarketScan, rank_liquid

from helpers import make_bars, uptrend


def engine_with(bars, cash=100_000, now=None, **kw):
    broker = SimBroker(cash, bars)
    extra = {"now": now} if now else {}
    return Engine(broker, Settings(**kw).clamped(), Journal(), **extra), broker


class TrendExitTests(unittest.TestCase):
    def test_finished_days_leaves_out_today_until_the_close(self):
        bars = make_bars([10, 11, 12])
        day = bars[-1].time
        self.assertEqual(len(strategy.finished_days(bars, day.replace(hour=11))), 2)
        self.assertEqual(len(strategy.finished_days(bars, day.replace(hour=16, minute=5))), 3)
        self.assertEqual(len(strategy.finished_days(bars, day + timedelta(days=1))), 3)
        # Alpaca's bars are midnight New York time, in UTC.
        utc = [Bar(datetime(2026, 11, 5, 5, tzinfo=timezone.utc), 1, 1, 1, 1, 1)]
        self.assertEqual(strategy.finished_days(utc, datetime(2026, 11, 5, 15, tzinfo=timezone.utc)), [])

    def test_a_dip_during_the_day_does_not_sell_but_a_close_does(self):
        closes = uptrend(daily=0.002)
        dipped = make_bars([*closes[:-1], closes[-1] * 0.93])
        reason = strategy.exit_reason(dipped, stop_price=1.0, closed=dipped[:-1])
        self.assertIsNone(reason)
        reason = strategy.exit_reason(dipped, stop_price=1.0)
        self.assertIn("Trend broke: closed at", reason)

    def test_stops_still_act_on_the_live_price(self):
        bars = make_bars(uptrend())
        reason = strategy.exit_reason(bars, stop_price=bars[-1].close + 1, closed=bars[:-1])
        self.assertIn("Hit stop", reason)

    def test_engine_waits_for_the_close(self):
        closes = uptrend(daily=0.001)
        bars = make_bars(closes, spread=0.03)  # jumpy, so the stop sits well below the 50-day
        clock = [bars[-1].time.replace(hour=15, tzinfo=timezone.utc)]  # 11am New York
        eng, broker = engine_with({"AAPL": bars}, now=lambda: clock[0])
        eng.run_cycle()
        self.assertIn("AAPL", broker.holdings)
        broker.bars["AAPL"] = make_bars([*closes[:-1], closes[-1] * 0.96], spread=0.03)
        report = eng.run_cycle()
        self.assertEqual(report.sells, [])  # 11am: under the 50-day, but the day isn't over
        clock[0] = clock[0].replace(hour=21, minute=1)  # after the close
        report = eng.run_cycle()
        self.assertTrue(report.sells and "Trend broke" in report.sells[0])


class StretchTests(unittest.TestCase):
    def test_a_stock_that_just_shot_up_waits(self):
        closes = uptrend()
        for i in range(1, 6):
            closes[-i] *= 1.25
        bars = make_bars(closes)
        sig = strategy.score_trend("X", bars)
        self.assertIn("up too far too fast", strategy.stretched(bars, sig.atr))
        eng, broker = engine_with({"X": bars}, always_watch=("X",))
        report = eng.run_cycle()
        self.assertEqual(broker.orders, [])
        self.assertTrue(any("pull-back" in s for s in report.skipped))

    def test_a_steady_trend_is_fine(self):
        bars = make_bars(uptrend())
        self.assertIsNone(strategy.stretched(bars, strategy.score_trend("X", bars).atr))


class SizingTests(unittest.TestCase):
    def risk(self, cash=100_000, positions=(), **kw):
        return RiskManager(Settings(**kw).clamped(), Account(100_000, cash, 100_000),
                           list(positions), 100_000)

    def test_wide_stops_get_smaller_positions(self):
        r = self.risk()
        steady = r.size_buy("A", 100, "Tech", False, stop_price=96)  # 4% stop: full 5%
        jumpy = r.size_buy("B", 100, "Tech", False, stop_price=92)  # 8% stop: 0.3% / 8% = 3.75%
        self.assertEqual(steady.qty, 50)
        self.assertEqual(jumpy.qty, 37)
        self.assertIn("risk per trade", jumpy.note)

    def test_crumbs_are_skipped(self):
        # Only $1,000 of spare cash after the reserve, next to a normal $5,000 position.
        r = self.risk(cash=11_000)
        size = r.size_buy("A", 100, "Tech", False, fractional=True)
        self.assertEqual(size.qty, 0)
        self.assertIn("too small", size.note)
        r = self.risk(cash=13_000)  # $3,000 of room is 60% of normal: fine
        self.assertEqual(r.size_buy("A", 100, "Tech", False).qty, 30)


class PacingTests(unittest.TestCase):
    def five_trends(self):
        return {s: make_bars(uptrend(start=20 + 5 * i)) for i, s in enumerate("ABCDE")}

    def test_daily_limit(self):
        bars = self.five_trends()
        eng, broker = engine_with(bars, max_new_buys_per_day=2, always_watch=tuple(bars))
        report = eng.run_cycle()
        self.assertEqual(len(broker.orders), 2)
        self.assertTrue(any("already bought 2 new stocks today" in s for s in report.skipped))
        report = eng.run_cycle()
        self.assertEqual(len(broker.orders), 2)
        self.assertIn("daily limit", report.paced)
        self.assertIn("no more buys today", report.summary())

    def test_yesterdays_buys_dont_count(self):
        j = Journal()
        j.record_trade("test", "A", "buy", 1, 10, None, ["x"])
        later = datetime.now(timezone.utc) + timedelta(days=1)
        self.assertEqual(j.buys_since("test", later), 0)
        self.assertEqual(j.buys_since("test", later - timedelta(days=2)), 1)

    def test_backtests_pace_by_the_replayed_day(self):
        history = {s: make_bars(uptrend(n=300, start=20 + 5 * i)) for i, s in enumerate("ABCDEF")}
        j = Journal()
        result = run_backtest(history, Settings(max_new_buys_per_day=2, always_watch=tuple(history)).clamped(),
                              warmup=260, journal=j)
        self.assertEqual(len(result.equity_curve), 40)
        self.assertGreater(len({t["symbol"] for t in j.trades("test") if t["side"] == "buy"}), 2)

    def test_backtest_cool_offs_end(self):
        # A stock sold by its stop can be bought again a week later in the replay, not never.
        closes = uptrend(n=300)
        for i in range(265, 268):
            closes[i] = closes[264] * 0.85  # a sharp drop: stopped out
        for i in range(268, 300):
            closes[i] = closes[267] * (1.004 ** (i - 267)) * 1.25  # then a fresh run to new highs
        j = Journal()
        run_backtest({"A": make_bars(closes), "SPY": make_bars(uptrend(n=300, start=400))},
                     Settings(always_watch=("A",)).clamped(), warmup=260, journal=j)
        buys = [t for t in j.trades("test") if t["symbol"] == "A" and t["side"] == "buy"]
        self.assertGreaterEqual(len(buys), 2, j.trades("test"))


class TrimTests(unittest.TestCase):
    def test_trims_a_holding_that_grew_too_big(self):
        closes = uptrend()
        eng, broker = engine_with({"AAPL": make_bars(closes), "SPY": make_bars(uptrend(start=500))})
        eng.run_cycle()
        qty = broker.holdings["AAPL"][0]
        broker.bars["AAPL"] = make_bars([c * 2 for c in closes])  # it doubled
        report = eng.run_cycle()
        self.assertTrue(report.sells and "Trimmed" in report.sells[0], report.sells)
        sold = broker.orders[-1]
        self.assertIs(sold.side, Side.SELL)
        left = qty - sold.qty
        value, equity = left * broker.price("AAPL"), broker.get_account().equity
        self.assertAlmostEqual(value / equity, 0.05, delta=0.005)
        state = eng.journal.get_stop("test", "AAPL")
        self.assertAlmostEqual(state.qty, left)
        # Not a protective sale (no cool-off), and the trade isn't finished yet.
        self.assertIsNone(eng.journal.last_protective_sell("test", "AAPL"))
        self.assertEqual(outcomes(eng.journal, "test"), [])
        report = eng.run_cycle()  # back at the limit: nothing more to trim
        self.assertEqual(report.sells, [])

    def test_no_trim_inside_the_limit(self):
        eng, broker = engine_with({"AAPL": make_bars(uptrend())})
        eng.run_cycle()
        report = eng.run_cycle()
        self.assertEqual(report.sells, [])

    def test_trims_wait_for_regular_hours(self):
        closes = uptrend()
        eng, broker = engine_with({"AAPL": make_bars(closes), "SPY": make_bars(uptrend(start=500))})
        eng.run_cycle()
        broker.bars["AAPL"] = make_bars([c * 2 for c in closes])
        eng.session = "after"
        report = eng.run_cycle()
        self.assertFalse(any("Trimmed" in s for s in report.sells))
        eng.session = "regular"
        report = eng.run_cycle()
        self.assertTrue(any("Trimmed" in s for s in report.sells))

    def test_whole_shares_for_whole_share_holdings(self):
        eng, _ = engine_with({})
        risk = RiskManager(eng.s, Account(100_000, 0, 100_000),
                           [Position("LLY", 9, 900, 1358.6)], 100_000)
        eng.journal.save_stop("test", "LLY", strategy_state(900, 9))
        eng.broker.holdings["LLY"] = (9, 900)
        eng.broker.bars["LLY"] = make_bars([1358.6] * 5)
        report = SimpleNamespace(sells=[], skipped=[])
        eng._trim_oversized(risk, report)
        self.assertEqual(eng.broker.orders[-1].qty, 5)  # 12% -> about 5%: 5 of 9 shares


def strategy_state(entry, qty):
    from autotrader.journal import StopState
    return StopState(entry, entry * 0.9, entry, qty=qty)


class ScanTests(unittest.TestCase):
    def test_ranks_by_dollars_traded(self):
        listing = [("BIG", "Big Corp"), ("MID", "Mid Inc"), ("CHEAP", "Cheap Co"),
                   ("TQQQ", "ProShares UltraPro QQQ 3x Shares ETF"), ("ABC.WS", "Warrant")]
        traded = {"BIG": (100.0, 9e9), "MID": (50.0, 1e9), "CHEAP": (3.0, 5e9), "TQQQ": (60.0, 8e9)}
        seen = []

        def liquidity(chunk):
            seen.extend(chunk)
            return {s: traded[s] for s in chunk if s in traded}

        self.assertEqual(rank_liquid(listing, liquidity, top=5), ["BIG", "MID"])
        self.assertNotIn("TQQQ", seen)  # leveraged funds aren't even looked up
        self.assertEqual(rank_liquid(listing, liquidity, top=1), ["BIG"])

    def test_daily_refresh_and_outage(self):
        day = [1]
        calls = []

        def listing():
            calls.append(day[0])
            if day[0] == 2:
                raise OSError("down")
            return [("AAA", "A")]

        scan = MarketScan(listing, lambda c: {"AAA": (20.0, 1e8)}, today=lambda: day[0])
        self.assertEqual(scan.symbols(), ["AAA"])
        scan.symbols()
        self.assertEqual(calls, [1])
        day[0] = 2
        self.assertEqual(scan.symbols(), ["AAA"])  # yesterday's list

    def test_engine_scans_the_market_list(self):
        bars = {"ZZZ": make_bars(uptrend())}
        broker = SimBroker(100_000, bars)
        eng = Engine(broker, Settings().clamped(), Journal(), market_scan=lambda: ["ZZZ"])
        report = eng.run_cycle()
        self.assertEqual([o.symbol for o in broker.orders], ["ZZZ"])
        self.assertNotIn("ZZZ", eng.screened)  # not counted as a screener pick
        self.assertGreater(report.checked, 1)


class BarCacheTests(unittest.TestCase):
    def broker(self, responses):
        from autotrader.broker.alpaca_broker import AlpacaBroker
        import threading

        b = AlpacaBroker.__new__(AlpacaBroker)
        b.data_feed = "iex"
        b._bars, b._bars_lock, b._assets = {}, threading.Lock(), {}
        b.requests = []

        def fetch(symbols, start):
            b.requests.append((tuple(symbols), start))
            return responses.pop(0)

        b._fetch_bars = fetch
        return b

    def test_full_history_once_then_recent_days(self):
        t0 = datetime(2026, 11, 3, 5, tzinfo=timezone.utc)
        day = lambda i, c: Bar(t0 + timedelta(days=i), c, c, c, c, 1)  # noqa: E731
        b = self.broker([{"A": [day(0, 1), day(1, 2)]},
                         {"A": [day(1, 2.5), day(2, 3)]}])
        self.assertEqual([x.close for x in b.get_daily_bars(["A"], 10)["A"]], [1, 2])
        got = b.get_daily_bars(["A"], 10)["A"]
        self.assertEqual([x.close for x in got], [1, 2.5, 3])  # today's bar updated, new day added
        first, second = b.requests
        self.assertLess(first[1], second[1])  # the second asked for far less history

    def test_deeper_history_fetches_again_and_missing_stays_missing(self):
        t0 = datetime(2026, 11, 3, 5, tzinfo=timezone.utc)
        b = self.broker([{"A": [Bar(t0, 1, 1, 1, 1, 1)]}, {"A": [Bar(t0, 1, 1, 1, 1, 1)]}])
        self.assertEqual(set(b.get_daily_bars(["A", "GONE"], 10)), {"A"})
        b.get_daily_bars(["A", "GONE"], 300)
        self.assertEqual(b.requests[1][0], ("A", "GONE"))
        self.assertEqual(b.get_daily_bars(["GONE"], 10), {})
        self.assertEqual(len(b.requests), 2)  # no prices for GONE: not asked again today


if __name__ == "__main__":
    unittest.main()


class CompareTests(unittest.TestCase):
    def test_runs_every_rule_set_and_judges_changes(self):
        import random

        from autotrader.compare import compare, table
        rng = random.Random(3)
        history = {}
        for s in ["A", "B", "C", "SPY"]:
            p, out = 50.0, []
            for _ in range(500):
                p *= 1 + 0.001 + rng.gauss(0, 0.012)
                out.append(p)
            history[s] = make_bars(out)
        base = Settings(always_watch=("A", "B", "C")).clamped()
        result = compare(history, base)
        self.assertEqual(result["old"]["name"], "Rules before Nov 6")
        self.assertTrue(all("holds_up" in c for c in result["changes"]))
        from autotrader.compare import EXPERIMENTS
        self.assertEqual(len(result["changes"]), 3 + 3 + 2 + 2 + len(EXPERIMENTS))
        self.assertIn("Current rules", table(result))


class ExitLineAndListingTests(unittest.TestCase):
    def test_trend_exit_line_is_tunable(self):
        closes = uptrend(daily=0.002)
        bars = make_bars([*closes[:-1], closes[-1] * 0.93])  # under the 50-day, over the 100-day
        self.assertIn("50-day", strategy.exit_reason(bars, 1.0))
        self.assertIsNone(strategy.exit_reason(bars, 1.0, trend_days=100))
        self.assertEqual(Settings(trend_exit_days=500).clamped().trend_exit_days, 200)

    def test_listing_keeps_listed_tradable_assets_and_new_exchanges_dont_break_it(self):
        from autotrader.signals.screener import listed
        raw = [{"symbol": "AAPL", "name": "Apple", "exchange": "NASDAQ", "tradable": True},
               {"symbol": "NEWCO", "name": "New", "exchange": "TXSE", "tradable": True},
               {"symbol": "PINK", "name": "Pink", "exchange": "OTC", "tradable": True},
               {"symbol": "HALT", "name": "Halted", "exchange": "NYSE", "tradable": False}]
        self.assertEqual(listed(raw), [("AAPL", "Apple")])
