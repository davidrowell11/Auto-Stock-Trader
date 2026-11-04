"""The bot's memory: learning from its own finished trades."""

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

from autotrader.config import Mode, Settings
from autotrader.engine import Engine
from autotrader.journal import Journal
from autotrader.learning import (WEIGHT_MAX, WEIGHT_MIN, Learner, Memory, outcomes, source_stats,
                                 weights_from)
from autotrader.market import MARKET
from autotrader.models import Side
from autotrader.signals.book import ExpertSignals
from autotrader.signals.sec import InsiderActivity

from helpers import make_bars, uptrend
from test_final_check import AUTH, TOKEN, SlowBroker, TestClient


def trade(j, symbol, buy, sell, sources, mode="test", reason="Hit stop"):
    j.record_trade(mode, symbol, "buy", 1, buy, None, ["x"], sources)
    j.record_trade(mode, symbol, "sell", 1, sell, None, [reason])


class OutcomeTests(unittest.TestCase):
    def test_pairs_buys_with_the_next_sell(self):
        j = Journal()
        trade(j, "AAA", 100, 110, ["trend", "screener"])
        j.record_trade("test", "AAA", "sell", 1, 108, None, ["Hit stop"])  # a re-priced sell
        trade(j, "BBB", 50, 40, ["trend"], reason="Kill switch")  # your call, not the bot's
        j.record_trade("test", "CCC", "buy", 1, 10, None, ["x"])  # still held
        got = outcomes(j, "test")
        self.assertEqual([o.symbol for o in got], ["AAA"])
        self.assertAlmostEqual(got[0].ret, 0.10)
        self.assertEqual(got[0].sources, ["trend", "screener"])

    def test_old_trades_without_sources_count_as_trend(self):
        j = Journal()
        j.record_trade("test", "AAA", "buy", 1, 100, None, ["x"])
        j.record_trade("test", "AAA", "sell", 1, 90, None, ["Trend broke"])
        self.assertEqual(outcomes(j, "test")[0].sources, ["trend"])


class WeightTests(unittest.TestCase):
    def stats(self, wins, losses, gain=0.08, loss=-0.05):
        j = Journal()
        for i in range(wins):
            trade(j, f"W{i}", 100, 100 * (1 + gain), ["trend", "screener"])
        for i in range(losses):
            trade(j, f"L{i}", 100, 100 * (1 + loss), ["trend", "screener"])
        return source_stats(outcomes(j, "test"))

    def test_few_trades_change_nothing(self):
        self.assertEqual(weights_from(self.stats(3, 1))["screener"], 1.0)

    def test_winners_gain_weight_losers_lose_it_within_bounds(self):
        self.assertGreater(weights_from(self.stats(8, 2))["screener"], 1.0)
        self.assertLess(weights_from(self.stats(1, 9))["screener"], 1.0)
        self.assertEqual(weights_from(self.stats(200, 0, gain=0.5))["screener"], WEIGHT_MAX)
        self.assertEqual(weights_from(self.stats(0, 200, loss=-0.5))["screener"], WEIGHT_MIN)

    def test_memory_clamps_stored_weights(self):
        m = Memory.from_record({"weights": {"screener": 9, "trend": 0.1}, "lessons": ["a"] * 10})
        self.assertEqual(m.weight("screener"), WEIGHT_MAX)
        self.assertEqual(m.weight("trend"), 1.0)  # only the expert sources are ever weighted
        self.assertEqual(len(m.lessons), 6)


