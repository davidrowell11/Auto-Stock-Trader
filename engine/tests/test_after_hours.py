"""Pre-market (4am-9:30am) and after-hours (4pm-8pm) trading."""

import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import date, datetime
from types import SimpleNamespace
from unittest import mock

from autotrader.broker import alpaca_broker
from autotrader.config import Mode, Settings
from autotrader.engine import Engine, limit_for
from autotrader.journal import Journal
from autotrader.market import MARKET
from autotrader.models import Asset, OrderRequest, Side

from helpers import make_bars, uptrend
from test_final_check import AUTH, TOKEN, SlowBroker, TestClient, crash


def engine(broker, journal, session, **settings):
    s = replace(Settings(), **settings).clamped()
    return Engine(broker, s, journal, session=session)


class LimitPriceTests(unittest.TestCase):
    def test_limits(self):
        self.assertIsNone(limit_for(Side.BUY, 100.0, False, False))  # regular hours: market order
        self.assertIsNone(limit_for(Side.SELL, 100.0, False, False))
        self.assertEqual(limit_for(Side.BUY, 100.0, True, False), 100.5)
        self.assertEqual(limit_for(Side.SELL, 100.0, True, False), 98.0)
        self.assertEqual(limit_for(Side.SELL, 0.5, False, True), 0.49)  # penny sells: always a limit


class AfterHoursEngineTests(unittest.TestCase):
    def setUp(self):
        self.broker = SlowBroker(100_000, {"AAPL": make_bars(uptrend())},
                                 assets={"AAPL": Asset("AAPL", exchange="NASDAQ", fractionable=True)})
        self.journal = Journal()

    def own_aapl(self):
        engine(self.broker, self.journal, "regular").run_cycle()
        self.broker.fill(self.broker.last_id())

    def test_safety_stop_sells_after_hours_with_a_limit_order(self):
        self.own_aapl()
        crash(self.broker)
        report = engine(self.broker, self.journal, "after").run_cycle()
        sell = self.broker.orders[-1]
        self.assertIs(sell.side, Side.SELL)
        self.assertTrue(sell.extended_hours)
        price = self.broker.price("AAPL")
        self.assertAlmostEqual(sell.limit_price, round(price * 0.98, 2))
        self.assertTrue(report.sells)

    def test_never_buys_more_of_a_stock_already_in_the_account(self):
        self.broker.holdings["AAPL"] = (5, 100.0)  # in the account, but not the bot's
        report = engine(self.broker, self.journal, "regular").run_cycle()
        self.assertEqual(self.broker.orders, [])
        self.assertTrue(any("not managed by the bot" in s for s in report.skipped))

    def test_leveraged_funds_are_never_bought(self):
        self.broker.assets["AAPL"] = Asset("AAPL", exchange="NASDAQ",
                                           name="Direxion Daily AAPL Bull 2X Shares")
        engine(self.broker, self.journal, "regular").run_cycle()
        self.assertEqual(self.broker.orders, [])

    def test_buys_wait_for_regular_hours_when_switched_off(self):
        report = engine(self.broker, self.journal, "pre", after_hours_buys=False).run_cycle()
        self.assertEqual(self.broker.orders, [])
        self.assertIn("regular hours", report.halted)

    def test_after_hours_buy_is_a_whole_share_limit_order(self):
        engine(self.broker, self.journal, "after", after_hours_buys=True).run_cycle()
        buy = self.broker.orders[-1]
        self.assertIs(buy.side, Side.BUY)
        self.assertTrue(buy.extended_hours)
        self.assertEqual(buy.qty, int(buy.qty))  # no fractional shares outside regular hours
        self.assertAlmostEqual(buy.limit_price, round(self.broker.price("AAPL") * 1.005, 2))

    def test_overnight_buys_only_stocks_traded_overnight(self):
        report = engine(self.broker, self.journal, "overnight", overnight_buys=True).run_cycle()
        self.assertEqual([o for o in self.broker.orders if o.side is Side.BUY], [])
        self.assertTrue(any("not traded overnight" in x for x in report.skipped))
        self.broker.assets["AAPL"] = Asset("AAPL", exchange="NASDAQ", overnight_tradable=True)
        engine(self.broker, self.journal, "overnight", overnight_buys=True).run_cycle()
        buy = self.broker.orders[-1]
        self.assertIs(buy.side, Side.BUY)
        self.assertTrue(buy.extended_hours)
        self.assertIsNotNone(buy.limit_price)
        self.assertIn("overnight", self.journal.trades("test")[-1]["sources"])

    def test_one_new_buy_a_night(self):
        bars = {s: make_bars(uptrend()) for s in ("AAPL", "MSFT")}
        assets = {s: Asset(s, exchange="NASDAQ", overnight_tradable=True) for s in bars}
        broker = SlowBroker(100_000, bars, assets=assets)
        e = engine(broker, self.journal, "overnight", overnight_buys=True, max_new_buys_per_day=3)
        e.run_cycle()
        e.run_cycle()
        self.assertEqual(len([o for o in broker.orders if o.side is Side.BUY]), 1)

    def test_overnight_buying_off_waits_for_4am(self):
        report = engine(self.broker, self.journal, "overnight", overnight_buys=False).run_cycle()
        self.assertEqual(self.broker.orders, [])
        self.assertIn("4am", report.halted)

    def test_overnight_sell_waits_when_the_stock_isnt_traded_overnight(self):
        self.own_aapl()
        crash(self.broker)
        before = len(self.broker.orders)
        report = engine(self.broker, self.journal, "overnight").run_cycle()
        self.assertEqual(len(self.broker.orders), before)
        self.assertTrue(any("waits for 4am" in x for x in report.skipped))

    def test_no_penny_stock_buys_after_hours(self):
        closes = [c / 60 for c in uptrend()]  # same trend, priced under $5
        broker = SlowBroker(100_000, {"PENY": make_bars(closes), MARKET: make_bars(uptrend())})
        s = replace(Settings(), after_hours_buys=True, always_watch=("PENY",)).clamped()
        report = Engine(broker, s, self.journal, session="after", penny_needs_analyst=False).run_cycle()
        self.assertFalse([o for o in broker.orders if o.symbol == "PENY"])
        self.assertIn("PENY: penny stocks are only bought in regular hours", report.skipped)

    def test_unfilled_after_hours_buy_is_repriced_not_left_hanging(self):
        engine(self.broker, self.journal, "after", after_hours_buys=True).run_cycle()
        first = self.broker.last_id()
        engine(self.broker, self.journal, "after", after_hours_buys=True).run_cycle()
        self.assertIn(first, self.broker.cancelled)
        self.assertEqual(len(self.broker.open), 1)  # one fresh order, not two

    def test_premarket_sell_left_unfilled_is_replaced_at_the_open(self):
        self.own_aapl()
        crash(self.broker)
        engine(self.broker, self.journal, "pre").run_cycle()
        premarket = self.broker.last_id()
        engine(self.broker, self.journal, "regular").run_cycle()
        self.assertIn(premarket, self.broker.cancelled)
        sell = self.broker.orders[-1]
        self.assertIs(sell.side, Side.SELL)
        self.assertFalse(sell.extended_hours)
        self.assertIsNone(sell.limit_price)  # a market order now that the market is open


