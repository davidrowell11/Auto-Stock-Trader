"""Live market data: the daily screener and the recent-news check."""

import unittest
from datetime import date, datetime, timedelta, timezone

from autotrader.config import Settings
from autotrader.engine import Engine
from autotrader.journal import Journal
from autotrader.market import MARKET
from autotrader.models import Asset, Side
from autotrader.signals.news import Headline, NewsCheck, NewsDesk, review
from autotrader.signals.screener import Screener

from helpers import make_bars, uptrend
from test_final_check import SlowBroker

NOW = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)


def h(text, hours_ago=1, source="Reuters"):
    return Headline(NOW - timedelta(hours=hours_ago), text, source)


class ScreenerTests(unittest.TestCase):
    def test_merges_actives_and_gainers_and_drops_odd_symbols(self):
        calls = []
        def fetch(top):
            calls.append(top)
            return ["NVDA", "tsla", "ABC.WS", "XYZ-P"], ["TSLA", "SOUN", "TOOLONGX"]
        s = Screener(fetch, top=50, today=lambda: date(2026, 11, 4))
        self.assertEqual(s.symbols(), ["NVDA", "TSLA", "SOUN"])
        s.symbols()
        self.assertEqual(calls, [50])  # one lookup a day

    def test_refreshes_on_a_new_day(self):
        day = [date(2026, 11, 4)]
        s = Screener(lambda top: (["AAA"], []) if day[0].day == 4 else (["BBB"], []),
                     today=lambda: day[0])
        self.assertEqual(s.symbols(), ["AAA"])
        day[0] = date(2026, 11, 5)
        self.assertEqual(s.symbols(), ["BBB"])

    def test_outage_keeps_yesterdays_list(self):
        day = [date(2026, 11, 4)]
        def fetch(top):
            if day[0].day == 5:
                raise OSError("down")
            return ["AAA"], []
        s = Screener(fetch, today=lambda: day[0])
        self.assertEqual(s.symbols(), ["AAA"])
        day[0] = date(2026, 11, 5)
        self.assertEqual(s.symbols(), ["AAA"])
        self.assertEqual(Screener(lambda top: 1 / 0).symbols(), [])  # first-ever outage: just nothing extra


class NewsReviewTests(unittest.TestCase):
    def test_red_flags(self):
        for text, reason in [
            ("Acme announces $200 million public offering of common stock", "share offering"),
            ("SEC launches investigation into Acme's revenue recognition", "under investigation"),
            ("Acme files for Chapter 11 bankruptcy protection", "bankruptcy"),
            ("Acme receives Nasdaq delisting notice", "delisting"),
            ("Acme CFO resigns effective immediately", "top executive"),
            ("Hindenburg Research targets Acme in new report", "short-seller"),
            ("Shareholder class action filed against Acme", "class-action"),
        ]:
            out = review([h(text)])
            self.assertIsNotNone(out.red_flag, text)
            self.assertIn(reason, out.red_flag, text)
            self.assertIn(text, out.red_flag)

    def test_ordinary_news_is_fine_and_formatted_newest_first(self):
        out = review([h("Acme beats estimates, raises guidance", hours_ago=30, source="AP"),
                      h("Acme unveils new product line", hours_ago=2)])
        self.assertIsNone(out.red_flag)
        self.assertEqual(out.headlines[0], "Nov 4: Acme unveils new product line (Reuters)")
        self.assertEqual(out.headlines[1], "Nov 3: Acme beats estimates, raises guidance (AP)")

    def test_at_most_eight_headlines(self):
        out = review([h(f"Headline {i}", hours_ago=i) for i in range(20)])
        self.assertEqual(len(out.headlines), 8)
        self.assertTrue(out.headlines[0].endswith("Headline 0 (Reuters)"))

    def test_no_false_alarm_on_common_words(self):
        for text in ["Analysts split on Acme after strong quarter",
                     "Acme to split stock 10-for-1",
                     "Acme in default position to win contract, analysts say"]:
            self.assertIsNone(review([h(text)]).red_flag, text)


class NewsDeskTests(unittest.TestCase):
    def test_caches_and_survives_outages(self):
        calls = []
        def fetch(symbol, since):
            calls.append(symbol)
            if symbol == "BAD":
                raise OSError("down")
            self.assertEqual(since, NOW - timedelta(days=2))
            return [h("Acme announces stock offering")]
        desk = NewsDesk(fetch, now=lambda: NOW)
        self.assertIsNotNone(desk.check("ACME").red_flag)
        desk.check("ACME")
        self.assertEqual(calls, ["ACME"])
        self.assertEqual(desk.check("BAD"), NewsCheck())


class EngineLiveDataTests(unittest.TestCase):
    def setUp(self):
        self.broker = SlowBroker(100_000, {"AAPL": make_bars(uptrend()), MARKET: make_bars(uptrend()),
                                           "SOUN": make_bars(uptrend(start=20.0))},
                                 assets={"SOUN": Asset("SOUN", exchange="NASDAQ")})
        self.journal = Journal()

    def engine(self, **kw):
        return Engine(self.broker, Settings(never_buy=("GME",)).clamped(), self.journal, **kw)

    def test_screened_stocks_are_scanned_and_bought(self):
        report = self.engine(screener=lambda: ["SOUN", "GME", "AAPL", "NOHISTORY"]).run_cycle()
        bought = {o.symbol for o in self.broker.orders if o.side is Side.BUY}
        self.assertIn("SOUN", bought)
        self.assertNotIn("GME", bought)  # never-buy list still wins
        self.assertGreater(report.checked, 50)  # the fixed list plus the screened extras

    def test_red_flag_headline_blocks_the_buy(self):
        checks = {"AAPL": NewsCheck(["Nov 4: Apple announces $5B share offering"],
                                    'a share offering was just announced: "Apple announces $5B share offering"')}
        report = self.engine(news=lambda s: checks.get(s, NewsCheck())).run_cycle()
        self.assertFalse([o for o in self.broker.orders if o.symbol == "AAPL"])
        self.assertTrue(any(s.startswith("AAPL: recent news: a share offering") for s in report.skipped))

    def test_headlines_reach_the_analyst(self):
        seen = {}
        class FakeAnalyst:
            def review(self, candidate):
                seen[candidate["symbol"]] = candidate
                from autotrader.analyst import AnalystView
                return AnalystView("buy", 0.9, "fine", [])
        checks = {"AAPL": NewsCheck(["Nov 4: Apple unveils new chips"])}
        self.engine(news=lambda s: checks.get(s, NewsCheck()), analyst=FakeAnalyst()).run_cycle()
        self.assertEqual(seen["AAPL"]["recent_headlines"], ["Nov 4: Apple unveils new chips"])
        self.assertTrue([o for o in self.broker.orders if o.symbol == "AAPL"])


if __name__ == "__main__":
    unittest.main()