class LearnerTests(unittest.TestCase):
    def setUp(self):
        self.j = Journal()
        self.now = [datetime(2026, 11, 4, tzinfo=timezone.utc)]
        self.seen = []

        def lessons(trades, stats, previous):
            self.seen.append((trades, stats, previous))
            return ["Screener gainers bought after a big jump tended to fall back."]

        self.learner = Learner(self.j, lessons, now=lambda: self.now[0])

    def test_needs_a_few_finished_trades(self):
        trade(self.j, "AAA", 100, 110, ["trend"])
        self.assertIsNone(self.learner.learn())
        self.assertEqual(self.seen, [])

    def test_test_learns_and_live_waits_for_approval(self):
        for i in range(3):
            trade(self.j, f"S{i}", 100, 95, ["trend", "screener"])
        trade(self.j, "LIVE", 100, 120, ["trend"], mode="live")  # Live's results teach it too
        record = self.learner.learn()
        self.assertEqual(record["trades"], 4)
        self.assertEqual(self.learner.memory("test").lessons, record["lessons"])
        self.assertEqual(self.learner.memory("live").lessons, [])  # not until you approve
        self.assertIsNotNone(self.j.memory("live", "proposed"))
        self.assertEqual(len(self.seen[0][0]), 4)

        self.learner.approve_live()
        self.assertEqual(self.learner.memory("live").lessons, record["lessons"])
        self.assertIsNone(self.j.memory("live", "proposed"))
        with self.assertRaises(LookupError):
            self.learner.reject_live()

    def test_weekly_schedule_and_earlier_lessons_carry_over(self):
        for i in range(3):
            trade(self.j, f"S{i}", 100, 105, ["trend"])
        self.assertTrue(self.learner.due())
        self.learner.maybe_learn()
        self.assertFalse(self.learner.due())
        self.now[0] += timedelta(days=7)
        self.learner._last_try = None
        self.assertTrue(self.learner.due())
        self.learner.maybe_learn()
        self.assertEqual(self.seen[1][2], ["Screener gainers bought after a big jump tended to fall back."])

    def test_lessons_failure_keeps_the_weights_and_old_lessons(self):
        def broken(*a):
            raise RuntimeError("out of credits")
        learner = Learner(self.j, broken)
        for i in range(3):
            trade(self.j, f"S{i}", 100, 105, ["trend"])
        record = learner.learn()
        self.assertEqual(record["lessons"], [])
        self.assertIn("screener", record["weights"])

    def test_rejected_proposal_leaves_live_alone(self):
        for i in range(3):
            trade(self.j, f"S{i}", 100, 105, ["trend"])
        self.learner.learn()
        self.learner.reject_live()
        self.assertEqual(self.learner.memory("live").lessons, [])
        self.learner.learn()  # the same lessons again: not asked a second time
        self.assertIsNone(self.j.memory("live", "proposed"))

    def test_failed_lessons_retry_in_hours_not_a_week(self):
        calls = []

        def flaky(*a):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("rate limited")
            return ["Now it worked."]

        learner = Learner(self.j, flaky, now=lambda: self.now[0])
        for i in range(3):
            trade(self.j, f"S{i}", 100, 105, ["trend"])
        self.assertIsNotNone(learner.learn()["lessons_error"])
        self.now[0] += timedelta(hours=7)
        self.assertTrue(learner.due())
        self.assertEqual(learner.maybe_learn()["lessons"], ["Now it worked."])

    def test_nothing_to_approve_when_nothing_changed(self):
        learner = Learner(self.j)  # no analyst key: weights only, all still 1.0
        for i in range(3):
            trade(self.j, f"S{i}", 100, 105, ["trend"])
        learner.learn()
        self.assertIsNone(self.j.memory("live", "proposed"))

    def test_one_review_at_a_time(self):
        from autotrader.learning import LearnerBusy
        self.learner._lock.acquire()
        with self.assertRaises(LearnerBusy):
            self.learner.learn()


