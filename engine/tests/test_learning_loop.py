"""The self-learning loop: calibration, the notebook of proven lessons, and post-mortems."""

import unittest
from datetime import date, datetime, timezone

from autotrader import calibration, scorecard
from autotrader.analyst import AnalystView
from autotrader.config import Settings
from autotrader.engine import Engine
from autotrader.journal import Journal
from autotrader.learning import Learner, Memory, outcomes, report
from autotrader.market import MARKET
from autotrader.notebook import (ACTIVE_SUPPORT, clean_reviews, lessons_for_analyst, settle,
                                 status_of)

from helpers import make_bars, uptrend
from test_final_check import SlowBroker
from test_scorecard import START, TODAY, bars, view

TODAY_D = date(2026, 11, 10)


def calls(n, confidence, wins):
    return [{"confidence": confidence, "return": 0.05 if i < wins else -0.05} for i in range(n)]


class CalibrationTests(unittest.TestCase):
    def test_too_few_calls_change_nothing(self):
        c = calibration.calibrate(calls(9, 0.8, 1))
        self.assertEqual(c["shift"], 0.0)
        self.assertIn("too few", c["note"])

    def test_overconfident_is_nudged_down_but_capped(self):
        c = calibration.calibrate(calls(40, 0.7, 16))  # said 70%, right 40%
        self.assertEqual(c["shift"], -calibration.MAX_SHIFT)
        self.assertIn("overconfident", c["note"])
        c = calibration.calibrate(calls(20, 0.6, 10))  # said 60%, right 50%: half way = -5 points
        self.assertAlmostEqual(c["shift"], -0.05)

    def test_well_calibrated_and_bins(self):
        c = calibration.calibrate(calls(20, 0.55, 11) + calls(20, 0.75, 15))
        self.assertLess(abs(c["shift"]), 0.01)
        self.assertEqual([b["label"] for b in c["bins"]], ["50-60%", "70% or more"])
        self.assertIn("Well calibrated", c["note"])

    def test_adjust_stays_in_range(self):
        self.assertEqual(calibration.adjust(0.95, 0.5), 1.0)
        self.assertAlmostEqual(calibration.adjust(0.7, -0.5), 0.55)

    def test_scorecard_carries_calibration(self):
        prices = {"SPY": bars(START, [100.0] * 60), "UP": bars(START, [100.0] * 10 + [110.0] * 50)}
        card = scorecard.grade([view("UP", "buy")], prices, TODAY)
        self.assertEqual(card["calibration"]["calls"], 1)
        self.assertEqual(card["approved"]["calls"], 1)


class NotebookTests(unittest.TestCase):
    def test_status_rules(self):
        self.assertEqual(status_of(ACTIVE_SUPPORT, 2), "active")
        self.assertEqual(status_of(ACTIVE_SUPPORT, 3), "candidate")  # not twice as many for
        self.assertEqual(status_of(4, 0), "candidate")
        self.assertEqual(status_of(2, 3), "retired")

    def test_settle_checks_evidence(self):
        known = {f"T{i} 2026-10-0{i}" for i in range(1, 7)}
        ids = sorted(known)
        proposed = [
            {"text": "Buys far above the 20-day average lost", "support": ids[:5] + ["FAKE 2026-01-01"],
             "against": []},
            {"text": "  buys far above the 20-day  average lost ", "support": ids, "against": []},  # dup
            {"text": "No evidence at all", "support": ["FAKE"], "against": []},
            {"text": "Weak market buys lose", "support": ids[:1], "against": ids[1:5]},
        ]
        previous = [{"text": "Weak market buys lose", "since": "2026-10-01"}]
        got = settle(proposed, known, previous, TODAY_D)
        self.assertEqual([e["text"] for e in got],
                         ["Buys far above the 20-day average lost", "Weak market buys lose"])
        self.assertEqual(got[0]["status"], "active")
        self.assertNotIn("FAKE 2026-01-01", got[0]["support"])
        self.assertEqual(got[1]["status"], "retired")
        self.assertEqual(got[1]["since"], "2026-10-01")

    def test_reviews_and_lessons_for_the_analyst(self):
        got = clean_reviews([{"trade": "A 2026-10-01", "cause": "astrology", "expected": "up"},
                             {"trade": "NOPE", "cause": "luck"}], {"A 2026-10-01"})
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["cause"], "luck")
        entries = [{"text": "Proven", "status": "active", "support": ["a"] * 5, "against": []},
                   {"text": "Hunch", "status": "candidate", "support": ["a"], "against": []},
                   {"text": "Old", "status": "retired", "support": [], "against": ["a"] * 3}]
        lessons = lessons_for_analyst(entries)
        self.assertEqual(lessons[0], "Proven")
        self.assertTrue(lessons[1].startswith("Unproven (1 trades for, 0 against)"))
        self.assertEqual(len(lessons), 2)


