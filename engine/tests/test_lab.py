"""Strategy lab: one tested rule change a week, kept only when it wins on unseen prices."""

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from autotrader.config import Mode, Settings
from autotrader.engine import Engine
from autotrader.journal import Journal
from autotrader.lab import Lab, apply_rules, clamp_rules, next_idea, split_history
from autotrader.market import MARKET

from helpers import make_bars, uptrend
from test_final_check import AUTH, TOKEN, SlowBroker, TestClient

NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)


def result(sharpe, ret=0.1, drop=0.05):
    overall = SimpleNamespace(sharpe=sharpe, total_return=ret, max_drawdown=drop)
    return SimpleNamespace(overall=overall, trades=10, win_rate=0.5)


class FakeStore:
    def __init__(self):
        self.data = {"test": {}, "live": {}}

    def get(self, mode):
        return dict(self.data[mode.value])

    def update(self, mode, changes):
        self.data[mode.value].update(changes)


def history():
    return {"AAA": make_bars(uptrend(900)), "SPY": make_bars(uptrend(900))}


class RulesTests(unittest.TestCase):
    def test_only_known_values_inside_the_limits(self):
        self.assertEqual(clamp_rules({"atr_stop_multiple": 9, "max_position_pct": 0.5,
                                      "min_score": "0.2"}), {"min_score": 0.2})
        s = apply_rules(Settings(), {"atr_stop_multiple": 3.0})
        self.assertEqual(s.atr_stop_multiple, 3.0)

    def test_one_step_on_one_knob_and_no_quick_retries(self):
        idea = next_idea({}, Settings(), {}, NOW)
        self.assertEqual((idea.knob, idea.value), ("atr_stop_multiple", 2.5))
        tried = {idea.key: NOW.isoformat()}
        self.assertEqual(next_idea({}, Settings(), tried, NOW).value, 1.5)
        later = NOW + timedelta(days=31)
        self.assertEqual(next_idea({}, Settings(), tried, later).value, 2.5)

    def test_unseen_stretch_never_overlaps_the_tuning_stretch(self):
        tune, unseen = split_history(history())
        last_tuned = max(b.time for b in tune["AAA"])
        tested = unseen["AAA"][252:]  # after its own warm-up year
        self.assertTrue(all(b.time > last_tuned for b in tested))


class LabTests(unittest.TestCase):
    def lab(self, results, losers=None):
        """results: Sharpe pairs (current, new) per stretch, in the order the lab runs them."""
        self.store = FakeStore()
        self.events = []
        queue = [result(*x) if isinstance(x, tuple) else result(x) for x in results]

        def backtest(hist, settings, journal=None):
            out = queue.pop(0)
            if journal is not None and losers is not None:
                new = settings.atr_stop_multiple != 2.0
                for sym, cur, nxt in losers:
                    sell = nxt if new else cur
                    if sell is None:
                        continue
                    journal.record_trade("test", sym, "buy", 1, 100, None, ["Score 0.5"], ["trend"])
                    journal.record_trade("test", sym, "sell", 1, sell, None, ["Hit stop"])
            return out

        return Lab(self.store, history, lambda: Settings(), backtest, now=lambda: NOW,
                   events=lambda m, msg: self.events.append((m, msg)))

    def test_kept_only_after_passing_every_gate(self):
        lab = self.lab([1.0, 1.2, 0.8, (1.0, 0.15)], losers=[("BAD", 90, 97), ("GONE", 92, None)])
        entry = lab.run()
        self.assertTrue(entry["kept"], entry)
        self.assertEqual(lab.rules("test"), {"atr_stop_multiple": 2.5})
        self.assertEqual(lab.rules("live"), {})  # Live waits for you
        self.assertTrue(any("BAD" in e for e in entry["evidence"]))
        self.assertTrue(any("not bought" in e for e in entry["evidence"]))
        lab.approve_live()
        self.assertEqual(lab.rules("live"), {"atr_stop_multiple": 2.5})
        lab.undo("live")
        self.assertEqual(lab.rules("live"), {})

    def test_rejected_when_it_fails_on_unseen_prices(self):
        lab = self.lab([1.0, 1.2, 0.8, 0.85])  # gain of 0.05 is under the 0.10 needed
        entry = lab.run()
        self.assertFalse(entry["kept"])
        self.assertIn("unseen", entry["verdict"])
        self.assertEqual(lab.rules("test"), {})

    def test_rejected_early_when_not_better_on_older_prices(self):
        lab = self.lab([1.0, 0.9])
        entry = lab.run()
        self.assertFalse(entry["kept"])
        self.assertNotIn("unseen", entry)

    def test_needs_evidence(self):
        lab = self.lab([1.0, 1.2, 0.8, (1.0, 0.15)], losers=[])
        self.assertFalse(lab.run()["kept"])

    def test_weekly(self):
        lab = self.lab([1.0, 0.9])
        self.assertTrue(lab.due())
        lab.run()
        lab._last_try = None
        self.assertFalse(lab.due())


class MinScoreTests(unittest.TestCase):
    def test_min_score_blocks_weak_trends(self):
        broker = SlowBroker(100_000, {"AAPL": make_bars(uptrend()), MARKET: make_bars(uptrend())})
        from dataclasses import replace
        report = Engine(broker, replace(Settings(), min_score=0.99).clamped(), Journal()).run_cycle()
        self.assertFalse(broker.orders)
        self.assertTrue(any("minimum" in s for s in report.skipped))


@unittest.skipIf(TestClient is None, "fastapi not installed")
class LabServerTests(unittest.TestCase):
    def test_lab_rules_reach_the_bot_and_live_needs_approval(self):
        from autotrader.server import BotRunner, SettingsStore, create_app

        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal()
            store = SettingsStore(f"{tmp}/s.json")
            brokers = {m: SlowBroker(100_000, {"AAPL": make_bars(uptrend())}) for m in Mode}
            runners = {m: BotRunner(m, store, journal, threading.RLock(), lambda mode: brokers[mode])
                       for m in Mode}
            runners[Mode.TEST].lab = Lab(store, history, lambda: store.settings_for(Mode.TEST, with_lab=False))
            c = TestClient(create_app(runners, journal, TOKEN, live_enabled=True))
            store.update(Mode.TEST, {"lab_rules": {"atr_stop_multiple": 2.5}})
            store.update(Mode.LIVE, {"lab_proposed": {"rules": {"atr_stop_multiple": 2.5},
                                                      "entry": {"change": "x"}}})
            self.assertEqual(runners[Mode.TEST].settings.atr_stop_multiple, 2.5)
            self.assertEqual(runners[Mode.LIVE].settings.atr_stop_multiple, 2.0)
            live = c.get("/api/live/lab", headers=AUTH).json()
            self.assertEqual(live["proposed"]["changes"][0]["now"], 2.5)
            c.post("/api/live/lab", headers=AUTH, json={"decision": "approve"})
            self.assertEqual(runners[Mode.LIVE].settings.atr_stop_multiple, 2.5)
            c.post("/api/test/lab/undo", headers=AUTH)
            self.assertEqual(runners[Mode.TEST].settings.atr_stop_multiple, 2.0)
            self.assertEqual(c.get("/api/test/lab", headers=AUTH).json()["changes"], [])


if __name__ == "__main__":
    unittest.main()