class EngineMemoryTests(unittest.TestCase):
    def setUp(self):
        self.broker = SlowBroker(100_000, {"AAPL": make_bars(uptrend()), MARKET: make_bars(uptrend()),
                                           "SOUN": make_bars(uptrend(start=20.0))})
        self.journal = Journal()

    def engine(self, **kw):
        return Engine(self.broker, Settings().clamped(), self.journal, **kw)

    def test_buys_record_their_sources(self):
        self.engine(screener=lambda: ["SOUN"]).run_cycle()
        sources = {t["symbol"]: t["sources"] for t in self.journal.trades("test")}
        self.assertEqual(sources["SOUN"], ["trend", "screener"])
        self.assertEqual(sources["AAPL"], ["trend"])

    def test_lessons_reach_the_analyst_and_it_is_recorded(self):
        seen = {}

        class FakeAnalyst:
            def review(self, candidate):
                seen[candidate["symbol"]] = candidate
                from autotrader.analyst import AnalystView
                return AnalystView("buy", 0.9, "fine", [])

        memory = Memory(lessons=["Avoid chasing gaps."])
        self.engine(analyst=FakeAnalyst(), memory=lambda: memory).run_cycle()
        self.assertEqual(seen["AAPL"]["lessons_from_past_trades"], ["Avoid chasing gaps."])
        aapl = [t for t in self.journal.trades("test") if t["symbol"] == "AAPL"][0]
        self.assertIn("analyst", aapl["sources"])

    def test_weights_scale_expert_boosts(self):
        class Book:
            def for_symbol(self, symbol):
                return ExpertSignals(InsiderActivity(), boost=0.1, reasons=["insiders bought"],
                                     boosts={"insider": 0.1})

        def score(weight):
            j = Journal()
            broker = SlowBroker(100_000, {"AAPL": make_bars(uptrend()), MARKET: make_bars(uptrend())})
            Engine(broker, Settings().clamped(), j, signals=Book(),
                   memory=lambda: Memory({"insider": weight})).run_cycle()
            buy = [t for t in j.trades("test") if t["side"] == "buy"][0]
            self.assertIn("insider", buy["sources"])
            return float(buy["reasons"][0].split()[1])

        self.assertGreater(score(1.3), score(0.7))

    def test_broken_memory_does_not_stop_trading(self):
        def broken():
            raise RuntimeError("bad")
        self.engine(memory=broken).run_cycle()
        self.assertTrue([o for o in self.broker.orders if o.side is Side.BUY])


@unittest.skipIf(TestClient is None, "fastapi not installed")
class LearningServerTests(unittest.TestCase):
    def setUp(self):
        from autotrader.server import BotRunner, SettingsStore, create_app

        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Journal()
        store = SettingsStore(f"{self.tmp.name}/s.json")
        brokers = {m: SlowBroker(100_000, {"AAPL": make_bars(uptrend())}) for m in Mode}
        runners = {m: BotRunner(m, store, self.journal, threading.RLock(), lambda mode: brokers[mode])
                   for m in Mode}
        self.learner = Learner(self.journal, lambda *a: ["Lesson one."])
        self.c = TestClient(create_app(runners, self.journal, TOKEN, live_enabled=True,
                                       learner=self.learner))

    def tearDown(self):
        self.tmp.cleanup()

    def test_learning_flow(self):
        empty = self.c.get("/api/test/learning", headers=AUTH).json()
        self.assertIsNone(empty["active"])
        self.assertEqual(empty["needs"], 3)
        self.assertEqual(self.c.post("/api/test/learning/run", headers=AUTH).status_code, 409)

        for i in range(3):
            trade(self.journal, f"S{i}", 100, 105, ["trend"])
        r = self.c.post("/api/test/learning/run", headers=AUTH).json()
        self.assertEqual(r["active"]["lessons"], ["Lesson one."])
        self.assertEqual({w["source"] for w in r["active"]["weights"]}, {"insider", "congress", "screener"})

        live = self.c.get("/api/live/learning", headers=AUTH).json()
        self.assertIsNone(live["active"])
        self.assertEqual(live["proposed"]["lessons"], ["Lesson one."])
        out = self.c.post("/api/live/learning", headers=AUTH, json={"decision": "approve"}).json()
        self.assertEqual(out["active"]["lessons"], ["Lesson one."])
        self.assertIsNone(out["proposed"])
        again = self.c.post("/api/live/learning", headers=AUTH, json={"decision": "reject"})
        self.assertEqual(again.status_code, 409)
        self.assertEqual(self.c.post("/api/live/learning", headers=AUTH,
                                     json={"decision": "maybe"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