class FakeTrading:
    def __init__(self, is_open, days):
        self.is_open, self.days, self.calendar_calls = is_open, days, 0

    def get_clock(self):
        return SimpleNamespace(is_open=self.is_open)

    def get_calendar(self, filters):
        self.calendar_calls += 1
        return self.days


class AlpacaSessionTests(unittest.TestCase):
    DAY = date(2026, 11, 3)

    def broker(self, is_open, days):
        b = alpaca_broker.AlpacaBroker.__new__(alpaca_broker.AlpacaBroker)
        b.trading = FakeTrading(is_open, days)
        b._calendar = {}
        return b

    def at(self, b, hour, minute=0):
        now = datetime(2026, 11, 3, hour, minute, tzinfo=alpaca_broker.NEW_YORK)
        with mock.patch.object(alpaca_broker, "datetime", wraps=datetime) as dt:
            dt.now.return_value = now
            dt.combine = datetime.combine
            return b.market_session()

    def test_sessions(self):
        day = SimpleNamespace(date=self.DAY, open=datetime(2026, 11, 3, 9, 30),
                              close=datetime(2026, 11, 3, 16, 0))
        b = self.broker(False, [day])
        self.assertEqual(self.at(b, 3, 59), "overnight")  # the overnight session before Nov 3
        self.assertEqual(self.at(b, 4), "pre")
        self.assertEqual(self.at(b, 9, 29), "pre")
        self.assertEqual(self.at(b, 16), "after")
        self.assertEqual(self.at(b, 19, 59), "after")
        self.assertEqual(self.at(b, 20), "closed")  # Nov 4 isn't a trading day in this calendar
        self.assertEqual(b.trading.calendar_calls, 2)  # each day looked up once
        b.trading.is_open = True
        self.assertEqual(self.at(b, 11), "regular")

    def test_overnight_runs_before_trading_days_only(self):
        tomorrow = SimpleNamespace(date=date(2026, 11, 4), open=datetime(2026, 11, 4, 9, 30),
                                   close=datetime(2026, 11, 4, 16, 0))
        self.assertEqual(self.at(self.broker(False, [tomorrow]), 21), "overnight")
        self.assertEqual(self.at(self.broker(False, []), 21), "closed")  # holiday tomorrow
        self.assertEqual(self.at(self.broker(False, []), 2), "closed")  # holiday today

    def test_the_newer_trade_wins_overnight(self):
        from autotrader.broker.alpaca_broker import AlpacaBroker
        b = AlpacaBroker.__new__(AlpacaBroker)
        b.data_feed, b._no_overnight_feed = "iex", None
        friday_night = datetime(2026, 11, 6, 19, 59, tzinfo=alpaca_broker.NEW_YORK)
        friday_morning = datetime(2026, 11, 6, 3, 59, tzinfo=alpaca_broker.NEW_YORK)
        feeds = {"iex": {"AAPL": SimpleNamespace(price=110.0, timestamp=friday_night),
                         "XYZ": SimpleNamespace(price=50.0, timestamp=friday_night)},
                 "overnight": {"AAPL": SimpleNamespace(price=100.0, timestamp=friday_morning),
                               "MSFT": SimpleNamespace(price=400.0, timestamp=friday_morning)}}
        b.data = SimpleNamespace(get_stock_latest_trade=lambda req: feeds[str(req.feed.value
                                 if hasattr(req.feed, "value") else req.feed)])
        sunday = datetime(2026, 11, 8, 20, 5, tzinfo=alpaca_broker.NEW_YORK)
        with mock.patch.object(alpaca_broker, "datetime", wraps=datetime) as dt:
            dt.now.return_value = sunday
            got = b.latest_prices(["AAPL", "MSFT", "XYZ"])
        self.assertEqual(got, {"AAPL": 110.0, "MSFT": 400.0, "XYZ": 50.0})

    def test_overnight_tradable_attribute(self):
        ok = SimpleNamespace(attributes=["fractional_eh_enabled", "overnight_tradable"])
        halted = SimpleNamespace(attributes=["overnight_tradable", "overnight_halted"])
        self.assertTrue(alpaca_broker._overnight_tradable(ok))
        self.assertFalse(alpaca_broker._overnight_tradable(halted))
        self.assertFalse(alpaca_broker._overnight_tradable(SimpleNamespace(attributes=None)))

    def test_early_close_and_holidays(self):
        early = SimpleNamespace(date=self.DAY, open=datetime(2026, 11, 3, 9, 30),
                                close=datetime(2026, 11, 3, 13, 0))
        self.assertEqual(self.at(self.broker(False, [early]), 14), "after")
        self.assertEqual(self.at(self.broker(False, []), 10), "closed")  # holiday