def finished(j, symbol, buy, sell, facts=None):
    j.record_trade("test", symbol, "buy", 1, buy, None, ["Score 0.60"], ["trend"], facts=facts)
    j.record_trade("test", symbol, "sell", 1, sell, None, ["Hit stop"])


class LearnerNotebookTests(unittest.TestCase):
    def setUp(self):
        self.j = Journal()
        self.calls = []
        self.fail = False

        def reflect(new, trades, notebook, stats, card=None):
            self.calls.append((new, trades, notebook))
            if self.fail:
                raise RuntimeError("API down")
            ids = [t["id"] for t in trades]
            return {"reviews": [{"trade": t["id"], "expected": "a trend", "happened": "stopped out",
                                 "cause": "rules", "lesson": ""} for t in new],
                    "notebook": [{"text": "Stretched buys get stopped out", "support": ids,
                                  "against": []}]}

        self.learner = Learner(self.j, now=lambda: datetime(2026, 11, 10, tzinfo=timezone.utc),
                               reflect_fn=reflect)

    def test_trades_keep_their_desk_facts(self):
        finished(self.j, "AAA", 100, 90, {"above_20_day_average": 0.12})
        o = outcomes(self.j, "test")[0]
        self.assertEqual(o.as_dict()["facts"], {"above_20_day_average": 0.12})
        self.assertEqual(o.as_dict()["id"], f"AAA {o.bought[:10]}")

    def test_reviews_new_trades_once_and_builds_the_notebook(self):
        for i in range(3):
            finished(self.j, f"S{i}", 100, 95, {"above_20_day_average": 0.1})
        record = self.learner.learn()
        self.assertEqual(len(self.calls[0][0]), 3)  # all three are new
        self.assertEqual(len(record["reviews"]), 3)
        self.assertEqual(record["notebook"][0]["status"], "candidate")  # 3 trades: not proven yet
        self.assertTrue(record["lessons"][0].startswith("Unproven (3 trades for"))
        self.assertEqual(self.learner.memory("test").lessons, record["lessons"])

        self.learner.learn()  # nothing new finished: no paid call
        self.assertEqual(len(self.calls), 1)

        for i in range(3, 6):
            finished(self.j, f"S{i}", 100, 95)
        record = self.learner.learn()
        self.assertEqual(len(self.calls[1][0]), 3)  # only the new ones to review
        self.assertEqual(record["notebook"][0]["status"], "active")  # 6 trades back it now
        self.assertEqual(record["lessons"], ["Stretched buys get stopped out"])
        shown = report(self.j, "test")["active"]
        self.assertEqual(len(shown["reviews"]), 6)
        self.assertEqual(shown["notebook"][0]["status"], "active")

    def test_a_failed_review_keeps_the_old_notebook(self):
        for i in range(3):
            finished(self.j, f"S{i}", 100, 95)
        first = self.learner.learn()
        finished(self.j, "NEW", 100, 95)
        self.fail = True
        record = self.learner.learn()
        self.assertEqual(record["notebook"], first["notebook"])
        self.assertIn("API down", record["lessons_error"])
        self.assertNotIn(f"NEW {outcomes(self.j, 'test')[-1].bought[:10]}", record["reviewed"])


class EngineCalibrationTests(unittest.TestCase):
    def run_with(self, shift, confidence=0.7):
        class Sure:
            def review(self, candidate):
                return AnalystView("buy", confidence, "fine", [])

        j = Journal()
        broker = SlowBroker(100_000, {"AAPL": make_bars(uptrend()), MARKET: make_bars(uptrend())})
        report_ = Engine(broker, Settings().clamped(), j, analyst=Sure(),
                         memory=lambda: Memory(calibration_shift=shift)).run_cycle()
        return j, report_

    def test_an_overconfident_record_can_turn_a_buy_away(self):
        j, r = self.run_with(-0.15)  # 70% becomes 55%, under the 60% bar
        self.assertFalse([t for t in j.trades("test") if t["side"] == "buy"])
        self.assertTrue(any("after its track record" in s for s in r.skipped))

    def test_buys_record_the_desk_facts(self):
        j, _ = self.run_with(0.0)
        buy = [t for t in j.trades("test") if t["side"] == "buy"][0]
        self.assertIn("above_50_day_average", buy["facts"])
        self.assertEqual(buy["facts"]["market_mood"], "calm")
        self.assertEqual(buy["facts"]["analyst_confidence"], 0.7)


if __name__ == "__main__":
    unittest.main()
