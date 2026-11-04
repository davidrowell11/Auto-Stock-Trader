"""Tests for the safety fixes found in the post-merge code review."""

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

from autotrader.broker.sim import SimBroker
from autotrader.config import Mode, Settings, load_settings
from autotrader.engine import Engine
from autotrader.journal import Journal
from autotrader.models import Asset, OrderUpdate

from helpers import make_bars, uptrend

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None

FRACTIONAL = {"AAPL": Asset("AAPL", exchange="NASDAQ", fractionable=True)}
TOKEN = "t"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FlakySellBroker(SimBroker):
    fail_sells = False

    def submit_order(self, order):
        if self.fail_sells and order.side.value == "sell":
            raise RuntimeError("broker down")
        return super().submit_order(order)


class PendingOrderBroker(SimBroker):
    """Accepts orders but never fills them, like an unfilled limit order."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.pending: set[str] = set()

    def submit_order(self, order):
        self.orders.append(order)
        self.pending.add(order.symbol)
        return f"o{len(self.orders)}"

    def open_order_symbols(self):
        return set(self.pending)

    def get_order(self, order_id):
        return OrderUpdate(done=False, filled_qty=0.0)


class EngineFixTests(unittest.TestCase):
    def test_hard_limits_cannot_be_loosened_by_env(self):
        s = load_settings({"AUTOTRADER_PENNY_PRICE": "0", "AUTOTRADER_MIN_PRICE": "0.01",
                           "AUTOTRADER_ANALYST_MIN_CONFIDENCE": "0",
                           "AUTOTRADER_PENNY_MIN_SCORE": "0"})
        self.assertEqual(s.penny_price, 5.0)
        self.assertEqual(s.min_price, 1.0)
        self.assertEqual(s.analyst_min_confidence, 0.55)  # the floor; Live itself uses 0.60
        self.assertEqual(s.penny_min_score, 0.75)

    def test_failed_sell_still_counts_as_held(self):
        broker = FlakySellBroker(100_000, {"AAPL": make_bars(uptrend())})
        eng = Engine(broker, Settings().clamped(), Journal())
        eng.run_cycle()
        crashed = uptrend()
        crashed[-1] *= 0.80
        broker.bars["AAPL"] = make_bars(crashed)
        broker.fail_sells = True
        before = len(broker.orders)
        report = eng.run_cycle()
        self.assertTrue(any("sell failed" in x for x in report.skipped))
        self.assertEqual(len(broker.orders), before)  # no re-buy of a stock it still holds

    def test_unfilled_order_not_placed_again(self):
        broker = PendingOrderBroker(100_000, {"AAPL": make_bars(uptrend())})
        eng = Engine(broker, Settings().clamped(), Journal())
        eng.run_cycle()
        eng.run_cycle()
        self.assertEqual(len(broker.orders), 1)

    def test_ignores_holdings_the_bot_did_not_buy(self):
        broker = SimBroker(100_000, {"AAPL": make_bars(uptrend()), "KO": make_bars(uptrend()[::-1])})
        broker.holdings["KO"] = (100, 60.0)  # yours, bought by hand, now falling
        eng = Engine(broker, Settings().clamped(), Journal())
        eng.run_cycle()
        self.assertIn("KO", broker.holdings)
        self.assertNotIn("KO", [o.symbol for o in broker.orders])

    def test_bad_symbol_does_not_stop_the_scan(self):
        class BadAsset(SimBroker):
            def get_asset(self, symbol):
                if symbol == "BAD":
                    raise RuntimeError("unknown symbol")
                return Asset(symbol, exchange="NASDAQ")

        broker = BadAsset(100_000, {"BAD": make_bars(uptrend()), "AAPL": make_bars(uptrend())})
        eng = Engine(broker, Settings(always_watch=("BAD",)).clamped(), Journal())
        report = eng.run_cycle()
        self.assertIn("AAPL", [o.symbol for o in broker.orders])
        self.assertTrue(any(x.startswith("BAD:") for x in report.skipped))

    def test_stop_mid_cycle_places_no_orders(self):
        broker = SimBroker(100_000, {"AAPL": make_bars(uptrend())})
        eng = Engine(broker, Settings().clamped(), Journal(), keep_going=lambda: False)
        eng.run_cycle()
        self.assertEqual(broker.orders, [])


@unittest.skipIf(TestClient is None, "fastapi not installed")
class ServerFixTests(unittest.TestCase):
    def setUp(self):
        from autotrader.server import BotRunner, SettingsStore, create_app

        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Journal()
        self.store = SettingsStore(f"{self.tmp.name}/s.json")
        self.brokers = {m: SimBroker(100_000, {"AAPL": make_bars(uptrend())}, assets=FRACTIONAL) for m in Mode}
        self.runners = {m: BotRunner(m, self.store, self.journal, threading.RLock(),
                                     lambda mode: self.brokers[mode]) for m in Mode}
        self.c = TestClient(create_app(self.runners, self.journal, TOKEN, live_enabled=True))

    def tearDown(self):
        self.tmp.cleanup()

    def _queue(self):
        self.runners[Mode.LIVE].run_once()
        return self.c.get("/api/live/approvals", headers=AUTH).json()[0]["id"]

    def test_old_approval_expires(self):
        aid = self._queue()
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
        with self.journal.db:
            self.journal.db.execute("UPDATE approvals SET ts = ? WHERE id = ?", (old, aid))
        r = self.c.post(f"/api/live/approvals/{aid}", headers=AUTH, json={"decision": "approve"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.brokers[Mode.LIVE].orders, [])

    def test_approval_rechecks_the_trend(self):
        aid = self._queue()
        crashed = uptrend()
        crashed[-1] *= 0.80
        self.brokers[Mode.LIVE].bars["AAPL"] = make_bars(crashed)
        r = self.c.post(f"/api/live/approvals/{aid}", headers=AUTH, json={"decision": "approve"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.brokers[Mode.LIVE].orders, [])

    def test_approve_refused_when_live_disabled(self):
        aid = self._queue()
        self.runners[Mode.LIVE].live_enabled = False
        r = self.c.post(f"/api/live/approvals/{aid}", headers=AUTH, json={"decision": "approve"})
        self.assertEqual(r.status_code, 403)

    def test_kill_cancels_approvals_and_spares_your_stocks(self):
        self._queue()
        self.brokers[Mode.LIVE].bars["KO"] = make_bars(uptrend())
        self.brokers[Mode.LIVE].holdings["KO"] = (10, 60.0)
        r = self.c.post("/api/live/kill", headers=AUTH, json={"sell_all": True}).json()
        self.assertEqual(r["cancelled_approvals"], 1)
        self.assertEqual(self.c.get("/api/live/approvals", headers=AUTH).json(), [])
        self.assertIn("KO", self.brokers[Mode.LIVE].holdings)

    def test_kill_does_not_wait_for_a_running_cycle(self):
        r = self.runners[Mode.TEST]
        r.lock.acquire()  # pretend a long cycle is running
        try:
            done = threading.Event()
            threading.Thread(target=lambda: (r.kill(False), done.set()), daemon=True).start()
            self.assertTrue(done.wait(2))
        finally:
            r.lock.release()


if __name__ == "__main__":
    unittest.main()
