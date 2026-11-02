import unittest
from datetime import date, timedelta
from types import SimpleNamespace

from autotrader.analyst import Analyst, AnalystUnavailable
from autotrader.backtest import run_backtest, stats
from autotrader.broker.sim import SimBroker
from autotrader.config import Settings
from autotrader.engine import Engine
from autotrader.journal import Journal
from autotrader.signals.book import SignalBook
from autotrader.signals.congress import build_leaderboard, parse_trades, top_member_buys
from autotrader.signals.sec import SecClient, parse_form4_purchases

from helpers import downtrend, make_bars, uptrend

TODAY = date(2026, 11, 2)

FORM4 = b"""<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner><reportingOwnerId><rptOwnerName>Jane CEO</rptOwnerName></reportingOwnerId></reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>10000</value></transactionShares>
        <transactionPricePerShare><value>25.50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>500</value></transactionShares>
        <transactionPricePerShare><value>30</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


class FakeFetch:
    """Stands in for the SEC website."""

    def __init__(self, forms):
        self.forms = forms  # list of (form, days_ago)
        self.urls = []

    def __call__(self, url, headers):
        self.urls.append(url)
        assert "User-Agent" in headers
        return FORM4

    def json(self, url, headers):
        self.urls.append(url)
        if url.endswith("company_tickers.json"):
            return {"0": {"cik_str": 320193, "ticker": "ABC", "title": "ABC Corp"}}
        return {"filings": {"recent": {
            "form": [f for f, _ in self.forms],
            "filingDate": [(TODAY - timedelta(days=d)).isoformat() for _, d in self.forms],
            "accessionNumber": [f"0000000000-26-00000{i}" for i in range(len(self.forms))],
            "primaryDocument": ["xslF345X05/form4.xml"] * len(self.forms),
        }}}


class SecTests(unittest.TestCase):
    def test_parse_form4_counts_only_open_market_buys(self):
        name, value = parse_form4_purchases(FORM4)
        self.assertEqual(name, "Jane CEO")
        self.assertAlmostEqual(value, 255_000.0)

    def test_insider_buying_uses_raw_xml_url(self):
        fetch = FakeFetch([("4", 5), ("4", 200), ("10-Q", 30)])
        act = SecClient("me@example.com", fetch, TODAY).insider_buying("ABC")
        self.assertEqual(act.buyers, ["Jane CEO"])
        self.assertTrue(any(u.endswith("/320193/000000000026000000/form4.xml") for u in fetch.urls))

    def test_penny_filings_check(self):
        ok = SecClient("me", FakeFetch([("10-Q", 30)]), TODAY).penny_filings_check("ABC")
        self.assertTrue(ok[0])
        dilution = SecClient("me", FakeFetch([("424B5", 10), ("10-Q", 30)]), TODAY)
        self.assertIn("dilution", dilution.penny_filings_check("ABC")[1])
        late = SecClient("me", FakeFetch([("NT 10-K", 100), ("10-Q", 30)]), TODAY)
        self.assertIn("late", late.penny_filings_check("ABC")[1])
        stale = SecClient("me", FakeFetch([("10-Q", 400)]), TODAY)
        self.assertFalse(stale.penny_filings_check("ABC")[0])
        unknown = SecClient("me", FakeFetch([]), TODAY)
        self.assertFalse(unknown.penny_filings_check("ZZZ")[0])  # fails closed


class CongressTests(unittest.TestCase):
    def trades(self):
        rows = []
        for i in range(6):  # good member: every buy went up
            rows.append({"Representative": "Good Rep", "Ticker": f"G{i}", "Transaction": "Purchase",
                         "TransactionDate": str(TODAY - timedelta(days=200 + i)),
                         "ReportDate": str(TODAY - timedelta(days=180))})
        for i in range(6):  # bad member: every buy went down
            rows.append({"Representative": "Bad Rep", "Ticker": f"B{i}", "Transaction": "Purchase",
                         "TransactionDate": str(TODAY - timedelta(days=200 + i)),
                         "ReportDate": str(TODAY - timedelta(days=180))})
        rows.append({"Representative": "Good Rep", "Ticker": "NEW", "Transaction": "Purchase",
                     "TransactionDate": str(TODAY - timedelta(days=20)), "ReportDate": str(TODAY - timedelta(days=5))})
        rows.append({"Representative": "Bad Rep", "Ticker": "NEW", "Transaction": "Purchase",
                     "TransactionDate": str(TODAY - timedelta(days=20)), "ReportDate": str(TODAY - timedelta(days=5))})
        rows.append({"Representative": "x", "Ticker": "", "Transaction": "Purchase"})  # junk row
        return parse_trades(rows)

    @staticmethod
    def price_on(symbol, day):
        if day > TODAY - timedelta(days=170):  # the "after" price
            return 110.0 if symbol.startswith("G") else 90.0
        return 100.0

    def test_leaderboard_and_top_buys(self):
        trades = self.trades()
        board = build_leaderboard(trades, self.price_on, TODAY)
        self.assertTrue(board["Good Rep"].is_top)
        self.assertFalse(board["Bad Rep"].is_top)
        buys = top_member_buys(trades, board, "NEW", TODAY)
        self.assertEqual([t.member for t in buys], ["Good Rep"])


class FakeSec:
    def __init__(self, buyers=(), value=0.0):
        self.buyers, self.value = list(buyers), value

    def insider_buying(self, symbol):
        from autotrader.signals.sec import InsiderActivity
        return InsiderActivity(self.buyers, self.value, TODAY)


class NoQuiver:
    def recent_trades(self):
        return []


class SignalBookTests(unittest.TestCase):
    def test_insider_boost(self):
        book = SignalBook(FakeSec(["A", "B"], 500_000), NoQuiver(), today=lambda: TODAY)
        sig = book.for_symbol("ABC")
        self.assertAlmostEqual(sig.boost, 0.10)
        self.assertIn("2 insider", sig.reasons[0])

    def test_small_insider_buys_ignored(self):
        book = SignalBook(FakeSec(["A"], 5_000), NoQuiver(), today=lambda: TODAY)
        self.assertEqual(book.for_symbol("ABC").boost, 0.0)


def fake_client(view=None, stop_reason="tool_use", error=None):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if error:
            raise error
        content = []
        if view is not None:
            content.append(SimpleNamespace(type="tool_use", name="submit_view", input=view))
        return SimpleNamespace(stop_reason=stop_reason, content=content)

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    return client, calls


class AnalystTests(unittest.TestCase):
    def test_review_parses_view_and_sends_expected_request(self):
        client, calls = fake_client({"action": "buy", "confidence": 0.8, "reasoning": "Solid.", "red_flags": []})
        view = Analyst(client).review({"symbol": "ABC"})
        self.assertTrue(view.approves)
        req = calls[0]
        self.assertEqual(req["model"], "claude-opus-5-5")
        self.assertEqual(req["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", req["betas"])
        self.assertEqual({t["name"] for t in req["tools"]}, {"web_search", "submit_view"})
        self.assertNotIn("tool_choice", req)

    def test_refusal_and_errors_raise_unavailable(self):
        client, _ = fake_client(stop_reason="refusal")
        with self.assertRaises(AnalystUnavailable):
            Analyst(client).review({"symbol": "ABC"})
        client, _ = fake_client(error=RuntimeError("down"))
        with self.assertRaises(AnalystUnavailable):
            Analyst(client).review({"symbol": "ABC"})


class StubAnalyst:
    def __init__(self, action="buy", confidence=0.9, fail=False):
        self.action, self.confidence, self.fail, self.calls = action, confidence, fail, 0

    def review(self, candidate):
        self.calls += 1
        if self.fail:
            raise AnalystUnavailable("down")
        from autotrader.analyst import AnalystView
        return AnalystView(self.action, self.confidence, "Because.", ["earnings next week"] if self.action == "skip" else [])


class EngineWithAnalystTests(unittest.TestCase):
    def run_with(self, analyst, closes=None):
        broker = SimBroker(100_000, {"AAPL": make_bars(closes or uptrend())})
        eng = Engine(broker, Settings().clamped(), Journal(), analyst=analyst)
        return eng, broker, eng.run_cycle()

    def test_analyst_approval_buys_and_is_logged(self):
        eng, broker, report = self.run_with(StubAnalyst())
        self.assertEqual(len(broker.orders), 1)
        self.assertTrue(any("Analyst" in r for r in eng.journal.trades("test")[0]["reasons"]))
        self.assertEqual(len(eng.journal.views("test")), 1)

    def test_analyst_skip_blocks_buy(self):
        _, broker, report = self.run_with(StubAnalyst(action="skip"))
        self.assertEqual(broker.orders, [])
        self.assertTrue(any("earnings next week" in s for s in report.skipped))

    def test_low_confidence_blocks_buy(self):
        _, broker, _ = self.run_with(StubAnalyst(confidence=0.4))
        self.assertEqual(broker.orders, [])

    def test_view_cached_for_the_day(self):
        analyst = StubAnalyst(action="skip")
        eng, _, _ = self.run_with(analyst)
        eng.run_cycle()
        self.assertEqual(analyst.calls, 1)

    def test_analyst_outage_still_trades_normal_stocks(self):
        _, broker, _ = self.run_with(StubAnalyst(fail=True))
        self.assertEqual(len(broker.orders), 1)

    def test_penny_stock_needs_analyst(self):
        bars = make_bars(uptrend(start=1.0), volume=5_000_000)
        broker = SimBroker(100_000, {"PNY": bars, "SPY": make_bars(uptrend())})  # calm market
        eng = Engine(broker, Settings().clamped(), Journal(),
                     filings_check=lambda s: (True, "ok"), penny_needs_analyst=True)
        eng.s = Settings(always_watch=("PNY",), never_buy=("SPY",)).clamped()
        report = eng.run_cycle()
        self.assertEqual(broker.orders, [])
        self.assertTrue(any("analyst" in s for s in report.skipped))


class BacktestTests(unittest.TestCase):
    def test_backtest_runs_and_beats_nothing_in_uptrend(self):
        history = {"AAPL": make_bars(uptrend(400)), "SPY": make_bars(uptrend(400, daily=0.001)),
                   "MSFT": make_bars(downtrend(400))}
        result = run_backtest(history, Settings().clamped(), cost_bps=10)
        self.assertGreater(result.overall.total_return, 0)
        self.assertIsNotNone(result.benchmark)
        self.assertGreater(result.trades, 0)
        self.assertIn("Bot:", result.summary())

    def test_no_peeking_ahead(self):
        # A stock that only starts trending after the test window can't be bought early.
        closes = [50.0] * 300 + uptrend(100, start=50.0)
        history = {"AAPL": make_bars(closes)}
        result = run_backtest(history, Settings().clamped(), warmup=252)
        first_buy_day = next((d for d, v in result.equity_curve if v != 100_000), None)
        self.assertTrue(first_buy_day is None or first_buy_day >= history["AAPL"][300].time)

    def test_stats(self):
        from datetime import datetime
        curve = [(datetime(2024, 1, 1), 100.0), (datetime(2024, 6, 1), 120.0), (datetime(2025, 1, 1), 90.0)]
        s = stats(curve)
        self.assertAlmostEqual(s.total_return, -0.10)
        self.assertAlmostEqual(s.max_drawdown, 0.25)


if __name__ == "__main__":
    unittest.main()
