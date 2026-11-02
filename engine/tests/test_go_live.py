"""Step 4: going live small (readiness gate, starter cap, safety stops, mood, earnings)."""

import tempfile
import threading
import unittest
from datetime import date, timedelta

from autotrader.broker.sim import SimBroker
from autotrader.config import Mode, Settings
from autotrader.engine import Engine
from autotrader.journal import Journal
from autotrader.market import market_mood
from autotrader.models import Account, Asset
from autotrader.readiness import live_checks, test_results
from autotrader.risk import RiskManager
from autotrader.signals.earnings import EarningsCalendar

from helpers import downtrend, make_bars, uptrend

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None

TOKEN = "t"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
FRACTIONAL = {"AAPL": Asset("AAPL", exchange="NASDAQ", fractionable=True)}


def days(n, start=date(2026, 1, 5)):
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


class MoodTests(unittest.TestCase):
    def test_moods(self):
        self.assertEqual(market_mood(make_bars(uptrend())).level, "calm")
        self.assertEqual(market_mood(make_bars(downtrend())).level, "risk-off")
        # A dip below the 200-day average while the 50-day is still above it.
        dip = uptrend()
        dip[-1] = sum(dip[-200:]) / 200 * 0.97
        self.assertEqual(market_mood(make_bars(dip)).level, "cautious")
        self.assertEqual(market_mood(None).level, "unknown")

    def test_risk_off_market_blocks_new_buys(self):
        broker = SimBroker(100_000, {"AAPL": make_bars(uptrend()), "SPY": make_bars(downtrend())})
        report = Engine(broker, Settings().clamped(), Journal()).run_cycle()
        self.assertEqual(broker.orders, [])
        self.assertIn("downtrend", report.halted)


class EarningsTests(unittest.TestCase):
    def test_blocks_stock_reporting_soon(self):
        class Fake:
            def json(self, url, headers):
                return {"earningsCalendar": [{"symbol": "AAPL", "date": "2026-11-05"}]}

        cal = EarningsCalendar(token="x", fetch=Fake(), today=date(2026, 11, 2))
        self.assertIn("Nov 05", cal.reports_soon("AAPL"))
        self.assertIsNone(cal.reports_soon("MSFT"))
        broker = SimBroker(100_000, {"AAPL": make_bars(uptrend()), "SPY": make_bars(uptrend())})
        eng = Engine(broker, Settings(never_buy=("SPY",)).clamped(), Journal(), earnings=cal.reports_soon)
        report = eng.run_cycle()
        self.assertEqual(broker.orders, [])
        self.assertTrue(any("earnings" in s for s in report.skipped))

    def test_no_key_or_outage_never_blocks(self):
        self.assertIsNone(EarningsCalendar(token="").reports_soon("AAPL"))

        class Down:
            def json(self, url, headers):
                raise OSError("down")

        self.assertIsNone(EarningsCalendar(token="x", fetch=Down()).reports_soon("AAPL"))


class SizingTests(unittest.TestCase):
    def test_fractional_shares_for_small_budgets(self):
        rm = RiskManager(Settings(budget=1000).clamped(), Account(100_000, 100_000, 100_000), [], 0)
        whole = rm.size_buy("NVDA", 180.0, "Technology", False)
        frac = rm.size_buy("NVDA", 180.0, "Technology", False, fractional=True)
        self.assertEqual(whole.qty, 0)  # $50 can't buy one $180 share
        self.assertAlmostEqual(frac.qty, 0.277, places=3)
        self.assertEqual(rm.size_buy("PNY", 2.0, "X", True, fractional=True).qty, 10)  # pennies: whole shares
        tiny = RiskManager(Settings(budget=21).clamped(), Account(100_000, 100_000, 100_000), [], 0)
        self.assertEqual(tiny.size_buy("BIG", 600.0, "X", False, fractional=True).qty, 0)  # under $1

    def test_paused_buying_still_protects_holdings(self):
        broker = SimBroker(100_000, {"AAPL": make_bars(uptrend())})
        j = Journal()
        Engine(broker, Settings().clamped(), j).run_cycle()
        crashed = uptrend()
        crashed[-1] *= 0.8
        broker.bars["AAPL"] = make_bars(crashed)
        broker.bars["MSFT"] = make_bars(uptrend())
        report = Engine(broker, Settings().clamped(), j, pause_buying="safety stop").run_cycle()
        self.assertTrue(report.sells)
        self.assertNotIn("MSFT", [o.symbol for o in broker.orders])