@unittest.skipIf(TestClient is None, "fastapi not installed")
class AfterHoursServerTests(unittest.TestCase):
    def setUp(self):
        from autotrader.server import BotRunner, SettingsStore, create_app

        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Journal()
        self.store = SettingsStore(f"{self.tmp.name}/s.json")
        self.brokers = {m: SlowBroker(100_000, {"AAPL": make_bars(uptrend())}, session="after")
                        for m in Mode}
        self.runners = {m: BotRunner(m, self.store, self.journal, threading.RLock(),
                                     lambda mode: self.brokers[mode]) for m in Mode}
        self.c = TestClient(create_app(self.runners, self.journal, TOKEN, live_enabled=True))

    def tearDown(self):
        self.tmp.cleanup()

    def test_after_hours_buying_on_for_test_off_for_live(self):
        self.assertTrue(self.c.get("/api/test/settings", headers=AUTH).json()["after_hours_buys"])
        self.assertFalse(self.c.get("/api/live/settings", headers=AUTH).json()["after_hours_buys"])
        r = self.c.put("/api/live/settings", headers=AUTH, json={"after_hours_buys": True})
        self.assertTrue(r.json()["after_hours_buys"])

    def test_summary_names_the_session(self):
        s = self.c.get("/api/test/summary", headers=AUTH).json()
        self.assertEqual(s["session"], "after")
        self.assertFalse(s["market_open"])

    def test_summary_counts_stocks_the_bot_did_not_buy(self):
        self.brokers[Mode.TEST].holdings["AAPL"] = (10, 100.0)  # bought by hand, not the bot
        s = self.c.get("/api/test/summary", headers=AUTH).json()
        self.assertEqual(s["invested"], 0)
        self.assertGreater(s["other_holdings"], 0)
        self.assertAlmostEqual(s["cash"] + s["invested"] + s["other_holdings"], s["equity"], places=2)

    def test_handing_untracked_stocks_to_the_bot(self):
        broker = self.brokers[Mode.TEST]
        broker.holdings["AAPL"] = (10, broker.price("AAPL"))  # the bot lost its records of this one
        res = self.c.post("/api/test/adopt", headers=AUTH).json()
        self.assertEqual([a["symbol"] for a in res["adopted"]], ["AAPL"])
        stop = self.journal.get_stop("test", "AAPL")
        self.assertEqual(stop.qty, 10)
        self.assertLess(stop.stop_price, broker.price("AAPL"))
        s = self.c.get("/api/test/summary", headers=AUTH).json()
        self.assertEqual(s["positions"], 1)
        self.assertEqual(s["other_holdings"], 0)
        self.assertEqual(self.c.post("/api/test/adopt", headers=AUTH).json(), {"adopted": []})

    def test_a_fallen_stock_is_not_sold_the_moment_its_handed_over(self):
        broker = self.brokers[Mode.TEST]
        broker.holdings["AAPL"] = (10, broker.price("AAPL") * 1.5)  # bought higher, now down a third
        self.c.post("/api/test/adopt", headers=AUTH)
        self.assertLess(self.journal.get_stop("test", "AAPL").stop_price, broker.price("AAPL"))

    def test_a_handover_during_a_check_waits_for_it(self):
        r = self.runners[Mode.TEST]
        self.brokers[Mode.TEST].holdings["AAPL"] = (10, self.brokers[Mode.TEST].price("AAPL"))
        held = threading.Event()
        done = threading.Event()

        def busy():  # a long check holding the lock
            with r.lock:
                held.set()
                done.wait(5)

        t = threading.Thread(target=busy)
        t.start()
        held.wait(5)
        r.ADOPT_WAIT_SECONDS = 0.1
        res = self.c.post("/api/test/adopt", headers=AUTH).json()
        done.set()
        t.join()
        self.assertEqual(res, {"adopted": [], "queued": True})
        self.assertIsNone(self.journal.get_stop("test", "AAPL"))
        r.run_once("regular")  # the next check hands them over first
        self.assertFalse(r.adopt_pending)
        self.assertEqual(self.journal.get_stop("test", "AAPL").qty, 10)

    def test_never_buy_stocks_are_not_handed_over(self):
        self.store.update(Mode.TEST, {"never_buy": ["AAPL"]})
        self.brokers[Mode.TEST].holdings["AAPL"] = (10, 100.0)
        self.assertEqual(self.c.post("/api/test/adopt", headers=AUTH).json(), {"adopted": []})

    def test_handing_over_extra_shares_keeps_the_stop(self):
        from autotrader.journal import StopState

        self.brokers[Mode.TEST].holdings["AAPL"] = (20, 100.0)
        self.journal.save_stop("test", "AAPL", StopState(100.0, 90.0, 100.0, qty=10))
        self.c.post("/api/test/adopt", headers=AUTH)
        stop = self.journal.get_stop("test", "AAPL")
        self.assertEqual((stop.qty, stop.stop_price), (20, 90.0))

    def test_test_buys_from_55_live_from_60(self):
        self.assertEqual(self.store.settings_for(Mode.TEST).analyst_min_confidence, 0.55)
        self.assertEqual(self.store.settings_for(Mode.LIVE).analyst_min_confidence, 0.60)
        # A server setting can lower Test, but never Live below its hard floor.
        with mock.patch.dict("os.environ", {"AUTOTRADER_ANALYST_MIN_CONFIDENCE": "0.5"}):
            self.assertEqual(self.store.settings_for(Mode.TEST).analyst_min_confidence, 0.55)
            self.assertEqual(self.store.settings_for(Mode.LIVE).analyst_min_confidence, 0.60)

    def test_closed_market_message(self):
        r = self.runners[Mode.TEST]
        self.brokers[Mode.TEST].session = "closed"
        self.c.post("/api/test/start", headers=AUTH)
        r._pause = lambda seconds: r._stop.set()  # one pass of the loop
        r.loop()
        self.assertIn("Sunday 8pm to Friday 8pm", self.c.get("/api/test/live", headers=AUTH).json()["now"])

    def test_overnight_buying_is_test_only(self):
        for m in Mode:
            self.brokers[m].session = "overnight"
        self.assertEqual(self.runners[Mode.TEST].session(), "overnight")
        self.assertEqual(self.runners[Mode.LIVE].session(), "closed")  # Live sits overnight out
        self.assertTrue(self.store.settings_for(Mode.TEST).overnight_buys)
        self.assertFalse(self.store.settings_for(Mode.LIVE).overnight_buys)
        r = self.c.put("/api/live/settings", headers=AUTH, json={"overnight_buys": True})
        self.assertEqual(r.status_code, 400)
        self.store.update(Mode.LIVE, {"overnight_buys": True})  # even if the file says so
        self.assertFalse(self.store.settings_for(Mode.LIVE).overnight_buys)
        r = self.c.put("/api/test/settings", headers=AUTH, json={"overnight_buys": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(self.store.settings_for(Mode.TEST).overnight_buys)

    def test_after_hours_check_runs_and_says_so(self):
        r = self.runners[Mode.TEST]
        self.c.post("/api/test/start", headers=AUTH)
        r._pause = lambda seconds: r._stop.set()
        r.loop()
        texts = [s["text"] for s in self.c.get("/api/test/live", headers=AUTH).json()["steps"]]
        self.assertTrue(any("after hours" in t for t in texts))
        self.assertTrue(self.brokers[Mode.TEST].orders[-1].extended_hours)

    def test_kill_switch_after_hours_uses_limit_orders(self):
        r = self.runners[Mode.TEST]
        r.engine("regular").run_cycle()
        self.brokers[Mode.TEST].fill(self.brokers[Mode.TEST].last_id())
        self.c.post("/api/test/kill", headers=AUTH, json={"sell_all": True})
        sell = self.brokers[Mode.TEST].orders[-1]
        self.assertIs(sell.side, Side.SELL)
        self.assertTrue(sell.extended_hours)
        self.assertIsNotNone(sell.limit_price)


@mock.patch.object(alpaca_broker, "LOOKUP_WAITS", (0, 0))
class LostReplyTests(unittest.TestCase):
    def test_rejected_order_counts_as_failed(self):
        class Trading:
            def submit_order(self, req):
                raise TimeoutError("read timed out")

            def get_order_by_client_id(self, client_id):
                return SimpleNamespace(id="x", status="OrderStatus.REJECTED")

        b = alpaca_broker.AlpacaBroker.__new__(alpaca_broker.AlpacaBroker)
        b.trading = Trading()
        with self.assertRaises(TimeoutError):
            b.submit_order(OrderRequest("AAPL", Side.BUY, 1, None, "test"))

    def test_order_that_shows_up_late_is_found(self):
        looks = []

        class Trading:
            def submit_order(self, req):
                raise TimeoutError("read timed out")

            def get_order_by_client_id(self, client_id):
                looks.append(1)
                if len(looks) == 1:
                    raise LookupError("not there yet")
                return SimpleNamespace(id="late1", status="new")

        b = alpaca_broker.AlpacaBroker.__new__(alpaca_broker.AlpacaBroker)
        b.trading = Trading()
        self.assertEqual(b.submit_order(OrderRequest("AAPL", Side.BUY, 1, None, "test")), "late1")

    def test_order_whose_reply_was_lost_is_found_by_its_id(self):
        placed = {}

        class Trading:
            def submit_order(self, req):
                placed[req.client_order_id] = SimpleNamespace(id="abc123", status="accepted")
                raise TimeoutError("read timed out")  # Alpaca took it; the reply never came

            def get_order_by_client_id(self, client_id):
                return placed[client_id]

        b = alpaca_broker.AlpacaBroker.__new__(alpaca_broker.AlpacaBroker)
        b.trading = Trading()
        order_id = b.submit_order(OrderRequest("AAPL", Side.BUY, 1, None, "test"))
        self.assertEqual(order_id, "abc123")

    def test_order_that_never_arrived_still_fails(self):
        class Trading:
            def submit_order(self, req):
                raise ConnectionError("no network")

            def get_order_by_client_id(self, client_id):
                raise LookupError("not found")

        b = alpaca_broker.AlpacaBroker.__new__(alpaca_broker.AlpacaBroker)
        b.trading = Trading()
        with self.assertRaises(ConnectionError):
            b.submit_order(OrderRequest("AAPL", Side.BUY, 1, 150.0, "test"))


if __name__ == "__main__":
    unittest.main()
