"""Monthly AI spending cap."""

import tempfile
import threading
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from autotrader.analyst import Analyst, AnalystUnavailable
from autotrader.config import Mode
from autotrader.journal import Journal
from autotrader.spend import (MeteredClient, SpendingCapReached, SpendTracker, cost_of,
                              most_it_could_cost)

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
        self.assertAlmostEqual(cost_of("claude-haiku-5-5-20261001", usage(1_000_000)), 0.10)


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
        self.analyst.review({"symbol": "A"})  # its worst case (~$0.70) fitted; the fake charged $4
        with self.assertRaises(AnalystUnavailable) as e:
            self.analyst.review({"symbol": "A"})
        self.assertIn("spending cap", str(e.exception))
        self.assertEqual(self.inner.calls, 2)  # the third call never reached Claude
        self.month[0] = datetime(2026, 12, 1, tzinfo=timezone.utc)
        self.analyst.review({"symbol": "A"})
        self.assertEqual(self.tracker.report()["spent"], 4.0)

    def test_calls_running_at_once_cant_pass_the_cap(self):
        worst = most_it_could_cost({"model": "claude-opus-5-5", "max_tokens": 16000,
                                    "tools": [{"type": "web_search_20260209", "max_uses": 3}]})
        self.assertGreater(worst, 0.3)  # 16k output tokens alone are $0.32 on Opus
        tracker = SpendTracker(self.journal, lambda: 1.0, now=lambda: self.month[0])
        tracker.reserve(0.6)  # one call still running
        with self.assertRaises(SpendingCapReached):
            tracker.reserve(0.6)  # a second one could take the month to $1.20
        tracker.release(0.6)
        tracker.reserve(0.6)  # fits again once the first has settled

    def test_streamed_answers_are_counted(self):
        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_final_message(self):
                return SimpleNamespace(model="claude-opus-5-5", usage=usage(100_000, 10_000))

        tracker = SpendTracker(self.journal, lambda: 5.0, now=lambda: self.month[0])
        raw = SimpleNamespace(messages=None,
                              beta=SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: Stream())))
        client = MeteredClient(raw, tracker)
        with client.beta.messages.stream(model="claude-opus-5-5", max_tokens=1000,
                                         system="Answer", messages=[]):
            self.assertGreater(tracker._reserved, 0)
        self.assertEqual(tracker._reserved, 0.0)
        self.assertAlmostEqual(tracker.spent(), 0.4 + 0.2)  # 100k in at $4/M, 10k out at $20/M

    def test_a_failed_call_gives_its_reservation_back(self):
        def boom(**kw):
            raise RuntimeError("network")
        tracker = SpendTracker(self.journal, lambda: 1.0, now=lambda: self.month[0])
        raw = SimpleNamespace(messages=SimpleNamespace(create=boom),
                              beta=SimpleNamespace(messages=None))
        client = MeteredClient(raw, tracker)
        with self.assertRaises(RuntimeError):
            client.messages.create(model="claude-haiku-5-5", max_tokens=1000, messages=[])
        self.assertEqual(tracker._reserved, 0.0)


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