class ReadinessTests(unittest.TestCase):
    def test_results_math(self):
        eq = list(zip(days(60), [10_000 + 20 * i for i in range(60)]))
        eq[30] = (eq[30][0], 9_000)  # a dip
        spy = list(zip(days(60), [100 + 0.05 * i for i in range(60)]))
        r = test_results(eq, 10_000, spy)
        self.assertGreater(r.weeks, 8)
        self.assertAlmostEqual(r.bot_return, 0.118)
        self.assertAlmostEqual(r.spy_return, 0.0295)
        self.assertAlmostEqual(r.max_drawdown, 0.158)

    def test_checks(self):
        good = test_results(list(zip(days(60), [10_000 + 10 * i for i in range(60)])), None,
                            list(zip(days(60), [100.0] * 60)))
        ok = live_checks(good, live_enabled=True, live_connected=True, live_cap=500, starter_active=True)
        self.assertTrue(ok["ready"])
        big = live_checks(good, live_enabled=True, live_connected=True, live_cap=5000, starter_active=True)
        self.assertFalse(big["ready"])
        short = test_results(list(zip(days(10), [10_000.0] * 10)), None, [])
        r = live_checks(short, live_enabled=True, live_connected=True, live_cap=500, starter_active=True)
        self.assertFalse(r["ready"])
        self.assertTrue(r["ready_except_results"])


@unittest.skipIf(TestClient is None, "fastapi not installed")
class ServerGoLiveTests(unittest.TestCase):
    def setUp(self):
        from autotrader.server import BotRunner, SettingsStore

        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Journal()
        self.store = SettingsStore(f"{self.tmp.name}/s.json")
        self.brokers = {m: SimBroker(100_000, {"AAPL": make_bars(uptrend())}, assets=FRACTIONAL) for m in Mode}
        self.runners = {m: BotRunner(m, self.store, self.journal, threading.RLock(),
                                     lambda mode: self.brokers[mode]) for m in Mode}

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, **kw):
        from autotrader.server import create_app

        return TestClient(create_app(self.runners, self.journal, TOKEN, live_enabled=True, **kw))

    def test_live_start_needs_test_results(self):
        c = self.client()
        c.put("/api/live/settings", headers=AUTH, json={"budget": 500})
        r = c.post("/api/live/start", headers=AUTH)
        self.assertEqual(r.status_code, 403)
        self.assertIn("8+ weeks", r.json()["detail"])
        self.assertFalse(c.get("/api/readiness", headers=AUTH).json()["ready"])

    def test_owner_can_skip_results_but_not_safety_checks(self):
        live = self.runners[Mode.LIVE]
        broker, live.broker = live.broker, None  # live keys missing
        c = self.client(skip_results=True)
        r = c.post("/api/live/start", headers=AUTH)
        self.assertEqual(r.status_code, 403)
        self.assertIn("connected", r.json()["detail"])
        self.assertIsNone(self.store.get(Mode.LIVE).get("first_started"))  # clock not started
        live.broker = broker
        self.assertEqual(c.post("/api/live/start", headers=AUTH).status_code, 200)
        self.assertIsNotNone(self.store.get(Mode.LIVE)["first_started"])

    def test_starter_cap(self):
        c = self.client()
        self.assertEqual(c.put("/api/live/settings", headers=AUTH, json={"budget": 5000}).status_code, 400)
        self.assertEqual(c.put("/api/live/settings", headers=AUTH, json={"clear_budget": True}).status_code, 400)
        self.store.update(Mode.LIVE, {"budget": 9999})  # even a hand-edited file is capped
        self.assertEqual(self.runners[Mode.LIVE].settings.budget, 1000)

    def test_big_live_loss_pauses_buying(self):
        r = self.runners[Mode.LIVE]
        self.store.update(Mode.LIVE, {"budget": 1000})
        r.check_live_safety(100_000)
        r.check_live_safety(99_850)  # lost $150 of a $1,000 budget
        self.assertIn("lost", self.store.get(Mode.LIVE)["buying_paused"])
        self.assertTrue(any(e["kind"] == "kill" for e in self.journal.events("live")))
        # A paused Live can't buy through an old approval either.
        aid = self.journal.queue_approval("live", "AAPL", "buy", 1, None, ["x"])
        c = self.client(skip_results=True)
        self.assertEqual(c.post(f"/api/live/approvals/{aid}", headers=AUTH,
                                json={"decision": "approve"}).status_code, 409)
        # Start clears it, and the peak starts fresh, so it doesn't trip again at once.
        c.post("/api/live/start", headers=AUTH)
        self.assertIsNone(self.store.get(Mode.LIVE)["buying_paused"])
        r.check_live_safety(99_850)
        self.assertIsNone(self.store.get(Mode.LIVE)["buying_paused"])

    def test_repeated_failures_pause_live(self):
        r = self.runners[Mode.LIVE]
        r.store.update(Mode.LIVE, {"running": True})

        class Boom(SimBroker):
            def get_account(self):
                raise RuntimeError("broker down")

        r.broker = Boom(1, {})
        r.settings  # noqa: B018
        calls = {"n": 0}

        def wait(_):
            calls["n"] += 1
            if calls["n"] >= 3:
                r._stop.set()
            return False

        r._stop.wait = wait
        r.loop()
        self.assertIn("failed in a row", self.store.get(Mode.LIVE)["buying_paused"])


if __name__ == "__main__":
    unittest.main()
