"""Monthly AI spending cap."""

import tempfile
import threading
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from autotrader.analyst import Analyst, AnalystUnavailable
from autotrader.config import Mode
from autotrader.journal import Journal
from autotrader.spend import MeteredClient, SpendTracker, cost_of

from helpers import make_bars, uptrend
from test_final_check import AUTH, TOKEN, SlowBroker, TestClient


def usage(i=1_000_000, o=0, searches=0):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0,
                           server_tool_use=SimpleNamespace(web_search_requests=searches))


class CostTests(unittest.TestCase):
    def test_prices(self):
        self.assertAlmostEqual(cost_of("claude-opus-5-5", usage(1_000_000, 100_000)), 6.0)
        self.assertAlmostEqual(cost_of("claude-haiku-5-5", usage(1_000_000)), 0.10)
        self.assertAlmostEqual(cost_of("claude-haiku-5-5", usage(0, 0, searches=3)), 0.03)
        self.assertEqual(cost_of("x", None), 0.0)


class FakeMessages:
    def __init__(self):
        self.calls = 0

    def create(self, **kw):
        self.calls += 1
        tool = SimpleNamespace(type="tool_use", name="submit_view",
                               input={"action": "buy", "confidence": 0.8, "reasoning": "r", "red_flags": []})
        return SimpleNamespace(model=kw["model"], usage=usage(1_000_000), stop_reason="tool_use",
                               content=[tool])


class CapTests(unittest.TestCase):
    def setUp(self):
        self.journal = Journal()
        self.month = [datetime(2026, 11, 4, tzinfo=timezone.utc)]
        self.tracker = SpendTracker(self.journal, lambda: 5.0, now=lambda: self.month[0])
        self.inner = FakeMessages()
        raw = SimpleNamespace(messages=self.inner, beta=SimpleNamespace(messages=self.inner))
        self.analyst = Analyst(MeteredClient(raw, self.tracker))

    def test_stops_at_the_cap_and_resets_next_month(self):
        self.analyst.review({"symbol": "A"})  # $4
        self.analyst.review({"symbol": "A"})  # $8: over the $5 cap now
        with self.assertRaises(AnalystUnavailable) as e:
            self.analyst.review({"symbol": "A"})
        self.assertIn("spending cap", str(e.exception))
        self.assertEqual(self.inner.calls, 2)  # the third call never reached Claude
        self.month[0] = datetime(2026, 12, 1, tzinfo=timezone.utc)
        self.analyst.review({"symbol": "A"})
        self.assertEqual(self.tracker.report()["spent"], 4.0)


@unittest.skipIf(TestClient is None, "fastapi not installed")
class SpendingServerTests(unittest.TestCase):
    def test_cap_is_shown_and_editable(self):
        from autotrader.server import BotRunner, SettingsStore, create_app

        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal()
            store = SettingsStore(f"{tmp}/s.json")
            brokers = {m: SlowBroker(100_000, {"AAPL": make_bars(uptrend())}) for m in Mode}
            runners = {m: BotRunner(m, store, journal, threading.RLock(), lambda mode: brokers[mode])
                       for m in Mode}
            tracker = SpendTracker(journal, lambda: store.ai_cap(20.0))
            c = TestClient(create_app(runners, journal, TOKEN, live_enabled=True, spend=tracker))
            self.assertEqual(c.get("/api/ai/spending", headers=AUTH).json()["cap"], 20.0)
            r = c.put("/api/ai/spending", headers=AUTH, json={"monthly_cap": 35})
            self.assertEqual(r.json()["cap"], 35.0)
            self.assertEqual(SettingsStore(f"{tmp}/s.json").ai_cap(20.0), 35.0)  # saved
            self.assertEqual(c.put("/api/ai/spending", headers=AUTH,
                                   json={"monthly_cap": -1}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
