"""Cheaper AI reviews: the fast model first, the full analyst for close calls."""

import tempfile
import threading
import unittest
from dataclasses import replace
from types import SimpleNamespace

from autotrader.analyst import QUICK_MODEL, Analyst, AnalystUnavailable, AnalystView
from autotrader.config import Mode, Settings
from autotrader.engine import Engine
from autotrader.journal import Journal
from autotrader.market import MARKET
from autotrader.models import Side

from helpers import make_bars, uptrend
from test_final_check import AUTH, TOKEN, SlowBroker, TestClient


class FakeAnalyst:
    supports_quick = True

    def __init__(self, quick, full=AnalystView("buy", 0.8, "full look", []), symbol="AAPL"):
        self.quick, self.full, self.calls, self.symbol = quick, full, [], symbol

    def review(self, candidate, quick=False):
        if candidate["symbol"] == self.symbol:  # SPY can be a candidate too; watch just this one
            self.calls.append("quick" if quick else "full")
        if quick:
            if isinstance(self.quick, Exception):
                raise self.quick
            return replace(self.quick, model=QUICK_MODEL)
        return self.full


class RoutingTests(unittest.TestCase):
    def run_with(self, analyst, quick_reviews=True):
        broker = SlowBroker(100_000, {"AAPL": make_bars(uptrend()), MARKET: make_bars(uptrend())})
        journal = Journal()
        s = replace(Settings(), quick_reviews=quick_reviews).clamped()
        Engine(broker, s, journal, analyst=analyst).run_cycle()
        bought = [o for o in broker.orders if o.side is Side.BUY]
        return bought, journal

    def test_confident_quick_buy_skips_the_full_review(self):
        a = FakeAnalyst(AnalystView("buy", 0.9, "clear", []))
        bought, journal = self.run_with(a)
        self.assertEqual(a.calls, ["quick"])
        self.assertTrue(bought)
        aapl = [v for v in journal.views("test") if v["symbol"] == "AAPL"]
        self.assertEqual(aapl[0]["model"], QUICK_MODEL)

    def test_quick_skip_with_a_red_flag_stands(self):
        a = FakeAnalyst(AnalystView("skip", 0.3, "offering", ["share offering"]))
        bought, _ = self.run_with(a)
        self.assertEqual(a.calls, ["quick"])
        self.assertFalse(bought)

    def test_close_calls_go_to_the_full_analyst(self):
        for quick in [AnalystView("buy", 0.65, "meh", []),           # buy, but not confident enough
                      AnalystView("buy", 0.95, "but", ["earnings"]),  # buy with a red flag
                      AnalystView("skip", 0.4, "unsure", []),          # skip with no reason
                      AnalystUnavailable("busy")]:
            a = FakeAnalyst(quick)
            bought, _ = self.run_with(a)
            self.assertEqual(a.calls, ["quick", "full"], quick)
            self.assertTrue(bought)

    def test_off_means_full_reviews_only(self):
        a = FakeAnalyst(AnalystView("buy", 0.9, "clear", []))
        self.run_with(a, quick_reviews=False)
        self.assertEqual(a.calls, ["full"])

    def test_penny_stocks_always_get_the_full_analyst(self):
        closes = [c / 60 for c in uptrend(daily=0.004)]
        broker = SlowBroker(100_000, {"PENY": make_bars(closes, volume=50_000_000), MARKET: make_bars(uptrend())})
        a = FakeAnalyst(AnalystView("buy", 0.99, "clear", []), AnalystView("buy", 0.9, "ok", []), "PENY")
        s = replace(Settings(), quick_reviews=True, always_watch=("PENY",)).clamped()
        Engine(broker, s, Journal(), analyst=a).run_cycle()
        self.assertEqual(a.calls, ["full"])


class AnalystRequestTests(unittest.TestCase):
    def test_quick_uses_the_fast_model_without_unsupported_options(self):
        sent = []
        tool = SimpleNamespace(type="tool_use", name="submit_view",
                               input={"action": "buy", "confidence": 0.8, "reasoning": "r", "red_flags": []})

        class Messages:
            def create(self, **kw):
                sent.append(kw)
                return SimpleNamespace(stop_reason="tool_use", content=[tool])

        client = SimpleNamespace(beta=SimpleNamespace(messages=Messages()))
        view = Analyst(client).review({"symbol": "AAPL"}, quick=True)
        self.assertEqual(view.model, QUICK_MODEL)
        self.assertEqual(sent[0]["model"], QUICK_MODEL)
        self.assertNotIn("fallbacks", sent[0])  # not supported on the fast model
        self.assertEqual(sent[0]["tools"][0]["type"], "web_search_20250305")
        Analyst(client).review({"symbol": "AAPL"})
        self.assertEqual(sent[1]["fallbacks"], "default")


@unittest.skipIf(TestClient is None, "fastapi not installed")
class QuickReviewSettingTests(unittest.TestCase):
    def test_on_for_test_off_for_live_and_editable(self):
        from autotrader.server import BotRunner, SettingsStore, create_app

        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal()
            store = SettingsStore(f"{tmp}/s.json")
            brokers = {m: SlowBroker(100_000, {"AAPL": make_bars(uptrend())}) for m in Mode}
            runners = {m: BotRunner(m, store, journal, threading.RLock(), lambda mode: brokers[mode])
                       for m in Mode}
            c = TestClient(create_app(runners, journal, TOKEN, live_enabled=True))
            self.assertTrue(c.get("/api/test/settings", headers=AUTH).json()["quick_reviews"])
            self.assertFalse(c.get("/api/live/settings", headers=AUTH).json()["quick_reviews"])
            r = c.put("/api/live/settings", headers=AUTH, json={"quick_reviews": True})
            self.assertTrue(r.json()["quick_reviews"])


if __name__ == "__main__":
    unittest.main()
