"""Tests for the fixes from the full pre-trading code check (Nov 2026)."""

import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from autotrader import analyst
from autotrader.broker.sim import SimBroker
from autotrader.config import Mode, Settings
from autotrader.engine import Engine
from autotrader.journal import Journal
from autotrader.models import Asset, OrderUpdate, Side

from helpers import make_bars, uptrend

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None

TOKEN = "t"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class SlowBroker(SimBroker):
    """Orders wait until the test fills or expires them, like a real exchange."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.open: dict[str, object] = {}
        self.done: dict[str, float] = {}
        self.cancelled: list[str] = []
        self.next_id = 0

    def submit_order(self, order):
        self.next_id += 1
        oid = f"o{self.next_id}"
        self.open[oid] = order
        self.orders.append(order)
        return oid

    def fill(self, oid, qty=None):
        order = self.open.pop(oid)
        if qty is not None:
            order = type(order)(order.symbol, order.side, qty, None, order.reason)
        super().submit_order(order)
        self.orders.pop()  # the parent records it again
        self.done[oid] = order.qty

    def expire(self, oid):
        self.open.pop(oid)
        self.done[oid] = 0.0

    def get_order(self, oid):
        if oid in self.open:
            return OrderUpdate(done=False, filled_qty=0.0)
        return OrderUpdate(done=True, filled_qty=self.done.get(oid, 0.0))

    def cancel_order(self, oid):
        self.cancelled.append(oid)
        if oid in self.open:
            self.expire(oid)

    def open_order_symbols(self):
        return {o.symbol for o in self.open.values()}

    def last_id(self):
        return f"o{self.next_id}"


def crash(broker, symbol="AAPL"):
    closes = uptrend()
    closes[-1] *= 0.80
    broker.bars[symbol] = make_bars(closes)


class OrderTrackingTests(unittest.TestCase):
    def setUp(self):
        self.broker = SlowBroker(100_000, {"AAPL": make_bars(uptrend())})
        self.journal = Journal()
        self.eng = Engine(self.broker, Settings().clamped(), self.journal)

    def buy_and_fill(self):
        self.eng.run_cycle()
        self.broker.fill(self.broker.last_id())
        self.eng.run_cycle()

    def test_unfilled_sell_keeps_the_stock_owned(self):
        self.buy_and_fill()
        crash(self.broker)
        self.eng.run_cycle()
        sells = [o for o in self.broker.orders if o.side is Side.SELL]
        self.assertEqual(len(sells), 1)
        self.assertIsNotNone(self.journal.get_stop("test", "AAPL"))  # still the bot's to look after
        first = self.broker.last_id()
        self.eng.run_cycle()  # sell still unfilled: cancel it and place it again at today's price
        self.assertIn(first, self.broker.cancelled)
        working_sells = [o for o in self.broker.open.values() if o.side is Side.SELL]
        self.assertEqual(len(working_sells), 1)  # never two sells working at once
        self.broker.fill(self.broker.last_id())
        self.eng.run_cycle()
        self.assertIsNone(self.journal.get_stop("test", "AAPL"))

    def test_expired_sell_is_tried_again(self):
        self.buy_and_fill()
        crash(self.broker)
        self.eng.run_cycle()
        self.broker.expire(self.broker.last_id())
        self.eng.run_cycle()
        self.assertEqual(len([o for o in self.broker.orders if o.side is Side.SELL]), 2)

    def test_partial_buy_counts_only_what_filled(self):
        self.eng.run_cycle()
        bought = self.broker.orders[0].qty
        self.broker.fill(self.broker.last_id(), qty=bought / 2)
        self.eng.run_cycle()
        self.assertAlmostEqual(self.journal.get_stop("test", "AAPL").qty, bought / 2)

    def test_buy_that_never_fills_is_forgotten(self):
        self.eng.run_cycle()
        self.broker.expire(self.broker.last_id())
        self.eng.run_cycle()
        # Record cleared and, with the trend still up, it may buy again.
        self.assertEqual(len([o for o in self.broker.orders if o.side is Side.BUY]), 2)

    def test_sells_only_its_own_shares(self):
        broker = SimBroker(100_000, {"AAPL": make_bars(uptrend())})
        eng = Engine(broker, Settings().clamped(), Journal())
        eng.run_cycle()
        bot_qty = broker.orders[0].qty
        mine = 10
        q, avg = broker.holdings["AAPL"]
        broker.holdings["AAPL"] = (q + mine, avg)  # you bought some by hand too
        crash(broker)
        eng.run_cycle()
        self.assertEqual(broker.orders[-1].side, Side.SELL)
        self.assertAlmostEqual(broker.orders[-1].qty, bot_qty)
        self.assertAlmostEqual(broker.holdings["AAPL"][0], mine)
        eng.run_cycle()  # and it doesn't come back for yours
        self.assertEqual(len([o for o in broker.orders if o.side is Side.SELL]), 1)

    def test_no_rebuy_for_a_week_after_a_stop_out(self):
        broker = SimBroker(100_000, {"AAPL": make_bars(uptrend())})
        j = Journal()
        eng = Engine(broker, Settings().clamped(), j)
        eng.run_cycle()
        crash(broker)
        eng.run_cycle()  # stop-out sell
        broker.bars["AAPL"] = make_bars(uptrend())  # trend looks fine again next day
        report = eng.run_cycle()
        self.assertEqual([o.side for o in broker.orders], [Side.BUY, Side.SELL])
        self.assertTrue(any("cooling off" in x for x in report.skipped))
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(timespec="seconds")
        with j.db:
            j.db.execute("UPDATE trades SET ts = ? WHERE side = 'sell'", (old,))
        eng.run_cycle()
        self.assertEqual(broker.orders[-1].side, Side.BUY)  # a week later it may buy again

    def test_kill_switch_sell_has_no_cooloff(self):
        j = Journal()
        j.record_trade("test", "AAPL", "sell", 1, 50, None, ["Kill switch"])
        self.assertIsNone(j.last_protective_sell("test", "AAPL"))

    def test_old_journal_is_upgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/j.sqlite3"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE stops (mode TEXT NOT NULL, symbol TEXT NOT NULL,"
                       " entry_price REAL NOT NULL, stop_price REAL NOT NULL,"
                       " highest_close REAL NOT NULL, opened_at TEXT NOT NULL,"
                       " PRIMARY KEY (mode, symbol))")
            db.execute("INSERT INTO stops VALUES ('test', 'AAPL', 50, 45, 55, '2026-01-01')")
            db.commit()
            db.close()
            j = Journal(path)
            state = j.get_stop("test", "AAPL")
            self.assertIsNone(state.qty)
            self.assertEqual(state.stop_price, 45)
            j.db.close()

    def test_old_approvals_expire_during_the_cycle(self):
        j = Journal()
        aid = j.queue_approval("live", "AAPL", "buy", 1, None, ["x"])
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
        with j.db:
            j.db.execute("UPDATE approvals SET ts = ? WHERE id = ?", (old, aid))
        eng = Engine(SimBroker(1000, {"AAPL": make_bars(uptrend())}), Settings(mode=Mode.LIVE).clamped(), j)
        eng.run_cycle()
        self.assertEqual(j.get_approval(aid)["status"], "expired")

    def test_journal_is_safe_across_threads(self):
        j = Journal()
        errors = []

        def work():
            try:
                for i in range(200):
                    j.add_event("test", "x", str(i))
                    j.events("test", 5)
            except Exception as exc:  # pragma: no cover - only on failure
                errors.append(exc)

        threads = [threading.Thread(target=work) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(j.events("test", 10_000)), 800)


class AskTests(unittest.TestCase):
    def test_paused_turn_is_continued(self):
        calls = []

        class Messages:
            def create(self, **kw):
                calls.append(kw)
                if len(calls) == 1:
                    return SimpleNamespace(stop_reason="pause_turn", content=[])
                return SimpleNamespace(stop_reason="end_turn",
                                       content=[SimpleNamespace(type="text", text="All good.")])

        client = SimpleNamespace(beta=SimpleNamespace(messages=Messages()))
        self.assertEqual(analyst.ask(client, "how am I doing?", {}), "All good.")
        self.assertEqual(len(calls), 2)


@unittest.skipIf(TestClient is None, "fastapi not installed")
class ServerTests(unittest.TestCase):
    def setUp(self):
        from autotrader.server import BotRunner, SettingsStore, create_app

        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Journal()
        self.store = SettingsStore(f"{self.tmp.name}/s.json")
        assets = {"AAPL": Asset("AAPL", exchange="NASDAQ", fractionable=True)}
        self.brokers = {m: SlowBroker(100_000, {"AAPL": make_bars(uptrend()), "KO": make_bars(uptrend())},
                                      assets=assets) for m in Mode}
        self.runners = {m: BotRunner(m, self.store, self.journal, threading.RLock(),
                                     lambda mode: self.brokers[mode]) for m in Mode}
        self.c = TestClient(create_app(self.runners, self.journal, TOKEN, live_enabled=True))

    def tearDown(self):
        self.tmp.cleanup()

    def test_kill_leaves_your_own_orders_alone(self):
        broker = self.brokers[Mode.TEST]
        self.store.update(Mode.TEST, {"never_buy": ["KO"]})
        self.runners[Mode.TEST].run_once()  # bot places an AAPL buy that hasn't filled
        bot_order = broker.last_id()
        broker.submit_order(SimpleNamespace(symbol="KO", side=Side.BUY, qty=1))  # yours
        yours = broker.last_id()
        r = self.c.post("/api/test/kill", headers=AUTH, json={"sell_all": True}).json()
        self.assertEqual(broker.cancelled, [bot_order])
        self.assertIn(yours, broker.open)
        self.assertTrue(r["stopped"])

    def test_kill_waits_for_the_cycle_before_selling(self):
        r = self.runners[Mode.TEST]
        r.KILL_WAIT_SECONDS = 5
        r.lock.acquire()
        done = threading.Event()
        threading.Thread(target=lambda: (r.kill(True), done.set()), daemon=True).start()
        self.assertFalse(done.wait(0.3))  # waiting for the "cycle"
        self.assertTrue(r.halted_by_you.is_set())  # but the bot is already stopped
        r.lock.release()
        self.assertTrue(done.wait(5))

    def test_live_status_shows_what_the_bot_is_doing(self):
        r = self.runners[Mode.TEST]
        self.brokers[Mode.TEST] = SimBroker(100_000, {"AAPL": make_bars(uptrend())})
        r.broker = self.brokers[Mode.TEST]
        self.c.post("/api/test/start", headers=AUTH)
        r.run_once()
        live = self.c.get("/api/test/live", headers=AUTH).json()
        texts = [s["text"] for s in live["steps"]]
        self.assertTrue(any(t.startswith("Looking for buys") for t in texts))
        self.assertTrue(any(t.startswith("Buying") and "AAPL" in t for t in texts))
        self.assertIn("bought AAPL", live["last_cycle"]["summary"])
        self.assertEqual(live["steps"][0]["text"], live["last_cycle"]["summary"])  # newest first
        self.assertFalse(live["working"])
        self.c.post("/api/test/stop", headers=AUTH)
        self.assertTrue(self.c.get("/api/test/live", headers=AUTH).json()["now"].startswith("Stopped"))

    def test_stopped_bot_never_shows_as_resting(self):
        r = self.runners[Mode.TEST]
        r.broker = SimBroker(100_000, {"AAPL": make_bars(uptrend())})
        self.c.post("/api/test/start", headers=AUTH)
        r.step("Resting until the next check.", log_it=False)
        r.next_check = "2099-01-01T00:00:00+00:00"
        self.store.update(Mode.TEST, {"running": False})  # Stop pressed during a check
        live = self.c.get("/api/test/live", headers=AUTH).json()
        self.assertTrue(live["now"].startswith("Stopped"))
        self.assertIsNone(live["next_check"])

    def test_summary_says_when_buying_was_blocked(self):
        from autotrader.engine import CycleReport
        rep = CycleReport(halted="market in a confirmed downtrend", checked=52)
        self.assertIn("no new buys (market in a confirmed downtrend)", rep.summary())
        self.assertNotIn("52", rep.summary())

    def test_reject_only_pending(self):
        aid = self.journal.queue_approval("live", "AAPL", "buy", 1, None, ["x"])
        self.journal.set_approval_status(aid, "expired")
        res = self.c.post(f"/api/live/approvals/{aid}", headers=AUTH, json={"decision": "reject"})
        self.assertEqual(res.status_code, 409)

    def test_live_never_falls_back_to_the_whole_account(self):
        old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        self.store.update(Mode.LIVE, {"first_started": old})
        self.assertFalse(self.store.starter_active())
        self.assertEqual(self.store.settings_for(Mode.LIVE).budget, 1000)
        res = self.c.put("/api/live/settings", headers=AUTH, json={"clear_budget": True})
        self.assertEqual(res.status_code, 400)
        res = self.c.put("/api/live/settings", headers=AUTH, json={"budget": 2500})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.store.settings_for(Mode.LIVE).budget, 2500)


if __name__ == "__main__":
    unittest.main()
