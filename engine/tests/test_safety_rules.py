"""Safety rules you can change in the app: ranges, Live protections, and how they mix with the lab."""

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

try:
    from fastapi.testclient import TestClient
except ImportError:  # server extras not installed
    TestClient = None

from autotrader.broker.sim import SimBroker
from autotrader.config import HARD_LIMITS, RULES, RULES_BY_KEY, Mode, Settings
from autotrader.journal import Journal
from autotrader.lab import next_idea

from helpers import make_bars, uptrend

TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
# Tuesday 2026-11-10, 2pm in New York
TUESDAY_2PM = datetime(2026, 11, 10, 19, 0, tzinfo=timezone.utc)


class RuleTableTests(unittest.TestCase):
    def test_every_range_sits_inside_the_hard_limits(self):
        h = HARD_LIMITS
        hard_max = {"max_position_pct": h.max_position_pct, "max_open_positions": h.max_open_positions,
                    "daily_loss_pct": h.max_daily_loss_pct, "risk_per_trade_pct": h.max_risk_per_trade_pct,
                    "max_new_buys_per_day": h.max_new_buys_per_day, "max_sector_pct": h.max_sector_pct}
        for key, top in hard_max.items():
            self.assertLessEqual(RULES_BY_KEY[key].high, top, key)
        self.assertGreaterEqual(RULES_BY_KEY["cash_reserve_pct"].low, h.min_cash_reserve_pct)
        for rule in RULES:
            self.assertTrue(rule.low <= rule.recommended <= rule.high, rule.key)
            self.assertEqual(rule.check(rule.recommended), rule.recommended)

    def test_check_rounds_and_refuses(self):
        r = RULES_BY_KEY["max_position_pct"]
        self.assertAlmostEqual(r.check(0.0712), 0.07)
        for bad in (0.2, 0.0, -1, float("nan"), "lots"):
            with self.assertRaises(ValueError):
                r.check(bad)
        self.assertIsInstance(RULES_BY_KEY["max_open_positions"].check(12.4), int)

    def test_looser_direction(self):
        self.assertTrue(RULES_BY_KEY["max_position_pct"].looser(0.05, 0.06))
        self.assertFalse(RULES_BY_KEY["max_position_pct"].looser(0.05, 0.04))
        self.assertTrue(RULES_BY_KEY["cash_reserve_pct"].looser(0.10, 0.05))  # less cash = more risk
        self.assertFalse(RULES_BY_KEY["cash_reserve_pct"].looser(0.10, 0.20))


class StoreTests(unittest.TestCase):
    def setUp(self):
        from autotrader.server import SettingsStore
        self.tmp = tempfile.TemporaryDirectory()
        self.path = f"{self.tmp.name}/app_settings.json"
        self.store = SettingsStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def live_running_for(self, days):
        started = datetime.now(timezone.utc) - timedelta(days=days)
        self.store.update(Mode.LIVE, {"first_started": started.isoformat()})

    def test_test_changes_start_now_both_ways(self):
        out = self.store.change_rules(Mode.TEST, {"max_position_pct": 0.08, "cash_reserve_pct": 0.05},
                                      now=TUESDAY_2PM)
        self.assertEqual(len(out["applied"]), 2)
        s = self.store.settings_for(Mode.TEST)
        self.assertEqual((s.max_position_pct, s.cash_reserve_pct), (0.08, 0.05))
        self.assertEqual(self.store.settings_for(Mode.LIVE).max_position_pct, 0.05)  # Live untouched

    def test_out_of_range_changes_nothing(self):
        with self.assertRaises(ValueError):
            self.store.change_rules(Mode.TEST, {"max_position_pct": 0.08, "max_open_positions": 99})
        with self.assertRaises(ValueError):
            self.store.change_rules(Mode.TEST, {"budget": 5})  # not a rule
        self.assertEqual(self.store.settings_for(Mode.TEST).max_position_pct, 0.05)

    def test_live_starter_period_only_allows_safer(self):
        self.live_running_for(3)
        self.store.change_rules(Mode.LIVE, {"max_position_pct": 0.03})  # safer: fine
        self.assertEqual(self.store.settings_for(Mode.LIVE).max_position_pct, 0.03)
        with self.assertRaisesRegex(ValueError, "only be made safer"):
            self.store.change_rules(Mode.LIVE, {"max_position_pct": 0.08})
        with self.assertRaisesRegex(ValueError, "only be made safer"):  # back to 5% is riskier than 3%
            self.store.change_rules(Mode.LIVE, {"max_position_pct": None})

    def test_live_loosening_waits_for_the_open(self):
        self.live_running_for(40)
        now = TUESDAY_2PM
        out = self.store.change_rules(Mode.LIVE, {"max_position_pct": 0.08, "daily_loss_pct": 0.01}, now=now)
        self.assertEqual(len(out["applied"]), 1)  # the tighter daily-loss stop starts now
        self.assertEqual(len(out["waiting"]), 1)
        opens = datetime.fromisoformat(out["starts"])
        self.assertEqual(opens, datetime(2026, 11, 11, 14, 30, tzinfo=timezone.utc))  # Wed 9:30am ET
        self.assertEqual(self.store.settings_for(Mode.LIVE, now=now).max_position_pct, 0.05)
        self.assertEqual(self.store.settings_for(Mode.LIVE, now=now).daily_loss_pct, 0.01)
        self.assertIn("max_position_pct", self.store.pending_rules(Mode.LIVE, now))
        after = opens + timedelta(minutes=1)
        self.assertEqual(self.store.settings_for(Mode.LIVE, now=after).max_position_pct, 0.08)
        self.assertEqual(self.store.pending_rules(Mode.LIVE, after), {})

    def test_safer_change_cancels_a_waiting_riskier_one(self):
        self.live_running_for(40)
        self.store.change_rules(Mode.LIVE, {"max_position_pct": 0.08}, now=TUESDAY_2PM)
        self.store.change_rules(Mode.LIVE, {"max_position_pct": 0.04}, now=TUESDAY_2PM)
        self.assertEqual(self.store.pending_rules(Mode.LIVE, TUESDAY_2PM), {})
        later = TUESDAY_2PM + timedelta(days=1)
        self.assertEqual(self.store.settings_for(Mode.LIVE, now=later).max_position_pct, 0.04)

    def test_your_rule_beats_the_lab_and_reset_hands_back(self):
        self.store.update(Mode.TEST, {"lab_rules": {"atr_stop_multiple": 2.5}})
        self.assertEqual(self.store.settings_for(Mode.TEST).atr_stop_multiple, 2.5)
        self.store.change_rules(Mode.TEST, {"atr_stop_multiple": 3.0})
        self.assertEqual(self.store.settings_for(Mode.TEST).atr_stop_multiple, 3.0)
        self.store.change_rules(Mode.TEST, {"atr_stop_multiple": None})
        self.assertEqual(self.store.settings_for(Mode.TEST).atr_stop_multiple, 2.5)

    def test_saved_rules_survive_reload_and_bad_file_values_are_ignored(self):
        from autotrader.server import SettingsStore
        self.store.change_rules(Mode.TEST, {"max_open_positions": 10})
        self.store.update(Mode.TEST, {"my_rules": {"max_open_positions": 10, "max_position_pct": 0.5}})
        again = SettingsStore(self.path)
        s = again.settings_for(Mode.TEST)
        self.assertEqual(s.max_open_positions, 10)
        self.assertEqual(s.max_position_pct, 0.05)  # 50% is outside the range, so it's dropped

    def test_lab_leaves_your_rules_alone(self):
        base = Settings()
        idea = next_idea({}, base, {}, TUESDAY_2PM)
        self.assertEqual(idea.knob, "atr_stop_multiple")
        idea = next_idea({}, base, {}, TUESDAY_2PM, fixed={"atr_stop_multiple"})
        self.assertNotEqual(idea.knob, "atr_stop_multiple")


