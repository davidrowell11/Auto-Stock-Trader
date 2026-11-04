import unittest
from datetime import date, datetime, timedelta, timezone

from autotrader import scorecard
from autotrader.journal import Journal
from autotrader.learning import Learner, report
from autotrader.models import Bar


def bars(start: date, closes):
    t = datetime(start.year, start.month, start.day)
    return [Bar(t + timedelta(days=i), c, c, c, c, 1e6) for i, c in enumerate(closes)]


START = date(2026, 6, 1)
TODAY = date(2026, 9, 1)


def view(symbol, action, day=START, price=100.0):
    return {"day": day.isoformat(), "symbol": symbol, "price": price, "action": action,
            "confidence": 0.7, "reasoning": "", "model": None}


class GradeTests(unittest.TestCase):
    def setUp(self):
        flat = [100.0] * 60
        self.prices = {"SPY": bars(START, flat)}
        for i in range(5):
            # Approved stocks rose 10%, blocked ones fell 5%, a month after the review.
            self.prices[f"UP{i}"] = bars(START, [100.0] * 10 + [110.0] * 50)
            self.prices[f"DN{i}"] = bars(START, [100.0] * 10 + [95.0] * 50)
        self.views = ([view(f"UP{i}", "buy") for i in range(5)]
                      + [view(f"DN{i}", "skip") for i in range(5)])

    def test_vetoes_that_avoided_losers_are_helping(self):
        card = scorecard.grade(self.views, self.prices, TODAY)
        self.assertEqual(card["approved"]["calls"], 5)
        self.assertAlmostEqual(card["approved"]["avg_vs_market"], 0.10)
        self.assertAlmostEqual(card["blocked"]["avg_vs_market"], -0.05)
        self.assertIn("helping", card["verdict"])
        self.assertIn("15.0%", card["verdict"])

    def test_vetoes_that_missed_winners_are_too_strict(self):
        flipped = [{**v, "action": "skip" if v["action"] == "buy" else "buy"} for v in self.views]
        card = scorecard.grade(flipped, self.prices, TODAY)
        self.assertIn("too strict", card["verdict"])
        self.assertTrue(card["missed"][0]["symbol"].startswith("UP"))

    def test_compares_with_the_market(self):
        self.prices["SPY"] = bars(START, [100.0] * 10 + [110.0] * 50)  # market rose 10% too
        card = scorecard.grade(self.views, self.prices, TODAY)
        self.assertAlmostEqual(card["approved"]["avg_vs_market"], 0.0)
        self.assertAlmostEqual(card["approved"]["avg_return"], 0.10)

    def test_recent_calls_wait_for_their_month(self):
        recent = [view("UP0", "buy", day=START + timedelta(days=50))]
        card = scorecard.grade(recent, self.prices, TODAY)
        self.assertEqual(card["waiting"], 1)
        self.assertEqual(card["approved"]["calls"], 0)
        self.assertIn("Not enough", card["verdict"])

    def test_one_call_per_stock_per_day(self):
        card = scorecard.grade(self.views + self.views, self.prices, TODAY)
        self.assertEqual(card["approved"]["calls"] + card["blocked"]["calls"], 10)

    def test_old_calls_are_dropped(self):
        old = [view("UP0", "buy", day=TODAY - timedelta(days=scorecard.LOOK_BACK_DAYS + 1))]
        self.assertEqual(scorecard.calls_to_grade(old, TODAY), [])


class LearnerScorecardTests(unittest.TestCase):
    def test_weekly_review_grades_calls_and_passes_them_to_the_lessons(self):
        j = Journal()
        fetched, seen = [], []
        prices = GradeTests()
        prices.setUp()
        for v in prices.views:
            j.save_view("test", v["symbol"], v["price"], v["action"], 0.7, "", [])
        j.db.execute("UPDATE analyst_views SET day = ?", (START.isoformat(),))

        def prices_fn(symbols, days):
            fetched.append((symbols, days))
            return prices.prices

        def lessons(trades, stats, previous, card=None):
            seen.append(card)
            return ["x"]

        learner = Learner(j, lessons, now=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
                          prices_fn=prices_fn)
        card = learner.grade_analyst()
        self.assertIn("SPY", fetched[0][0])
        self.assertGreater(fetched[0][1], 60)
        self.assertIn("helping", card["verdict"])
        self.assertIn("helping", report(j, "live")["scorecard"]["verdict"])

    def test_no_prices_fetched_until_a_call_is_a_month_old(self):
        j = Journal()
        j.save_view("test", "AAA", 10, "buy", 0.7, "", [])  # today: nothing to grade yet
        fetched = []
        learner = Learner(j, prices_fn=lambda s, d: fetched.append(s) or {})
        self.assertIsNone(learner.grade_analyst())
        self.assertEqual(fetched, [])

    def test_bad_data_never_stops_the_weekly_review(self):
        j = Journal()
        j.save_view("test", "AAA", 10, "buy", 0.7, "", [])
        j.db.execute("UPDATE analyst_views SET day = '2026-06-01'")
        zero = {"AAA": bars(START, [0.0] * 60), "SPY": bars(START, [100.0] * 60)}
        learner = Learner(j, now=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
                          prices_fn=lambda s, d: zero)
        card = learner.grade_analyst()  # a zero price is skipped, not a crash
        self.assertEqual(card["waiting"], 1)

    def test_price_failure_skips_the_scorecard(self):
        j = Journal()
        j.save_view("test", "AAA", 10, "buy", 0.7, "", [])
        j.db.execute("UPDATE analyst_views SET day = '2026-06-01'")

        def broken(symbols, days):
            raise RuntimeError("no data")

        learner = Learner(j, now=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
                          prices_fn=broken)
        self.assertIsNone(learner.grade_analyst())
        self.assertIsNone(report(j, "test")["scorecard"])


if __name__ == "__main__":
    unittest.main()