class MarketOpenTests(unittest.TestCase):
    def test_next_open(self):
        from autotrader.server import next_market_open
        utc = timezone.utc
        # Before the open on a weekday: today. After it: the next weekday.
        self.assertEqual(next_market_open(datetime(2026, 11, 10, 13, 0, tzinfo=utc)),
                         datetime(2026, 11, 10, 14, 30, tzinfo=utc))
        # Friday afternoon -> Monday
        self.assertEqual(next_market_open(datetime(2026, 11, 13, 20, 0, tzinfo=utc)),
                         datetime(2026, 11, 16, 14, 30, tzinfo=utc))
        # Summer time: 9:30am ET is 13:30 UTC
        self.assertEqual(next_market_open(datetime(2026, 7, 1, 20, 0, tzinfo=utc)),
                         datetime(2026, 7, 2, 13, 30, tzinfo=utc))


@unittest.skipIf(TestClient is None, "fastapi not installed")
class RulesApiTests(unittest.TestCase):
    def setUp(self):
        from autotrader.server import BotRunner, SettingsStore, create_app
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Journal()
        self.store = SettingsStore(f"{self.tmp.name}/app_settings.json")
        lock = threading.RLock()
        brokers = {m: SimBroker(100_000, {"AAPL": make_bars(uptrend())}) for m in Mode}
        self.runners = {m: BotRunner(m, self.store, self.journal, lock, lambda mode: brokers[mode])
                        for m in Mode}
        self.c = TestClient(create_app(self.runners, self.journal, TOKEN, live_enabled=True))

    def tearDown(self):
        self.tmp.cleanup()

    def test_get_lists_every_rule(self):
        r = self.c.get("/api/test/rules", headers=AUTH).json()
        self.assertEqual([x["key"] for x in r["rules"]], [x.key for x in RULES])
        first = r["rules"][0]
        self.assertEqual((first["value"], first["recommended"], first["set_by"]), (0.05, 0.05, "recommended"))
        self.assertFalse(r["starter_lock"])
        self.assertTrue(self.c.get("/api/live/rules", headers=AUTH).json()["starter_lock"])

    def test_put_changes_logs_and_resets(self):
        r = self.c.put("/api/test/rules", headers=AUTH, json={"values": {"max_open_positions": 10}})
        self.assertEqual(r.status_code, 200)
        row = next(x for x in r.json()["rules"] if x["key"] == "max_open_positions")
        self.assertEqual((row["value"], row["set_by"]), (10, "you"))
        self.assertEqual(self.runners[Mode.TEST].settings.max_open_positions, 10)
        self.assertTrue(any("Max stocks at once: 15 → 10" in e["message"]
                            for e in self.journal.events("test")))
        self.c.put("/api/test/rules", headers=AUTH, json={"reset": True})
        self.assertEqual(self.runners[Mode.TEST].settings.max_open_positions, 15)

    def test_put_refuses_bad_values(self):
        r = self.c.put("/api/test/rules", headers=AUTH, json={"values": {"max_position_pct": 0.5}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("between", r.json()["detail"])
        r = self.c.put("/api/live/rules", headers=AUTH, json={"values": {"max_position_pct": 0.08}})
        self.assertEqual(r.status_code, 400)  # Live starter period
        self.assertEqual(self.c.put("/api/test/rules", json={"values": {}}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
